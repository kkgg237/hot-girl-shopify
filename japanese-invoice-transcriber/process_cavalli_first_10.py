import os
import sys
import json
import openpyxl
import pandas as pd
import requests
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(usecwd=True), override=True)
serp_key = os.getenv("SERPAPI_KEY", "")

excel_path = '/home/kat/.hermes/cache/documents/doc_38416a2c6abc_2026_09_Cavalli_Confirmed_Purchase_List.xlsx'

# 1. Load dataframe for row text metadata
df = pd.read_excel(excel_path, sheet_name='Confirmed Purchase List')

# 2. Load openpyxl workbook for embedded images
wb = openpyxl.load_workbook(excel_path, data_only=True)
sheet = wb['Confirmed Purchase List']

# Map row index (0-indexed row in sheet, Row 1 = 0 (Header), Row 2 = 1 (Item 0))
row_images = {}
for img in sheet._images:
    row_idx = img.anchor._from.row  # 1 for Row 2 (Item 0), 2 for Row 3 (Item 1), etc.
    row_images[row_idx] = img

results = []

print(f"Processing first 10 items from Cavalli sheet...")

for idx in range(min(10, len(df))):
    row_data = df.iloc[idx]
    
    sku = str(row_data.get('SKU', '')).strip()
    brand = str(row_data.get('Brand', '')).strip()
    item_type = str(row_data.get('Item Type', '')).strip()
    size = str(row_data.get('Size', '')).strip()
    material = str(row_data.get('Material / Fabric', '')).strip()
    notes = str(row_data.get('Notes', '')).strip()
    if notes == '—':
        notes = ''
    orig_desc = str(row_data.get('Original Description', '')).strip()
    euro_price = str(row_data.get('Final Price (€)', '')).strip()
    
    # Get image bytes (Row 2 = index 1 in openpyxl _from.row)
    sheet_row_idx = idx + 1
    img_obj = row_images.get(sheet_row_idx)
    pub_url = ""
    visual_matches = []
    
    if img_obj:
        img_bytes = img_obj._data()
        img_ext = img_obj.format or "jpeg"
        if img_ext.lower() == "png":
            mime = "image/png"
        else:
            mime = "image/jpeg"
            
        # Upload to Litterbox CDN for direct unauthenticated public URL
        try:
            resp = requests.post(
                "https://litterbox.catbox.moe/resources/internals/api.php",
                data={"reqtype": "fileupload", "time": "72h"},
                files={"fileToUpload": (f"{sku}.{img_ext}", img_bytes, mime)},
                timeout=15,
            )
            if resp.status_code == 200 and resp.text.strip().startswith("http"):
                pub_url = resp.text.strip()
        except Exception as e:
            print(f"Failed to upload image for {sku}: {e}")
            
    # Query SerpAPI Google Lens if public image URL exists
    if pub_url and serp_key:
        try:
            serp_resp = requests.get(
                "https://serpapi.com/search.json",
                params={"engine": "google_lens", "url": pub_url, "api_key": serp_key},
                timeout=20,
            )
            if serp_resp.status_code == 200:
                sdata = serp_resp.json()
                visual_matches = sdata.get("visual_matches", [])
        except Exception as e:
            print(f"SerpAPI query failed for {sku}: {e}")

    # Extract top QA links and prices
    qa_links = []
    found_prices = []
    for vm in visual_matches[:10]:
        link = vm.get("link", "")
        source = vm.get("source") or "Web"
        v_title = vm.get("title", "")
        p_dict = vm.get("price") if isinstance(vm.get("price"), dict) else {}
        price_val = p_dict.get("value") or p_dict.get("extracted_value") or ""
        if price_val:
            found_prices.append(str(price_val))
        
        if link and len(qa_links) < 2:
            qa_links.append({"source": source, "title": v_title, "link": link, "price": str(price_val)})

    item_res = {
        "index": idx + 1,
        "sku": sku,
        "brand": brand,
        "item_type": item_type,
        "size": size,
        "material": material,
        "notes": notes,
        "orig_desc": orig_desc,
        "euro_price": euro_price,
        "public_image_url": pub_url,
        "visual_matches_count": len(visual_matches),
        "found_prices": found_prices,
        "qa_links": qa_links,
        "top_matches": visual_matches[:5],
    }
    results.append(item_res)
    print(f"Row {idx+1} [{sku}]: {brand} - {orig_desc} | Image URL: {pub_url[:35]}... | Matches: {len(visual_matches)}")

with open("cavalli_first_10_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

print("\nDone processing Cavalli sheet first 10 items!")

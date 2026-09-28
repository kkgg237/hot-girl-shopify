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

df = pd.read_excel(excel_path, sheet_name='Confirmed Purchase List')
wb = openpyxl.load_workbook(excel_path, data_only=True)
sheet = wb['Confirmed Purchase List']

row_images = {}
for img in sheet._images:
    row_idx = img.anchor._from.row
    row_images[row_idx] = img

results = []

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
    
    img_obj = row_images.get(idx + 1)
    pub_url = ""
    visual_matches = []
    
    if img_obj:
        img_bytes = img_obj._data()
        img_ext = img_obj.format or "jpeg"
        
        # Upload to Uguu CDN
        try:
            resp = requests.post(
                "https://uguu.se/upload",
                files={"files[]": (f"{sku}.{img_ext}", img_bytes, f"image/{img_ext}")},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                if "files" in data and len(data["files"]) > 0:
                    pub_url = data["files"][0]["url"]
        except Exception as e:
            print(f"Uguu upload failed for {sku}: {e}")
            
    if pub_url and serp_key:
        try:
            sresp = requests.get(
                "https://serpapi.com/search.json",
                params={"engine": "google_lens", "url": pub_url, "api_key": serp_key},
                timeout=20,
            )
            if sresp.status_code == 200:
                sdata = sresp.json()
                visual_matches = sdata.get("visual_matches", [])
        except Exception as e:
            print(f"SerpAPI Google Lens query failed for {sku}: {e}")
            
    qa_links = []
    for vm in visual_matches[:12]:
        link = vm.get("link", "")
        source = vm.get("source") or "Web"
        v_title = vm.get("title", "")
        p_dict = vm.get("price") if isinstance(vm.get("price"), dict) else {}
        price_val = p_dict.get("value") or p_dict.get("extracted_value") or ""
        
        if link and len(qa_links) < 3:
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
        "qa_links": qa_links,
        "visual_matches": visual_matches[:8],
    }
    results.append(item_res)
    print(f"Row {idx+1} [{sku}]: {brand} - {orig_desc} | Matches: {len(visual_matches)}")

with open("cavalli_10_processed.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

print("\nFinished processing Cavalli sheet first 10 items!")

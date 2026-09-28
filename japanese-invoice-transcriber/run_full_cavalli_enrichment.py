import openpyxl
import os
import sys
import time
import json
import re
import io
from pathlib import Path
from dotenv import load_dotenv, find_dotenv
import requests
import pandas as pd
from PIL import Image as PILImage
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.drawing.image import Image as OpenPyXLElementImage

load_dotenv(find_dotenv(usecwd=True), override=True)
sys.path.insert(0, '/home/kat/workspace/hot-girl-shopify/japanese-invoice-transcriber')

from reverse_search import (
    save_image_for_public_lens,
    extract_print_color_from_matches,
    extract_prices_from_visual_matches,
    format_shopify_title,
    APPROVED_PLATFORM_DOMAINS,
    FAST_FASHION_DOMAINS
)

cache_file = Path('/tmp/cavalli_serpapi_cache.json')
serp_cache = {}
if cache_file.exists():
    try:
        serp_cache = json.loads(cache_file.read_text())
    except Exception:
        pass

def fetch_serpapi_with_pacing(pub_url: str, brand: str = "Cavalli", sku: str = "") -> list:
    if sku and sku in serp_cache:
        return serp_cache[sku]

    serp_key = os.getenv("SERPAPI_KEY", "")
    if not serp_key or not pub_url:
        return []

    brand_keywords = ["cavalli"]
    if brand and "cavalli" not in brand.lower():
        brand_keywords.append(brand.lower().split()[0])

    matches = []

    # 1. Primary Query: Google Lens via SerpAPI
    for attempt in range(15):
        try:
            r1 = requests.get(
                "https://serpapi.com/search.json",
                params={"engine": "google_lens", "url": pub_url, "api_key": serp_key},
                timeout=15,
            )
            if r1.status_code == 200:
                data1 = r1.json()
                if "error" in data1 and ("throttled" in data1["error"].lower() or "limit" in data1["error"].lower()):
                    print(f"⏳ [{sku}] SerpAPI Throttled limit reached. Waiting 60 seconds before retry (attempt {attempt+1})...")
                    time.sleep(60)
                    continue
                raw_matches = data1.get("visual_matches", []) or []
                for m in raw_matches:
                    title = m.get("title", "")
                    link = m.get("link", "")
                    source = m.get("source", "")

                    # Exclude fast fashion
                    if any(ff in link.lower() or ff in source.lower() for ff in FAST_FASHION_DOMAINS):
                        continue

                    # STRICT BRAND CHECK: Must explicitly contain "cavalli" or target brand
                    has_brand = any(bk in title.lower() or bk in link.lower() or bk in source.lower() for bk in brand_keywords)
                    if not has_brand:
                        continue

                    matches.append({
                        "title": title,
                        "link": link,
                        "source": source,
                        "price": m.get("price")
                    })

                    # Stop once we have 2 good Cavalli matches!
                    if len(matches) >= 2:
                        break

                break
            elif r1.status_code in [429, 403] or "throttled" in r1.text.lower():
                print(f"⏳ [{sku}] Rate limit hit (HTTP {r1.status_code}). Waiting 60 seconds...")
                time.sleep(60)
            else:
                print(f"⚠️ [{sku}] HTTP {r1.status_code}: {r1.text[:100]}")
                break
        except Exception as ex:
            print(f"⚠️ [{sku}] Fetch error: {ex}. Waiting 5 seconds...")
            time.sleep(5)

    # 2. Bing Reverse Image Fallback ONLY if 0 matches found from Google Lens
    if not matches:
        for attempt in range(5):
            try:
                r2 = requests.get(
                    "https://serpapi.com/search.json",
                    params={"engine": "bing_reverse_image", "image_url": pub_url, "q": brand or "Cavalli", "api_key": serp_key},
                    timeout=15,
                )
                if r2.status_code == 200:
                    data2 = r2.json()
                    if "error" in data2 and ("throttled" in data2["error"].lower() or "limit" in data2["error"].lower()):
                        print(f"⏳ [{sku}] Bing SerpAPI Throttled. Waiting 60 seconds...")
                        time.sleep(60)
                        continue
                    rc = data2.get("related_content", []) or []
                    for item in rc:
                        title = item.get("title", "")
                        link = item.get("source") or item.get("link", "")
                        source = item.get("source", "")

                        if any(ff in link.lower() or ff in source.lower() for ff in FAST_FASHION_DOMAINS):
                            continue

                        has_brand = any(bk in title.lower() or bk in link.lower() or bk in source.lower() for bk in brand_keywords)
                        if not has_brand:
                            continue

                        matches.append({
                            "title": title,
                            "link": link,
                            "source": source,
                            "price": item.get("price")
                        })
                        if len(matches) >= 2:
                            break
                    break
                elif r2.status_code in [429, 403] or "throttled" in r2.text.lower():
                    print(f"⏳ [{sku}] Bing Rate limit hit. Waiting 60 seconds...")
                    time.sleep(60)
                else:
                    break
            except Exception:
                time.sleep(3)

    if sku:
        serp_cache[sku] = matches
        cache_file.write_text(json.dumps(serp_cache, indent=2))

    return matches

def to_title_case(s: str) -> str:
    if not s:
        return ""
    words = s.split()
    out = []
    for i, w in enumerate(words):
        wl = w.lower()
        if wl in ["ss", "fw"]:
            out.append(wl.upper())
        elif wl in ["2000s", "1990s", "1980s"]:
            out.append(wl)
        elif i > 0 and wl in ["and", "with", "in", "of", "for", "or", "a", "an", "the"]:
            out.append(wl)
        else:
            out.append(w.capitalize())
    return " ".join(out)

def main():
    excel_path = "/home/kat/.hermes/cache/documents/doc_38416a2c6abc_2026_09_Cavalli_Confirmed_Purchase_List.xlsx"
    wb_in = openpyxl.load_workbook(excel_path, data_only=True)
    ws_in = wb_in.active

    row_images = {}
    for img in ws_in._images:
        r = img.anchor._from.row + 1 if hasattr(img.anchor, "_from") else None
        if r:
            try:
                image_bytes = img._data()
            except Exception:
                image_bytes = None
            if image_bytes:
                row_images[r] = image_bytes

    rows = list(ws_in.iter_rows(values_only=True))
    items_to_process = []

    for r_idx in range(2, len(rows) + 1):
        row_data = rows[r_idx - 1]
        if not row_data or not any(row_data):
            continue
        sku = str(row_data[1] or "").strip()
        brand = str(row_data[2] or "").strip()
        item_type = str(row_data[3] or "").strip()
        size = str(row_data[4] or "").strip()
        material = str(row_data[6] or "").strip()
        notes = str(row_data[7] or "").strip()
        orig_desc = str(row_data[8] or "").strip()
        price = row_data[9] if len(row_data) > 9 else 0
        img_bytes = row_images.get(r_idx)

        if sku:
            items_to_process.append({
                "row_idx": r_idx,
                "sku": sku,
                "brand": brand,
                "item_type": item_type,
                "size": size,
                "material": material,
                "notes": notes,
                "orig_desc": orig_desc,
                "price": price,
                "img_bytes": img_bytes
            })

    total_items = len(items_to_process)
    print(f"🚀 Starting Cavalli Set Processing with Pacing ({total_items} items)...")
    results = []

    for idx, item in enumerate(items_to_process, start=1):
        sku = item["sku"]
        brand = item["brand"]
        item_type = item["item_type"]
        material = item["material"]
        notes = item["notes"]
        img_bytes = item["img_bytes"]

        pub_url = ""
        matches = []
        if img_bytes:
            pub_url = save_image_for_public_lens(img_bytes, f"{sku}.jpg")
            if pub_url:
                matches = fetch_serpapi_with_pacing(pub_url, brand=brand or "Cavalli", sku=sku)

        min_p, max_p = extract_prices_from_visual_matches(matches)
        if not min_p or not max_p:
            cost = float(item["price"] or 30)
            min_p = int(cost * 3.5)
            max_p = int(cost * 6.0)

        default_print = material if "runway" not in notes.lower() else ""
        bolstered_print = extract_print_color_from_matches(matches, default_print)
        garment = "top" if "top" in item_type.lower() or "t-shirt" in item_type.lower() else item_type.split("/")[0].strip().lower()

        raw_title = format_shopify_title(
            designer=brand,
            year_era="2000s",
            print_color=bolstered_print,
            garment_type=garment,
            notes=notes
        )
        title = to_title_case(raw_title)

        target_b = brand or "Cavalli"
        match_status = f"Found {len(matches)} Cavalli visual matches" if matches else f"No exact {target_b} visual match found — needs manual QA"

        qa_link_1 = matches[0].get("link", "") if len(matches) > 0 else ""
        qa_title_1 = matches[0].get("title", "") if len(matches) > 0 else ""
        qa_link_2 = matches[1].get("link", "") if len(matches) > 1 else ""
        qa_title_2 = matches[1].get("title", "") if len(matches) > 1 else ""

        results.append({
            "SKU": sku,
            "Brand": brand,
            "Item Type": item_type,
            "Size": item["size"],
            "Purchase Price (€)": item["price"],
            "Title": title,
            "Low Resale ($USD)": min_p,
            "High Resale ($USD)": max_p,
            "Era": "2000s (Runway)" if "runway" in notes.lower() else "2000s",
            "Notes": notes if notes != "—" else "",
            "Match Status": match_status,
            "QA Listing 1 Title": qa_title_1,
            "QA Listing 1": qa_link_1,
            "QA Listing 2 Title": qa_title_2,
            "QA Listing 2": qa_link_2,
            "Photo URL": pub_url,
            "img_bytes": img_bytes
        })

        if idx % 5 == 0 or idx == total_items:
            print(f"✅ Processed {idx}/{total_items} items... [SKU: {sku} | Matches: {len(matches)} | Title: {title}]")

        # Pacing: sleep 1.8 seconds between requests so we do ~33 req/min (under 200/hr limit)
        time.sleep(1.8)

    # Export CSV
    df_csv = pd.DataFrame([{k: v for k, v in r.items() if k != "img_bytes"} for r in results])
    csv_out_path = "/home/kat/workspace/hot-girl-shopify/japanese-invoice-transcriber/static/2026_09_Cavalli_Full_Enriched_Inventory.csv"
    df_csv.to_csv(csv_out_path, index=False)

    # Export Formatted Excel with Embedded Photos
    wb_out = openpyxl.Workbook()
    ws_out = wb_out.active
    ws_out.title = "Cavalli Inventory"
    ws_out.views.sheetView[0].showGridLines = True

    headers = [
        "Photo",
        "SKU",
        "Brand",
        "Item Type",
        "Size",
        "Title",
        "Purchase Price (€)",
        "Low Resale ($USD)",
        "High Resale ($USD)",
        "Era",
        "Notes",
        "Match Status",
        "QA Listing 1",
        "QA Listing 2",
        "Photo URL"
    ]

    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    alt_row_fill = PatternFill(start_color="F8F9FA", end_color="F8F9FA", fill_type="solid")
    white_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9")
    )

    align_center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    align_left = Alignment(horizontal="left", vertical="center", wrap_text=True)

    ws_out.append(headers)
    ws_out.row_dimensions[1].height = 28

    for col_idx in range(1, len(headers) + 1):
        cell = ws_out.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = align_center

    thumb_dir = Path("/tmp/cavalli_excel_thumbs")
    thumb_dir.mkdir(parents=True, exist_ok=True)

    for idx, item in enumerate(results, start=2):
        ws_out.row_dimensions[idx].height = 75

        sku = item["SKU"]
        brand = item["Brand"]
        item_type = item["Item Type"]
        size = item["Size"]
        title = item["Title"]
        price_eur = item["Purchase Price (€)"]
        low_usd = item["Low Resale ($USD)"]
        high_usd = item["High Resale ($USD)"]
        era = item["Era"]
        notes = item["Notes"]
        status = item["Match Status"]
        qa1_url = item["QA Listing 1"]
        qa1_title = item["QA Listing 1 Title"] or "🔗 View Comp 1"
        qa2_url = item["QA Listing 2"]
        qa2_title = item["QA Listing 2 Title"] or "🔗 View Comp 2"
        photo_url = item["Photo URL"]

        ws_out.cell(row=idx, column=2, value=sku)
        ws_out.cell(row=idx, column=3, value=brand)
        ws_out.cell(row=idx, column=4, value=item_type)
        ws_out.cell(row=idx, column=5, value=size)
        ws_out.cell(row=idx, column=6, value=title)

        cell_p = ws_out.cell(row=idx, column=7, value=float(price_eur or 0))
        cell_p.number_format = "€#,##0"

        cell_l = ws_out.cell(row=idx, column=8, value=int(low_usd or 0))
        cell_l.number_format = "$#,##0"

        cell_h = ws_out.cell(row=idx, column=9, value=int(high_usd or 0))
        cell_h.number_format = "$#,##0"

        ws_out.cell(row=idx, column=10, value=era)
        ws_out.cell(row=idx, column=11, value=notes)
        ws_out.cell(row=idx, column=12, value=status)

        cell_qa1 = ws_out.cell(row=idx, column=13)
        if qa1_url:
            cell_qa1.value = qa1_title[:40] + ("..." if len(qa1_title) > 40 else "")
            cell_qa1.hyperlink = qa1_url
            cell_qa1.font = Font(name="Calibri", size=10, color="0563C1", underline="single")

        cell_qa2 = ws_out.cell(row=idx, column=14)
        if qa2_url:
            cell_qa2.value = qa2_title[:40] + ("..." if len(qa2_title) > 40 else "")
            cell_qa2.hyperlink = qa2_url
            cell_qa2.font = Font(name="Calibri", size=10, color="0563C1", underline="single")

        cell_url = ws_out.cell(row=idx, column=15, value=photo_url)
        if photo_url:
            cell_url.hyperlink = photo_url
            cell_url.font = Font(name="Calibri", size=10, color="0563C1", underline="single")

        for c_idx in range(1, len(headers) + 1):
            c = ws_out.cell(row=idx, column=c_idx)
            c.border = thin_border
            if c_idx not in [13, 14, 15]:
                c.font = Font(name="Calibri", size=10)
            c.alignment = align_center if c_idx in [2, 5, 7, 8, 9, 10, 12] else align_left

        img_b = item.get("img_bytes")
        if img_b:
            try:
                im = PILImage.open(io.BytesIO(img_b))
                im.thumbnail((90, 90))
                t_path = thumb_dir / f"thumb_{idx}.jpg"
                im.convert("RGB").save(t_path, "JPEG", quality=85)

                xl_img = OpenPyXLElementImage(t_path)
                xl_img.width = im.width
                xl_img.height = im.height

                cell_ref = f"A{idx}"
                ws_out.add_image(xl_img, cell_ref)
            except Exception:
                pass

    col_widths = {
        "A": 14, # Photo
        "B": 16, # SKU
        "C": 22, # Brand
        "D": 20, # Item Type
        "E": 10, # Size
        "F": 45, # Title
        "G": 18, # Purchase Price
        "H": 18, # Low Resale
        "I": 18, # High Resale
        "J": 16, # Era
        "K": 30, # Notes
        "L": 28, # Match Status
        "M": 32, # QA Listing 1
        "N": 32, # QA Listing 2
        "O": 35, # Photo URL
    }

    for col_letter, width in col_widths.items():
        ws_out.column_dimensions[col_letter].width = width

    xlsx_out_path = "/home/kat/workspace/hot-girl-shopify/japanese-invoice-transcriber/static/2026_09_Cavalli_Full_Enriched_Inventory.xlsx"
    wb_out.save(xlsx_out_path)

    print(f"🎉 SUCCESS! Completed full processing of {total_items} items!")
    print("Exported CSV:", csv_out_path)
    print("Exported Excel with embedded photos:", xlsx_out_path)

if __name__ == "__main__":
    main()

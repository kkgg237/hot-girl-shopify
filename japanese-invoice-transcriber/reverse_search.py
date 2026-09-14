"""Reverse Image Search & Vintage Garment Research Tool for Past Studies tools.

Performs batch reverse image search, garment extraction, competitor pricing
research, and Shopify title generation for Y2K/vintage luxury studio shots.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, quote_plus

import streamlit as st

try:
    import anthropic
except ImportError:
    anthropic = None

try:
    from PIL import Image
except ImportError:
    Image = None

RESALE_PLATFORMS = [
    ("Google Lens", "https://lens.google.com/uploadbyurl?url={url}"),
    ("Google Search", "https://www.google.com/search?q={query}"),
    ("Grailed", "https://www.grailed.com/shop?query={query}"),
    ("Vestiaire Collective", "https://www.vestiairecollective.com/search/?q={query}"),
    ("1stDibs", "https://www.1stdibs.com/search/?q={query}"),
    ("eBay", "https://www.ebay.com/sch/i.html?_nkw={query}"),
    ("The RealReal", "https://www.therealreal.com/products?search={query}"),
    ("Depop", "https://www.depop.com/search/?q={query}"),
]


def build_search_urls(query: str, image_url: str = "") -> dict[str, str]:
    """Generate direct search URLs for Google Lens and major resale platforms."""
    encoded_q = quote_plus(query)
    urls: dict[str, str] = {}
    if image_url:
        urls["Google Lens"] = f"https://lens.google.com/uploadbyurl?url={quote(image_url, safe='')}"
    else:
        urls["Google Lens"] = f"https://lens.google.com/search?p={encoded_q}"

    urls["Google Search"] = f"https://www.google.com/search?q={encoded_q}"
    urls["Grailed"] = f"https://www.grailed.com/shop?query={encoded_q}"
    urls["Vestiaire Collective"] = f"https://www.vestiairecollective.com/search/?q={encoded_q}"
    urls["1stDibs"] = f"https://www.1stdibs.com/search/?q={encoded_q}"
    urls["eBay"] = f"https://www.ebay.com/sch/i.html?_nkw={encoded_q}"
    urls["The RealReal"] = f"https://www.therealreal.com/products?search={encoded_q}"
    urls["Depop"] = f"https://www.depop.com/search/?q={encoded_q}"
    return urls


def format_shopify_title(
    designer: str = "",
    year_era: str = "",
    collection: str = "",
    print_color: str = "",
    garment_type: str = "",
    is_set: bool = False,
) -> str:
    """Format a title according to Past Studies Shopify title conventions:

    Formula: [Designer] [Year/Era] [Collection Name] [Color/Print Description] [Garment Type(s)] [Set (if applicable)]
    """
    parts = []
    if designer:
        parts.append(designer.strip().lower())
    if year_era:
        parts.append(year_era.strip().lower())
    if collection:
        parts.append(collection.strip().lower())
    if print_color:
        parts.append(print_color.strip().lower())
    if garment_type:
        parts.append(garment_type.strip().lower())

    title = " ".join(parts).strip()
    if is_set and not title.endswith("set"):
        title += " set"
    return title


def analyze_garment_image_with_ai(
    image_bytes: bytes,
    filename: str = "",
    mime_type: str = "image/jpeg",
    client: Optional[Any] = None,
    model: str = "claude-sonnet-4-5",
) -> dict[str, Any]:
    """Call Claude Vision to analyze a garment studio photo, identify details,

    apply set/separate logic, and generate standardized title & price estimates.
    """
    if client is None:
        if anthropic is None:
            raise RuntimeError("anthropic package is not installed")
        client = anthropic.Anthropic(timeout=90.0, max_retries=2)

    b64_data = base64.b64encode(image_bytes).decode("utf-8")

    prompt = """Analyze this Y2K/designer/vintage garment studio photo for an e-commerce catalog.

Apply the following evaluation rules:
1. GARMENT & SET LOGIC:
   - If top and bottom share a matching print/pattern/colorway, classify as a SET (e.g., Top + Trousers Set).
   - If prints do NOT match (e.g. basic top + statement jeans), focus on the statement garment and ignore basic items.
   - If two distinct statement items are present, identify both separately and note if two listings are needed.

2. IDENTIFICATION:
   - Designer / Brand (e.g. Roberto Cavalli, Jean Paul Gaultier, Blumarine, Just Cavalli, Dolce & Gabbana, Missoni)
   - Era / Year (e.g. 2002, S/S 2003, 2000s, Y2K, 1990s)
   - Collection Name (if famous/identifiable, e.g. "Mon Amour", "Cyberbaba", "Butterflies")
   - Color & Print Description (e.g. white tiger tattoo graphic denim, blue floral zebra, pink snakeskin)
   - Garment Type (e.g. jacket skirt set, mesh tank top, asymmetrical handkerchief midi skirt, trousers)
   - Fabric / Material (e.g. denim, mesh, silk, leather, knit)

3. PRICING & SEARCH QUERY:
   - Estimated market price range on luxury resale markets (min_usd and max_usd integer estimates).
   - Concise search query string for finding exact or same-model comps on eBay/Grailed/1stDibs.

Return ONLY a single valid JSON object with the following schema:
{
  "item_type": "Set" or "Single",
  "designer": "string",
  "year_era": "string",
  "collection": "string",
  "print_color": "string",
  "garment_type": "string",
  "fabric": "string",
  "suggested_title": "string",
  "min_price_usd": int,
  "max_price_usd": int,
  "search_query": "string",
  "notes": "string"
}
"""

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": b64_data,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )

    text = response.content[0].text if response.content else ""
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()

    try:
        data = json.loads(text)
    except Exception:
        data = {
            "item_type": "Single",
            "designer": "",
            "year_era": "2000s",
            "collection": "",
            "print_color": "",
            "garment_type": "",
            "fabric": "",
            "suggested_title": "",
            "min_price_usd": 150,
            "max_price_usd": 450,
            "search_query": "designer vintage garment",
            "notes": text,
        }

    if not data.get("suggested_title"):
        data["suggested_title"] = format_shopify_title(
            designer=data.get("designer", ""),
            year_era=data.get("year_era", ""),
            collection=data.get("collection", ""),
            print_color=data.get("print_color", ""),
            garment_type=data.get("garment_type", ""),
            is_set=(data.get("item_type") == "Set"),
        )

    data["filename"] = filename
    return data


def generate_manifest_csv(results: list[dict[str, Any]]) -> str:
    """Generate CSV string from research results."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Source",
        "Item Type",
        "Designer / Brand",
        "Year / Era",
        "Collection",
        "Print / Color",
        "Garment Type",
        "Fabric",
        "Shopify Title",
        "Est Price Range (USD)",
        "Search Query",
        "Google Lens Link",
        "Grailed Link",
        "Vestiaire Link",
        "1stDibs Link",
        "eBay Link",
    ])

    for item in results:
        query = item.get("search_query") or item.get("suggested_title", "")
        img_url = item.get("image_url", "")
        links = build_search_urls(query, img_url)
        min_p = item.get("min_price_usd", 0)
        max_p = item.get("max_price_usd", 0)
        price_str = f"${min_p} - ${max_p}" if min_p and max_p else "N/A"

        writer.writerow([
            item.get("filename") or item.get("image_url") or "Uploaded Image",
            item.get("item_type", "Single"),
            item.get("designer", ""),
            item.get("year_era", ""),
            item.get("collection", ""),
            item.get("print_color", ""),
            item.get("garment_type", ""),
            item.get("fabric", ""),
            item.get("suggested_title", ""),
            price_str,
            query,
            links.get("Google Lens", ""),
            links.get("Grailed", ""),
            links.get("Vestiaire Collective", ""),
            links.get("1stDibs", ""),
            links.get("eBay", ""),
        ])

    return output.getvalue()


def render_reverse_search_tab() -> None:
    """Render the Reverse Image Search & Garment Research Streamlit tab."""
    st.markdown("## 🔎 Reverse Image Search & Product Research")
    st.caption(
        "Batch process e-commerce studio photos to identify Y2K/designer garments, "
        "apply Set vs. Separate rules, research resale comps, and generate standardized Shopify titles."
    )

    if "reverse_search_results" not in st.session_state:
        st.session_state["reverse_search_results"] = []

    input_mode = st.radio(
        "Select Input Source",
        ["Upload Images", "Local Folder Path", "Paste Image URLs"],
        horizontal=True,
    )

    items_to_process = []

    if input_mode == "Upload Images":
        uploaded_files = st.file_uploader(
            "Upload look studio photos (JPG, PNG, WEBP)",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
        )
        if uploaded_files:
            for f in uploaded_files:
                items_to_process.append({
                    "name": f.name,
                    "bytes": f.getvalue(),
                    "mime": f.type or "image/jpeg",
                    "url": "",
                })

    elif input_mode == "Local Folder Path":
        folder_path_str = st.text_input(
            "Enter path to local directory containing images",
            value="",
            placeholder="/home/kat/workspace/looks_folder",
        )
        if folder_path_str:
            p = Path(folder_path_str)
            if p.exists() and p.is_dir():
                valid_exts = {".jpg", ".jpeg", ".png", ".webp"}
                img_paths = [f for f in p.iterdir() if f.suffix.lower() in valid_exts]
                st.info(f"Found {len(img_paths)} image(s) in `{p}`.")
                if st.button("Load Folder Images"):
                    for img_p in img_paths:
                        try:
                            data_bytes = img_p.read_bytes()
                            items_to_process.append({
                                "name": img_p.name,
                                "bytes": data_bytes,
                                "mime": f"image/{img_p.suffix.lower().lstrip('.')}",
                                "url": "",
                            })
                        except Exception as ex:
                            st.error(f"Failed to read {img_p.name}: {ex}")
            else:
                st.warning("Directory path does not exist or is not a folder.")

    elif input_mode == "Paste Image URLs":
        urls_text = st.text_area(
            "Paste image URLs (one per line)",
            placeholder="https://cdn.shopify.com/s/files/1/xxx/products/look1.jpg\nhttps://cdn.shopify.com/s/files/1/xxx/products/look2.jpg",
            height=120,
        )
        if urls_text.strip():
            raw_urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
            for i, u in enumerate(raw_urls, 1):
                items_to_process.append({
                    "name": f"URL #{i}",
                    "bytes": None,
                    "mime": "image/jpeg",
                    "url": u,
                })

    col_btn1, col_btn2 = st.columns([1, 1])
    with col_btn1:
        run_analysis = st.button(
            "🚀 Run AI Reverse Research",
            type="primary",
            disabled=(not items_to_process and not st.session_state["reverse_search_results"]),
        )
    with col_btn2:
        if st.button("🗑️ Clear Results"):
            st.session_state["reverse_search_results"] = []
            st.rerun()

    if run_analysis and items_to_process:
        client = None
        if anthropic is not None:
            try:
                client = anthropic.Anthropic(timeout=90.0, max_retries=2)
            except Exception as e:
                st.warning(f"Could not initialize Anthropic client: {e}")

        new_results = []
        progress_bar = st.progress(0, text="Processing images...")

        for idx, item in enumerate(items_to_process):
            progress_bar.progress((idx) / len(items_to_process), text=f"Analyzing {item['name']}...")
            
            image_bytes = item["bytes"]
            if not image_bytes and item["url"]:
                # Fetch image from URL if needed
                try:
                    import urllib.request
                    req = urllib.request.Request(
                        item["url"],
                        headers={"User-Agent": "Mozilla/5.0 (PastStudies Tools)"},
                    )
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        image_bytes = resp.read()
                except Exception as ex:
                    st.error(f"Failed to fetch image from {item['url']}: {ex}")

            if image_bytes and client:
                try:
                    ai_data = analyze_garment_image_with_ai(
                        image_bytes=image_bytes,
                        filename=item["name"],
                        mime_type=item["mime"],
                        client=client,
                    )
                    ai_data["image_bytes"] = image_bytes
                    ai_data["image_url"] = item["url"]
                    new_results.append(ai_data)
                except Exception as ex:
                    st.error(f"Error analyzing {item['name']}: {ex}")
                    # Fallback entry
                    new_results.append({
                        "filename": item["name"],
                        "item_type": "Single",
                        "designer": "",
                        "year_era": "2000s",
                        "collection": "",
                        "print_color": "",
                        "garment_type": "",
                        "fabric": "",
                        "suggested_title": item["name"],
                        "min_price_usd": 0,
                        "max_price_usd": 0,
                        "search_query": item["name"],
                        "notes": f"Analysis failed: {ex}",
                        "image_bytes": image_bytes,
                        "image_url": item["url"],
                    })

        progress_bar.progress(1.0, text="Done!")
        st.session_state["reverse_search_results"] = new_results
        st.success(f"Processed {len(new_results)} item(s) successfully!")

    # Display Results
    results = st.session_state.get("reverse_search_results", [])
    if results:
        st.markdown(f"### Research Manifest ({len(results)} items)")

        csv_data = generate_manifest_csv(results)
        st.download_button(
            label="📥 Download Manifest as CSV",
            data=csv_data,
            file_name="reverse_search_manifest.csv",
            mime="text/csv",
        )

        for idx, res in enumerate(results):
            with st.container(border=True):
                c_img, c_details, c_links = st.columns([1.2, 2.5, 1.8])

                with c_img:
                    if res.get("image_bytes"):
                        st.image(res["image_bytes"], use_container_width=True)
                    elif res.get("image_url"):
                        st.image(res["image_url"], use_container_width=True)
                    st.caption(f"**Source:** {res.get('filename') or 'URL'}")

                with c_details:
                    title_input = st.text_input(
                        "Shopify Title Formula",
                        value=res.get("suggested_title", ""),
                        key=f"title_{idx}",
                    )
                    res["suggested_title"] = title_input

                    c_d1, c_d2 = st.columns(2)
                    with c_d1:
                        res["designer"] = st.text_input("Designer/Brand", res.get("designer", ""), key=f"des_{idx}")
                        res["year_era"] = st.text_input("Year / Era", res.get("year_era", ""), key=f"era_{idx}")
                        res["item_type"] = st.selectbox("Item Type", ["Single", "Set"], index=1 if res.get("item_type") == "Set" else 0, key=f"type_{idx}")
                    with c_d2:
                        res["collection"] = st.text_input("Collection", res.get("collection", ""), key=f"coll_{idx}")
                        res["print_color"] = st.text_input("Print / Colorway", res.get("print_color", ""), key=f"print_{idx}")
                        res["garment_type"] = st.text_input("Garment Type", res.get("garment_type", ""), key=f"garment_{idx}")

                    p_col1, p_col2 = st.columns(2)
                    with p_col1:
                        res["min_price_usd"] = st.number_input("Est. Min Price ($USD)", value=int(res.get("min_price_usd") or 0), key=f"pmin_{idx}")
                    with p_col2:
                        res["max_price_usd"] = st.number_input("Est. Max Price ($USD)", value=int(res.get("max_price_usd") or 0), key=f"pmax_{idx}")

                    if res.get("notes"):
                        st.caption(f"**Notes:** {res['notes']}")

                with c_links:
                    st.markdown("**🔎 Comp Search Links**")
                    query = res.get("search_query") or res.get("suggested_title", "")
                    img_url = res.get("image_url", "")
                    links = build_search_urls(query, img_url)

                    st.link_button("🌐 Google Lens Search", links["Google Lens"], use_container_width=True)
                    st.link_button("🛍️ Grailed Comps", links["Grailed"], use_container_width=True)
                    st.link_button("👗 Vestiaire Collective", links["Vestiaire Collective"], use_container_width=True)
                    st.link_button("💎 1stDibs Comps", links["1stDibs"], use_container_width=True)
                    st.link_button("🏷️ eBay Search", links["eBay"], use_container_width=True)
                    st.link_button("📦 The RealReal", links["The RealReal"], use_container_width=True)

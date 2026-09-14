"""Reverse Image Search & Vintage Garment Research Tool for Past Studies tools.

Performs batch reverse image search, garment extraction, competitor pricing
research, and Shopify title generation for Y2K/vintage luxury studio shots.
Includes recursive directory scanning, directory picker popup dialog, and
an interactive editable table view (st.data_editor).
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


def get_directory_info(dir_path: Path, recursive: bool = True) -> tuple[list[Path], list[Path]]:
    """Return subdirectories and image files inside dir_path (optionally recursive)."""
    valid_exts = {".jpg", ".jpeg", ".png", ".webp"}
    subdirs = []
    images = []
    try:
        if dir_path.exists() and dir_path.is_dir():
            if recursive:
                for p in dir_path.rglob("*"):
                    if p.name.startswith("."):
                        continue
                    if p.is_dir():
                        subdirs.append(p)
                    elif p.is_file() and p.suffix.lower() in valid_exts:
                        images.append(p)
            else:
                for item in dir_path.iterdir():
                    if item.name.startswith("."):
                        continue
                    if item.is_dir():
                        subdirs.append(item)
                    elif item.is_file() and item.suffix.lower() in valid_exts:
                        images.append(item)
    except Exception:
        pass

    subdirs.sort(key=lambda x: str(x).lower())
    images.sort(key=lambda x: str(x).lower())
    return subdirs, images


if hasattr(st, "dialog"):
    @st.dialog("📁 Select Local Directory Path")
    def folder_picker_dialog() -> None:
        if "browse_current_dir" not in st.session_state:
            st.session_state["browse_current_dir"] = str(Path.home())

        curr = Path(st.session_state["browse_current_dir"]).resolve()
        st.write(f"**Current Directory:** `{curr}`")

        c1, c2, c3 = st.columns([1, 1, 1])
        with c1:
            if curr.parent != curr and st.button("⬆️ Up", key="picker_up"):
                st.session_state["browse_current_dir"] = str(curr.parent)
                st.rerun()
        with c2:
            if st.button("🏠 Home", key="picker_home"):
                st.session_state["browse_current_dir"] = str(Path.home())
                st.rerun()
        with c3:
            if st.button("💼 Workspace", key="picker_work"):
                st.session_state["browse_current_dir"] = "/home/kat/workspace"
                st.rerun()

        subdirs, images = get_directory_info(curr, recursive=False)

        if subdirs:
            options = ["-- Navigate to Subdirectory --"] + [d.name for d in subdirs]
            chosen = st.selectbox("Subdirectories", options, key="picker_subdir_select")
            if chosen != "-- Navigate to Subdirectory --":
                st.session_state["browse_current_dir"] = str(curr / chosen)
                st.rerun()

        if images:
            st.success(f"🖼️ Found **{len(images)}** image file(s) in this folder.")
        else:
            st.info("No image files (.jpg, .png, .webp) found in this folder.")

        st.divider()
        if st.button("✅ Select This Folder", type="primary", key="picker_confirm"):
            st.session_state["selected_folder_path"] = str(curr)
            st.rerun()


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
        "Batch scan e-commerce studio photo folders to identify Y2K/designer garments, "
        "apply Set vs. Separate rules, research resale comps, and generate standardized Shopify titles."
    )

    if "reverse_search_results" not in st.session_state:
        st.session_state["reverse_search_results"] = []

    input_mode = st.radio(
        "Select Input Source",
        ["Local Folder Path", "Upload Images", "Paste Image URLs"],
        horizontal=True,
    )

    items_to_process = []

    if input_mode == "Local Folder Path":
        col_input, col_popup = st.columns([3.5, 1.2])

        default_folder = st.session_state.get("selected_folder_path", "")

        with col_input:
            folder_path_str = st.text_input(
                "Local directory path containing look photos",
                value=default_folder,
                placeholder="/home/kat/workspace/looks_folder",
                key="folder_path_text_input",
            )
            st.session_state["selected_folder_path"] = folder_path_str

        with col_popup:
            st.write(" ")
            st.write(" ")
            if st.button("📁 Browse Directory", key="open_folder_popup", use_container_width=True):
                if hasattr(st, "dialog"):
                    folder_picker_dialog()

        is_recursive = st.checkbox(
            "Recursive Scan (Scan all subdirectories in folder)",
            value=True,
            key="folder_recursive_checkbox",
        )

        if folder_path_str:
            p = Path(folder_path_str)
            if p.exists() and p.is_dir():
                subdirs, img_paths = get_directory_info(p, recursive=is_recursive)
                st.info(f"📁 Selected Folder: `{p}` — Found **{len(img_paths)}** total image file(s) across folder structure.")
                for img_p in img_paths:
                    try:
                        rel_path = str(img_p.relative_to(p)) if p in img_p.parents else img_p.name
                        items_to_process.append({
                            "name": rel_path,
                            "path": img_p,
                            "bytes": None,  # read on demand
                            "mime": f"image/{img_p.suffix.lower().lstrip('.')}",
                            "url": "",
                        })
                    except Exception as ex:
                        st.error(f"Failed to index {img_p.name}: {ex}")
            else:
                st.warning("Directory path does not exist or is not a folder.")

    elif input_mode == "Upload Images":
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
            f"🚀 Run AI Reverse Research ({len(items_to_process)} items)",
            type="primary",
            disabled=(not items_to_process),
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
            progress_bar.progress((idx) / len(items_to_process), text=f"Analyzing [{idx+1}/{len(items_to_process)}] {item['name']}...")

            image_bytes = item.get("bytes")
            if not image_bytes and item.get("path"):
                try:
                    image_bytes = item["path"].read_bytes()
                except Exception as ex:
                    st.error(f"Failed to read file {item['name']}: {ex}")

            if not image_bytes and item.get("url"):
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

    results = st.session_state.get("reverse_search_results", [])
    if results:
        st.markdown(f"### Research Manifest ({len(results)} items)")

        view_tab_table, view_tab_cards = st.tabs([
            "📊 Editable Spreadsheet Table",
            "🖼️ Photo Cards Grid",
        ])

        with view_tab_table:
            # Build table records
            table_rows = []
            for res in results:
                query = res.get("search_query") or res.get("suggested_title", "")
                img_url = res.get("image_url", "")
                links = build_search_urls(query, img_url)

                table_rows.append({
                    "Source": res.get("filename") or res.get("image_url") or "Uploaded Image",
                    "Item Type": res.get("item_type", "Single"),
                    "Designer / Brand": res.get("designer", ""),
                    "Year / Era": res.get("year_era", ""),
                    "Collection": res.get("collection", ""),
                    "Print / Color": res.get("print_color", ""),
                    "Garment Type": res.get("garment_type", ""),
                    "Fabric": res.get("fabric", ""),
                    "Shopify Title": res.get("suggested_title", ""),
                    "Est. Min Price ($)": int(res.get("min_price_usd") or 0),
                    "Est. Max Price ($)": int(res.get("max_price_usd") or 0),
                    "Search Query": query,
                    "Google Lens Link": links.get("Google Lens", ""),
                    "Grailed Link": links.get("Grailed", ""),
                    "Vestiaire Link": links.get("Vestiaire Collective", ""),
                    "1stDibs Link": links.get("1stDibs", ""),
                    "eBay Link": links.get("eBay", ""),
                    "The RealReal Link": links.get("The RealReal", ""),
                    "Depop Link": links.get("Depop", ""),
                    "Notes": res.get("notes", ""),
                })

            column_config = {
                "Source": st.column_config.TextColumn("Source / Filename", width="medium", disabled=True),
                "Item Type": st.column_config.SelectboxColumn("Item Type", options=["Single", "Set"], width="small"),
                "Designer / Brand": st.column_config.TextColumn("Designer / Brand", width="medium"),
                "Year / Era": st.column_config.TextColumn("Year / Era", width="small"),
                "Collection": st.column_config.TextColumn("Collection Name", width="medium"),
                "Print / Color": st.column_config.TextColumn("Print / Colorway", width="medium"),
                "Garment Type": st.column_config.TextColumn("Garment Type", width="medium"),
                "Fabric": st.column_config.TextColumn("Fabric / Material", width="small"),
                "Shopify Title": st.column_config.TextColumn("Generated Shopify Title", width="large"),
                "Est. Min Price ($)": st.column_config.NumberColumn("Min Price ($)", format="$%d", width="small"),
                "Est. Max Price ($)": st.column_config.NumberColumn("Max Price ($)", format="$%d", width="small"),
                "Search Query": st.column_config.TextColumn("Search Query", width="medium"),
                "Google Lens Link": st.column_config.LinkColumn("Google Lens", display_text="🔎 Lens"),
                "Grailed Link": st.column_config.LinkColumn("Grailed", display_text="🛍️ Grailed"),
                "Vestiaire Link": st.column_config.LinkColumn("Vestiaire", display_text="👗 Vestiaire"),
                "1stDibs Link": st.column_config.LinkColumn("1stDibs", display_text="💎 1stDibs"),
                "eBay Link": st.column_config.LinkColumn("eBay", display_text="🏷️ eBay"),
                "The RealReal Link": st.column_config.LinkColumn("The RealReal", display_text="📦 RealReal"),
                "Depop Link": st.column_config.LinkColumn("Depop", display_text="🛍️ Depop"),
                "Notes": st.column_config.TextColumn("Notes", width="large"),
            }

            edited_df = st.data_editor(
                table_rows,
                column_config=column_config,
                use_container_width=True,
                num_rows="dynamic",
                key="reverse_search_data_editor",
            )

            # Sync edits back to session state
            if edited_df is not None:
                updated_results = []
                for row, orig in zip(edited_df, results):
                    item_copy = dict(orig)
                    item_copy["item_type"] = row.get("Item Type", orig.get("item_type"))
                    item_copy["designer"] = row.get("Designer / Brand", orig.get("designer"))
                    item_copy["year_era"] = row.get("Year / Era", orig.get("year_era"))
                    item_copy["collection"] = row.get("Collection", orig.get("collection"))
                    item_copy["print_color"] = row.get("Print / Color", orig.get("print_color"))
                    item_copy["garment_type"] = row.get("Garment Type", orig.get("garment_type"))
                    item_copy["fabric"] = row.get("Fabric", orig.get("fabric"))
                    item_copy["suggested_title"] = row.get("Shopify Title", orig.get("suggested_title"))
                    item_copy["min_price_usd"] = row.get("Est. Min Price ($)", orig.get("min_price_usd"))
                    item_copy["max_price_usd"] = row.get("Est. Max Price ($)", orig.get("max_price_usd"))
                    item_copy["search_query"] = row.get("Search Query", orig.get("search_query"))
                    item_copy["notes"] = row.get("Notes", orig.get("notes"))
                    updated_results.append(item_copy)

                st.session_state["reverse_search_results"] = updated_results

            csv_data = generate_manifest_csv(st.session_state["reverse_search_results"])
            st.download_button(
                label="📥 Download Research Manifest as CSV",
                data=csv_data,
                file_name="reverse_search_manifest.csv",
                mime="text/csv",
                type="primary",
            )

        with view_tab_cards:
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
                            key=f"title_card_{idx}",
                        )
                        res["suggested_title"] = title_input

                        c_d1, c_d2 = st.columns(2)
                        with c_d1:
                            res["designer"] = st.text_input("Designer/Brand", res.get("designer", ""), key=f"des_card_{idx}")
                            res["year_era"] = st.text_input("Year / Era", res.get("year_era", ""), key=f"era_card_{idx}")
                            res["item_type"] = st.selectbox("Item Type", ["Single", "Set"], index=1 if res.get("item_type") == "Set" else 0, key=f"type_card_{idx}")
                        with c_d2:
                            res["collection"] = st.text_input("Collection", res.get("collection", ""), key=f"coll_card_{idx}")
                            res["print_color"] = st.text_input("Print / Colorway", res.get("print_color", ""), key=f"print_card_{idx}")
                            res["garment_type"] = st.text_input("Garment Type", res.get("garment_type", ""), key=f"garment_card_{idx}")

                        p_col1, p_col2 = st.columns(2)
                        with p_col1:
                            res["min_price_usd"] = st.number_input("Est. Min Price ($USD)", value=int(res.get("min_price_usd") or 0), key=f"pmin_card_{idx}")
                        with p_col2:
                            res["max_price_usd"] = st.number_input("Est. Max Price ($USD)", value=int(res.get("max_price_usd") or 0), key=f"pmax_card_{idx}")

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

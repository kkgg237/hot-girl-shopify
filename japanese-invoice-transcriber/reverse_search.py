"""Reverse Image Search & Vintage Garment Research Tool for Past Studies tools.

Performs batch reverse image search, garment extraction, competitor pricing
research, and Shopify title generation for Y2K/vintage luxury studio shots.
Includes combined photo card + editable field layout, flattened comp search links,
and 2000s era normalization rule.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, quote_plus

import streamlit as st
import streamlit.components.v1 as components

COMPONENT_DIR = Path(__file__).parent / "folder_picker_component"
_native_folder_picker = components.declare_component(
    "native_folder_picker",
    path=str(COMPONENT_DIR),
)

STATIC_LENS_DIR = Path(__file__).parent / "static" / "lens_cache"


def save_image_for_public_lens(image_bytes: bytes, filename: str = "") -> str:
    """Upload studio photo bytes to temporary public host (Litterbox CDN) so Google Lens/Bing can access the image directly without Cloudflare Access auth blocks."""
    if not image_bytes:
        return ""
    try:
        STATIC_LENS_DIR.mkdir(parents=True, exist_ok=True)
        img_hash = hashlib.md5(image_bytes).hexdigest()
        ext = ".jpg"
        if filename:
            p_ext = Path(filename).suffix.lower()
            if p_ext in [".jpg", ".jpeg", ".png", ".webp"]:
                ext = p_ext
        out_file = STATIC_LENS_DIR / f"{img_hash}{ext}"
        if not out_file.exists():
            out_file.write_bytes(image_bytes)

        import requests
        resp = requests.post(
            "https://litterbox.catbox.moe/resources/internals/api.php",
            data={"reqtype": "fileupload", "time": "72h"},
            files={"fileToUpload": (f"{img_hash}{ext}", image_bytes, f"image/{ext.lstrip('.')}")},
            timeout=10,
        )
        if resp.status_code == 200 and resp.text.strip().startswith("http"):
            return resp.text.strip()
    except Exception:
        pass
    return f"https://invoices.paststudies-tools.com/app/static/lens_cache/{img_hash}{ext}"


def fetch_serpapi_visual_matches(image_url: str) -> list[dict[str, Any]]:
    """Query SerpAPI Google Lens API with public image URL to fetch exact visual matches."""
    serp_key = os.getenv("SERPAPI_KEY", "")
    if not serp_key or not image_url:
        return []
    try:
        import requests
        resp = requests.get(
            "https://serpapi.com/search.json",
            params={"engine": "google_lens", "url": image_url, "api_key": serp_key},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("visual_matches", [])
    except Exception:
        pass
    return []

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
    """Generate direct search URLs for Bing Visual Search, Google Lens, and major resale platforms."""
    encoded_q = quote_plus(query)
    urls: dict[str, str] = {}
    if image_url:
        urls["Bing Visual"] = f"https://www.bing.com/images/search?q=imgurl:{quote(image_url, safe='')}&view=detailv2&iss=sbi"
        urls["Google Lens"] = f"https://lens.google.com/uploadbyurl?url={quote(image_url, safe='')}"
    else:
        urls["Bing Visual"] = f"https://www.bing.com/images/search?q={encoded_q}"
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
    Note: Normalizes 'y2k' / 'y2k era' to '2000s'.
    """
    parts = []
    if designer:
        parts.append(designer.strip().lower())
    if year_era:
        era_clean = year_era.strip().lower()
        if era_clean in ["y2k", "y2k era"]:
            era_clean = "2000s"
        parts.append(era_clean)
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


def crawl_local_directory(dir_path: Path, recursive: bool = True) -> tuple[list[dict[str, Any]], list[Path]]:
    """Crawl filesystem directory directly on disk.

    Returns list of item metadata dicts and list of subdirectories found.
    """
    valid_exts = {".jpg", ".jpeg", ".png", ".webp"}
    subdirs = []
    crawled_items = []

    if not dir_path.exists() or not dir_path.is_dir():
        return [], []

    try:
        iterator = dir_path.rglob("*") if recursive else dir_path.iterdir()
        for p in iterator:
            if p.name.startswith("."):
                continue
            if p.is_dir():
                subdirs.append(p)
            elif p.is_file() and p.suffix.lower() in valid_exts:
                try:
                    rel_path = str(p.relative_to(dir_path)) if dir_path in p.parents else p.name
                except ValueError:
                    rel_path = p.name

                crawled_items.append({
                    "name": rel_path,
                    "path": p,
                    "bytes": None,
                    "mime": f"image/{p.suffix.lower().lstrip('.')}",
                    "url": "",
                    "size_bytes": p.stat().st_size if p.exists() else 0,
                })
    except Exception:
        pass

    subdirs.sort(key=lambda x: str(x).lower())
    crawled_items.sort(key=lambda x: x["name"].lower())
    return crawled_items, subdirs


def get_directory_info(dir_path: Path, recursive: bool = True) -> tuple[list[Path], list[Path]]:
    """Return subdirectories and image files inside dir_path (optionally recursive)."""
    items, subdirs = crawl_local_directory(dir_path, recursive=recursive)
    images = [item["path"] for item in items if item.get("path")]
    return subdirs, images


if hasattr(st, "dialog"):
    @st.dialog("📁 Select Directory Path", width="large")
    def folder_picker_dialog() -> None:
        if "browse_current_dir" not in st.session_state:
            st.session_state["browse_current_dir"] = str(Path.home())

        curr = Path(st.session_state["browse_current_dir"]).resolve()
        st.markdown(f"**Current Directory:** `{curr}`")

        c_path, c_go = st.columns([4, 1])
        with c_path:
            typed_path = st.text_input("Type or Paste Directory Path", value=str(curr), key="picker_path_input")
        with c_go:
            st.write(" ")
            st.write(" ")
            if st.button("Go ➡️", key="picker_path_go"):
                tp = Path(typed_path).resolve()
                if tp.exists() and tp.is_dir():
                    st.session_state["browse_current_dir"] = str(tp)
                    st.rerun()
                else:
                    st.error("Directory path does not exist.")

        crawled_direct, subdirs = crawl_local_directory(curr, recursive=False)
        crawled_all, _ = crawl_local_directory(curr, recursive=True)

        st.success(f"🖼️ Found **{len(crawled_direct)}** images directly in folder (**{len(crawled_all)}** total across subfolders).")

        if st.button(f"✅ SELECT THIS FOLDER (`{curr.name or str(curr)}`)", type="primary", key="picker_confirm_top", use_container_width=True):
            st.session_state["selected_folder_path"] = str(curr)
            st.rerun()

        st.divider()

        st.markdown("**Quick Drive / Folder Shortcuts:**")
        sc1, sc2, sc3, sc4 = st.columns(4)
        with sc1:
            if curr.parent != curr and st.button("⬆️ Parent Dir", key="picker_up", use_container_width=True):
                st.session_state["browse_current_dir"] = str(curr.parent)
                st.rerun()
        with sc2:
            if st.button("💾 /media", key="picker_media", use_container_width=True):
                st.session_state["browse_current_dir"] = "/media"
                st.rerun()
        with sc3:
            if st.button("💾 /mnt", key="picker_mnt", use_container_width=True):
                st.session_state["browse_current_dir"] = "/mnt"
                st.rerun()
        with sc4:
            if st.button("💼 Workspace", key="picker_work", use_container_width=True):
                st.session_state["browse_current_dir"] = "/home/kat/workspace"
                st.rerun()

        st.divider()

        st.markdown("**Subdirectories:**")
        if subdirs:
            sub_col1, sub_col2, sub_col3 = st.columns([3, 1.2, 1.2])
            with sub_col1:
                chosen = st.selectbox("Subdirectories", [d.name for d in subdirs], key="picker_subdir_select")
            with sub_col2:
                st.write(" ")
                st.write(" ")
                if st.button("Open Folder ➡️", key="picker_open_sub", use_container_width=True):
                    if chosen:
                        st.session_state["browse_current_dir"] = str(curr / chosen)
                        st.rerun()
            with sub_col3:
                st.write(" ")
                st.write(" ")
                if st.button("Select Folder ✅", key="picker_select_sub", use_container_width=True):
                    if chosen:
                        st.session_state["selected_folder_path"] = str(curr / chosen)
                        st.rerun()
        else:
            st.caption("No subdirectories found in this directory.")


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

    prompt = """Analyze this designer/vintage garment studio photo for an e-commerce catalog.

Apply the following evaluation rules:
1. GARMENT & SET LOGIC:
   - If top and bottom share a matching print/pattern/colorway, classify as a SET (e.g., Top + Trousers Set).
   - If prints do NOT match (e.g. basic top + statement jeans), focus on the statement garment and ignore basic items.
   - If two distinct statement items are present, identify both separately and note if two listings are needed.

2. IDENTIFICATION & ERA RULES:
   - Designer / Brand (e.g. Roberto Cavalli, Jean Paul Gaultier, Blumarine, Just Cavalli, Dolce & Gabbana, Missoni)
   - Era / Year: Use '2000s', '1990s', or specific year like '2003', '2002', 'S/S 2003'. Do NOT use 'Y2K' or 'y2k' — use '2000s' instead.
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

    # Normalize Y2K -> 2000s
    year_era = data.get("year_era", "2000s")
    if str(year_era).strip().lower() in ["y2k", "y2k era"]:
        data["year_era"] = "2000s"

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
        img_url = item.get("public_image_url")
        if not img_url and item.get("image_bytes"):
            img_url = save_image_for_public_lens(item["image_bytes"], item.get("filename", ""))
            item["public_image_url"] = img_url
        if not img_url:
            img_url = item.get("image_url", "")

        links = build_search_urls(query, img_url)
        min_p = item.get("min_price_usd", 0)
        max_p = item.get("max_price_usd", 0)
        price_str = f"${min_p} - ${max_p}" if min_p and max_p else "N/A"

        # Normalize Y2K -> 2000s
        year_era = item.get("year_era", "")
        if str(year_era).strip().lower() in ["y2k", "y2k era"]:
            year_era = "2000s"

        writer.writerow([
            item.get("filename") or item.get("image_url") or "Uploaded Image",
            item.get("item_type", "Single"),
            item.get("designer", ""),
            year_era,
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
        "Select a photo folder from your Mac/PC or external drive to identify designer garments, "
        "apply Set vs. Separate rules, research resale comps, and generate standardized Shopify titles."
    )

    if "reverse_search_results" not in st.session_state:
        st.session_state["reverse_search_results"] = []

    items_to_process = []

    folder_picker_result = _native_folder_picker(key="native_folder_picker_ui")
    if folder_picker_result and isinstance(folder_picker_result, list):
        st.session_state["native_folder_files"] = folder_picker_result

    native_files = st.session_state.get("native_folder_files", [])
    if native_files and isinstance(native_files, list) and native_files:
        st.success(f"🖼️ Loaded **{len(native_files)}** photo(s) from selected folder ready for research!")
        for item in native_files:
            try:
                raw_bytes = base64.b64decode(item.get("data_b64", ""))
            except Exception:
                raw_bytes = b""
            items_to_process.append({
                "name": item.get("name", "folder_image.jpg"),
                "bytes": raw_bytes,
                "mime": item.get("mime", "image/jpeg"),
                "url": "",
            })

    with st.expander("📤 Manual File Upload Fallback (Drag & Drop)", expanded=not items_to_process):
        uploaded_files = st.file_uploader(
            "Upload look photos manually",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
            key="folder_photos_uploader",
        )
        if uploaded_files and not items_to_process:
            st.success(f"🖼️ Uploaded **{len(uploaded_files)}** photo(s)!")
            for f in uploaded_files:
                items_to_process.append({
                    "name": f.name,
                    "bytes": f.getvalue(),
                    "mime": f.type or "image/jpeg",
                    "url": "",
                })

    col_btn1, col_btn2 = st.columns([1, 1])
    with col_btn1:
        run_analysis = st.button(
            f"🚀 Run AI Reverse Research ({len(items_to_process)} photos)",
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
                    pub_url = save_image_for_public_lens(image_bytes, item["name"])
                    ai_data["public_image_url"] = pub_url
                    if pub_url:
                        ai_data["visual_matches"] = fetch_serpapi_visual_matches(pub_url)
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
        st.success(f"Processed {len(new_results)} photo(s) successfully!")

    results = st.session_state.get("reverse_search_results", [])
    if results:
        st.markdown(f"### Research Manifest ({len(results)} items)")

        csv_data = generate_manifest_csv(results)
        st.download_button(
            label="📥 Download Research Manifest as CSV",
            data=csv_data,
            file_name="reverse_search_manifest.csv",
            mime="text/csv",
            type="primary",
        )

        view_tab_combined, view_tab_table = st.tabs([
            "📸 Combined Photo Cards & Editable Fields",
            "📊 Bulk Spreadsheet View (st.data_editor)",
        ])

        with view_tab_combined:
            for idx, res in enumerate(results):
                # Normalize Y2K -> 2000s
                if str(res.get("year_era", "")).strip().lower() in ["y2k", "y2k era"]:
                    res["year_era"] = "2000s"

                with st.container(border=True):
                    col_img, col_fields = st.columns([1.3, 3.7])

                    with col_img:
                        if res.get("image_bytes"):
                            st.image(res["image_bytes"], use_container_width=True)
                        elif res.get("image_url"):
                            st.image(res["image_url"], use_container_width=True)
                        st.caption(f"**Source:** `{res.get('filename') or 'Photo'}`")

                    with col_fields:
                        res["suggested_title"] = st.text_input(
                            "Shopify Title Formula",
                            value=res.get("suggested_title", ""),
                            key=f"title_{idx}",
                        )

                        f1, f2, f3 = st.columns(3)
                        with f1:
                            res["designer"] = st.text_input("Designer / Brand", value=res.get("designer", ""), key=f"des_{idx}")
                            res["year_era"] = st.text_input("Year / Era", value=res.get("year_era", "2000s"), key=f"era_{idx}")
                            res["item_type"] = st.selectbox("Item Type", ["Single", "Set"], index=1 if res.get("item_type") == "Set" else 0, key=f"type_{idx}")
                        with f2:
                            res["collection"] = st.text_input("Collection Name", value=res.get("collection", ""), key=f"coll_{idx}")
                            res["print_color"] = st.text_input("Print / Colorway", value=res.get("print_color", ""), key=f"print_{idx}")
                            res["garment_type"] = st.text_input("Garment Type", value=res.get("garment_type", ""), key=f"garment_{idx}")
                        with f3:
                            res["fabric"] = st.text_input("Fabric / Material", value=res.get("fabric", ""), key=f"fab_{idx}")
                            res["min_price_usd"] = st.number_input("Est. Min Price ($USD)", value=int(res.get("min_price_usd") or 0), key=f"pmin_{idx}")
                            res["max_price_usd"] = st.number_input("Est. Max Price ($USD)", value=int(res.get("max_price_usd") or 0), key=f"pmax_{idx}")

                        res["notes"] = st.text_input("Notes", value=res.get("notes", ""), key=f"notes_{idx}")

                        st.markdown("**🔎 Flattened Comp Search Links:**")
                        query = res.get("search_query") or res.get("suggested_title", "")
                        img_url = res.get("public_image_url")
                        if not img_url and res.get("image_bytes"):
                            img_url = save_image_for_public_lens(res["image_bytes"], res.get("filename", ""))
                            res["public_image_url"] = img_url
                        if not img_url:
                            img_url = res.get("image_url", "")
                        links = build_search_urls(query, img_url)

                        l1, l2, l3, l4, l5, l6, l7, l8 = st.columns(8)
                        with l1:
                            st.link_button("👁️ Bing Visual", links.get("Bing Visual", "#"), use_container_width=True)
                        with l2:
                            st.link_button("🌐 Lens", links.get("Google Lens", "#"), use_container_width=True)
                        with l3:
                            st.link_button("🛍️ Grailed", links.get("Grailed", "#"), use_container_width=True)
                        with l4:
                            st.link_button("👗 Vestiaire", links.get("Vestiaire Collective", "#"), use_container_width=True)
                        with l5:
                            st.link_button("💎 1stDibs", links.get("1stDibs", "#"), use_container_width=True)
                        with l6:
                            st.link_button("🏷️ eBay", links.get("eBay", "#"), use_container_width=True)
                        with l7:
                            st.link_button("📦 RealReal", links.get("The RealReal", "#"), use_container_width=True)
                        with l8:
                            st.link_button("🛍️ Depop", links.get("Depop", "#"), use_container_width=True)

                        v_matches = res.get("visual_matches") or []
                        if v_matches:
                            with st.expander(f"🎯 Exact Visual Matches Found ({len(v_matches)})", expanded=True):
                                for vm in v_matches[:8]:
                                    title = vm.get("title", "Matched Item")
                                    source = vm.get("source", "Marketplace")
                                    link = vm.get("link", "#")
                                    p_dict = vm.get("price") if isinstance(vm.get("price"), dict) else {}
                                    price_val = p_dict.get("value") or p_dict.get("extracted_value") or ""
                                    price_str = f" · **{price_val}**" if price_val else ""
                                    st.markdown(f"- **{source}**: [{title}]({link}){price_str}")

        with view_tab_table:
            table_rows = []
            for res in results:
                query = res.get("search_query") or res.get("suggested_title", "")
                img_url = res.get("public_image_url")
                if not img_url and res.get("image_bytes"):
                    img_url = save_image_for_public_lens(res["image_bytes"], res.get("filename", ""))
                    res["public_image_url"] = img_url
                if not img_url:
                    img_url = res.get("image_url", "")
                links = build_search_urls(query, img_url)

                era_val = res.get("year_era", "2000s")
                if str(era_val).strip().lower() in ["y2k", "y2k era"]:
                    era_val = "2000s"

                table_rows.append({
                    "Source": res.get("filename") or res.get("image_url") or "External Drive Photo",
                    "Item Type": res.get("item_type", "Single"),
                    "Designer / Brand": res.get("designer", ""),
                    "Year / Era": era_val,
                    "Collection": res.get("collection", ""),
                    "Print / Color": res.get("print_color", ""),
                    "Garment Type": res.get("garment_type", ""),
                    "Fabric": res.get("fabric", ""),
                    "Shopify Title": res.get("suggested_title", ""),
                    "Est. Min Price ($)": int(res.get("min_price_usd") or 0),
                    "Est. Max Price ($)": int(res.get("max_price_usd") or 0),
                    "Search Query": query,
                    "Bing Visual Link": links.get("Bing Visual", ""),
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
                "Source": st.column_config.TextColumn("Source / Photo Name", width="medium", disabled=True),
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
                "Bing Visual Link": st.column_config.LinkColumn("Bing Visual", display_text="👁️ Bing Visual"),
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

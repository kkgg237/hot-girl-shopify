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
import re
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
    """Upload studio photo bytes to temporary public host (Uguu/Litterbox CDN) so Google Lens/Bing can access the image directly without Cloudflare Access auth blocks."""
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
        # Primary: Litterbox CDN upload
        try:
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
    except Exception:
        pass
    return f"https://invoices.paststudies-tools.com/app/static/lens_cache/{img_hash}{ext}"


APPROVED_PLATFORM_DOMAINS = [
    "therealreal",
    "grailed",
    "vestiaire",
    "depop",
    "ebay",
    "poshmark",
    "etsy",
    "vinted",
    "farfetch",
    "rubylane",
    "paststudies",
    "intoarchive",
    "recessla",
    "sublimearchive",
    "vintagebymisty",
    "empressvintage",
    "vintagedesigner",
    "heroine",
]

FAST_FASHION_DOMAINS = [
    "shein",
    "asos",
    "zara",
    "hm.com",
    "h&m",
    "forever21",
    "boohoo",
    "prettylittlething",
    "cider",
    "fashionnova",
    "fashion nova",
    "zaful",
    "aliexpress",
    "temu",
    "dhgate",
    "amazon",
    "walmart",
    "target",
    "nastygal",
    "garage",
    "cottonon",
    "romwe",
    "pacsun",
]


def fetch_serpapi_visual_matches(image_url: str, brand: str = "Cavalli", engine: str = "bing_reverse_image") -> list[dict[str, Any]]:
    """Query SerpAPI (Bing Reverse Image or Google Lens) with public image URL to fetch exact visual matches.
    
    Filters strictly by approved resale/luxury platforms and excludes fast-fashion sites.
    Applies brand-guided visual search and Option A fallback flag.
    """
    serp_key = os.getenv("SERPAPI_KEY", "")
    if not serp_key:
        try:
            from dotenv import find_dotenv, load_dotenv
            load_dotenv(find_dotenv(usecwd=True), override=True)
            serp_key = os.getenv("SERPAPI_KEY", "")
        except Exception:
            pass
    if not serp_key or not image_url:
        return []

    matches = []
    try:
        import requests
        # Primary: Bing Reverse Image Search via SerpAPI
        params = {"engine": engine, "url": image_url, "api_key": serp_key}
        if brand:
            params["q"] = brand

        resp = requests.get(
            "https://serpapi.com/search.json",
            params=params,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            matches = data.get("visual_matches") or data.get("organic_results") or data.get("image_results") or []
        else:
            # Fallback engine: google_lens
            params["engine"] = "google_lens"
            resp2 = requests.get(
                "https://serpapi.com/search.json",
                params=params,
                timeout=15,
            )
            if resp2.status_code == 200:
                data2 = resp2.json()
                matches = data2.get("visual_matches") or data2.get("organic_results") or []
    except Exception:
        pass

    # Filter results by approved luxury/resale platforms and fast-fashion exclusion
    filtered = []
    brand_terms = [b.strip().lower() for b in brand.split() if len(b.strip()) > 2] if brand else []

    for m in matches:
        link = (m.get("link") or m.get("source_url") or "").lower()
        title = (m.get("title") or m.get("snippet") or "").lower()
        source = (m.get("source") or "").lower()

        # 1. Exclude fast fashion
        if any(ff in link or ff in source for ff in FAST_FASHION_DOMAINS):
            continue

        # 2. Match approved platform or brand context
        is_approved = any(ap in link or ap in source for ap in APPROVED_PLATFORM_DOMAINS)
        has_brand = any(bt in title or bt in link or bt in source for bt in brand_terms) if brand_terms else True

        if is_approved or (has_brand and not any(ff in link for ff in FAST_FASHION_DOMAINS)):
            filtered.append(m)

    return filtered


def extract_print_color_from_matches(matches: list[dict[str, Any]], default_print: str = "") -> str:
    """Extract descriptive color, print, or pattern details from visual match listing titles to bolster Shopify titles."""
    if not matches:
        return default_print

    # Key vintage/designer print & pattern descriptors to extract
    keywords = [
        "newspaper print", "gazette print", "newspaper", "gazette",
        "floral print", "floral", "flowers",
        "zebra print", "tiger print", "animal print", "zebra", "tiger",
        "snake print", "snakeskin", "reptile print", "reptile",
        "tattoo print", "tattoo effect", "tattoo",
        "mosaic print", "mosaic",
        "abstract print", "abstract",
        "crystal embellished", "embellished", "rhinestone", "beaded",
        "burnout", "sheer mesh", "geometric knit", "satin halter"
    ]

    colors = ["blue", "purple", "green", "beige", "red", "black", "pink", "brown", "gold", "silver", "white", "nude", "yellow"]

    found_color = ""
    found_print = ""

    for m in matches[:10]:
        title = (m.get("title") or m.get("snippet") or "").lower()

        if not found_color:
            for c in colors:
                if f" {c} " in f" {title} " or title.startswith(f"{c} ") or title.endswith(f" {c}"):
                    found_color = c
                    break

        if not found_print:
            for kw in keywords:
                if kw in title:
                    found_print = kw
                    break

        if found_color and found_print:
            break

    combined = f"{found_color} {found_print}".strip() if (found_color or found_print) else default_print
    if default_print and combined:
        if default_print.lower() not in combined.lower():
            combined = f"{default_print} {combined}".strip()
    return combined or default_print


def extract_prices_from_visual_matches(matches: list[dict[str, Any]]) -> tuple[int, int]:
    """Extract min and max numeric prices ($USD) from exact visual match listings."""
    prices = []
    for m in matches:
        p_dict = m.get("price") if isinstance(m.get("price"), dict) else {}
        val = p_dict.get("extracted_value") or p_dict.get("value")

        text_to_search = str(val) if val else f"{m.get('title', '')} {m.get('snippet', '')}"
        found = re.findall(r'\$\s*(\d+(?:,\d{3})*(?:\.\d{2})?)', text_to_search)
        for f in found:
            try:
                num = float(f.replace(",", ""))
                if 20 <= num <= 10000:
                    prices.append(int(num))
            except ValueError:
                pass

        if isinstance(val, (int, float)) and 20 <= val <= 10000:
            prices.append(int(val))

    if prices:
        return min(prices), max(prices)
    return 0, 0

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
    notes: str = "",
    is_runway: bool = False,
    runway_season: str = "SS",
    runway_year: str = "",
) -> str:
    """Format a title according to Past Studies Shopify title conventions:

    Standard Formula: [Year/Era] [Designer] [Color/Print Description] [Garment Type(s)] [Set (if applicable)]
    Runway Formula: [Year] [Season] Runway [Designer] [Color/Print Description] [Garment Type]
    Example Runway: 2002 SS Runway Roberto Cavalli Black Blouse
    Note: Normalizes 'y2k' / 'y2k era' to '2000s'.
    """
    import re
    notes_lower = notes.lower() if notes else ""
    if "runway" in notes_lower or is_runway:
        year_match = re.search(r"\b(19\d\d|20\d\d)\b", notes_lower + " " + str(year_era) + " " + str(runway_year))
        year = year_match.group(1) if year_match else (runway_year or "2000")

        season = runway_season.strip().upper() if runway_season else "SS"
        if any(s in notes_lower for s in ["spring", "summer", "s/s", "ss"]):
            season = "SS"
        elif any(f in notes_lower for f in ["fall", "winter", "f/w", "fw", "autumn"]):
            season = "FW"

        parts = [year, season, "Runway"]
        if designer:
            parts.append(designer.strip().title())
        if print_color:
            parts.append(print_color.strip().title())
        if garment_type:
            parts.append(garment_type.strip().title())

        title = " ".join(parts).strip()
        if is_set and not title.lower().endswith("set"):
            title += " Set"
        return title

    # Standard non-runway title formula
    parts = []
    if year_era:
        era_clean = year_era.strip().lower()
        if era_clean in ["y2k", "y2k era"]:
            era_clean = "2000s"
        parts.append(era_clean)
    if designer:
        parts.append(designer.strip().lower())
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


def deduplicate_photo_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Group multi-angle studio photos (e.g. front/back/tag/detail shots) by look/item filename prefix.
    
    Returns (deduplicated_primary_items, total_duplicates_skipped).
    """
    if not items:
        return [], 0

    groups: dict[str, list[dict[str, Any]]] = {}

    for item in items:
        name = item.get("name", "")
        stem = Path(name).stem.lower()

        # Clean explicit multi-angle suffixes (_front, _back, _tag, _detail, _side, _close, _zoom, _a, _b, _c, _d)
        clean_stem = re.sub(r'[\_\-\s]+(front|back|tag|detail|side|close|zoom|angle\d*)$', '', stem, flags=re.IGNORECASE)
        clean_stem = re.sub(r'[\_\-\s]+[a-d]$', '', clean_stem, flags=re.IGNORECASE)
        clean_stem = re.sub(r'\s*\(\d+\)$', '', clean_stem)
        # Shot index numbers like _1, _2 ONLY when preceded by an item number (e.g. _001_1 -> _001)
        clean_stem = re.sub(r'([\_\-\s]+\d{2,})[\_\-\s]+[1-9]$', r'\1', clean_stem)
        clean_stem = clean_stem.strip() or stem

        if clean_stem not in groups:
            groups[clean_stem] = []
        groups[clean_stem].append(item)

    deduped = []
    skipped_count = 0

    for group_key, group_items in groups.items():
        primary = group_items[0]
        for g in group_items:
            g_name = g.get("name", "").lower()
            if any(k in g_name for k in ["front", "main", "_1.", "-1.", "_a."]):
                primary = g
                break

        secondary = [g for g in group_items if g != primary]
        primary["secondary_angles"] = secondary
        deduped.append(primary)
        skipped_count += len(secondary)

    return deduped, skipped_count


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

    # 1. Local Folder Path Selector (if chosen via dialog)
    sel_path = st.session_state.get("selected_folder_path")
    if sel_path and Path(sel_path).exists() and Path(sel_path).is_dir():
        crawled_items, _ = crawl_local_directory(Path(sel_path), recursive=True)
        if crawled_items:
            st.success(f"📁 Loaded **{len(crawled_items)}** photo(s) from local directory `{sel_path}`!")
            for c in crawled_items:
                items_to_process.append({
                    "name": c["name"],
                    "path": c["path"],
                    "mime": "image/jpeg",
                    "bytes": None,
                    "url": "",
                })

    # 2. Native OS Folder Chooser (Client Webkit directory)
    if not items_to_process:
        folder_picker_result = _native_folder_picker(key="native_folder_picker_ui")
        if folder_picker_result and isinstance(folder_picker_result, list) and len(folder_picker_result) > 0:
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

    # 3. Manual Upload Fallback
    with st.expander("📤 Manual File Upload Fallback / Server Folder Path", expanded=not items_to_process):
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

        if hasattr(st, "dialog"):
            if st.button("📁 Browse Local Directory Path on Server", key="open_server_dir_dialog"):
                folder_picker_dialog()

    if items_to_process:
        if st.button("🔄 Reset / Select Different Photo Folder", key="reset_folder_selection"):
            st.session_state["native_folder_files"] = []
            st.session_state["selected_folder_path"] = None
            st.rerun()

    dedup_multi_angles = st.checkbox(
        "🎯 Auto-Deduplicate Multi-Angle Shots (Group front/back/tag photos per item & process 1 primary photo)",
        value=True,
        help="Skips duplicate orientation/back/detail photos of the same garment to save time and streamline results.",
    )

    if items_to_process and dedup_multi_angles:
        items_to_process, dups_skipped = deduplicate_photo_items(items_to_process)
        if dups_skipped > 0:
            st.info(f"✨ Auto-grouped photos into **{len(items_to_process)} unique garment(s)** (skipped {dups_skipped} multi-angle/tag duplicate shots).")

    if not items_to_process:
        st.warning("⚠️ **0 photos currently loaded.** Please click **'Select Photo Folder'** above to pick your folder.")

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

        from concurrent.futures import ThreadPoolExecutor, as_completed

        new_results = [None] * len(items_to_process)
        progress_bar = st.progress(0, text="Processing images in parallel...")

        def _process_single_item(args):
            idx, item = args
            image_bytes = item.get("bytes")
            if not image_bytes and item.get("path"):
                try:
                    image_bytes = item["path"].read_bytes()
                except Exception as ex:
                    pass

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
                    pass

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
                    designer = ai_data.get("designer") or "Cavalli"
                    if pub_url:
                        matches = fetch_serpapi_visual_matches(pub_url, brand=designer, engine="bing_reverse_image")
                        ai_data["visual_matches"] = matches
                        bolstered_print = extract_print_color_from_matches(matches, ai_data.get("print_color", ""))
                        if bolstered_print:
                            ai_data["print_color"] = bolstered_print
                            ai_data["suggested_title"] = format_shopify_title(
                                designer=ai_data.get("designer", ""),
                                year_era=ai_data.get("year_era", ""),
                                collection=ai_data.get("collection", ""),
                                print_color=bolstered_print,
                                garment_type=ai_data.get("garment_type", ""),
                                is_set=(ai_data.get("item_type") == "Set"),
                                notes=ai_data.get("notes", ""),
                            )
                        min_p, max_p = extract_prices_from_visual_matches(matches)
                        if min_p and max_p:
                            ai_data["min_price_usd"] = min_p
                            ai_data["max_price_usd"] = max_p
                        if not matches:
                            ai_data["match_status"] = f"No exact {designer} visual match found — needs manual QA"
                        else:
                            ai_data["match_status"] = f"Found {len(matches)} approved visual matches"
                    return idx, ai_data
                except Exception as ex:
                    return idx, {
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
                    }
            return idx, {
                "filename": item["name"],
                "item_type": "Single",
                "designer": "",
                "year_era": "2000s",
                "suggested_title": item["name"],
                "notes": "No image data available",
            }

        max_workers = min(12, max(2, len(items_to_process)))
        completed = 0
        total = len(items_to_process)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(_process_single_item, (i, item))
                for i, item in enumerate(items_to_process)
            ]
            for future in as_completed(futures):
                idx, result = future.result()
                new_results[idx] = result
                completed += 1
                progress_bar.progress(
                    completed / total,
                    text=f"Analyzing images [{completed}/{total}] ({max_workers} parallel workers)...",
                )

        new_results = [r for r in new_results if r is not None]

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

                        st.markdown("**🎯 Google Lens Exact Visual Matches (Title Comparison & Pricing Research):**")
                        v_matches = res.get("visual_matches") or []
                        img_url = res.get("public_image_url")
                        if not img_url and res.get("image_bytes"):
                            img_url = save_image_for_public_lens(res["image_bytes"], res.get("filename", ""))
                            res["public_image_url"] = img_url
                            if "reverse_search_results" in st.session_state and idx < len(st.session_state["reverse_search_results"]):
                                st.session_state["reverse_search_results"][idx]["public_image_url"] = img_url
                        if not img_url:
                            img_url = res.get("image_url", "")

                        if not v_matches and img_url:
                            if st.button(f"🔍 Fetch Google Lens Matches for {res.get('filename') or 'Photo'}", key=f"fetch_lens_{idx}"):
                                with st.spinner("Fetching exact visual matches from Google Lens..."):
                                    fetched = fetch_serpapi_visual_matches(img_url)
                                    res["visual_matches"] = fetched
                                    min_p, max_p = extract_prices_from_visual_matches(fetched)
                                    if min_p and max_p:
                                        res["min_price_usd"] = min_p
                                        res["max_price_usd"] = max_p
                                    if "reverse_search_results" in st.session_state and idx < len(st.session_state["reverse_search_results"]):
                                        st.session_state["reverse_search_results"][idx]["visual_matches"] = fetched
                                        st.session_state["reverse_search_results"][idx]["min_price_usd"] = res.get("min_price_usd")
                                        st.session_state["reverse_search_results"][idx]["max_price_usd"] = res.get("max_price_usd")
                                    v_matches = fetched
                                    st.rerun()

                        p_min = int(res.get("min_price_usd") or 0)
                        p_max = int(res.get("max_price_usd") or 0)
                        if p_min > 0 or p_max > 0:
                            st.info(f"💵 **Estimated Market Resale Price Range:** **${p_min} – ${p_max} USD** (Low: **${p_min}** | High: **${p_max}** based on exact comps & market valuation)")

                        if v_matches:
                            match_data = []
                            # Display top 2 to 4 exact matches from reputable platforms
                            top_matches = v_matches[:4]
                            for vm in top_matches:
                                title = vm.get("title", "Matched Item")
                                source = vm.get("source", "Marketplace")
                                link = vm.get("link", "#")
                                p_dict = vm.get("price") if isinstance(vm.get("price"), dict) else {}
                                price_val = p_dict.get("value") or p_dict.get("extracted_value") or "N/A"
                                match_data.append({
                                    "Platform / Source": source,
                                    "Listing Title (Exact Visual Match)": title,
                                    "Price": price_val,
                                    "Listing Link": link,
                                })
                            st.caption("Showing top exact matches from reputable resale platforms (Grailed, Vestiaire, 1stDibs, The RealReal, eBay, Depop, Poshmark). Fast fashion filtered out.")
                            st.dataframe(
                                match_data,
                                column_config={
                                    "Platform / Source": st.column_config.TextColumn("Source", width="medium"),
                                    "Listing Title (Exact Visual Match)": st.column_config.TextColumn("Listing Title", width="large"),
                                    "Price": st.column_config.TextColumn("Price", width="small"),
                                    "Listing Link": st.column_config.LinkColumn("Listing Link", display_text="🔗 View Listing"),
                                },
                                use_container_width=True,
                                hide_index=True,
                            )
                        else:
                            st.caption("Click 'Run AI Reverse Research' or 'Fetch Google Lens Matches' to pull exact visual comps.")

                        links = build_search_urls(res.get("search_query") or res.get("suggested_title", ""), img_url)
                        st.link_button("👁️ Open Bing Visual Search (Browser View)", links.get("Bing Visual", "#"), use_container_width=True)

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

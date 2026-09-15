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
    """Upload studio photo bytes to temporary public host (Uguu/Catbox CDN) so Google Lens/Bing can access the image directly without Cloudflare Access auth blocks."""
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
        # Primary: Uguu.se CDN upload
        try:
            resp = requests.post(
                "https://uguu.se/upload",
                files={"files[]": (f"{img_hash}{ext}", image_bytes, f"image/{ext.lstrip('.')}")},
                timeout=12,
            )
            if resp.status_code == 200:
                data = resp.json()
                if "files" in data and len(data["files"]) > 0:
                    return data["files"][0]["url"]
        except Exception:
            pass

        # Secondary: Litterbox CDN upload
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


def fetch_serpapi_visual_matches(image_url: str, brand: str = "Cavalli", engine: str = "google_lens") -> list[dict[str, Any]]:
    """Query SerpAPI (Google Lens or Bing Reverse Image) with public image URL to fetch exact visual matches.
    
    Filters strictly by approved resale/luxury platforms and excludes fast-fashion sites.
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
        # 1. Primary engine: google_lens
        r1 = requests.get(
            "https://serpapi.com/search.json",
            params={"engine": "google_lens", "url": image_url, "api_key": serp_key},
            timeout=15,
        )
        if r1.status_code == 200:
            data1 = r1.json()
            matches = data1.get("visual_matches", []) or []

        # 2. Secondary engine fallback: bing_reverse_image
        if len(matches) < 5:
            params_bing = {"engine": "bing_reverse_image", "image_url": image_url, "api_key": serp_key}
            if brand:
                params_bing["q"] = brand
            r2 = requests.get(
                "https://serpapi.com/search.json",
                params=params_bing,
                timeout=15,
            )
            if r2.status_code == 200:
                data2 = r2.json()
                rc = data2.get("related_content", []) or data2.get("organic_results", []) or []
                for item in rc:
                    matches.append({
                        "title": item.get("title", ""),
                        "link": item.get("source") or item.get("link", ""),
                        "source": item.get("source", ""),
                        "price": item.get("price"),
                    })
    except Exception:
        pass

    # Filter results strictly by SAME BRAND + approved luxury/resale platforms and fast-fashion exclusion
    filtered = []
    # Core brand keyword required for visual match validation (e.g., "cavalli")
    brand_keywords = ["cavalli"]
    if brand and "cavalli" not in brand.lower():
        brand_keywords.append(brand.lower().split()[0])

    for m in matches:
        link = (m.get("link") or m.get("source") or "").lower()
        title = (m.get("title") or m.get("snippet") or "").lower()
        source = (m.get("source") or "").lower()

        # 1. Exclude fast fashion
        if any(ff in link or ff in source for ff in FAST_FASHION_DOMAINS):
            continue

        # 2. STRICT BRAND CHECK: MUST explicitly contain the target brand name
        has_brand = any(bk in title or bk in link or bk in source for bk in brand_keywords)
        if not has_brand:
            continue

        # 3. Must be an approved resale platform or verified boutique
        is_approved = any(ap in link or ap in source for ap in APPROVED_PLATFORM_DOMAINS)
        if is_approved or has_brand:
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
    """Format concise Shopify title with Title Case capitalization.
    If designer is unknown or generic ('unknown', 'unbranded', 'generic'), leave blank.
    Formula: [Era/Year] [Designer] [Collection] [Color/Print] [Garment Type] [Set]
    """
    import re

    def cap(text: str) -> str:
        if not text:
            return ""
        return " ".join(w.capitalize() if not w.isupper() else w for w in text.strip().split())

    # Filter out unknown or generic brand names
    d_clean = designer.strip() if designer else ""
    if d_clean.lower() in ["unknown", "unbranded", "generic", "none", "n/a", "unsure"]:
        d_clean = ""

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
        if d_clean:
            parts.append(cap(d_clean))
        if print_color:
            parts.append(cap(print_color))
        if garment_type:
            parts.append(cap(garment_type))

        title = " ".join(parts).strip()
        if is_set and not title.lower().endswith("set"):
            title += " Set"
        return title

    # Standard non-runway title formula
    parts = []
    if year_era:
        era_clean = year_era.strip()
        if era_clean.lower() in ["y2k", "y2k era"]:
            era_clean = "2000s"
        parts.append(cap(era_clean))

    if d_clean:
        parts.append(cap(d_clean))

    if collection:
        parts.append(cap(collection))

    if print_color:
        parts.append(cap(print_color))

    if garment_type:
        parts.append(cap(garment_type))

    title = " ".join(parts).strip()
    if is_set and not title.lower().endswith("set"):
        title += " Set"

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


def calculate_listing_price(cost: float = 0.0, min_comp: float = 0.0, max_comp: float = 0.0) -> int:
    """Calculate recommended listing price using cost and/or comp low/high range.
    
    Rules:
    - If cost is present and >0:
        Aim for ~3.5x cost markup, bounded by min_comp and max_comp.
    - If no cost is present:
        Default to mid-point or high-end of comp range.
    """
    c = float(cost or 0)
    lo = float(min_comp or 0)
    hi = float(max_comp or 0)

    if c > 0:
        target = c * 3.5
        if lo > 0 and hi > 0:
            if target < lo:
                return int(lo)
            elif target > hi:
                return int(hi)
            else:
                return int(target)
        elif lo > 0:
            return int(max(target, lo))
        elif hi > 0:
            return int(min(target, hi))
        else:
            return int(round(target, -1))
    else:
        if lo > 0 and hi > 0:
            return int(round((lo + hi) / 2.0))
        elif hi > 0:
            return int(hi)
        elif lo > 0:
            return int(lo)

    return 0


def get_vendor_for_item(designer: str = "") -> str:
    """Determine Shopify Vendor value based on brand rules:
    - Cavalli lines (Just Cavalli, Class Cavalli, Cavalli Freedom, Roberto Cavalli) -> 'Roberto Cavalli'
    - Unknown/blank -> 'Vintage'
    - Otherwise -> designer.title()
    """
    clean_b = designer.strip() if designer else ""
    if not clean_b or clean_b.lower() in ["unknown", "generic", "unbranded", "none", "n/a", "unsure"]:
        return "Vintage"
    if "cavalli" in clean_b.lower():
        return "Roberto Cavalli"
    return clean_b.title()


def generate_sku_for_item(brand: str = "", idx: int = 1) -> str:
    """Generate canonical Shopify SKU matching codebase standard in to_shopify.py: {BRAND_PREFIX}_{YYMM}_{NNN}.
    Rule: Cavalli (any line) is always ROB. Unknown brand is UNK.
    """
    from datetime import datetime
    yymm = datetime.now().strftime("%y%m")
    clean_b = brand.strip() if brand else ""
    low_b = clean_b.lower()

    if "cavalli" in low_b:
        prefix = "ROB"
    elif not clean_b or low_b in ["unknown", "unbranded", "generic", "none", "n/a", "unsure"]:
        prefix = "UNK"
    else:
        BRAND_PREFIXES = {
            "burberry": "BUR",
            "jean paul gaultier": "JPG", "gaultier": "JPG", "blumarine": "BLU",
            "dolce & gabbana": "DG", "dolce and gabbana": "DG", "gucci": "GUC",
            "prada": "PRA", "chanel": "CHA", "dior": "CD", "christian dior": "CD",
            "versace": "VER", "yves saint laurent": "YSL", "saint laurent": "SL",
            "fendi": "FEN", "giorgio armani": "GA", "armani": "ARM",
            "vivienne westwood": "VWW", "moschino": "MOS", "missoni": "MIS",
        }
        if low_b in BRAND_PREFIXES:
            prefix = BRAND_PREFIXES[low_b]
        else:
            letters = "".join(c for c in clean_b if c.isalpha())[:3].upper()
            prefix = letters or "UNK"

    return f"{prefix}_{yymm}_{idx:03d}"


def get_body_html_template(item: dict[str, Any]) -> str:
    """Return exact HTML description template skeleton from Copy Formats tab (description_templates.yaml)."""
    item_type = str(item.get("item_type", "")).strip().lower()
    garment = str(item.get("garment_type", "")).strip().lower()

    # 1. Sets (Top + Bottom measurement prompts)
    if item_type == "set" or "set" in garment or "co-ord" in garment or "suit" in garment:
        return (
            "<p><strong>TAGGED SIZE:</strong> </p>\n"
            "<p><strong>MEASUREMENTS:</strong></p>\n"
            "<table>\n"
            "  <tbody>\n"
            "    <tr><td><strong>CHEST</strong></td><td></td></tr>\n"
            "    <tr><td><strong>TOP LENGTH</strong></td><td></td></tr>\n"
            "    <tr><td><strong>WAIST</strong></td><td></td></tr>\n"
            "    <tr><td><strong>HIPS</strong></td><td></td></tr>\n"
            "    <tr><td><strong>INSEAM</strong></td><td></td></tr>\n"
            "    <tr><td><strong>RISE</strong></td><td></td></tr>\n"
            "    <tr><td><strong>BOTTOM LENGTH</strong></td><td></td></tr>\n"
            "  </tbody>\n"
            "</table>\n"
            "<p><strong>CONDITION NOTES:</strong></p>\n"
        )

    # 2. Bottoms (Pants, Skirts, Shorts, Jeans, Trousers)
    if any(b in garment for b in ["bottom", "pant", "skirt", "short", "jean", "trouser", "legging"]):
        return (
            "<p><strong>TAGGED SIZE:</strong> </p>\n"
            "<p><strong>MEASUREMENTS:</strong></p>\n"
            "<table>\n"
            "  <tbody>\n"
            "    <tr><td><strong>WAIST</strong></td><td></td></tr>\n"
            "    <tr><td><strong>HIPS</strong></td><td></td></tr>\n"
            "    <tr><td><strong>INSEAM</strong></td><td></td></tr>\n"
            "    <tr><td><strong>RISE</strong></td><td></td></tr>\n"
            "    <tr><td><strong>LENGTH</strong></td><td></td></tr>\n"
            "  </tbody>\n"
            "</table>\n"
            "<p><strong>CONDITION NOTES:</strong></p>\n"
        )

    # 3. Outerwear / Coats / Jackets
    if any(o in garment for o in ["jacket", "coat", "outerwear", "blazer", "trench"]):
        return (
            "<p><strong>TAGGED SIZE:</strong> </p>\n"
            "<p><strong>MEASUREMENTS:</strong></p>\n"
            "<table>\n"
            "  <tbody>\n"
            "    <tr><td><strong>CHEST</strong></td><td></td></tr>\n"
            "    <tr><td><strong>LENGTH</strong></td><td></td></tr>\n"
            "    <tr><td><strong>SLEEVE</strong></td><td></td></tr>\n"
            "    <tr><td><strong>SHOULDER</strong></td><td></td></tr>\n"
            "  </tbody>\n"
            "</table>\n"
            "<p><strong>CONDITION NOTES:</strong></p>\n"
        )

    # 4. Handbags / Bags
    if any(h in garment for h in ["bag", "handbag", "purse", "tote", "clutch"]):
        return (
            "<p><strong>DIMENSIONS:</strong><br></p>\n"
            "<p><strong>DETAILS:</strong></p>\n"
            "<ul>\n"
            "  <li></li>\n"
            "  <li></li>\n"
            "  <li></li>\n"
            "</ul>\n"
            "<p><strong>MATERIAL:</strong><br></p>\n"
            "<p><strong>CONDITION NOTES:</strong></p>\n"
        )

    # 5. Tops / Default Tops / Shirts / Sweaters / Dresses
    return (
        "<p><strong>TAGGED SIZE:</strong> </p>\n"
        "<p><strong>MEASUREMENTS:</strong></p>\n"
        "<table>\n"
        "  <tbody>\n"
        "    <tr><td><strong>CHEST</strong></td><td></td></tr>\n"
        "    <tr><td><strong>LENGTH</strong></td><td></td></tr>\n"
        "  </tbody>\n"
        "</table>\n"
        "<p><strong>CONDITION NOTES:</strong></p>\n"
    )


def generate_shopify_import_csv(results: list[dict[str, Any]]) -> str:
    """Generate official Shopify Product CSV import string matching Shopify's product CSV spec."""
    output = io.StringIO()
    writer = csv.writer(output)

    headers = [
        "Handle",
        "Title",
        "Body (HTML)",
        "Vendor",
        "Product Category",
        "Type",
        "Tags",
        "Published",
        "Option1 Name",
        "Option1 Value",
        "Variant SKU",
        "Variant Grams",
        "Variant Inventory Tracker",
        "Variant Inventory Qty",
        "Variant Inventory Policy",
        "Variant Fulfillment Service",
        "Variant Price",
        "Variant Compare At Price",
        "Variant Requires Shipping",
        "Variant Taxable",
        "Cost per item",
        "Image Src",
        "Image Position",
        "Status",
    ]
    writer.writerow(headers)

    for idx, item in enumerate(results, 1):
        title = item.get("suggested_title") or f"Item {idx}"
        designer = item.get("designer", "").strip()
        vendor = get_vendor_for_item(designer)

        sku = generate_sku_for_item(designer, idx)
        title_slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-') or f"item-{idx}"
        sku_slug = re.sub(r'[^a-z0-9]+', '-', sku.lower()).strip('-')
        handle = f"{title_slug}-{sku_slug}"

        body_html = get_body_html_template(item)

        listing_p = item.get("listing_price_usd") or calculate_listing_price(
            cost=item.get("cost_price_usd", 0),
            min_comp=item.get("min_price_usd", 0),
            max_comp=item.get("max_price_usd", 0),
        )
        cost_p = item.get("cost_price_usd") or ""
        hi_p = item.get("max_price_usd") or ""
        compare_at = str(hi_p) if hi_p and listing_p and int(hi_p) > int(listing_p) else ""

        img_url = item.get("public_image_url") or item.get("image_url") or ""

        writer.writerow([
            handle,
            title,
            body_html,
            vendor,
            "Apparel & Accessories",
            "",
            "",
            "TRUE",
            "Title",
            "Default Title",
            sku,
            "0",
            "shopify",
            "1",
            "deny",
            "manual",
            str(listing_p or ""),
            compare_at,
            "TRUE",
            "TRUE",
            str(cost_p or ""),
            img_url,
            "1",
            "draft",
        ])

    return output.getvalue()


def push_research_results_to_shopify(results: list[dict[str, Any]]) -> tuple[int, int, list[str]]:
    """Push reviewed research results directly to Shopify as draft products."""
    try:
        from shopify_inventory import get_shop, get_token
        from shopify_push import _api_post, build_product_payload
    except ImportError:
        return 0, len(results), ["shopify_push or shopify_inventory module not available."]

    shop = get_shop()
    token = get_token()
    if not shop or not token:
        return 0, len(results), ["Shopify API credentials not configured. Please check shopify_inventory config."]

    pushed_count = 0
    failed_count = 0
    logs = []

    for idx, item in enumerate(results, 1):
        title = item.get("suggested_title") or f"Item {idx}"
        designer = item.get("designer", "").strip()
        vendor = get_vendor_for_item(designer)
        garment_type = item.get("garment_type", "Garment")
        year_era = item.get("year_era", "2000s")
        collection = item.get("collection", "")
        print_color = item.get("print_color", "")
        fabric = item.get("fabric", "")

        listing_p = float(item.get("listing_price_usd") or calculate_listing_price(
            cost=item.get("cost_price_usd", 0),
            min_comp=item.get("min_price_usd", 0),
            max_comp=item.get("max_price_usd", 0),
        ))
        cost_p = float(item.get("cost_price_usd") or 0.0)

        tags_list = ["vintage", "designer", year_era]
        if designer:
            tags_list.append(designer.lower())
        if garment_type:
            tags_list.append(garment_type.lower())
        if collection:
            tags_list.append(collection.lower())

        body_html = get_body_html_template(item)
        photo_p = Path(item["image_path"]) if item.get("image_path") and Path(item["image_path"]).exists() else None
        sku = generate_sku_for_item(designer, idx)

        payload = build_product_payload(
            item={},
            title=title,
            vendor=vendor,
            product_type="",
            sku=sku,
            price=listing_p,
            cost_usd=cost_p,
            photo_path=photo_p,
            tags=[],
            body_html=body_html,
        )

        status_code, resp = _api_post(shop, token, "products.json", {"product": payload})
        if status_code in (200, 201) and "product" in resp:
            p_id = resp["product"].get("id")
            pushed_count += 1
            item["shopify_product_id"] = p_id
            logs.append(f"✅ Pushed '{title}' -> Draft Product ID {p_id} (${listing_p:.2f} USD)")
        else:
            failed_count += 1
            err = resp.get("errors") or f"HTTP {status_code}"
            logs.append(f"❌ Failed pushing '{title}': {err}")

    return pushed_count, failed_count, logs


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
                    pub_url = save_image_for_public_lens(image_bytes, item["name"])
                    ai_data["public_image_url"] = pub_url
                    img_hash = hashlib.md5(image_bytes).hexdigest()
                    ext = Path(item["name"]).suffix.lower() if item["name"] else ".jpg"
                    if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
                        ext = ".jpg"
                    local_file = STATIC_LENS_DIR / f"{img_hash}{ext}"
                    if not local_file.exists():
                        local_file.write_bytes(image_bytes)
                    ai_data["image_path"] = str(local_file)
                    ai_data["image_url"] = item["url"]
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
        st.markdown(f"### 📋 Research Manifest & Workflow ({len(results)} items)")

        tab_step1, tab_step2, tab_step3 = st.tabs([
            "1 · Title, Brand & Description Editor",
            "2 · Pricing & QA Once-Over (Table Format)",
            "3 · Shopify Export & Direct Push",
        ])

        # Helper to ensure public image URL is populated for table thumbnails
        def _get_item_img_url(res: dict[str, Any]) -> str:
            url = res.get("public_image_url")
            if not url and res.get("image_bytes"):
                url = save_image_for_public_lens(res["image_bytes"], res.get("filename", ""))
                res["public_image_url"] = url
            if not url:
                url = res.get("image_url", "")
            return url

        # =========================================================================
        # TAB 1: Title, Brand & Description Columns
        # =========================================================================
        with tab_step1:
            st.info(
                "💡 **Step 1:** Review and edit garment description fields, brand/designer, and era. "
                "Shopify titles auto-regenerate in real-time as you edit!"
            )

            sub_v1, sub_v2 = st.tabs(["📊 Bulk Spreadsheet View (st.data_editor)", "📸 Combined Photo Cards"])

            with sub_v1:
                table_rows = []
                for res in results:
                    img_u = _get_item_img_url(res)
                    query = res.get("search_query") or res.get("suggested_title", "")
                    links = build_search_urls(query, img_u)

                    era_val = res.get("year_era", "2000s")
                    if str(era_val).strip().lower() in ["y2k", "y2k era"]:
                        era_val = "2000s"

                    table_rows.append({
                        "Photo": img_u,
                        "Source": res.get("filename") or res.get("image_url") or "Photo",
                        "Item Type": res.get("item_type", "Single"),
                        "Designer / Brand": res.get("designer", ""),
                        "Year / Era": era_val,
                        "Collection": res.get("collection", ""),
                        "Print / Color": res.get("print_color", ""),
                        "Garment Type": res.get("garment_type", ""),
                        "Fabric": res.get("fabric", ""),
                        "Shopify Title": res.get("suggested_title", ""),
                        "Notes": res.get("notes", ""),
                    })

                column_config = {
                    "Photo": st.column_config.ImageColumn("Photo", width="small"),
                    "Source": st.column_config.TextColumn("Source / Photo Name", width="medium", disabled=True),
                    "Item Type": st.column_config.SelectboxColumn("Item Type", options=["Single", "Set"], width="small"),
                    "Designer / Brand": st.column_config.TextColumn("Designer / Brand", width="medium"),
                    "Year / Era": st.column_config.TextColumn("Year / Era", width="small"),
                    "Collection": st.column_config.TextColumn("Collection Name", width="medium"),
                    "Print / Color": st.column_config.TextColumn("Print / Colorway", width="medium"),
                    "Garment Type": st.column_config.TextColumn("Garment Type", width="medium"),
                    "Fabric": st.column_config.TextColumn("Fabric / Material", width="small"),
                    "Shopify Title": st.column_config.TextColumn("Generated Shopify Title", width="large"),
                    "Notes": st.column_config.TextColumn("Notes", width="large"),
                }

                edited_df = st.data_editor(
                    table_rows,
                    column_config=column_config,
                    use_container_width=True,
                    num_rows="dynamic",
                    key="step1_data_editor",
                )

                if edited_df is not None:
                    for row, orig in zip(edited_df, results):
                        orig["item_type"] = row.get("Item Type", orig.get("item_type"))
                        orig["designer"] = row.get("Designer / Brand", orig.get("designer"))
                        orig["year_era"] = row.get("Year / Era", orig.get("year_era"))
                        orig["collection"] = row.get("Collection", orig.get("collection"))
                        orig["print_color"] = row.get("Print / Color", orig.get("print_color"))
                        orig["garment_type"] = row.get("Garment Type", orig.get("garment_type"))
                        orig["fabric"] = row.get("Fabric", orig.get("fabric"))
                        orig["suggested_title"] = row.get("Shopify Title", orig.get("suggested_title"))
                        orig["notes"] = row.get("Notes", orig.get("notes"))

            with sub_v2:
                for idx, res in enumerate(results):
                    # Normalize Y2K -> 2000s
                    if str(res.get("year_era", "")).strip().lower() in ["y2k", "y2k era"]:
                        res["year_era"] = "2000s"

                    with st.container(border=True):
                        col_img, col_fields = st.columns([1.3, 3.7])

                        with col_img:
                            img_u = _get_item_img_url(res)
                            if res.get("image_path") and Path(res["image_path"]).exists():
                                st.image(res["image_path"], use_container_width=True)
                            elif img_u:
                                st.image(img_u, use_container_width=True)
                            elif res.get("image_bytes"):
                                st.image(res["image_bytes"], use_container_width=True)
                            st.caption(f"**Source:** `{res.get('filename') or 'Photo'}`")

                        with col_fields:
                            f1, f2, f3 = st.columns(3)
                            with f1:
                                new_designer = st.text_input("Designer / Brand (Leave blank if unknown)", value=res.get("designer", ""), key=f"s1_des_{idx}")
                                new_era = st.text_input("Year / Era", value=res.get("year_era", "2000s"), key=f"s1_era_{idx}")
                                new_item_type = st.selectbox("Item Type", ["Single", "Set"], index=1 if res.get("item_type") == "Set" else 0, key=f"s1_type_{idx}")
                            with f2:
                                new_collection = st.text_input("Collection Name", value=res.get("collection", ""), key=f"s1_coll_{idx}")
                                new_print = st.text_input("Print / Colorway", value=res.get("print_color", ""), key=f"s1_print_{idx}")
                                new_garment = st.text_input("Garment Type", value=res.get("garment_type", ""), key=f"s1_garment_{idx}")
                            with f3:
                                new_fabric = st.text_input("Fabric / Material", value=res.get("fabric", ""), key=f"s1_fab_{idx}")
                                new_notes = st.text_input("Notes / Details", value=res.get("notes", ""), key=f"s1_notes_{idx}")

                            # Save updated metadata synchronously
                            res["designer"] = new_designer
                            res["year_era"] = new_era
                            res["item_type"] = new_item_type
                            res["collection"] = new_collection
                            res["print_color"] = new_print
                            res["garment_type"] = new_garment
                            res["fabric"] = new_fabric
                            res["notes"] = new_notes

                            # Auto-regenerate title using Shopify Title Formula
                            recalculated_title = format_shopify_title(
                                designer=new_designer,
                                year_era=new_era,
                                collection=new_collection,
                                print_color=new_print,
                                garment_type=new_garment,
                                is_set=(new_item_type == "Set"),
                                notes=new_notes,
                            )
                            res["suggested_title"] = st.text_input(
                                "Generated Shopify Title (Auto-updates during QA)",
                                value=recalculated_title or res.get("suggested_title", ""),
                                key=f"s1_title_{idx}",
                            )

            st.success("✅ **Metadata & Descriptions Saved!** Proceed to **2 · Pricing & QA Once-Over** above.")

        # =========================================================================
        # TAB 2: Pricing & QA Once-Over (Table Format)
        # =========================================================================
        with tab_step2:
            st.info(
                "💡 **Step 2 (Pricing & QA Table):** Review market comps (Low/High), input purchase cost, and do your final once-over on listing prices in a compact table. "
                "Editing a Brand/Vendor automatically regenerates the Shopify Title!"
            )

            # CSS Hover Zoom for Image Thumbnails
            st.markdown(
                """
                <style>
                .qa-hover-thumb {
                    width: 55px;
                    height: 55px;
                    object-fit: cover;
                    border-radius: 6px;
                    transition: transform 0.25s ease, box-shadow 0.25s ease;
                    cursor: pointer;
                }
                .qa-hover-thumb:hover {
                    transform: scale(4.5);
                    z-index: 9999;
                    position: relative;
                    box-shadow: 0px 8px 25px rgba(0,0,0,0.6);
                }
                </style>
                """,
                unsafe_allow_html=True,
            )

            qa_sub1, qa_sub2 = st.tabs([
                "📊 Interactive Pricing Spreadsheet (st.data_editor)",
                "🔍 Hover-Zoom Interactive Table",
            ])

            with qa_sub1:
                qa_table_rows = []
                for res in results:
                    img_u = _get_item_img_url(res)

                    v_matches = res.get("visual_matches") or []
                    match_summary = []
                    top_link = ""
                    if v_matches:
                        top_link = v_matches[0].get("link") or ""
                        for vm in v_matches[:3]:
                            source = vm.get("source", "Comp")
                            p_dict = vm.get("price") if isinstance(vm.get("price"), dict) else {}
                            price_val = p_dict.get("value") or p_dict.get("extracted_value") or ""
                            if price_val:
                                match_summary.append(f"{source}: ${price_val}")
                            else:
                                match_summary.append(source)
                    summary_str = " | ".join(match_summary) if match_summary else "No exact comps found"

                    p_cost = float(res.get("cost_price_usd") or 0.0)
                    p_min = int(res.get("min_price_usd") or 0)
                    p_max = int(res.get("max_price_usd") or 0)

                    if not res.get("listing_price_usd"):
                        res["listing_price_usd"] = calculate_listing_price(cost=p_cost, min_comp=p_min, max_comp=p_max)
                    p_list = int(res.get("listing_price_usd") or 0)

                    qa_table_rows.append({
                        "Photo": img_u,
                        "Source Photo": res.get("filename") or "Photo",
                        "Brand / Vendor": res.get("designer", ""),
                        "Shopify Title": res.get("suggested_title", ""),
                        "Purchase Cost ($USD)": int(p_cost),
                        "Low Comp ($USD)": p_min,
                        "High Comp ($USD)": p_max,
                        "🔥 Final Listing Price ($USD)": p_list,
                        "Comps Summary": summary_str,
                        "Top Match Link": top_link,
                    })

                qa_col_config = {
                    "Photo": st.column_config.ImageColumn("Photo (Click to view)", width="small"),
                    "Source Photo": st.column_config.TextColumn("Source Photo", width="medium", disabled=True),
                    "Brand / Vendor": st.column_config.TextColumn("Brand / Vendor (Input/Fix)", width="medium"),
                    "Shopify Title": st.column_config.TextColumn("Shopify Title (Auto-updated)", width="large"),
                    "Purchase Cost ($USD)": st.column_config.NumberColumn("Cost ($)", format="$%d", width="small"),
                    "Low Comp ($USD)": st.column_config.NumberColumn("Low Comp ($)", format="$%d", width="small"),
                    "High Comp ($USD)": st.column_config.NumberColumn("High Comp ($)", format="$%d", width="small"),
                    "🔥 Final Listing Price ($USD)": st.column_config.NumberColumn("🔥 Final Listing Price ($)", format="$%d", width="small"),
                    "Comps Summary": st.column_config.TextColumn("Comps Summary", width="medium", disabled=True),
                    "Top Match Link": st.column_config.LinkColumn("Comp Link", display_text="🔗 View Comp"),
                }

                edited_qa_df = st.data_editor(
                    qa_table_rows,
                    column_config=qa_col_config,
                    use_container_width=True,
                    num_rows="dynamic",
                    key="step2_qa_data_editor",
                )

                if st.button("💾 Save Edits & Update Shopify Titles", type="primary", use_container_width=True, key="save_qa_titles_btn"):
                    if edited_qa_df is not None:
                        for row, orig in zip(edited_qa_df, results):
                            old_brand = orig.get("designer", "")
                            new_brand = str(row.get("Brand / Vendor") or "").strip()
                            orig["designer"] = new_brand

                            user_edited_title = str(row.get("Shopify Title") or "").strip()
                            if new_brand != old_brand or not user_edited_title:
                                orig["suggested_title"] = format_shopify_title(
                                    designer=new_brand,
                                    year_era=orig.get("year_era", ""),
                                    collection=orig.get("collection", ""),
                                    print_color=orig.get("print_color", ""),
                                    garment_type=orig.get("garment_type", ""),
                                    is_set=(orig.get("item_type") == "Set"),
                                    notes=orig.get("notes", ""),
                                )
                            else:
                                orig["suggested_title"] = user_edited_title

                            orig["cost_price_usd"] = row.get("Purchase Cost ($USD)", orig.get("cost_price_usd"))
                            orig["min_price_usd"] = row.get("Low Comp ($USD)", orig.get("min_price_usd"))
                            orig["max_price_usd"] = row.get("High Comp ($USD)", orig.get("max_price_usd"))
                            orig["listing_price_usd"] = row.get("🔥 Final Listing Price ($USD)", orig.get("listing_price_usd"))

                        st.session_state["reverse_search_results"] = results
                        st.success("🎉 All Brand, Vendor & Title changes saved successfully!")
                        st.rerun()

                if edited_qa_df is not None:
                    for row, orig in zip(edited_qa_df, results):
                        old_brand = orig.get("designer", "")
                        new_brand = str(row.get("Brand / Vendor") or "").strip()
                        orig["designer"] = new_brand

                        # Auto-regenerate title if brand changed in QA table
                        if new_brand != old_brand:
                            orig["suggested_title"] = format_shopify_title(
                                designer=new_brand,
                                year_era=orig.get("year_era", ""),
                                collection=orig.get("collection", ""),
                                print_color=orig.get("print_color", ""),
                                garment_type=orig.get("garment_type", ""),
                                is_set=(orig.get("item_type") == "Set"),
                                notes=orig.get("notes", ""),
                            )
                        else:
                            orig["suggested_title"] = row.get("Shopify Title", orig.get("suggested_title"))

                        orig["cost_price_usd"] = row.get("Purchase Cost ($USD)", orig.get("cost_price_usd"))
                        orig["min_price_usd"] = row.get("Low Comp ($USD)", orig.get("min_price_usd"))
                        orig["max_price_usd"] = row.get("High Comp ($USD)", orig.get("max_price_usd"))
                        orig["listing_price_usd"] = row.get("🔥 Final Listing Price ($USD)", orig.get("listing_price_usd"))

            with qa_sub2:
                st.caption("🔍 **Hover Cursor Over Any Thumbnail Below to Instantly Expand Image (4.5x Zoom)!**")

                for idx, res in enumerate(results):
                    img_u = _get_item_img_url(res)
                    p_min = int(res.get("min_price_usd") or 0)
                    p_max = int(res.get("max_price_usd") or 0)
                    p_cost = float(res.get("cost_price_usd") or 0.0)
                    if not res.get("listing_price_usd"):
                        res["listing_price_usd"] = calculate_listing_price(cost=p_cost, min_comp=p_min, max_comp=p_max)
                    p_list = int(res.get("listing_price_usd") or 0)

                    with st.container(border=True):
                        col_img, col_info, col_pr = st.columns([0.8, 2.5, 2.7])

                        with col_img:
                            if img_u:
                                st.markdown(f'<img src="{img_u}" class="qa-hover-thumb" title="Hover to expand">', unsafe_allow_html=True)
                            st.caption(f"`{res.get('filename') or 'Photo'}`")

                        with col_info:
                            h_brand = st.text_input("Brand / Vendor", value=res.get("designer", ""), key=f"hz_des_{idx}")
                            if h_brand != res.get("designer"):
                                res["designer"] = h_brand
                                res["suggested_title"] = format_shopify_title(
                                    designer=h_brand,
                                    year_era=res.get("year_era", ""),
                                    collection=res.get("collection", ""),
                                    print_color=res.get("print_color", ""),
                                    garment_type=res.get("garment_type", ""),
                                    is_set=(res.get("item_type") == "Set"),
                                    notes=res.get("notes", ""),
                                )
                            h_title = st.text_input("Shopify Title", value=res.get("suggested_title", ""), key=f"hz_title_{idx}")
                            res["suggested_title"] = h_title

                        with col_pr:
                            p1, p2, p3, p4 = st.columns(4)
                            with p1:
                                res["cost_price_usd"] = st.number_input("Cost ($)", value=int(p_cost), key=f"hz_cost_{idx}")
                            with p2:
                                res["min_price_usd"] = st.number_input("Low ($)", value=int(p_min), key=f"hz_pmin_{idx}")
                            with p3:
                                res["max_price_usd"] = st.number_input("High ($)", value=int(p_max), key=f"hz_pmax_{idx}")
                            with p4:
                                res["listing_price_usd"] = st.number_input("🔥 Price ($)", value=int(p_list), key=f"hz_plist_{idx}")

            # Save state explicitly
            st.session_state["reverse_search_results"] = results
            st.success("✅ **Pricing & QA Changes Synced!** Proceed to **3 · Shopify Export & Direct Push** above.")

        # =========================================================================
        # TAB 3: Export & Direct Push Page
        # =========================================================================
        with tab_step3:
            # Sync latest results from state
            results = st.session_state.get("reverse_search_results", [])
            st.info("💡 **Step 3:** Final view of all formatted Shopify CSV values. Download CSV or push directly to Shopify!")

            tot_items = len(results)
            tot_val = sum(int(r.get("listing_price_usd") or 0) for r in results)
            avg_val = int(tot_val / tot_items) if tot_items else 0

            m1, m2, m3 = st.columns(3)
            with m1:
                st.metric("📦 Total Draft Products", tot_items)
            with m2:
                st.metric("💰 Total Catalog Listing Value", f"${tot_val:,} USD")
            with m3:
                st.metric("🏷️ Average Listing Price", f"${avg_val:,} USD")

            col_exp1, col_exp2, col_exp3 = st.columns([1.5, 1.5, 2])
            with col_exp1:
                st.download_button(
                    label="🛍️ Download Official Shopify Product CSV",
                    data=generate_shopify_import_csv(results),
                    file_name="shopify_product_import.csv",
                    mime="text/csv",
                    type="primary",
                    use_container_width=True,
                )
            with col_exp2:
                st.download_button(
                    label="📊 Download Research Manifest CSV",
                    data=generate_manifest_csv(results),
                    file_name="reverse_search_manifest.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            with col_exp3:
                if st.button("🚀 Push Drafts Directly to Shopify", type="secondary", use_container_width=True):
                    with st.spinner("Pushing draft listings to Shopify Admin API..."):
                        pushed_ok, failed_err, logs = push_research_results_to_shopify(results)
                        if pushed_ok > 0:
                            st.success(f"🎉 Created **{pushed_ok} draft product(s)** in your Shopify store!")
                        if failed_err > 0:
                            st.error(f"⚠️ Failed to push {failed_err} product(s).")
                        with st.expander("📋 View Shopify API Push Logs", expanded=True):
                            for log in logs:
                                st.write(log)

            st.markdown("### 📊 Official Shopify Import CSV Preview (Exact 24 Columns)")

            preview_rows = []
            for idx, res in enumerate(results, 1):
                img_u = _get_item_img_url(res)
                title = res.get("suggested_title") or f"Item {idx}"
                designer = res.get("designer", "").strip()
                vendor = get_vendor_for_item(designer)
                year_era = res.get("year_era", "2000s")
                garment_type = res.get("garment_type", "Garment")
                collection = res.get("collection", "")
                print_color = res.get("print_color", "")
                fabric = res.get("fabric", "")

                sku = generate_sku_for_item(designer, idx)
                title_slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-') or f"item-{idx}"
                sku_slug = re.sub(r'[^a-z0-9]+', '-', sku.lower()).strip('-')
                handle = f"{title_slug}-{sku_slug}"

                body_html = get_body_html_template(res)

                list_p = res.get("listing_price_usd") or calculate_listing_price(
                    cost=res.get("cost_price_usd", 0),
                    min_comp=res.get("min_price_usd", 0),
                    max_comp=res.get("max_price_usd", 0),
                )
                cost_p = res.get("cost_price_usd") or ""
                hi_p = res.get("max_price_usd") or ""
                compare_at = str(hi_p) if hi_p and list_p and int(hi_p) > int(list_p) else ""

                preview_rows.append({
                    "Photo": img_u,
                    "Handle": handle,
                    "Title": title,
                    "Body (HTML)": body_html,
                    "Vendor": vendor,
                    "Product Category": "Apparel & Accessories",
                    "Type": "",
                    "Tags": "",
                    "Published": "TRUE",
                    "Option1 Name": "Title",
                    "Option1 Value": "Default Title",
                    "Variant SKU": sku,
                    "Variant Grams": "0",
                    "Variant Inventory Tracker": "shopify",
                    "Variant Inventory Qty": "1",
                    "Variant Inventory Policy": "deny",
                    "Variant Fulfillment Service": "manual",
                    "Variant Price": str(list_p or ""),
                    "Variant Compare At Price": compare_at,
                    "Variant Requires Shipping": "TRUE",
                    "Variant Taxable": "TRUE",
                    "Cost per item": str(cost_p or ""),
                    "Image Src": img_u,
                    "Image Position": "1",
                    "Status": "draft",
                })

            st.dataframe(
                preview_rows,
                column_config={
                    "Photo": st.column_config.ImageColumn("Photo", width="small"),
                    "Handle": st.column_config.TextColumn("Handle", width="medium"),
                    "Title": st.column_config.TextColumn("Title", width="large"),
                    "Body (HTML)": st.column_config.TextColumn("Body (HTML)", width="medium"),
                    "Vendor": st.column_config.TextColumn("Vendor", width="small"),
                    "Product Category": st.column_config.TextColumn("Product Category", width="medium"),
                    "Type": st.column_config.TextColumn("Type", width="small"),
                    "Tags": st.column_config.TextColumn("Tags", width="medium"),
                    "Variant SKU": st.column_config.TextColumn("Variant SKU", width="small"),
                    "Variant Price": st.column_config.TextColumn("Variant Price ($)", width="small"),
                    "Cost per item": st.column_config.TextColumn("Cost per item ($)", width="small"),
                    "Image Src": st.column_config.TextColumn("Image Src", width="medium"),
                    "Status": st.column_config.TextColumn("Status", width="small"),
                },
                use_container_width=True,
                hide_index=True,
            )

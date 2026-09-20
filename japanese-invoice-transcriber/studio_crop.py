"""Studio Auto-Crop & Retroactive Background Fixer Tab for Past Studies.

Documented in RULES.md (2026-08-13).
Multi-Threaded Parallel Bulk Batch Processing & Condensed UI Grid.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import urllib.request
import urllib.parse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
import numpy as np
import cv2
from PIL import Image
import streamlit as st

CROP_PIPELINE_DIR = Path("/home/kat/workspace/hot-girl-shopify/ecomm-crop-pipeline")
if str(CROP_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(CROP_PIPELINE_DIR))

from crop_pipeline.tabletop_exposure import (
    process_pure_white_bg_equalization,
    process_tabletop_exposure,
    extend_photo_edges_seamless,
)

TARGET_W = 1536
TARGET_H = 2048

PREVIEW_CACHE_DIR = Path("/tmp/studio_crop_cache")
PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)

RAW_CACHE_DIR = Path("/tmp/shopify_raw_cache")
RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_HTTP_SESSION = None

def get_http_session() -> requests.Session:
    global _HTTP_SESSION
    if _HTTP_SESSION is None:
        s = requests.Session()
        retries = Retry(
            total=4,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504, 429],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=25, pool_maxsize=25)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"})
        _HTTP_SESSION = s
    return _HTTP_SESSION

def get_cached_preview(prod_id: int, image_id: int) -> bytes | None:
    cache_file = PREVIEW_CACHE_DIR / f"{prod_id}_{image_id}.jpg"
    if cache_file.exists():
        try:
            return cache_file.read_bytes()
        except Exception:
            return None
    return None

def save_cached_preview(prod_id: int, image_id: int, img_bytes: bytes):
    try:
        cache_file = PREVIEW_CACHE_DIR / f"{prod_id}_{image_id}.jpg"
        cache_file.write_bytes(img_bytes)
    except Exception:
        pass

def clear_cached_previews(prod_id: int, images: list[dict]):
    for img in images:
        cache_file = PREVIEW_CACHE_DIR / f"{prod_id}_{img['id']}.jpg"
        if cache_file.exists():
            try:
                cache_file.unlink()
            except Exception:
                pass

def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """Convert hex string '#RRGGBB' to (R, G, B) tuple."""
    hex_str = hex_str.lstrip('#')
    return tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))

def get_shopify_credentials():
    from shopify_inventory import get_shop, get_token
    shop = get_shop() or os.environ.get("SHOPIFY_SHOP", "paststudies.myshopify.com")
    token = get_token()
    return shop, token

@st.cache_data(ttl=120)
def fetch_active_shopify_products_search(search_query: str = "") -> list[dict]:
    """Fetch active & draft listings from Shopify Admin GraphQL API across all 3,300+ store items sorted newest first."""
    shop, token = get_shopify_credentials()
    if not token:
        return []

    # Accept both active and draft products; support tag:tag_name and freeform text search
    if search_query.strip():
        q_str = f"(status:active OR status:draft) ({search_query.strip()})"
    else:
        q_str = "status:active OR status:draft"

    gql = """
    query ($q: String!) {
      products(first: 250, query: $q, sortKey: CREATED_AT, reverse: true) {
        edges {
          node {
            id
            createdAt
            status
            title
            vendor
            productType
            tags
            images(first: 30) {
              edges {
                node {
                  id
                  url
                }
              }
            }
          }
        }
      }
    }
    """

    req = urllib.request.Request(
        f"https://{shop}/admin/api/2024-10/graphql.json",
        data=json.dumps({"query": gql, "variables": {"q": q_str}}).encode("utf-8"),
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json"
        }
    )

    try:
        res = urllib.request.urlopen(req)
        data = json.loads(res.read().decode("utf-8"))
        edges = data.get("data", {}).get("products", {}).get("edges", [])
        
        products = []
        for edge in edges:
            node = edge["node"]
            raw_p_gid = node.get("id", "")
            p_id = int(raw_p_gid.split("/")[-1]) if "/" in raw_p_gid else int(raw_p_gid) if raw_p_gid.isdigit() else raw_p_gid
            created_at = node.get("createdAt", "")
            status = node.get("status", "ACTIVE")
            tags = node.get("tags", [])
            
            img_edges = node.get("images", {}).get("edges", [])
            images = []
            for img_edge in img_edges:
                i_node = img_edge["node"]
                raw_i_gid = i_node.get("id", "")
                i_id = int(raw_i_gid.split("/")[-1]) if "/" in raw_i_gid else int(raw_i_gid) if raw_i_gid.isdigit() else raw_i_gid
                images.append({"id": i_id, "src": i_node.get("url", "")})
            
            # STRICT FILTER: ONLY PRODUCTS WITH 1 OR MORE IMAGES!
            if len(images) > 0:
                products.append({
                    "id": p_id,
                    "created_at": created_at,
                    "status": status,
                    "title": node.get("title", ""),
                    "vendor": node.get("vendor", "Past Studies"),
                    "product_type": node.get("productType", ""),
                    "tags": tags,
                    "images": images,
                })

        # Guarantee newest first sorting
        products.sort(key=lambda p: (p.get("created_at", ""), p.get("id", 0)), reverse=True)
        return products
    except Exception as e:
        st.error(f"Error fetching Shopify products: {e}")
        return []

def fetch_active_shopify_products():
    return fetch_active_shopify_products_search("")

def update_shopify_product_image(product_id: int, image_id: int, img_pil: Image.Image) -> bool:
    """Update an active image on Shopify with the background-equalized image."""
    shop, token = get_shopify_credentials()
    if not token:
        st.error("No SHOPIFY_ADMIN_TOKEN found in .env")
        return False

    buf = io.BytesIO()
    img_pil.save(buf, format="JPEG", quality=98, subsampling=0)
    b64_data = base64.b64encode(buf.getvalue()).decode("utf-8")

    url = f"https://{shop}/admin/api/2024-10/products/{product_id}/images/{image_id}.json"
    payload = json.dumps({"image": {"id": image_id, "attachment": b64_data}}).encode("utf-8")
    
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json"
        },
        method="PUT"
    )
    try:
        urllib.request.urlopen(req)
        return True
    except Exception as e:
        st.error(f"Shopify Image Update Failed: {e}")
        return False

_REMBG_LOCK = threading.Lock()

def get_alpha_mask_safe(img: Image.Image, model_name: str = "isnet-general-use") -> np.ndarray:
    w, h = img.size
    max_dim = 1200
    if max(w, h) > max_dim:
        scale = max_dim / float(max(w, h))
        small_w, small_h = max(1, int(w * scale)), max(1, int(h * scale))
        small_img = img.resize((small_w, small_h), Image.Resampling.BILINEAR)
    else:
        small_img = img

    from crop_pipeline.subject import extract_alpha
    with _REMBG_LOCK:
        rgba_small = extract_alpha(small_img, model_name)
    alpha_small = rgba_small.split()[3]

    if (small_img.width, small_img.height) != (w, h):
        alpha_full = alpha_small.resize((w, h), Image.Resampling.LANCZOS)
    else:
        alpha_full = alpha_small

    return np.array(alpha_full)


def detect_subject_safe(img: Image.Image, model_name: str = "isnet-general-use"):
    from crop_pipeline.subject import mask_to_box
    alpha_arr = get_alpha_mask_safe(img, model_name)
    alpha_pil = Image.fromarray(alpha_arr)
    return mask_to_box(alpha_pil)


def apply_photo_skills(
    img_bytes: bytes,
    bg_mode: str = "pure_white",
    target_bg_color: tuple[int, int, int] = (255, 255, 255),
    do_edge_extension: bool = False,
    do_autocrop: bool = False,
    category: str = "Tops",
    do_detail_crop: bool = False,
    edge_padding: int = 4,
    canvas_ratio: str = "3:4 (Shopify Default)",
    framing_preset: str = "Auto (Category Default)",
    img_position: int = 1,
) -> Image.Image:
    """Modular pipeline applying selected photo processing skills to an image while preserving 100% full original resolution."""
    orig_img_pil = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    current_img = orig_img_pil

    # Resolve Canvas Dimensions
    if "Square" in canvas_ratio or "1:1" in canvas_ratio:
        target_w, target_h = 2048, 2048
    elif "Portrait" in canvas_ratio or "4:5" in canvas_ratio:
        target_w, target_h = 1638, 2048
    else:
        target_w, target_h = TARGET_W, TARGET_H

    # 1. Background Equalization Skill
    if bg_mode in ("pure_white", "soft_20"):
        try:
            alpha_mask = get_alpha_mask_safe(current_img, "isnet-general-use")
        except Exception:
            img_np = np.array(current_img)
            gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
            bg_val = np.median(gray[0:30, 0:30])
            diff = cv2.absdiff(gray, int(bg_val))
            alpha_mask = (diff > 15).astype(np.uint8) * 255

        if bg_mode == "soft_20":
            current_img = process_tabletop_exposure(current_img, alpha_mask, curve_gamma=0.78, highlight_lift=1.06, edge_padding=edge_padding)
        else:
            current_img = process_pure_white_bg_equalization(current_img, alpha_mask, target_color=target_bg_color, edge_padding=edge_padding)

    # 2. Outer Edge Extension Skill (Zero Model Cutout)
    if do_edge_extension:
        current_img = extend_photo_edges_seamless(current_img, target_w=target_w, target_h=target_h)

    # 3. Auto-Crop & Framing Centering Skill
    if do_autocrop:
        try:
            from crop_pipeline.crop import compute_crop_box, compute_crop_box_bottom_anchored, compute_region_crop_box

            subj = detect_subject_safe(current_img, "isnet-general-use")
            src_w, src_h = current_img.size

            # Smart framing resolution
            is_detail_shot = (do_detail_crop or ("Macro" in framing_preset)) and ("dress" not in category.lower())

            if is_detail_shot:
                if any(b_kw in category.lower() for b_kw in ("bottom", "skirt", "pant", "jean", "short", "trouser")):
                    # Extra Tighter Zoomed Bottoms from upper hip/waistband down through feet (100% feet & hem intact, 0% cutoff)
                    top_y = subj.top + 0.38 * subj.height
                    bot_y = subj.bottom + 0.04 * subj.height
                    body_h = bot_y - top_y
                    crop_h = body_h / 0.93
                    crop_w = crop_h * (target_w / target_h)
                    bottom = subj.bottom + 0.04 * crop_h
                    top = bottom - crop_h
                    cx = (subj.left + subj.right) / 2.0
                    left = cx - crop_w / 2.0
                    right = left + crop_w
                    if left < 0: right -= left; left = 0
                    if top < 0: bottom -= top; top = 0
                    if right > src_w: diff = right - src_w; left -= diff; right = src_w
                    if bottom > src_h: diff = bottom - src_h; top -= diff; bottom = src_h
                    crop_box = (int(round(left)), int(round(top)), int(round(right)), int(round(bottom)))
                elif "handbag" in category.lower() or "bag" in category.lower() or "shoe" in category.lower() or "accessory" in category.lower():
                    crop_box = compute_region_crop_box((src_w, src_h), subj, (target_w, target_h), region_of_subject=(0.0, 1.0), region_fill=0.88)
                else:
                    # Moderate Zoom Half-Body Framing with 20% Top White Space (Tops / Sets top piece)
                    top_margin_frac = 0.20
                    body_frac = 0.48
                    fill_frac = 0.78
                    body_top = subj.top
                    body_bot = subj.top + body_frac * subj.height
                    half_body_h = body_bot - body_top
                    crop_h = half_body_h / fill_frac
                    crop_w = crop_h * (target_w / target_h)
                    top = body_top - top_margin_frac * crop_h
                    bottom = top + crop_h
                    cx = (subj.left + subj.right) / 2.0
                    left = cx - crop_w / 2.0
                    right = left + crop_w
                    if left < 0:
                        right -= left; left = 0
                    if top < 0:
                        bottom -= top; top = 0
                    if right > src_w:
                        diff = right - src_w; left -= diff; right = src_w
                    if bottom > src_h:
                        diff = bottom - src_h; top -= diff; bottom = src_h
                    crop_box = (int(round(left)), int(round(top)), int(round(right)), int(round(bottom)))
            elif "Handbag" in framing_preset or ("Auto" in framing_preset and ("bag" in category.lower() or "accessory" in category.lower())):
                # Fixed Retail Display Shelf Baseline: 15% bottom padding (Y=85% pixel line)
                crop_box = compute_crop_box_bottom_anchored((src_w, src_h), subj, (target_w, target_h), subject_height_fraction=0.68, bottom_margin_fraction=0.15)
            elif "Footwear" in framing_preset or ("Auto" in framing_preset and "shoe" in category.lower()):
                # Fixed Floor Baseline for Shoes: 12% bottom padding (Y=88% pixel line)
                crop_box = compute_crop_box_bottom_anchored((src_w, src_h), subj, (target_w, target_h), subject_height_fraction=0.75, bottom_margin_fraction=0.12)
            elif "Tops" in framing_preset:
                # Tops / Upper Body Framing: Upper torso (head/neck down to hips = 0.0 to 0.62 of subject)
                crop_box = compute_region_crop_box((src_w, src_h), subj, (target_w, target_h), region_of_subject=(0.0, 0.62), region_fill=0.82)
            else:
                if "Full-Body" in framing_preset:
                    subj_height_frac = 0.84
                    v_bias = -0.02
                elif "bottom" in category.lower():
                    subj_height_frac = 0.78
                    v_bias = 0.0
                else:
                    # Default category framing (e.g. Tops)
                    if any(top_word in category.lower() for top_word in ("top", "shirt", "tank", "tee", "blouse", "sweater", "jacket")):
                        crop_box = compute_region_crop_box((src_w, src_h), subj, (target_w, target_h), region_of_subject=(0.0, 0.65), region_fill=0.82)
                    else:
                        subj_height_frac = 0.84
                        v_bias = -0.02
                        crop_box = compute_crop_box((src_w, src_h), subj, (target_w, target_h), subj_height_frac, vertical_bias=v_bias)

                if 'crop_box' not in locals():
                    crop_box = compute_crop_box((src_w, src_h), subj, (target_w, target_h), subj_height_frac, vertical_bias=v_bias)

            cropped = current_img.crop(crop_box)
            current_img = cropped.resize((target_w, target_h), Image.Resampling.LANCZOS)
        except Exception as e:
            st.warning(f"Auto-crop fallback due to subject detection: {e}")

    return current_img

def download_image_with_retry(img_src: str, img_id: int | None = None, max_retries: int = 3) -> bytes:
    """Option 1 (Persistent Keep-Alive Connection Pool) + Option 3 (Source Disk Cache)."""
    cache_key = f"raw_{img_id}.jpg" if img_id else f"raw_{hashlib.md5(img_src.encode()).hexdigest()}.jpg"
    cache_path = RAW_CACHE_DIR / cache_key
    if cache_path.exists() and cache_path.stat().st_size > 5000:
        try:
            return cache_path.read_bytes()
        except Exception:
            pass

    session = get_http_session()
    last_err = None
    for attempt in range(max_retries):
        try:
            res = session.get(img_src, timeout=20)
            res.raise_for_status()
            img_bytes = res.content
            if len(img_bytes) > 5000:
                try:
                    cache_path.write_bytes(img_bytes)
                except Exception:
                    pass
                return img_bytes
        except Exception as e:
            last_err = e
            time.sleep(1)

    if last_err:
        raise last_err
    raise RuntimeError("Failed to download image after retries")


def process_single_image_worker(
    img_info: dict,
    prod_id: int,
    bg_mode: str,
    target_bg_color: tuple[int, int, int],
    do_edge_ext: bool,
    do_autocrop: bool,
    category_name: str,
    do_detail_crop: bool,
    edge_padding: int,
    canvas_ratio: str = "3:4 (Shopify Default)",
    framing_preset: str = "Auto (Category Default)",
    img_position: int = 1,
) -> tuple[int, bytes]:
    img_id = img_info["id"]
    img_src = img_info["src"]
    raw_bytes = download_image_with_retry(img_src, img_id=img_id)

    fixed_pil = apply_photo_skills(
        raw_bytes,
        bg_mode=bg_mode,
        target_bg_color=target_bg_color,
        do_edge_extension=do_edge_ext,
        do_autocrop=do_autocrop,
        category=category_name,
        do_detail_crop=do_detail_crop,
        edge_padding=edge_padding,
        canvas_ratio=canvas_ratio,
        framing_preset=framing_preset,
        img_position=img_position,
    )
    buf = io.BytesIO()
    fixed_pil.save(buf, format="JPEG", quality=98, subsampling=0)
    out_bytes = buf.getvalue()
    save_cached_preview(prod_id, img_id, out_bytes)
    del fixed_pil, buf, raw_bytes
    import gc
    gc.collect()
    return img_id, out_bytes

def render_studio_crop_tab():
    st.session_state["studio_crop_version"] = "2.2"

    if "saved_custom_colors" not in st.session_state:
        st.session_state["saved_custom_colors"] = ["#F8F2F2", "#E5E5E5"]

    st.markdown("### Studio Photo Skills & Processing Pipeline")

    # Condensed 2-Column Controls Strip
    with st.expander("Configure Applied Skills & Color Presets", expanded=True):
        col_bg, col_crop = st.columns([1.2, 1.0])

        with col_bg:
            st.markdown("**1. Background Equalization Skill**")
            bg_option = st.radio(
                "Background Processing Mode",
                ["Equalize Background Color", "Soft Tabletop Contact Shadow (Bags)", "Off"],
                index=0,
                horizontal=True,
                key="bg_option_radio_v4"
            )
            
            target_bg_color = (255, 255, 255)
            if bg_option.startswith("Equalize"):
                bg_mode = "pure_white"
                
                preset_options = [
                    "Pure White (#FFFFFF)",
                    "Warm Cyc (#F8F2F2)",
                    "Studio Light Grey (#E5E5E5)",
                    "Dark Charcoal (#222222)",
                    "Pure Black (#000000)",
                ] + [f"Saved: {c}" for c in st.session_state["saved_custom_colors"]] + ["Custom Color Picker"]

                col_preset, col_picker, col_btn = st.columns([1.2, 1.0, 0.9])
                with col_preset:
                    preset_choice = st.selectbox("Presets", preset_options, index=0, key="bg_preset_select_v4")

                with col_picker:
                    default_hex = "#FFFFFF"
                    if "Warm" in preset_choice:
                        default_hex = "#F8F2F2"
                    elif "Light Grey" in preset_choice:
                        default_hex = "#E5E5E5"
                    elif "Charcoal" in preset_choice:
                        default_hex = "#222222"
                    elif "Pure Black" in preset_choice:
                        default_hex = "#000000"
                    elif preset_choice.startswith("Saved:"):
                        default_hex = preset_choice.replace("Saved: ", "").strip()

                    picked_hex = st.color_picker("Custom Color", value=default_hex, key="bg_color_picker_v4")
                    target_bg_color = hex_to_rgb(picked_hex)

                with col_btn:
                    st.write("")
                    if st.button("Save Color", key="btn_save_color_v4", use_container_width=True):
                        if picked_hex not in st.session_state["saved_custom_colors"]:
                            st.session_state["saved_custom_colors"].append(picked_hex)
                            st.success("Saved!")

            elif bg_option.startswith("Soft"):
                bg_mode = "soft_20"
            else:
                bg_mode = "none"

            edge_padding = st.number_input(
                "Piping & Edge Safety Margin (px)",
                min_value=0,
                max_value=20,
                value=4,
                step=1,
                key="studio_edge_padding_v2",
                help="Expands protected subject boundary outward (in pixels) to guarantee dark piping, leather seams, and bottom edges are 100% protected."
            )

        with col_crop:
            st.markdown("**2. Framing & Canvas Skills**")
            col_c1, col_c2 = st.columns([1.0, 1.0])
            with col_c1:
                canvas_ratio = st.selectbox(
                    "Canvas Ratio",
                    ["3:4 (Shopify Default)", "Square 1:1", "Portrait 4:5", "Native High-Res"],
                    index=0,
                    key="studio_canvas_ratio_v3",
                    help="Target output dimension and aspect ratio"
                )
            with col_c2:
                framing_preset = st.selectbox(
                    "Framing Rule",
                    ["Auto (Category Default)", "Full-Body / Model", "Tops & Dresses", "Handbag (Shelf Line)", "Footwear / Shoes", "Macro Detail"],
                    index=0,
                    key="studio_framing_preset_v3",
                    help="Headroom & subject centering calibration"
                )

            do_autocrop = st.checkbox("Apply Auto-Crop & Framing", value=False, key="studio_do_autocrop_v2", help="Re-frame photo using selected Canvas & Framing rules")
            do_edge_ext = st.checkbox("Zero-Cutout Outer Edge Extension", value=False, key="studio_do_edge_ext_v2", help="Replicate outer edges seamlessly to widen canvas without clipping model")
            do_detail_crop = st.checkbox("Garment Item-Focus Detail Crop", value=False, key="studio_do_detail_crop_v2", help="Focus crop directly on garment silhouette")

    st.markdown("---")
    target_source = st.radio("Select Target Source", ["Batch Queue Table (Active Shopify Products)", "New Raw Shoots (Upload Camera Exports)"], horizontal=True, key="studio_target_source_radio_v3")

    if target_source.startswith("Batch Queue"):
        col_s1, col_s2, col_s3 = st.columns([1.5, 1.0, 1.0])
        with col_s1:
            search_query = st.text_input("🔍 Search Products (Title, Brand, SKU, Tag)", value="", key="studio_batch_search_v2")
        with col_s2:
            status_filter = st.selectbox("Status Filter", ["All Statuses", "DRAFT Only", "ACTIVE Only"], index=0, key="studio_status_filter_v1")
        with col_s3:
            cat_filter = st.selectbox("Category Filter", ["All Categories", "Tops", "Bottoms", "Sets", "Dresses", "Handbags", "Shoes"], index=0, key="studio_cat_filter_v1")

        products = fetch_active_shopify_products_search(search_query)
        if not products:
            st.info(f"No Shopify products matching '{search_query}' found.")
            return

        # Apply filters
        filtered_prods = []
        for p in products:
            st_val = p.get("status", "ACTIVE").upper()
            if status_filter == "DRAFT Only" and st_val != "DRAFT":
                continue
            if status_filter == "ACTIVE Only" and st_val != "ACTIVE":
                continue
            
            p_type = (p.get("product_type") or p.get("title") or "").lower()
            if cat_filter != "All Categories":
                c_target = cat_filter.lower()
                if c_target == "tops" and not any(k in p_type for k in ("top", "shirt", "tank", "tee", "blouse", "sweater", "jacket")):
                    continue
                if c_target == "bottoms" and not any(k in p_type for k in ("bottom", "skirt", "pant", "jean", "short", "trouser")):
                    continue
                if c_target == "sets" and not any(k in p_type for k in ("set", "suit", "two piece", "co-ord", "matching")):
                    continue
                if c_target == "dresses" and not any(k in p_type for k in ("dress", "gown")):
                    continue
                if c_target == "handbags" and not any(k in p_type for k in ("bag", "handbag", "clutch", "tote")):
                    continue
                if c_target == "shoes" and not any(k in p_type for k in ("shoe", "boot", "heel", "sandal")):
                    continue
            filtered_prods.append(p)

        if not filtered_prods:
            st.info("No products match the selected filters.")
            return

        st.markdown(f"### Batch Queue Table ({len(filtered_prods)} Matching Products)")

        # Master Checkbox & Toolbar
        col_m1, col_m2 = st.columns([1.0, 3.0])
        with col_m1:
            select_all = st.checkbox("Select All Matching", value=True, key="studio_select_all_cb_v1")

        selected_prod_ids = []
        for p in filtered_prods:
            p_id = p["id"]
            cb_k = f"select_prod_{p_id}"
            if select_all:
                st.session_state[cb_k] = True
            if st.session_state.get(cb_k, False):
                selected_prod_ids.append(p_id)

        st.caption(f"**{len(selected_prod_ids)} of {len(filtered_prods)} Products Selected** for Batch Action")

        # Global Action Bar
        col_act1, col_act2, col_act3 = st.columns([1.5, 1.5, 1.0])
        with col_act1:
            if st.button(f"⚡ Batch Process Selected ({len(selected_prod_ids)} Items)", type="primary", use_container_width=True, disabled=len(selected_prod_ids) == 0, key="btn_batch_process_queue_v1"):
                import time
                now_str = time.strftime("%I:%M:%S %p")
                with st.spinner(f"Batch processing {len(selected_prod_ids)} product(s) in parallel..."):
                    import gc
                    for p in filtered_prods:
                        p_id = p["id"]
                        if p_id in selected_prod_ids:
                            images = p.get("images", [])
                            cat_name = p.get("product_type") or p.get("title") or ""
                            with ThreadPoolExecutor(max_workers=min(len(images), 2)) as executor:
                                futures = [
                                    executor.submit(
                                        process_single_image_worker,
                                        img_info,
                                        p_id,
                                        bg_mode,
                                        target_bg_color,
                                        do_edge_ext,
                                        do_autocrop,
                                        cat_name,
                                        do_detail_crop,
                                        int(edge_padding),
                                        canvas_ratio,
                                        framing_preset,
                                        idx + 1,
                                    )
                                    for idx, img_info in enumerate(images)
                                ]
                                for future in futures:
                                    try:
                                        img_id, img_bytes = future.result()
                                        if img_bytes:
                                            st.session_state[f"edited_img_{p_id}_{img_id}"] = img_bytes
                                            st.session_state[f"processed_time_{p_id}_{img_id}"] = now_str
                                    except Exception as err:
                                        st.error(f"Error in image worker: {err}")
                            gc.collect()
                st.success(f"Successfully processed {len(selected_prod_ids)} products at {now_str}!")
                st.rerun()

        with col_act2:
            if st.button(f"🚀 Push {len(selected_prod_ids)} Approved Products to Shopify", type="primary", use_container_width=True, disabled=len(selected_prod_ids) == 0, key="btn_batch_push_queue_v1"):
                pushed_prods = 0
                with st.spinner(f"Pushing {len(selected_prod_ids)} products directly to Shopify..."):
                    for p in filtered_prods:
                        p_id = p["id"]
                        if p_id in selected_prod_ids:
                            images = p.get("images", [])
                            to_push = []
                            for img in images:
                                s_key = f"edited_img_{p_id}_{img['id']}"
                                inc_k = f"inc_img_{p_id}_{img['id']}"
                                if s_key in st.session_state and st.session_state.get(inc_k, True):
                                    to_push.append((img['id'], st.session_state[s_key]))

                            if to_push:
                                def push_worker_single(item):
                                    img_id, img_bytes = item
                                    try:
                                        fixed_img = Image.open(io.BytesIO(img_bytes))
                                        ok = update_shopify_product_image(p_id, img_id, fixed_img)
                                        return img_id, ok
                                    except Exception:
                                        return img_id, False

                                with ThreadPoolExecutor(max_workers=min(len(to_push), 4)) as executor:
                                    res_list = list(executor.map(push_worker_single, to_push))

                                ok_cnt = sum(1 for _, ok in res_list if ok)
                                if ok_cnt > 0:
                                    pushed_prods += 1
                                    for img_id, ok in res_list:
                                        if ok:
                                            st.session_state[f"pushed_ok_{p_id}_{img_id}"] = True

                try:
                    fetch_active_shopify_products.clear()
                except Exception:
                    pass

                if pushed_prods > 0:
                    st.success(f"Successfully updated {pushed_prods} product(s) on Shopify!")
                else:
                    st.warning("No processed photos pushed. Please run Batch Process first.")
                st.rerun()

        with col_act3:
            if st.button("Clear Selected Previews", use_container_width=True, disabled=len(selected_prod_ids) == 0, key="btn_clear_queue_v1"):
                for p in filtered_prods:
                    p_id = p["id"]
                    if p_id in selected_prod_ids:
                        images = p.get("images", [])
                        clear_cached_previews(p_id, images)
                        for img in images:
                            i_id = img["id"]
                            st.session_state.pop(f"edited_img_{p_id}_{i_id}", None)
                            st.session_state.pop(f"pushed_ok_{p_id}_{i_id}", None)
                            st.session_state.pop(f"processed_time_{p_id}_{i_id}", None)
                st.toast("Cleared previews for selected products!")
                st.rerun()

        st.markdown("---")

        # Table Rows & Expandable Pre-Push Review Drawers
        for idx, prod in enumerate(filtered_prods):
            prod_id = prod["id"]
            prod_title = prod["title"]
            images = prod.get("images", [])
            status_tag = prod.get("status", "ACTIVE")
            vendor = prod.get("vendor", "Past Studies")
            cat_tag = prod.get("product_type") or "Garment"

            # Check if processed
            processed_keys = [f"edited_img_{prod_id}_{img['id']}" for img in images if f"edited_img_{prod_id}_{img['id']}" in st.session_state]
            is_done = len(processed_keys) > 0
            pushed_ok_cnt = sum(1 for img in images if st.session_state.get(f"pushed_ok_{prod_id}_{img['id']}"))

            first_thumb = images[0]["src"] if images else ""

            # Row Table Strip
            with st.container(border=True):
                r_col1, r_col2, r_col3, r_col4, r_col5 = st.columns([0.4, 0.8, 3.5, 1.2, 1.5])
                with r_col1:
                    st.checkbox("", value=select_all, key=f"select_prod_{prod_id}")
                with r_col2:
                    if first_thumb:
                        st.markdown(f'<img src="{first_thumb}" style="width:50px; height:66px; object-fit:cover; border-radius:4px;">', unsafe_allow_html=True)
                with r_col3:
                    st.markdown(f"**{prod_title}**")
                    st.caption(f"ID: `{prod_id}` · Vendor: `{vendor}` · Category: `{cat_tag}`")
                with r_col4:
                    if pushed_ok_cnt > 0:
                        st.success(f"✓ Pushed ({pushed_ok_cnt})")
                    elif is_done:
                        st.info(f"✓ Processed ({len(processed_keys)}/{len(images)})")
                    else:
                        st.caption("⏳ Pending Process")
                with r_col5:
                    drawer_label = f"👁 Review ({len(images)} Photos)" if is_done else f"👁 Inspect ({len(images)} Photos)"

                # Expandable Review Drawer
                with st.expander(f"👁 Pre-Push Review Drawer — {prod_title}", expanded=False):
                    st.markdown(f"##### Pre-Push Review & Photo Approval ({len(images)} Photos)")

                    grid_cols = st.columns(min(len(images), 4))
                    for img_i, img_info in enumerate(images):
                        img_id = img_info["id"]
                        img_src = img_info["src"]
                        state_key = f"edited_img_{prod_id}_{img_id}"
                        time_key = f"processed_time_{prod_id}_{img_id}"
                        is_proc = state_key in st.session_state

                        with grid_cols[img_i % len(grid_cols)]:
                            st.markdown(f"**Photo {img_i+1}**")
                            inc_key = f"inc_img_{prod_id}_{img_id}"
                            st.checkbox("Include in Push", value=True, key=inc_key)

                            if is_proc:
                                try:
                                    b64_str = base64.b64encode(st.session_state[state_key]).decode("utf-8")
                                    st.markdown(f'<img src="data:image/jpeg;base64,{b64_str}" style="width:100%; border-radius:4px; display:block; margin-bottom:8px;">', unsafe_allow_html=True)
                                except Exception:
                                    st.image(img_src, use_container_width=True)
                            else:
                                st.image(img_src, caption="Original Active", use_container_width=True)

                            if st.button(f"↻ Process {img_i+1}", key=f"btn_reproc_{prod_id}_{img_id}", use_container_width=True):
                                import time
                                now_str = time.strftime("%I:%M:%S %p")
                                with st.spinner(f"Processing Photo {img_i+1}..."):
                                    raw_bytes = download_image_with_retry(img_src, img_id=img_id)
                                    fixed_pil = apply_photo_skills(
                                        raw_bytes,
                                        bg_mode=bg_mode,
                                        target_bg_color=target_bg_color,
                                        do_edge_extension=do_edge_ext,
                                        do_autocrop=do_autocrop,
                                        category=cat_tag,
                                        do_detail_crop=do_detail_crop,
                                        edge_padding=int(edge_padding),
                                        canvas_ratio=canvas_ratio,
                                        framing_preset=framing_preset,
                                        img_position=img_i + 1,
                                    )
                                    buf = io.BytesIO()
                                    fixed_pil.save(buf, format="JPEG", quality=98, subsampling=0)
                                    out_bytes = buf.getvalue()
                                    st.session_state[state_key] = out_bytes
                                    st.session_state[time_key] = now_str
                                    save_cached_preview(prod_id, img_id, out_bytes)
                                st.toast(f"Processed Photo {img_i+1} at {now_str}")
                                st.rerun()

    else:
        st.markdown("#### New Raw Shoots (Upload Camera Exports)")
        col_cat, col_sku = st.columns([1, 1])
        with col_cat:
            category_name = st.selectbox("Product Category", ["Tops (Shirts / Jackets)", "Bottoms (Pants / Skirts)", "Dresses & Jumpsuits", "Handbags & Accessories"])
        with col_sku:
            sku = st.text_input("SKU Code", value="SKU_STUDIO_001")

        uploaded_files = st.file_uploader("Drop raw uncropped camera photos", type=["jpg", "jpeg", "png"], accept_multiple_files=True)
        if uploaded_files:
            st.markdown(f"#### Processing {len(uploaded_files)} raw photos for SKU `{sku}`...")
            cols = st.columns(min(len(uploaded_files), 4))
            for idx, file in enumerate(uploaded_files):
                raw_bytes = file.read()
                transformed_pil = apply_photo_skills(
                    raw_bytes,
                    bg_mode=bg_mode,
                    target_bg_color=target_bg_color,
                    do_edge_extension=do_edge_ext,
                    do_autocrop=do_autocrop,
                    category=category_name,
                    do_detail_crop=do_detail_crop,
                    edge_padding=int(edge_padding),
                )
                with cols[idx % len(cols)]:
                    st.image(transformed_pil, caption=f"{file.name} (Transformed)", use_container_width=True)

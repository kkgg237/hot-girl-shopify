"""Studio Auto-Crop & Retroactive Background Fixer Tab for Past Studies.

Documented in RULES.md (2026-08-13).
Multi-Threaded Parallel Bulk Batch Processing & Condensed UI Grid.
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import urllib.request
import urllib.parse
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

def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """Convert hex string '#RRGGBB' to (R, G, B) tuple."""
    hex_str = hex_str.lstrip('#')
    return tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))

def get_shopify_credentials():
    from shopify_inventory import get_shop, get_token
    shop = get_shop() or os.environ.get("SHOPIFY_SHOP", "paststudies.myshopify.com")
    token = get_token()
    return shop, token

@st.cache_data(ttl=300)
def fetch_active_shopify_products():
    """Fetch active listings from Shopify Admin API. Strictly excludes products without images, sorted newest first."""
    shop, token = get_shopify_credentials()
    if not token:
        return []
    url = f"https://{shop}/admin/api/2024-10/products.json?status=active&limit=100"
    req = urllib.request.Request(url, headers={"X-Shopify-Access-Token": token})
    try:
        res = urllib.request.urlopen(req)
        data = json.loads(res.read().decode("utf-8"))
        products = data.get("products", [])
        
        # Strictly filter ONLY products that have 1 or more active images!
        products_with_images = [p for p in products if p.get("images") and len(p.get("images")) > 0]
        
        # Sort newest first (by id descending)
        products_with_images.sort(key=lambda p: p.get("id", 0), reverse=True)
        return products_with_images
    except Exception as e:
        st.error(f"Error fetching Shopify products: {e}")
        return []

def update_shopify_product_image(product_id: int, image_id: int, img_pil: Image.Image) -> bool:
    """Update an active image on Shopify with the background-equalized image."""
    shop, token = get_shopify_credentials()
    if not token:
        st.error("No SHOPIFY_ADMIN_TOKEN found in .env")
        return False

    buf = io.BytesIO()
    img_pil.save(buf, format="JPEG", quality=95)
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

def apply_photo_skills(
    img_bytes: bytes,
    bg_mode: str = "pure_white",
    target_bg_color: tuple[int, int, int] = (255, 255, 255),
    do_edge_extension: bool = False,
    do_autocrop: bool = False,
    category: str = "Tops",
    do_detail_crop: bool = False,
    edge_padding: int = 4,
) -> Image.Image:
    """Modular pipeline applying selected photo processing skills to an image."""
    orig_img_pil = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    current_img = orig_img_pil

    # 1. Background Equalization Skill
    if bg_mode in ("pure_white", "soft_20"):
        try:
            from crop_pipeline.subject import extract_alpha
            rgba = extract_alpha(current_img, "isnet-general-use")
            alpha_mask = np.array(rgba.split()[3])
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
        current_img = extend_photo_edges_seamless(current_img, target_w=TARGET_W, target_h=TARGET_H)

    # 3. Auto-Crop & Framing Centering Skill (3:4 Ratio)
    if do_autocrop:
        try:
            from crop_pipeline.subject import detect_subject
            from crop_pipeline.crop import compute_crop_box, compute_region_crop_box

            subj = detect_subject(current_img, "isnet-general-use")
            src_w, src_h = current_img.size

            if do_detail_crop:
                if "bottom" in category.lower():
                    region = (0.25, 1.0)
                else:
                    region = (0.08, 0.65)
                crop_box = compute_region_crop_box((src_w, src_h), subj, (TARGET_W, TARGET_H), region_of_subject=region)
            else:
                if "bag" in category.lower() or "accessory" in category.lower():
                    subj_height_frac = 0.68
                    v_bias = 0.05
                elif "bottom" in category.lower():
                    subj_height_frac = 0.78
                    v_bias = 0.0
                else:
                    subj_height_frac = 0.84
                    v_bias = -0.04

                crop_box = compute_crop_box((src_w, src_h), subj, (TARGET_W, TARGET_H), subj_height_frac, vertical_bias=v_bias)

            cropped = current_img.crop(crop_box)
            current_img = cropped.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
        except Exception as e:
            st.warning(f"Auto-crop fallback due to subject detection: {e}")

    return current_img

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
) -> tuple[int, bytes]:
    img_id = img_info["id"]
    img_src = img_info["src"]
    req = urllib.request.Request(img_src, headers={"User-Agent": "Mozilla/5.0"})
    raw_bytes = urllib.request.urlopen(req, timeout=12).read()

    fixed_pil = apply_photo_skills(
        raw_bytes,
        bg_mode=bg_mode,
        target_bg_color=target_bg_color,
        do_edge_extension=do_edge_ext,
        do_autocrop=do_autocrop,
        category=category_name,
        do_detail_crop=do_detail_crop,
        edge_padding=edge_padding,
    )
    buf = io.BytesIO()
    fixed_pil.save(buf, format="JPEG", quality=95)
    return img_id, buf.getvalue()

def render_studio_crop_tab():
    if st.session_state.get("studio_crop_version") != "2.2":
        st.session_state["studio_crop_version"] = "2.2"
        for k in list(st.session_state.keys()):
            if k != "studio_crop_version":
                del st.session_state[k]

    if "saved_custom_colors" not in st.session_state:
        st.session_state["saved_custom_colors"] = ["#F8F2F2", "#E5E5E5"]

    st.markdown("### Studio Photo Skills & Processing Pipeline")

    # Condensed 2-Column Controls Strip
    with st.expander("⚙️ Configure Applied Skills & Color Presets", expanded=True):
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
                    if st.button("💾 Save Color", key="btn_save_color_v4", use_container_width=True):
                        if picked_hex not in st.session_state["saved_custom_colors"]:
                            st.session_state["saved_custom_colors"].append(picked_hex)
                            st.success(f"Saved!")

            elif bg_option.startswith("Soft"):
                bg_mode = "soft_20"
            else:
                bg_mode = "none"

            edge_padding = st.number_input(
                "🛡️ Piping & Edge Safety Margin (px)",
                min_value=0,
                max_value=20,
                value=4,
                step=1,
                help="Expands protected subject boundary outward (in pixels) to guarantee dark piping, leather seams, and bottom edges are 100% protected."
            )

        with col_crop:
            st.markdown("**2. Framing & Canvas Skills**")
            do_autocrop = st.checkbox("3:4 Auto-Crop & Framing Centering", value=False, help="Re-frame photo to 1536x2048 canvas using category headroom rules")
            do_edge_ext = st.checkbox("Zero-Cutout Outer Edge Extension", value=False, help="Replicate outer edges seamlessly to widen canvas without clipping model")
            do_detail_crop = st.checkbox("Garment Item-Focus Detail Crop", value=False, help="Focus crop directly on garment silhouette")

    st.markdown("---")
    target_source = st.radio("Select Target Source", ["Retroactive Audit (Active Shopify Products)", "New Raw Shoots (Upload Camera Exports)"], horizontal=True)

    if target_source.startswith("Retroactive"):
        products = fetch_active_shopify_products()
        if not products:
            st.info("No active Shopify products with images found.")
            return

        prod_titles = [f"{p['title']} ({len(p.get('images', []))} photos) — {p.get('vendor', 'Past Studies')}" for p in products]
        selected_idx = st.selectbox("Select Active Product to Audit & Fix (Newest First)", range(len(prod_titles)), format_func=lambda i: prod_titles[i])
        
        prod = products[selected_idx]
        prod_id = prod["id"]
        prod_title = prod["title"]
        images = prod.get("images", [])
        category_name = prod.get("product_type") or prod_title

        st.markdown(f"#### Product: **{prod_title}** ({len(images)} photos) — ID: `{prod_id}`")

        # Global Bulk Batch Action Toolbar
        col_b1, col_b2 = st.columns([1, 1])
        with col_b1:
            if st.button("⚡ Batch Process ALL Photos in Listing", type="primary", use_container_width=True, key=f"btn_batch_process_{prod_id}"):
                with st.spinner(f"⚡ Processing {len(images)} photos in parallel via multi-threading..."):
                    with ThreadPoolExecutor(max_workers=min(len(images), 6)) as executor:
                        futures = [
                            executor.submit(
                                process_single_image_worker,
                                img_info,
                                prod_id,
                                bg_mode,
                                target_bg_color,
                                do_edge_ext,
                                do_autocrop,
                                category_name,
                                do_detail_crop,
                                int(edge_padding),
                            )
                            for img_info in images
                        ]
                        for future in futures:
                            img_id, img_bytes = future.result()
                            st.session_state[f"edited_img_{prod_id}_{img_id}"] = img_bytes
                st.success(f"✓ Parallel batch processing complete for {len(images)} photos!")
                st.rerun()

        with col_b2:
            all_edited = all(f"edited_img_{prod_id}_{img['id']}" in st.session_state for img in images)
            if st.button("✓ Push ALL Processed Photos to Shopify", type="primary", use_container_width=True, disabled=not all_edited, key=f"btn_batch_push_{prod_id}"):
                with st.spinner(f"Pushing {len(images)} updated photos to Shopify..."):
                    def push_worker(img_info):
                        img_id = img_info["id"]
                        state_key = f"edited_img_{prod_id}_{img_id}"
                        if state_key in st.session_state:
                            fixed_img = Image.open(io.BytesIO(st.session_state[state_key]))
                            ok = update_shopify_product_image(prod_id, img_id, fixed_img)
                            if ok:
                                st.session_state[f"pushed_ok_{prod_id}_{img_id}"] = True
                                return True
                        return False

                    with ThreadPoolExecutor(max_workers=min(len(images), 4)) as executor:
                        list(executor.map(push_worker, images))
                st.success(f"✓ Successfully pushed all {len(images)} photos to Shopify!")
                st.rerun()

        st.markdown("---")

        # Compact Condensed Photo Grid (2 photo cards per row)
        grid_cols = st.columns(2)
        for idx, img_info in enumerate(images):
            img_id = img_info["id"]
            img_src = img_info["src"]
            state_key = f"edited_img_{prod_id}_{img_id}"

            with grid_cols[idx % 2]:
                with st.container(border=True):
                    st.markdown(f"**Photo {idx+1} of {len(images)}** (ID: `{img_id}`)")
                    
                    # Row of side-by-side images directly on the exact same Y baseline
                    img_col1, img_col2 = st.columns(2)
                    with img_col1:
                        st.caption("1. Original Active Photo")
                        st.image(img_src, use_container_width=True)

                    with img_col2:
                        st.caption("2. Transformed Preview")
                        if state_key in st.session_state:
                            st.image(st.session_state[state_key], use_container_width=True)
                        else:
                            st.info("Click '⚡ Process Photo' below to generate preview.")

                    # Individual Action Bar below images
                    btn_col1, btn_col2 = st.columns(2)
                    with btn_col1:
                        if st.button(f"⚡ Process Photo {idx+1}", key=f"btn_edit_{img_id}", use_container_width=True):
                            req = urllib.request.Request(img_src, headers={"User-Agent": "Mozilla/5.0"})
                            raw_bytes = urllib.request.urlopen(req, timeout=12).read()
                            
                            fixed_pil = apply_photo_skills(
                                raw_bytes,
                                bg_mode=bg_mode,
                                target_bg_color=target_bg_color,
                                do_edge_extension=do_edge_ext,
                                do_autocrop=do_autocrop,
                                category=category_name,
                                do_detail_crop=do_detail_crop,
                                edge_padding=int(edge_padding),
                            )
                            buf = io.BytesIO()
                            fixed_pil.save(buf, format="JPEG", quality=95)
                            st.session_state[state_key] = buf.getvalue()
                            st.rerun()

                    with btn_col2:
                        if state_key in st.session_state:
                            if st.button(f"✓ Push Photo {idx+1}", key=f"btn_push_{img_id}", type="primary", use_container_width=True):
                                fixed_img = Image.open(io.BytesIO(st.session_state[state_key]))
                                success = update_shopify_product_image(prod_id, img_id, fixed_img)
                                if success:
                                    st.session_state[f"pushed_ok_{prod_id}_{img_id}"] = True

                            if st.session_state.get(f"pushed_ok_{prod_id}_{img_id}"):
                                st.success("✓ Updated on Shopify!")

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

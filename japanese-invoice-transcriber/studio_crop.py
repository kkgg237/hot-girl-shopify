"""Studio Auto-Crop & Retroactive Background Fixer Tab for Past Studies.

Documented in RULES.md (2026-08-13).
Integrates:
  1. Retroactive Background Fixer for Active Shopify Listings (ZERO re-cropping, full human oversight)
  2. Auto-Crop 3:4 Listing Generator (for new raw camera exports)
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
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
)

TARGET_W = 1536
TARGET_H = 2048

def get_shopify_credentials():
    from shopify_inventory import get_shop, get_token
    shop = get_shop() or os.environ.get("SHOPIFY_SHOP", "paststudies.myshopify.com")
    token = get_token()
    return shop, token

@st.cache_data(ttl=300)
def fetch_active_shopify_products():
    """Fetch active listings from Shopify Admin API."""
    shop, token = get_shopify_credentials()
    if not token:
        return []
    url = f"https://{shop}/admin/api/2024-10/products.json?status=active&limit=100"
    req = urllib.request.Request(url, headers={"X-Shopify-Access-Token": token})
    try:
        res = urllib.request.urlopen(req)
        data = json.loads(res.read().decode("utf-8"))
        return data.get("products", [])
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

def fix_active_photo_background_only(img_bytes: bytes, is_bag: bool = False) -> Image.Image:
    """Fix background lighting ONLY. Zero re-cropping or framing changes."""
    orig_img_pil = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    img_np = np.array(orig_img_pil)
    h, w, _ = img_np.shape

    gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
    bg_val = np.median(gray[0:30, 0:30])
    diff = cv2.absdiff(gray, int(bg_val))
    alpha_mask = (diff > 15).astype(np.uint8) * 255

    if is_bag:
        # Soft 20 Contact Shadow Retention for Handbags
        return process_tabletop_exposure(orig_img_pil, alpha_mask, curve_gamma=0.78, highlight_lift=1.06)
    else:
        # Pure White Equalization (#FFFFFF) for Clothing
        return process_pure_white_bg_equalization(orig_img_pil, alpha_mask)

def render_studio_crop_tab():
    st.markdown("### Studio Auto-Crop & Retroactive Background Fixer")
    
    mode = st.radio("Select Workflow Mode", ["1 · Retroactive Fixer (Active Shopify Listings)", "2 · Auto-Crop 3:4 Generator (New Raw Shoots)"], horizontal=True)

    if mode.startswith("1"):
        st.markdown("#### Retroactive Background Fixer (Active Shopify Store)")
        st.caption("Browse live active listings → Edit background (NO re-cropping) → Preview Before/After → Human Approval.")

        products = fetch_active_shopify_products()
        if not products:
            st.info("No active Shopify products found or SHOPIFY_ADMIN_TOKEN required in .env")
            return

        prod_titles = [f"{p['title']} ({p['vendor']}) — ID: {p['id']}" for p in products]
        selected_idx = st.selectbox("Select Active Product to Audit & Fix", range(len(prod_titles)), format_func=lambda i: prod_titles[i])
        
        prod = products[selected_idx]
        prod_id = prod["id"]
        prod_title = prod["title"]
        images = prod.get("images", [])

        st.markdown(f"##### Product: **{prod_title}** ({len(images)} active photos)")
        is_bag = any(k in (prod.get("product_type") or "").lower() or k in prod_title.lower() for k in ["bag", "tote", "clutch", "handbag", "purse"])
        
        if is_bag:
            st.info("🏷️ Detected Bag/Accessory category: Applies **Soft 20 Background Equalization + Tabletop Contact Shadow Retention**.")
        else:
            st.info("🏷️ Detected Clothing/Model category: Applies **Pure White Equalization (#FFFFFF) + Zero Model Cutout**.")

        for idx, img_info in enumerate(images):
            img_id = img_info["id"]
            img_src = img_info["src"]

            st.markdown("---")
            st.markdown(f"**Photo {idx+1} of {len(images)}** (ID: `{img_id}`)")
            col1, col2, col3 = st.columns([1, 1, 1])

            with col1:
                st.markdown("**Current Active Shopify Photo (Before)**")
                st.image(img_src, use_container_width=True)

            state_key = f"edited_img_{prod_id}_{img_id}"

            with col2:
                if st.button(f"Edit Background (Photo {idx+1})", key=f"btn_edit_{img_id}"):
                    req = urllib.request.Request(img_src, headers={"User-Agent": "Mozilla/5.0"})
                    raw_bytes = urllib.request.urlopen(req).read()
                    
                    fixed_pil = fix_active_photo_background_only(raw_bytes, is_bag=is_bag)
                    buf = io.BytesIO()
                    fixed_pil.save(buf, format="JPEG", quality=95)
                    st.session_state[state_key] = buf.getvalue()

                if state_key in st.session_state:
                    st.markdown("**Background Fixed Preview (After)**")
                    st.image(st.session_state[state_key], use_container_width=True)

            with col3:
                if state_key in st.session_state:
                    st.markdown("**Human Approval & Shopify Push**")
                    if st.button(f"✓ Approve & Update Shopify (Photo {idx+1})", key=f"btn_push_{img_id}", type="primary"):
                        fixed_img = Image.open(io.BytesIO(st.session_state[state_key]))
                        success = update_shopify_product_image(prod_id, img_id, fixed_img)
                        if success:
                            st.success(f"✓ Successfully updated Photo {idx+1} on Shopify!")

    else:
        st.markdown("#### Auto-Crop 3:4 Listing Generator (New Raw Shoots)")
        st.caption("Upload raw camera exports → Auto-crop 3:4 e-commerce listing set.")

        col_cat, col_sku = st.columns([1, 1])
        with col_cat:
            category = st.selectbox("Product Category", ["Tops (Shirts / Jackets)", "Bottoms (Pants / Skirts)", "Dresses & Jumpsuits", "Handbags & Accessories"])
        with col_sku:
            sku = st.text_input("SKU Code", value="SKU_STUDIO_001")

        uploaded_files = st.file_uploader("Drop raw uncropped camera photos", type=["jpg", "jpeg", "png"], accept_multiple_files=True)
        if uploaded_files:
            st.markdown(f"#### Processing {len(uploaded_files)} raw photos for SKU `{sku}`...")

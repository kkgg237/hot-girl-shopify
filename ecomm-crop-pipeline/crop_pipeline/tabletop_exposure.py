"""Photoshop-style background exposure lift with tabletop contact shadow retention & Retail Display Shelf Framing.

Documented in RULES.md (2026-08-13).
Optimized for high-speed, zero-RAM execution using OpenCV Look-Up Tables (cv2.LUT).
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

TARGET_BG_WHITE = np.array([255, 255, 255], dtype=np.uint8)

def _build_exposure_lut(curve_gamma: float = 0.78, highlight_lift: float = 1.06) -> np.ndarray:
    """Pre-compute 256-entry uint8 Look-Up Table for LAB L-channel exposure curve."""
    in_vals = np.arange(256, dtype=np.float32) / 255.0
    out_vals = np.power(in_vals, curve_gamma) * highlight_lift * 255.0
    return np.clip(out_vals, 0, 255).astype(np.uint8)

# Pre-computed LUT for Soft 20 (curve_gamma=0.78, highlight_lift=1.06)
_LUT_SOFT20 = _build_exposure_lut(0.78, 1.06)
# Pre-computed LUT for Soft 30 (curve_gamma=0.85, highlight_lift=0.98)
_LUT_SOFT30 = _build_exposure_lut(0.85, 0.98)

def process_tabletop_exposure(
    orig_img_pil: Image.Image,
    alpha_mask: np.ndarray,
    curve_gamma: float = 0.85,
    highlight_lift: float = 0.98,
) -> Image.Image:
    """Fast, low-memory Photoshop LAB L-channel exposure curve via cv2.LUT."""
    orig_img_pil = orig_img_pil.convert("RGB")
    img_np = np.array(orig_img_pil, dtype=np.uint8)
    
    # 1. Background weight mask
    bg_weight = (255 - alpha_mask).astype(np.float32) / 255.0
    bg_weight = cv2.GaussianBlur(bg_weight, (7, 7), 0)[:, :, np.newaxis]

    # 2. Convert to LAB and apply LUT to L channel
    lab = cv2.cvtColor(img_np, cv2.COLOR_RGB2LAB)
    L, A, B = cv2.split(lab)
    
    if abs(curve_gamma - 0.85) < 0.02 and abs(highlight_lift - 0.98) < 0.02:
        L_boosted = cv2.LUT(L, _LUT_SOFT30)
    elif abs(curve_gamma - 0.78) < 0.02 and abs(highlight_lift - 1.06) < 0.02:
        L_boosted = cv2.LUT(L, _LUT_SOFT20)
    else:
        lut = _build_exposure_lut(curve_gamma, highlight_lift)
        L_boosted = cv2.LUT(L, lut)

    lab_boosted = cv2.merge([L_boosted, A, B])
    bg_lifted_rgb = cv2.cvtColor(lab_boosted, cv2.COLOR_LAB2RGB).astype(np.float32)

    # 3. Blend background
    lum = (bg_lifted_rgb.mean(axis=2, keepdims=True) / 255.0) ** 3.0
    bg_final = bg_lifted_rgb * (1.0 - lum) + 255.0 * lum

    final_rgb = img_np.astype(np.float32) * (1.0 - bg_weight) + bg_final * bg_weight
    return Image.fromarray(np.clip(final_rgb, 0, 255).astype(np.uint8))


def extend_photo_edges_seamless(
    orig_img_pil: Image.Image | np.ndarray,
    target_w: int = 1536,
    target_h: int = 2048,
    blend_margin: int = 30,
) -> Image.Image:
    """Human Form / Model Rule (RULES.md 2026-08-13): Zero-Cutout Outer Edge Extension.
    Keeps model 100% crisp. Replicates outer background pixels and blends seams horizontally.
    """
    if isinstance(orig_img_pil, Image.Image):
        img_np = np.array(orig_img_pil, dtype=np.uint8)
    else:
        img_np = orig_img_pil.astype(np.uint8)

    h_orig, w_orig, c = img_np.shape

    scale = target_h / float(h_orig)
    new_w = int(w_orig * scale)
    new_h = target_h

    resized_photo = cv2.resize(img_np, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)

    if new_w < target_w:
        pad_total = target_w - new_w
        pad_left = pad_total // 2
        pad_right = pad_total - pad_left

        canvas_np = np.zeros((target_h, target_w, 3), dtype=np.uint8)
        canvas_np[:, pad_left:pad_left+new_w, :] = resized_photo

        left_column = resized_photo[:, 0:1, :]
        canvas_np[:, :pad_left, :] = np.tile(left_column, (1, pad_left, 1))

        right_column = resized_photo[:, -1:, :]
        canvas_np[:, pad_left+new_w:, :] = np.tile(right_column, (1, pad_right, 1))

        blend_w = min(blend_margin, pad_left, new_w // 4)
        if blend_w > 0:
            seam_left_x = pad_left
            left_zone = canvas_np[:, seam_left_x - blend_w : seam_left_x + blend_w, :]
            canvas_np[:, seam_left_x - blend_w : seam_left_x + blend_w, :] = cv2.GaussianBlur(left_zone, (blend_w*2+1, 1), 0)

            seam_right_x = pad_left + new_w
            right_zone = canvas_np[:, seam_right_x - blend_w : seam_right_x + blend_w, :]
            canvas_np[:, seam_right_x - blend_w : seam_right_x + blend_w, :] = cv2.GaussianBlur(right_zone, (blend_w*2+1, 1), 0)

        return Image.fromarray(canvas_np)
    else:
        crop_x = (new_w - target_w) // 2
        cropped = resized_photo[:, crop_x:crop_x+target_w, :]
        return Image.fromarray(cropped)


def process_pure_white_bg_equalization(
    orig_img_pil: Image.Image,
    alpha_mask: np.ndarray,
    target_color: tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    """Studio Background Equalization with customizable target background color/tint.
    Lifts studio background to target_color (default #FFFFFF pure white)
    while keeping model, skin, face, hair, and clothing 100% UNTOUCHED.
    """
    orig_img_pil = orig_img_pil.convert("RGB")
    img_np = np.array(orig_img_pil, dtype=np.float32)
    alpha_float = alpha_mask.astype(np.float32) / 255.0
    alpha_feathered = cv2.GaussianBlur(alpha_float, (7, 7), 0)
    bg_weight = np.expand_dims(1.0 - alpha_feathered, axis=2)
    
    img_bgr = cv2.cvtColor(np.array(orig_img_pil), cv2.COLOR_RGB2BGR)
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    L, A, B = cv2.split(lab)
    
    L_norm = L / 255.0
    L_boosted = np.power(L_norm, 0.50) * 1.30 * 255.0
    L_boosted = np.clip(L_boosted, 0, 255.0)
    
    lab_boosted = cv2.merge([L_boosted, A, B]).astype(np.uint8)
    bg_lifted_rgb = cv2.cvtColor(cv2.cvtColor(lab_boosted, cv2.COLOR_LAB2BGR), cv2.COLOR_BGR2RGB).astype(np.float32)
    
    lum = (bg_lifted_rgb.mean(axis=2, keepdims=True) / 255.0) ** 2.0
    target_rgb_np = np.array(target_color, dtype=np.float32)
    bg_final = bg_lifted_rgb * (1.0 - lum) + target_rgb_np * lum
    
    final_rgb = img_np * (1.0 - bg_weight) + bg_final * bg_weight
    return Image.fromarray(np.clip(final_rgb, 0, 255).astype(np.uint8))


def frame_garment_item_focus(
    orig_img_pil: Image.Image,
    alpha_mask: np.ndarray,
    is_bottoms: bool = False,
    target_w: int = 1536,
    target_h: int = 2048,
) -> Image.Image:
    """Garment Item-Focus Crop (RULES.md 2026-08-13):
      - Bottoms: Waistband to shoes crop with 5% top margin above waistband (matches Image #1).
      - Tops: Shoulders to hemline crop with 5% margin above collar.
      - Pure White Studio Background Equalization (#FFFFFF).
    """
    orig_w, orig_h = orig_img_pil.size
    y_indices, x_indices = np.where(alpha_mask > 40)
    m_top = y_indices.min()
    m_bottom = y_indices.max()
    m_left = x_indices.min()
    m_right = x_indices.max()
    m_h = m_bottom - m_top
    center_x = (m_left + m_right) // 2

    if is_bottoms:
        waist_y = m_top + int(m_h * 0.35)
        garment_h = m_bottom - waist_y
        crop_top = max(0, waist_y - int(garment_h * 0.06))
        crop_bottom = min(orig_h, m_bottom + int(garment_h * 0.04))
    else:
        shoulders_y = m_top + int(m_h * 0.12)
        hemline_y = m_top + int(m_h * 0.65)
        garment_h = hemline_y - shoulders_y
        crop_top = max(0, shoulders_y - int(garment_h * 0.08))
        crop_bottom = min(orig_h, hemline_y + int(garment_h * 0.12))

    crop_h = crop_bottom - crop_top
    crop_w = int(crop_h * (target_w / float(target_h)))
    center_y = (crop_top + crop_bottom) // 2

    y1 = max(0, center_y - crop_h // 2)
    y2 = min(orig_h, y1 + crop_h)
    x1 = max(0, center_x - crop_w // 2)
    x2 = min(orig_w, x1 + crop_w)

    raw_crop_pil = orig_img_pil.crop((x1, y1, x2, y2))
    raw_crop_alpha = alpha_mask[y1:y2, x1:x2]

    equalized_pil = process_pure_white_bg_equalization(raw_crop_pil, raw_crop_alpha)
    return equalized_pil.resize((target_w, target_h), Image.Resampling.LANCZOS)

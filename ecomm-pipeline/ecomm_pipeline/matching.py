"""Crop-SKU → Shopify-Variant-SKU resolution — the one place the two namespaces meet.

CONFIRMED (kat, 2026-06): the SKU embedded in the Capture One filename IS the
Shopify Variant SKU. Exports are named ``BRU_2605_001_1.jpg``, ``…_2.jpg`` … for
SKU ``BRU_2605_001``. ``ecomm_pipeline.crop_runner`` normalizes that underscore
form to the crop engine's dash form before cropping, and the crop output
``BRU_2605_001_01_hero.jpg`` strips back (via sku.py) to ``BRU_2605_001``, which
matches the variant SKU verbatim. So ``normalize_sku`` is the identity function.

It still exists as the single seam so a future divergence is a one-function
change. If a mapping (not a formula) is ever needed, drop a ``sku_map.json`` in
``ecomm-pipeline/state/`` (``{"<crop_sku>": "<shopify_sku>"}``) and it takes
precedence below.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Union

from ecomm_pipeline.config import PIPELINE_DIR
from ecomm_pipeline.models import Collision, ProductMatch
from ecomm_pipeline.shopify import products as products_api
from ecomm_pipeline.shopify.client import ShopifyClient

_SKU_MAP_PATH = PIPELINE_DIR / "state" / "sku_map.json"


@lru_cache(maxsize=1)
def _sku_map() -> dict[str, str]:
    if not _SKU_MAP_PATH.exists():
        return {}
    try:
        data = json.loads(_SKU_MAP_PATH.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()}
    except Exception:
        return {}


def normalize_sku(crop_sku: str) -> str:
    """Map a crop/filename SKU to the matching Shopify Variant SKU.

    Order of precedence:
      1. An explicit entry in ``state/sku_map.json`` (escape hatch / overrides).
      2. The deterministic transform below.

    Confirmed identity for this store — the crop SKU equals the Shopify variant
    SKU verbatim. Encode a real transform here only if that ever changes.
    """
    override = _sku_map().get(crop_sku)
    if override:
        return override

    # --- deterministic transform goes here -------------------------------
    shopify_sku = crop_sku
    # ---------------------------------------------------------------------
    return shopify_sku


def resolve_sku(
    client: ShopifyClient, crop_sku: str
) -> Union[ProductMatch, Collision, None]:
    """Normalize a crop SKU then resolve it to an existing Shopify product."""
    return products_api.find_product_by_sku(client, normalize_sku(crop_sku))

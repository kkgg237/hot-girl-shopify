"""The push orchestration loop — Phase 1 builds the read-only plan.

``plan()`` runs the crops, groups them by SKU, resolves each SKU to an existing
draft, and buckets it. The Phase-2 write path (staged upload → attach → reorder →
tag → ledger) hangs off the ``ready`` bucket and is not built yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

from ecomm_pipeline.config import Config
from ecomm_pipeline.crop_runner import run_crops
from ecomm_pipeline.matching import normalize_sku, resolve_sku
from ecomm_pipeline.models import Collision, ProductMatch
from ecomm_pipeline.shopify.client import ShopifyClient, ShopifyError
from ecomm_pipeline.sku import group_crops_by_sku

# Per-SKU outcome buckets.
READY = "ready"                      # draft found, untagged → would attach
ALREADY_COMPLETE = "already-complete"  # draft already carries the completion tag
NO_DRAFT = "no-draft"                # no product with this exact variant SKU
COLLISION = "collision"             # >1 product carries this SKU → never guess
ERROR = "error"                      # Shopify lookup blew up for this SKU


@dataclass
class SkuPlan:
    sku: str                          # crop SKU
    shopify_sku: str                  # after normalize_sku (identity today)
    present_slots: tuple[str, ...]
    missing_slots: tuple[str, ...]
    crops: tuple[tuple[str, Path], ...]
    status: str
    match: Optional[ProductMatch] = None
    detail: str = ""                  # human note (collision count, error text, …)

    @property
    def is_photo_complete(self) -> bool:
        return not self.missing_slots


def plan(
    cfg: Config,
    client: ShopifyClient,
    export_dir: Path,
    *,
    limit: Optional[int] = None,
    only_sku: Optional[str] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> list[SkuPlan]:
    """Crop ``export_dir`` and produce a per-SKU plan. Performs NO writes."""
    run_crops(cfg, export_dir, cfg.staging_dir, on_progress=on_progress)
    grouped = group_crops_by_sku(cfg.staging_dir, cfg.slot_order)

    plans: list[SkuPlan] = []
    for sku in sorted(grouped):
        if only_sku and sku != only_sku:
            continue

        crops = tuple(grouped[sku])
        present = tuple(slot for slot, _ in crops)
        missing = tuple(s for s in cfg.slot_order if s not in present)
        shopify_sku = normalize_sku(sku)

        status, match, detail = _resolve_status(cfg, client, sku)
        plans.append(
            SkuPlan(
                sku=sku,
                shopify_sku=shopify_sku,
                present_slots=present,
                missing_slots=missing,
                crops=crops,
                status=status,
                match=match,
                detail=detail,
            )
        )
        if limit is not None and len(plans) >= limit:
            break
    return plans


def _resolve_status(
    cfg: Config, client: ShopifyClient, crop_sku: str
) -> tuple[str, Optional[ProductMatch], str]:
    try:
        result: Union[ProductMatch, Collision, None] = resolve_sku(client, crop_sku)
    except ShopifyError as e:
        return ERROR, None, str(e)

    if result is None:
        return NO_DRAFT, None, ""
    if isinstance(result, Collision):
        gids = ", ".join(p.product_gid for p in result.products)
        return COLLISION, None, f"{len(result.products)} products: {gids}"
    # ProductMatch
    if cfg.complete_tag in result.tags:
        return ALREADY_COMPLETE, result, ""
    return READY, result, ""

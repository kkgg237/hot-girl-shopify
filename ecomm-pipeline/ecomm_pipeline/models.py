"""Small frozen value types shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProductMatch:
    """An existing Shopify product whose variant SKU exactly matches the query."""

    product_gid: str           # gid://shopify/Product/123
    legacy_id: str             # "123" — the numeric REST id
    title: str
    handle: str
    status: str                # DRAFT | ACTIVE | ARCHIVED
    tags: tuple[str, ...]
    variant_skus: tuple[str, ...]


@dataclass(frozen=True)
class Collision:
    """More than one product carries a variant with the exact SKU.

    The pipeline never guesses between collisions — it logs and skips, because
    attaching photos to the wrong listing is worse than attaching none.
    """

    sku: str
    products: tuple[ProductMatch, ...]

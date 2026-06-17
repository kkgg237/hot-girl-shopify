"""Product reads: find-by-variant-SKU, shop identity, granted scopes.

Writes (media attach, tagging) land in Phase 2.
"""

from __future__ import annotations

from typing import Optional, Union

from ecomm_pipeline.models import Collision, ProductMatch
from ecomm_pipeline.shopify.client import ShopifyClient

# Shopify's ``query: "sku:X"`` search is fuzzy/tokenized and may return products
# whose matching variant isn't first — so we fetch candidates and confirm an
# EXACT, case-sensitive variant.sku match in Python before trusting any result.
_FIND_BY_SKU = """
query FindBySku($q: String!) {
  products(first: 10, query: $q) {
    edges {
      node {
        id
        legacyResourceId
        title
        handle
        status
        tags
        variants(first: 50) { nodes { sku } }
      }
    }
  }
}
"""


def find_product_by_sku(
    client: ShopifyClient, sku: str
) -> Union[ProductMatch, Collision, None]:
    """Resolve an EXACT Shopify variant SKU to a product.

    Returns:
      * ``ProductMatch`` — exactly one product has a variant with this exact SKU
      * ``Collision``    — more than one does (never guessed; caller skips + logs)
      * ``None``         — no product has this exact SKU (caller skips; never creates)
    """
    data = client.gql(_FIND_BY_SKU, {"q": f'sku:"{sku}"'})
    edges = ((data.get("products") or {}).get("edges")) or []

    matches: list[ProductMatch] = []
    for edge in edges:
        node = edge.get("node") or {}
        variant_skus = tuple(
            v["sku"]
            for v in (((node.get("variants") or {}).get("nodes")) or [])
            if v.get("sku")
        )
        # Exact, case-sensitive — the crop SKU after normalization must match
        # a real variant SKU verbatim, not just tokenize-match the search.
        if any(s == sku for s in variant_skus):
            matches.append(
                ProductMatch(
                    product_gid=node.get("id", ""),
                    legacy_id=str(node.get("legacyResourceId") or ""),
                    title=node.get("title") or "",
                    handle=node.get("handle") or "",
                    status=node.get("status") or "",
                    tags=tuple(node.get("tags") or ()),
                    variant_skus=variant_skus,
                )
            )

    if not matches:
        return None
    if len(matches) > 1:
        return Collision(sku=sku, products=tuple(matches))
    return matches[0]


def get_shop_name(client: ShopifyClient) -> Optional[str]:
    """Return the store's display name — a cheap auth smoke test."""
    data = client.gql("{ shop { name myshopifyDomain } }")
    return (data.get("shop") or {}).get("name")


def get_granted_scopes(client: ShopifyClient) -> list[str]:
    """Return the access scopes the current token actually carries.

    Lets the operator confirm write_products / write_files are granted BEFORE a
    Phase 2 mutation 403s in the middle of an upload.
    """
    data = client.gql("{ currentAppInstallation { accessScopes { handle } } }")
    inst = data.get("currentAppInstallation") or {}
    return [s["handle"] for s in (inst.get("accessScopes") or []) if s.get("handle")]

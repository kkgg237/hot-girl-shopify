"""CLI entry point.

Phase 0 ships two read-only smoke-test commands that prove credentials, scopes,
the API endpoint, and the riskiest unknown (does a SKU find the right draft)
BEFORE any byte is uploaded:

  python -m ecomm_pipeline whoami [--sku SHOPIFY_SKU]
  python -m ecomm_pipeline find --sku SHOPIFY_SKU

The ``push`` command (crop → attach → tag) lands in Phase 1–2.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from ecomm_pipeline import push as push_mod
from ecomm_pipeline.config import Config, ConfigError
from ecomm_pipeline.crop_runner import CropError
from ecomm_pipeline.models import Collision, ProductMatch
from ecomm_pipeline.shopify import products as products_api
from ecomm_pipeline.shopify.auth import get_token
from ecomm_pipeline.shopify.client import ShopifyClient, ShopifyError

# Scopes the Phase 2 write path will require; whoami flags any that are missing.
REQUIRED_WRITE_SCOPES = ("write_products", "write_files")


def _build_client(cfg: Config) -> ShopifyClient:
    token = get_token()
    if not token:
        raise ConfigError(
            "Could not obtain a Shopify token. The repo-root .env should carry "
            "SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET for the client_credentials "
            "flow; verify the app is installed on this store."
        )
    return ShopifyClient(cfg.shop, token, cfg.api_version)


def _print_resolution(sku: str, result) -> None:
    """Render a find_product_by_sku result for the terminal."""
    if result is None:
        print(f"  ✗ no draft found with exact variant SKU {sku!r} — would be skipped (never created)")
        return
    if isinstance(result, Collision):
        print(f"  ⚠ COLLISION: {len(result.products)} products carry SKU {sku!r} — would be skipped:")
        for p in result.products:
            print(f"      {p.product_gid}  [{p.status}]  {p.title}")
        return
    if isinstance(result, ProductMatch):
        print(f"  ✓ matched {result.product_gid}  [{result.status}]  {result.title}")
        print(f"    handle: {result.handle}")
        print(f"    variant SKUs: {', '.join(result.variant_skus)}")
        if result.tags:
            print(f"    tags: {', '.join(result.tags)}")


def cmd_whoami(args: argparse.Namespace) -> int:
    cfg = Config.load()
    client = _build_client(cfg)

    shop_name = products_api.get_shop_name(client)
    print(f"shop:         {cfg.shop}")
    print(f"shop name:    {shop_name or '(unknown)'}")
    print(f"api version:  {cfg.api_version}")

    scopes = products_api.get_granted_scopes(client)
    print(f"granted scopes ({len(scopes)}): {', '.join(sorted(scopes)) or '(none)'}")
    all_ok = True
    for scope in REQUIRED_WRITE_SCOPES:
        ok = scope in scopes
        all_ok = all_ok and ok
        flag = "✓" if ok else "✗"
        note = "" if ok else "  ← MISSING: Phase 2 mutations will 403"
        print(f"  {flag} {scope}{note}")
    if not all_ok:
        print("\n  → grant the missing scopes on the app, or add a Custom App "
              "token (SHOPIFY_ADMIN_TOKEN) with write_products + write_files.")

    if args.sku:
        print()
        print(f"find variant SKU {args.sku!r}:")
        _print_resolution(args.sku, products_api.find_product_by_sku(client, args.sku))
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    cfg = Config.load()
    client = _build_client(cfg)
    print(f"find variant SKU {args.sku!r}:")
    _print_resolution(args.sku, products_api.find_product_by_sku(client, args.sku))
    return 0


_STATUS_GLYPH = {
    push_mod.READY: "✓",
    push_mod.ALREADY_COMPLETE: "·",
    push_mod.NO_DRAFT: "✗",
    push_mod.COLLISION: "⚠",
    push_mod.ERROR: "!",
}


def _progress(line: str) -> None:
    print(f"  {line}", file=sys.stderr)


def cmd_push(args: argparse.Namespace) -> int:
    cfg = Config.load()
    client = _build_client(cfg)
    export_dir = Path(args.export_folder)

    print(f"cropping {export_dir} → {cfg.staging_dir} (template: {cfg.template_name})", file=sys.stderr)
    plans = push_mod.plan(
        cfg, client, export_dir,
        limit=args.limit, only_sku=args.sku, on_progress=_progress,
    )

    print(f"\nplan for {len(plans)} SKU(s)  ·  completion tag: {cfg.complete_tag!r}\n")
    counts: dict[str, int] = {}
    for p in plans:
        counts[p.status] = counts.get(p.status, 0) + 1
        glyph = _STATUS_GLYPH.get(p.status, "?")
        slots = f"{len(p.present_slots)}/{len(cfg.slot_order)}"
        miss = f" missing: {','.join(p.missing_slots)}" if p.missing_slots else ""
        draft = p.match.product_gid if p.match else "—"
        line = f"{glyph} {p.sku:<16} {slots} slots{miss:<28} {p.status:<16} {draft}"
        print(line)
        if p.detail:
            print(f"    {p.detail}")

    print("\nsummary: " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    if not args.dry_run:
        print(
            "\nNote: the upload+attach+tag step lands in Phase 2 — nothing was "
            "written. Re-run with --dry-run to silence this notice.",
            file=sys.stderr,
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ecomm-pipeline",
        description="Photo lane: Capture One export → crop → attach to Shopify drafts.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_whoami = sub.add_parser(
        "whoami", help="Verify Shopify credentials + granted scopes (optionally find a SKU)"
    )
    p_whoami.add_argument("--sku", help="Also resolve this exact Shopify variant SKU")
    p_whoami.set_defaults(func=cmd_whoami)

    p_find = sub.add_parser(
        "find", help="Resolve an exact Shopify variant SKU to a product (read-only)"
    )
    p_find.add_argument("--sku", required=True, help="The exact Shopify variant SKU")
    p_find.set_defaults(func=cmd_find)

    p_push = sub.add_parser(
        "push",
        help="Crop an export folder and plan the attach to matching drafts "
             "(Phase 1: read-only plan; uploads land in Phase 2).",
    )
    p_push.add_argument("export_folder", help="Folder of Capture One exports ({SKU} {shot}.jpg)")
    p_push.add_argument("--dry-run", action="store_true",
                        help="Preview the crop→SKU→draft→slot plan without writing (default behavior in Phase 1)")
    p_push.add_argument("--sku", help="Only process this one crop SKU")
    p_push.add_argument("--limit", type=int, help="Stop after N SKUs")
    p_push.set_defaults(func=cmd_push)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, ShopifyError, CropError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

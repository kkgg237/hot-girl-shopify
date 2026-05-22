"""Entry point for the bag-pipeline CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline.ingest import ingest


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST_DIR = REPO_ROOT / "db" / "manifests"


def _cmd_ingest(args: argparse.Namespace) -> int:
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"error: folder not found: {folder}", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_MANIFEST_DIR
    manifest_path = ingest(folder, args.shoot_id, output_dir)

    manifest = json.loads(manifest_path.read_text())
    flagged = [b for b in manifest["bags"] if b["flags"]]
    print(f"shoot {args.shoot_id}: {len(manifest['bags'])} bag(s), {len(flagged)} flagged")
    print(f"manifest: {manifest_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bag-pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Scan a Capture One export folder")
    p_ingest.add_argument("--folder", required=True, help="Path to exported .jpg folder")
    p_ingest.add_argument("--shoot-id", required=True, help="Identifier for this shoot")
    p_ingest.add_argument("--output-dir", help="Where to write the manifest JSON")
    p_ingest.set_defaults(func=_cmd_ingest)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

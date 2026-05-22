"""Shared pytest fixtures + path helpers for the title regression suite.

Two layers of tests:
  1. test_anchors.py   — user-confirmed titles in rules.yaml:regression_anchors.
                         These NEVER auto-update; failure means a regression.
  2. test_snapshots.py — full-coverage snapshot of every item's title across
                         every invoice in output/. Auto-update with:
                           UPDATE_SNAPSHOTS=1 uv run --with pytest \\
                             --with pyyaml --with pydantic pytest tests/

Run all tests:
  uv run --with pytest --with pyyaml --with pydantic pytest tests/ -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


# Make project root importable so `from pricing import ...` works
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


OUTPUT_DIR = PROJECT_ROOT / "output"
SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
SNAPSHOT_DIR.mkdir(exist_ok=True)


def update_snapshots_enabled() -> bool:
    """Whether to regenerate snapshots instead of asserting against them."""
    return os.environ.get("UPDATE_SNAPSHOTS", "").lower() in ("1", "true", "yes")


@pytest.fixture(scope="session")
def invoice_files() -> list[Path]:
    """All transcribed invoice JSONs available for regression testing."""
    return sorted(OUTPUT_DIR.glob("*.json"))


def load_invoice(path: Path):
    """Load an invoice JSON into the Pydantic Invoice model."""
    from costs import Invoice
    data = json.loads(path.read_text(encoding="utf-8"))
    # Strip any underscore-prefixed metadata keys (e.g. __source_file)
    return Invoice(**{k: v for k, v in data.items() if not k.startswith("_")})


def snapshot_path_for(invoice_path: Path) -> Path:
    """Where to store/load the snapshot for a given invoice file."""
    return SNAPSHOT_DIR / f"{invoice_path.stem}.titles.json"


def write_snapshot(snapshot_path: Path, titles: dict[str, str]) -> None:
    """Persist a {source_id: title} map. Sorted for stable diffs."""
    snapshot_path.write_text(
        json.dumps(dict(sorted(titles.items())), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def read_snapshot(snapshot_path: Path) -> dict[str, str]:
    if not snapshot_path.exists():
        return {}
    return json.loads(snapshot_path.read_text(encoding="utf-8"))

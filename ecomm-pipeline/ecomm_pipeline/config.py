"""Configuration — loaded from the repo-root ``.env`` plus module defaults.

The store credentials live in the *repo-root* ``.env`` (shared with the rest of
the repo: ``SHOPIFY_SHOP`` / ``SHOPIFY_CLIENT_ID`` / ``SHOPIFY_CLIENT_SECRET``).
A pipeline-local ``ecomm-pipeline/.env`` may override anything for local tweaks.

Everything an operator might want to change is an ``ECOMM_*`` env var with a
sensible default, so the happy path needs zero extra config beyond the Shopify
credentials that already exist.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # python-dotenv is a declared dependency; guard so imports never explode
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]

# ecomm_pipeline/config.py → parents[1] = ecomm-pipeline/, parents[2] = repo root
PIPELINE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]

# The 4-shot listing-standard template's slots, in display order. The crop
# pipeline writes ``{SKU}_{slot}.jpg`` for each of these.
DEFAULT_SLOT_ORDER: tuple[str, ...] = (
    "01_hero",
    "02_three_quarter",
    "03_back",
    "04_detail",
)


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or the toolchain is absent.

    The message always names the specific missing key/path so the operator can
    fix it without reading the source.
    """


def _load_env() -> None:
    """Load the repo-root ``.env`` then an optional pipeline-local override."""
    if load_dotenv is None:  # pragma: no cover
        return
    load_dotenv(REPO_ROOT / ".env", override=False)
    local = PIPELINE_DIR / ".env"
    if local.exists():
        load_dotenv(local, override=True)


@dataclass(frozen=True)
class Config:
    """Resolved, immutable run configuration."""

    shop: str
    api_version: str = "2025-01"
    complete_tag: str = "photos-complete"
    template_name: str = "listing-standard"
    slot_order: tuple[str, ...] = DEFAULT_SLOT_ORDER

    # Regex matching ONE SKU (no shot suffix), used to split underscore-separated
    # Capture One exports — e.g. ``BRU_2605_001_1.jpg`` → SKU ``BRU_2605_001`` +
    # shot ``1``. The store's SKUs are LETTERS_DIGITS_DIGITS. Override via
    # ECOMM_SKU_PATTERN if that ever changes. Used only for input normalization;
    # matching against Shopify is still verbatim.
    sku_pattern: str = r"[A-Za-z]+_\d+_\d+"

    crop_repo_dir: Path = REPO_ROOT / "ecomm-crop-pipeline"
    crop_venv_python: Path = REPO_ROOT / "ecomm-crop-pipeline" / ".venv" / "bin" / "python"
    staging_dir: Path = PIPELINE_DIR / "staging"
    state_dir: Path = PIPELINE_DIR / "state"

    @property
    def ledger_path(self) -> Path:
        return self.state_dir / "push_ledger.json"

    @property
    def template_path(self) -> Path:
        return self.crop_repo_dir / "templates" / f"{self.template_name}.yaml"

    @classmethod
    def load(cls) -> "Config":
        """Build a Config from the environment, failing fast on a missing shop."""
        _load_env()
        shop = (
            os.environ.get("SHOPIFY_SHOP")
            or os.environ.get("SHOPIFY_STORE_DOMAIN")
            or ""
        ).strip()
        shop = shop.replace("https://", "").replace("http://", "").rstrip("/")
        if not shop:
            raise ConfigError(
                "No store domain. Set SHOPIFY_SHOP (or SHOPIFY_STORE_DOMAIN) "
                f"in {REPO_ROOT / '.env'}"
            )
        return cls(
            shop=shop,
            api_version=os.environ.get("ECOMM_API_VERSION", "2025-01").strip(),
            complete_tag=os.environ.get("ECOMM_COMPLETE_TAG", "photos-complete").strip(),
            template_name=os.environ.get("ECOMM_TEMPLATE", "listing-standard").strip(),
            sku_pattern=os.environ.get("ECOMM_SKU_PATTERN", r"[A-Za-z]+_\d+_\d+").strip(),
        )

    def validate_crop_toolchain(self) -> None:
        """Fail fast if the crop venv interpreter or the template is missing.

        Only needed by commands that actually run crops (Phase 1+); the auth /
        find smoke tests don't touch the crop pipeline.
        """
        if not self.crop_venv_python.exists():
            raise ConfigError(
                f"Crop venv interpreter not found at {self.crop_venv_python}. "
                "Run ecomm-crop-pipeline/setup.sh first."
            )
        if not self.template_path.exists():
            raise ConfigError(
                f"Crop template {self.template_name!r} not found at {self.template_path}."
            )

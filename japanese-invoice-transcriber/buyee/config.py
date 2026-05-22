"""Configuration for Buyee — bot tokens, authorized chat IDs, etc.

Stored in buyee/state/config.json. Gitignored because it contains secrets
(the Telegram bot token gives anyone with it the ability to send messages
as your bot).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


HERE = Path(__file__).parent
CONFIG_PATH = HERE / "state" / "config.json"


class BuyeeConfig(BaseModel):
    """Persistent config for Buyee integration. All fields optional so a
    partially-configured setup still loads cleanly."""

    # Telegram bot integration
    telegram_token: Optional[str] = None
    telegram_authorized_chat_id: Optional[int] = None
    telegram_last_update_id: Optional[int] = None

    # Auto-discovery toggle (background launchd job uses this)
    auto_discovery_enabled: bool = False

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_token and self.telegram_authorized_chat_id)


def load_config() -> BuyeeConfig:
    if not CONFIG_PATH.exists():
        return BuyeeConfig()
    try:
        return BuyeeConfig(**json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:
        return BuyeeConfig()


def save_config(cfg: BuyeeConfig) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        cfg.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

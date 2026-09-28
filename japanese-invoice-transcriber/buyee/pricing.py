"""Pricing Curve & Rules Engine for Buyee Auctions.

Implements a smooth continuous power-law curve model:
  Max Bid = Round_1000( Start_Price + ( (1 + Category_Boost) * k * Start_Price^gamma ) )

Levers:
  - k (Base Aggressiveness): e.g. 3.5 (Conservative=2.5, Standard=3.5, Aggressive=4.5)
  - gamma (High-Ticket Safety Taper): default 0.70
  - Category Boost (C): Bags +50%, Shoes +25%, Watches +25%, Clothing +0%
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).parent
STATE_DIR = HERE / "state"
CONFIG_PATH = STATE_DIR / "smooth_pricing_config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "k": 3.5,
    "gamma": 0.70,
    "category_boosts": {
        "Bags": 0.50,
        "Shoes": 0.25,
        "Watches": 0.25,
        "Clothing": 0.00,
        "Other": 0.15,
    }
}

def load_pricing_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        save_pricing_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_CONFIG

def save_pricing_config(cfg: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

def round_to_buyee_increment(amount: int, step: int = 1000) -> int:
    if amount <= 0:
        return 0
    remainder = amount % step
    if remainder == 0:
        return amount
    return amount + (step - remainder)

def calculate_smooth_bid(
    start_price: int,
    category: str,
    k: Optional[float] = None,
    gamma: Optional[float] = None,
    category_boosts: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    cfg = load_pricing_config()
    k_val = k if k is not None else cfg.get("k", 3.5)
    gamma_val = gamma if gamma is not None else cfg.get("gamma", 0.70)
    boosts = category_boosts if category_boosts is not None else cfg.get("category_boosts", {})

    cat_key = category.strip().capitalize() if category else "Other"
    if cat_key not in boosts:
        cat_key = "Other"

    c_boost = boosts.get(cat_key, 0.15)

    if start_price <= 0:
        start_price = 1000

    base_inc = k_val * (start_price ** gamma_val)
    raw_inc = base_inc * (1.0 + c_boost)
    raw_bid = start_price + raw_inc
    final_bid = round_to_buyee_increment(int(raw_bid), 1000)
    final_inc = final_bid - start_price
    markup_pct = (final_inc / start_price) * 100.0 if start_price > 0 else 0.0

    return {
        "start_price": start_price,
        "category": cat_key,
        "k": k_val,
        "gamma": gamma_val,
        "category_boost": c_boost,
        "increment": final_inc,
        "calculated_max_bid": final_bid,
        "markup_pct": round(markup_pct, 1),
    }

def calculate_bid(start_price: int, category: str, matrix: Any = None) -> Dict[str, Any]:
    return calculate_smooth_bid(start_price, category)

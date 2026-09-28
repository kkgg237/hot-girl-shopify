"""Chrome DevTools Protocol (CDP) Tab Attachment Helper for Buyee.

Attaches to a running Chrome instance with remote debugging enabled (--remote-debugging-port=9222)
and extracts open Buyee item URLs automatically.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, List, Tuple

DEFAULT_CDP_HOST = "127.0.0.1"
DEFAULT_CDP_PORT = 9222


def fetch_open_buyee_tabs(
    host: str = DEFAULT_CDP_HOST,
    port: int = DEFAULT_CDP_PORT,
    timeout: float = 3.0,
) -> Tuple[bool, List[Dict[str, str]], str]:
    """Connect to Chrome CDP endpoint and return open Buyee item URLs.

    Returns (success, list_of_tab_dicts, message).
    Each dict has {"title": str, "url": str, "id": str}.
    """
    endpoint = f"http://{host}:{port}/json"
    try:
        req = urllib.request.Request(endpoint, headers={"User-Agent": "BuyeeTabFetcher/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False, [], f"Chrome CDP returned HTTP {resp.status}"
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return (
            False,
            [],
            f"Could not connect to Chrome on http://{host}:{port}/json ({e.reason}). "
            "Ensure Chrome was started with --remote-debugging-port=9222.",
        )
    except Exception as e:
        return False, [], f"Error querying Chrome CDP: {e}"

    buyee_tabs: List[Dict[str, str]] = []
    seen_urls = set()

    for item in data:
        if item.get("type") != "page":
            continue
        url = item.get("url", "")
        title = item.get("title", "Buyee Item")

        # Match Buyee auction / item pages
        if ("buyee.jp/item/" in url or "buyee.jp/btob/item/" in url or "buyee.jp/item/v1/" in url) and url not in seen_urls:
            seen_urls.add(url)
            buyee_tabs.append(
                {
                    "id": item.get("id", ""),
                    "title": title,
                    "url": url,
                }
            )

    if not buyee_tabs:
        return True, [], f"Chrome is running on port {port}, but no open Buyee item tabs were found."

    return True, buyee_tabs, f"Successfully retrieved {len(buyee_tabs)} open Buyee tab(s)."

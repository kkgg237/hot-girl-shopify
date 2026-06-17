"""The single chokepoint for all Shopify Admin GraphQL traffic.

One ``gql()`` helper with 429/5xx exponential backoff and a courtesy pause when
the cost-based throttle bucket runs low. Every query and mutation in this
package goes through here so retry/throttle policy lives in one place.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import requests


class ShopifyError(RuntimeError):
    """Any non-recoverable Shopify API failure (HTTP error or GraphQL errors)."""


# HTTP statuses worth retrying with backoff (transient).
_RETRYABLE = {429, 500, 502, 503, 504}


class ShopifyClient:
    def __init__(
        self,
        shop: str,
        token: str,
        api_version: str = "2025-01",
        *,
        timeout: int = 30,
    ) -> None:
        self.shop = shop
        self.token = token
        self.api_version = api_version
        self.timeout = timeout
        self.endpoint = f"https://{shop}/admin/api/{api_version}/graphql.json"
        self._session = requests.Session()
        self._session.headers.update(
            {
                "X-Shopify-Access-Token": token,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "ecomm-pipeline/0.1",
            }
        )

    def gql(
        self,
        query: str,
        variables: Optional[dict] = None,
        *,
        max_retries: int = 5,
    ) -> dict[str, Any]:
        """POST a GraphQL operation and return its ``data`` object.

        Raises ShopifyError on auth failure, exhausted retries, or top-level
        ``errors`` (which include the cost-throttle "Throttled" message). Per-
        mutation ``userErrors``/``mediaUserErrors`` are left for callers to read,
        since they are part of a successful response.
        """
        payload = {"query": query, "variables": variables or {}}
        backoff = 1.0
        last_detail = ""

        for attempt in range(max_retries):
            try:
                resp = self._session.post(self.endpoint, json=payload, timeout=self.timeout)
            except requests.RequestException as e:
                last_detail = f"network error: {e}"
                if attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                raise ShopifyError(last_detail) from e

            if resp.status_code == 401:
                raise ShopifyError(
                    "401 Unauthorized — token invalid/expired or the app lacks the "
                    "required scopes. Check SHOPIFY_CLIENT_ID/SECRET and app scopes."
                )

            if resp.status_code in _RETRYABLE:
                last_detail = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if attempt < max_retries - 1:
                    # Honor Retry-After when present, else exponential backoff.
                    retry_after = resp.headers.get("Retry-After")
                    time.sleep(float(retry_after) if retry_after else backoff)
                    backoff *= 2
                    continue
                raise ShopifyError(f"retries exhausted — {last_detail}")

            if resp.status_code != 200:
                raise ShopifyError(f"HTTP {resp.status_code}: {resp.text[:300]}")

            data = resp.json()

            errors = data.get("errors")
            if errors:
                # A THROTTLED top-level error is retryable; everything else isn't.
                if _is_throttled(errors) and attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                raise ShopifyError(f"GraphQL errors: {errors}")

            self._maybe_throttle(data)
            return data.get("data") or {}

        raise ShopifyError(f"retries exhausted — {last_detail or 'unknown error'}")

    def _maybe_throttle(self, data: dict) -> None:
        """Pause briefly when the cost bucket is nearly drained, to avoid a 429."""
        cost = (data.get("extensions") or {}).get("cost") or {}
        status = cost.get("throttleStatus") or {}
        available = status.get("currentlyAvailable")
        if isinstance(available, (int, float)) and available < 100:
            time.sleep(1.0)


def _is_throttled(errors: Any) -> bool:
    try:
        return any(
            (e.get("extensions") or {}).get("code") == "THROTTLED"
            or "throttled" in (e.get("message") or "").lower()
            for e in errors
        )
    except (AttributeError, TypeError):
        return False

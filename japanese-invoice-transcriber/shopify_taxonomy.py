"""Fetch and cache Shopify's Standard Product Taxonomy.

Used by the Copy formats tab to give each template a category-picker
dropdown instead of forcing the user to paste a GID. The taxonomy has
~10k nodes; we cache the full list locally as JSON and reuse it until
the user hits Refresh.

API surface:
    is_cached()              -> bool
    load_taxonomy()          -> list[{"id", "name", "full_name"}]
    fetch_taxonomy()         -> (count, items | error_message)
    cache_path() / cache_age_seconds()

The fetch hits Shopify's Admin GraphQL API (`taxonomy.categories`,
added 2024-04). Same auth as the REST helpers in shopify_inventory.
"""
from __future__ import annotations

import datetime as _dt
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Union

# Reuse the API version constant that's already pinned in shopify_push so
# the GraphQL endpoint matches the REST one.
from shopify_push import DEFAULT_API_VERSION


HERE = Path(__file__).parent
CACHE_PATH = HERE / "heuristics" / "shopify_taxonomy.json"


def cache_path() -> Path:
    return CACHE_PATH


def is_cached() -> bool:
    """True when the cache file exists and is non-empty."""
    return CACHE_PATH.exists() and CACHE_PATH.stat().st_size > 0


def cache_age_seconds() -> Optional[float]:
    """Return seconds since the cache file was last modified, or None."""
    if not is_cached():
        return None
    return time.time() - CACHE_PATH.stat().st_mtime


def load_taxonomy() -> list[dict]:
    """Load cached taxonomy. Returns [] if the cache is missing or malformed."""
    if not is_cached():
        return []
    try:
        with CACHE_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return [d for d in data if isinstance(d, dict) and d.get("id")]
    except (json.JSONDecodeError, OSError):
        return []


_ROOTS_QUERY = """
query Roots($cursor: String, $size: Int!) {
  taxonomy {
    categories(first: $size, after: $cursor) {
      edges {
        cursor
        node {
          id
          name
          fullName
          childrenIds
        }
      }
      pageInfo { hasNextPage }
    }
  }
}
"""

_NODES_QUERY = """
query Nodes($ids: [ID!]!) {
  nodes(ids: $ids) {
    __typename
    ... on TaxonomyCategory {
      id
      name
      fullName
      childrenIds
    }
  }
}
"""


# Default to apparel-only — a fashion resale store has ~zero products outside
# these roots, so descending into 24 other root subtrees (Electronics, Home,
# Pet Supplies, …) just makes the fetch slow for no benefit. Override by
# passing a different list (e.g. ["*"] for everything, or specific root names).
DEFAULT_ROOT_NAMES = ["Apparel & Accessories", "Luggage & Bags"]


def fetch_taxonomy(
    page_size: int = 250,
    batch_size: int = 100,
    root_names: Optional[list[str]] = None,
) -> tuple[int, Union[list[dict], str]]:
    """Fetch the Shopify Standard Product Taxonomy.

    By default, fetches ONLY the apparel-related roots — see
    DEFAULT_ROOT_NAMES — because the other 24 root subtrees are noise for a
    fashion catalogue. Pass `root_names=["*"]` to fetch everything, or pass
    a specific list (matched against the root's `name`, case-insensitive).

    Approach (BFS via batched nodes(ids:) queries):
      1. Fetch all roots (one paginated query) — cheap, ~26 nodes.
      2. Keep only roots in `root_names`; queue their `childrenIds`.
      3. Batch pending IDs and resolve them with the top-level `nodes(ids:)`
         query (Relay batch lookup, up to N categories per round-trip).
         Each resolved node contributes more `childrenIds` to the queue.

    Apparel-only typically resolves in 5-15 seconds (~1500 nodes).

    Returns (count, items) on success and writes the cache atomically.
    Returns (0, error_message) on failure (cache is left alone).
    """
    from shopify_inventory import get_shop, get_token

    shop = get_shop()
    token = get_token()
    if not shop or not token:
        return 0, "Shopify not configured (missing shop or token)"

    url = f"https://{shop}/admin/api/{DEFAULT_API_VERSION}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    def _gql(query: str, variables: dict) -> tuple[Optional[dict], Optional[str]]:
        payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8")), None
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                body = ""
            return None, f"HTTP {e.code}: {body}"
        except urllib.error.URLError as e:
            return None, f"Network error: {e.reason}"

    by_id: dict[str, dict] = {}
    pending: list[str] = []

    # Normalize the root filter. ["*"] = no filter (fetch all).
    selected = root_names if root_names is not None else DEFAULT_ROOT_NAMES
    fetch_all = any(s.strip() == "*" for s in selected)
    selected_lower = {s.strip().lower() for s in selected if s.strip() != "*"}

    def _absorb(node: dict, enqueue_children: bool = True) -> None:
        nid = node.get("id")
        if not nid or nid in by_id:
            return
        by_id[nid] = {
            "id": nid,
            "name": node.get("name") or "",
            "full_name": node.get("fullName") or "",
        }
        if enqueue_children:
            for cid in (node.get("childrenIds") or []):
                if cid and cid not in by_id:
                    pending.append(cid)

    # --- Pass 1: all roots (cheap — only ~26 nodes total) -------------------
    cursor: Optional[str] = None
    while True:
        data, err = _gql(_ROOTS_QUERY, {"cursor": cursor, "size": page_size})
        if err:
            return 0, err
        if data.get("errors"):
            return 0, f"GraphQL errors: {data['errors']}"

        cats = (((data.get("data") or {}).get("taxonomy") or {})
                .get("categories") or {})
        edges = cats.get("edges") or []
        for edge in edges:
            node = edge.get("node") or {}
            # Only descend into roots matching the filter; absorb the rest
            # as well so they appear in the cache (they're cheap and useful
            # for any edge-case routing).
            root_name = (node.get("name") or "").strip().lower()
            should_descend = fetch_all or root_name in selected_lower
            _absorb(node, enqueue_children=should_descend)
        page_info = cats.get("pageInfo") or {}
        if not page_info.get("hasNextPage") or not edges:
            break
        cursor = edges[-1].get("cursor")
        time.sleep(0.1)

    # --- Pass 2: BFS through the tree via batched nodes(ids:) queries -------
    while pending:
        # Take up to batch_size unseen IDs from the queue
        batch: list[str] = []
        leftover: list[str] = []
        seen: set[str] = set()
        for nid in pending:
            if nid in by_id or nid in seen:
                continue
            if len(batch) < batch_size:
                batch.append(nid)
                seen.add(nid)
            else:
                leftover.append(nid)
        pending = leftover
        if not batch:
            break

        data, err = _gql(_NODES_QUERY, {"ids": batch})
        if err:
            # Don't abort the whole fetch; partial cache is still useful
            continue
        if data.get("errors"):
            # Same — log via the cache (these will retry on next fetch)
            continue

        for node in (data.get("data") or {}).get("nodes") or []:
            if not node:
                continue
            if node.get("__typename") and node["__typename"] != "TaxonomyCategory":
                continue
            _absorb(node)

        time.sleep(0.05)

    items = sorted(by_id.values(), key=lambda x: x.get("full_name") or "")

    # Atomic write: temp file + rename so a partial fetch can't corrupt the cache
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    tmp.replace(CACHE_PATH)

    return len(items), items


def find_by_gid(gid: str, taxonomy: Optional[list[dict]] = None) -> Optional[dict]:
    """Look up a taxonomy node by canonical GID. Returns None if not found."""
    if not gid:
        return None
    if taxonomy is None:
        taxonomy = load_taxonomy()
    for item in taxonomy:
        if item.get("id") == gid:
            return item
    return None


# Subtree segments we de-prioritize when scoring — adult resale catalogue
# should almost never land in these. Each subtracts from the candidate's score.
_NOISE_SEGMENTS = (
    "baby", "children", "girls'", "boys'", "activewear", "maternity",
    "nursing", "lingerie", "sleepwear", "loungewear", "uniforms",
    "ceremonial", "undergarments", "wedding", "bridal", "swimwear",
    "costume", "dance",
)

# Aliases for fashion words that DON'T appear in any Shopify leaf — point
# them at the closest existing leaf so the suggester still produces a useful
# default. The target_leaf must be a real leaf name (case-insensitive).
_WORD_TO_LEAF_ALIAS = {
    # Generic handbag synonyms (Shopify's leaf is "Handbags", not "Bag")
    "bag":         "handbags",
    "bags":        "handbags",
    "purse":       "handbags",
    "purses":      "handbags",
    # Iconic bag models — strong handbag signals
    "kelly":       "handbags",
    "birkin":      "handbags",
    "speedy":      "handbags",
    "constance":   "handbags",
    "neverfull":   "handbags",
    "marmont":     "handbags",
    # Specific bag silhouettes — route to their compound-name leaves
    "clutch":      "clutch bags",
    "clutches":    "clutch bags",
    "tote":        "shopper bags",
    "totes":       "shopper bags",
    "crossbody":   "cross body bags",
    "cross-body":  "cross body bags",
    "satchel":     "satchel bags",
    "satchels":    "satchel bags",
    "saddle":      "saddle bags",
    "hobo":        "hobo bags",
    "bucket":      "bucket bags",
    # Outerwear — no Coats/Trenches/Blazers leaves; route to Coats & Jackets
    "blazer":      "coats & jackets",
    "blazers":     "coats & jackets",
    "trench":      "coats & jackets",
    "duster":      "coats & jackets",
    "peacoat":     "pea coats",
    "peacoats":    "pea coats",
    "jacket":      "coats & jackets",
    "jackets":     "coats & jackets",
    # "coat" / "coats" omitted on purpose — too general. "Peacoat", "Trench",
    # "Blazer", "Duster", "Jacket" all alias to coats already; bare "coat"
    # would over-fire and steal from the more specific aliases above.
    "baguette":    "baguette handbags",
    # Bottoms — common synonyms
    "trousers":    "pants",
    "trouser":     "pants",
    "slacks":      "pants",
    "chinos":      "pants",
    "leggings":    "pants",
    # Tops — Shopify has compound "Tank Tops" etc.; "top" alone hits nothing
    "blouse":      "shirts",
    "blouses":     "shirts",
    "tee":         "t-shirts",
    "tees":        "t-shirts",
    # Bustiers / corset tops / generic "top" — no dedicated leaves in Shopify,
    # route to Shirts (the most generic Clothing Tops leaf).
    "bustier":     "shirts",
    "bustiers":    "shirts",
    "corset":      "shirts",
    "corsets":     "shirts",
    "top":         "shirts",
    "tops":        "shirts",
    "halter":      "shirts",
    "halters":     "shirts",
    "tunic":       "shirts",
    "tunics":      "shirts",
    # Footwear — no Loafers / Pumps leaves
    "pump":        "heels",
    "pumps":       "heels",
    "loafer":      "flats",
    "loafers":     "flats",
    "oxfords":     "flats",
    "mules":       "flats",
    "stilettos":   "heels",
    # Accessories — no Scarves leaf
    "scarf":       "clothing accessories",
    "scarves":     "clothing accessories",
    "shawl":       "clothing accessories",
    "shawls":      "clothing accessories",
    # Dresses / one-pieces
    "jumpsuit":    "one-pieces",
    "jumpsuits":   "one-pieces",
    "romper":      "one-pieces",
    "rompers":     "one-pieces",
    # Coordinated sets (two-pieces, burnout sets, matching sets, co-ords).
    # Shopify has "Outfit Sets" as the canonical leaf.
    "set":         "outfit sets",
    "sets":        "outfit sets",
    "two-piece":   "outfit sets",
    "twopiece":    "outfit sets",
    "co-ord":      "outfit sets",
    "coord":       "outfit sets",
    "coords":      "outfit sets",
}

# Weak aliases: words common enough in non-product contexts that they should
# only fire when no stronger signal exists. Their alias score is dropped so
# a real product noun (Dress, Pants, etc.) outranks them in tied haystacks
# (e.g., "Tea Set Print Dress" → Dresses, not Outfit Sets).
_WEAK_ALIAS_WORDS = {
    "set", "sets", "two-piece", "twopiece", "co-ord", "coord", "coords",
}

# Minimum total score to consider a node a valid suggestion. Filters out
# weak 1-of-many partial matches (e.g., the word "Decorative" hitting
# "Decorative Fans" with a single token).
_MIN_SUGGESTION_SCORE = 6.0


def _word_match(word: str, haystack_lc: str) -> bool:
    """Whole-word match against a lowercase haystack."""
    import re as _re
    return bool(_re.search(rf"\b{_re.escape(word)}\b", haystack_lc))


def _singularize(word: str) -> str:
    """Best-effort singular form — handles -ies, -ches/-shes/-xes/-ses/-zes, -s."""
    if len(word) <= 3:
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if any(word.endswith(suf) for suf in ("ches", "shes", "xes", "ses", "zes")):
        return word[:-2]
    if word.endswith("s"):
        return word[:-1]
    return word


_STOPWORDS = {"and", "the", "of", "in", "or", "for", "with"}


def _tokenize(s: str) -> list[str]:
    """Lowercase word tokens (skipping ampersand-like separators and stopwords)."""
    import re as _re
    toks = _re.findall(r"[a-z]+(?:[-'][a-z]+)*", s.lower())
    return [t for t in toks if t not in _STOPWORDS]


def suggest_category_for_product(
    title: str = "",
    product_type: str = "",
    tags: str = "",
    taxonomy: Optional[list[dict]] = None,
) -> Optional[dict]:
    """Suggest the best Shopify taxonomy node directly from a product's
    title + product_type + tags. No template indirection.

    Scoring per taxonomy node:
      - leaf tokens that whole-word-match the haystack (with smart
        singularization) — score = (matched / total) × 10
      - alias bump: if the haystack contains an aliased word (e.g. "blazer")
        that points to this leaf, score = max(score, 8)
      - noise subtree (Activewear/Baby/Maternity/…) penalty: -5
      - depth penalty: -0.3 per level (shallower preferred)
      - length bonus: up to +1 (longer leaf = more specific)

    Returns the highest-scored taxonomy dict {"id","name","full_name"} or None.
    """
    if taxonomy is None:
        taxonomy = load_taxonomy()
    if not taxonomy:
        return None

    haystack = f" {title} {product_type} {tags} ".lower()

    # Resolve aliases — for each target leaf, remember the best alias-score
    # it earned. Strong aliases (e.g. "kelly", "blazer") = 11; weak aliases
    # (e.g. "set", which is common in non-product phrases) = 9 so they
    # don't outrank real product-noun token matches.
    aliased_leaves: dict[str, float] = {}
    for alias_word, target_leaf in _WORD_TO_LEAF_ALIAS.items():
        if not _word_match(alias_word, haystack):
            continue
        score = 9.0 if alias_word in _WEAK_ALIAS_WORDS else 11.0
        if score > aliased_leaves.get(target_leaf, 0.0):
            aliased_leaves[target_leaf] = score

    best_node = None
    best_score = 0.0

    for node in taxonomy:
        leaf = (node.get("name") or "").strip().lower()
        full = (node.get("full_name") or "").strip().lower()
        if not leaf or not full:
            continue

        # Token-level match against haystack (handles multi-word leaves like
        # "Clutch Bags", "Cross Body Bags", "Tank Tops")
        leaf_tokens = _tokenize(leaf)
        if not leaf_tokens:
            continue
        matched = 0
        for tok in leaf_tokens:
            tok_sing = _singularize(tok)
            if _word_match(tok, haystack) or (
                tok_sing != tok and _word_match(tok_sing, haystack)
            ):
                matched += 1
        token_score = (matched / len(leaf_tokens)) * 10.0
        # Multi-token bonus — a fully-matched compound leaf is a stronger
        # signal than a single-word alias. "Shoulder Bags" 2/2 should beat
        # the "bag → Handbags" alias (a more general parent).
        if matched == len(leaf_tokens):
            token_score += matched * 0.75  # 1/1=10.75, 2/2=11.5, 3/3=12.25

        # Alias bump — per-target score (11 for strong aliases, 9 for weak).
        # Strong aliases beat single-word leaf matches but lose to fully-
        # matched compound leaves. Weak aliases lose to any real noun match.
        alias_score = aliased_leaves.get(leaf, 0.0)

        score = max(token_score, alias_score)
        if score <= 0:
            continue

        # Noise penalty
        for noise in _NOISE_SEGMENTS:
            if noise in full:
                score -= 5.0
                break

        # Depth tiebreaker — shallower preferred
        score -= full.count(">") * 0.3

        # Length bonus — longer leaf names are more specific
        score += min(len(leaf) * 0.05, 1.0)

        if score > best_score:
            best_score = score
            best_node = node

    return best_node if best_score >= _MIN_SUGGESTION_SCORE else None


def humanize_age(seconds: Optional[float]) -> str:
    """Render seconds-since as 'X seconds/minutes/hours/days ago'."""
    if seconds is None:
        return "never"
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds / 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h ago"
    return f"{int(seconds / 86400)}d ago"

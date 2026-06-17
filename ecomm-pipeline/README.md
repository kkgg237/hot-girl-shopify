# ecomm-pipeline

The **photo lane** for the one-of-one vintage store. It wires together two
existing pieces and pushes the result to Shopify:

```
Capture One export folder   →   ecomm-crop-pipeline   →   {SKU}_{slot}.jpg   →   attach to the existing Shopify draft (matched by Variant SKU)
   (edited, uncropped)            (crop + template)        (ready to upload)        + tag `photos-complete`
```

This layer **never creates or publishes products.** Drafts are created at intake
(SKU + title + price); a human reviews and publishes after photos land.

## How the pieces fit

- **Crop engine** — [`../ecomm-crop-pipeline`](../ecomm-crop-pipeline) (Python
  3.9.6, heavy ML deps). Consumed as a **subprocess** against its own
  `.venv/bin/python`, so this package stays on a clean modern interpreter with
  only `requests` + `python-dotenv`.
- **Shopify client** — `ecomm_pipeline/shopify/`, the Python sibling of
  `shop-photo-editor/lib/shopify.ts` (the proven staged-upload path against this
  store, API `2025-01`). Auth helpers are **copied** from the invoice
  transcriber — no cross-project import.
- **Credentials** — read from the **repo-root `.env`**
  (`SHOPIFY_SHOP` / `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET`, OAuth
  client_credentials). No `.env` is needed in this folder.

## Setup

```sh
cd ecomm-pipeline
uv sync          # creates .venv (Python 3.12) with requests + python-dotenv + pytest
```

## What works today (read-only — no writes)

```sh
# Phase 0 — auth + lookup smoke tests
uv run python -m ecomm_pipeline whoami                 # token + granted scopes (flags write_products/write_files)
uv run python -m ecomm_pipeline find --sku "001"       # resolve an exact Shopify variant SKU to a product

# Phase 1 — crop an export folder and PLAN the attach (zero writes)
uv run python -m ecomm_pipeline push --dry-run /path/to/capture-one-export

uv run pytest                                          # SKU + crop-argv invariants (no network, no creds)
```

`push --dry-run` crops every SKU into `staging/`, recovers each SKU, checks how
many of the 4 slots are present, resolves the matching draft, and prints a plan:

```
✓ BRU_2605_010   4/4 slots                     ready       gid://shopify/Product/…
· ISS_2605_011   4/4 slots                     already-complete  gid://…
✗ ISS_2605_02    3/4 slots missing: 03_back    no-draft    —
```

> **Filename convention.** Exports are `{SKU}_{shot}.jpg` —
> `BRU_2605_001_1.jpg`, `BRU_2605_001_2.jpg`, … for SKU `BRU_2605_001`.
> `crop_runner` normalizes the underscore-before-shot into the crop engine's
> dash form (`BRU_2605_001-1.jpg`) before cropping, using the SKU shape
> `ECOMM_SKU_PATTERN` (default `[A-Za-z]+_\d+_\d+`); a bare `BRU_2605_001.jpg`
> safety frame is left as shot 0. The recovered SKU **is** the Shopify Variant
> SKU — `matching.py::normalize_sku` is identity (one seam, with a
> `state/sku_map.json` escape hatch).

## Roadmap

| Phase | Deliverable |
|---|---|
| **0** ✅ | Scaffold + auth + find-by-SKU smoke tests (`whoami`, `find`). |
| **1** ✅ | `crop_runner` (subprocess to the 3.9.6 crop venv) + SKU grouping behind `push --dry-run` — prints the per-SKU `slots → draft → status` plan, zero writes. |
| **2** | Staged upload → `productCreateMedia` → poll READY → reorder → `tagsAdd` + ledger. *The usable v1: one command, photos on the draft, re-runs are no-ops.* |
| **3** | Scope-doc parity: read-only `audit`, measurements-sheet → description. |

## Idempotency (Phase 2 contract)

Three layers, the **remote tag is the source of truth**: the `photos-complete`
tag is added *last* (after all media attach + reorder), `push` re-checks the live
tag before touching any SKU, and a deterministic `alt="<sku> <slot>"` lets a
half-pushed SKU resume only its missing slots after a crash. A wiped local ledger
never causes duplicate photos.

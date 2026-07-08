# Bulk Drop Audit Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add a new `Bulk Drop Audit` tab to `invoices.paststudies-tools.com` for SKU-based bag/accessory lookup, AI-generated Shopify description drafts, review, and approved Shopify pushes.

**Architecture:** Keep the existing Shopify bulk editor intact. Reuse its Shopify lookup and product update helpers where possible, but build a separate guided Streamlit workflow for descriptions. All generated descriptions are drafts until manually approved.

**Tech Stack:** Python, Streamlit, Shopify Admin API helpers already in `japanese-invoice-transcriber/app.py`, Anthropic API for multimodal description generation, web lookup for exact matching bag/accessory dimensions.

---

## Confirmed Product Requirements

### New tab
Add a new top-level Streamlit tab:

```text
Bulk Drop Audit
```

Place it between:

```text
Shopify audit
Shopify bulk editor
```

Current tab section is in:

```text
japanese-invoice-transcriber/app.py:5717-5733
```

### Workflow
1. Kat pastes SKUs, one per line.
2. Tool looks up Shopify products by SKU, not barcode.
3. Tool displays title, existing description, price, status, product type/tags, first image, product ID, and admin link.
4. Only blank descriptions are eligible by default.
5. Only bags and accessories are eligible.
6. AI input should be limited to product title and first image.
7. For measurements, the tool must search online for exact matching listings of the same bag/accessory. It must not infer measurements from the image.
8. Generated copy must use the Past Studies Shopify template.
9. Kat previews/edits/approves drafts.
10. Only approved descriptions are pushed to Shopify.

### Tone/template reference
Use this product page as the canonical tone/template:

```text
https://paststudies.shop/products/chanel-pink-suede-turnlock-flap-bag?variant=52373066809620
```

Reference output format:

```text
DIMENSIONS:
10" L x 3" W x 6" H

DETAILS:
Pink quilted suede exterior
Rectangular turn-lock closure
Front flap design
Chain-link shoulder strap
Interior compartment with slip pocket
Structured flap bag silhouette

MATERIAL:
Suede Leather, Fabric Lining, Silver-Tone Hardware

CONDITION NOTES:
8/10 – Light wear to suede. Minor surface marks and slight corner wear. Light surface wear to hardware. Interior remains clean.
```

Tone rules:

```text
Professional
Plain
Specific
Limited to description only
DETAILS section max 3-4 lines
No salesy language
No flowery wording
No "perfect for"
No invented measurements
No overwriting existing descriptions by default
```

Condition grading style:

```text
Use the same compact format as the Shopify reference example, e.g.:
8/10 – Light wear to suede. Minor surface marks and slight corner wear. Light surface wear to hardware. Interior remains clean.
```

Do not add a separate verbose grading rubric to the final product description. Keep the grade and notes in one professional line under `CONDITION NOTES:`.

Dimensions fallback:

```text
If exact measurements cannot be verified from an accepted matching source, show:
DIMENSIONS:
Needs review
```

This should not block draft generation. It should show a warning and require Kat's review before pushing.

Accepted measurement/detail sources:

```text
Fashionphile
The RealReal
Rebag
Vestiaire Collective
1stDibs
Brand pages/archive pages
Other resale listings only if title/image clearly match
```

Rejected sources:

```text
Pinterest
AI summaries
Random blogs
Unclear duplicates
Listings for similar but not exact bags/accessories
```

---

## Task 1: Add the new tab shell

**Objective:** Add `Bulk Drop Audit` as a new Streamlit tab without changing existing bulk editor behavior.

**Files:**
- Modify: `japanese-invoice-transcriber/app.py`

**Implementation notes:**
- Add a placeholder function near the other Shopify tab render functions:

```python
def render_bulk_drop_audit_tab():
    st.subheader("Bulk Drop Audit")
    st.caption("Generate Shopify-ready bag and accessory descriptions from SKU, title, and first image. Drafts only until approved.")
    st.info("Paste SKUs to begin. This workflow only updates approved product descriptions.")
```

- Update tabs:

```python
home_tab, catalogue_tab, drop_audit_tab, bulk_tab, copy_tab, pricing_tab, knowledge_tab = st.tabs([
    "Invoices",
    "Shopify audit",
    "Bulk Drop Audit",
    "Shopify bulk editor",
    "Copy formats",
    "Pricing",
    "Notes & rules",
])
```

- Render it:

```python
with drop_audit_tab:
    render_bulk_drop_audit_tab()
```

**Verify:**
Run the Streamlit app locally and confirm the new tab appears, existing tabs still render.

---

## Task 2: Add SKU input and normalization

**Objective:** Let Kat paste SKUs and normalize them into a clean unique list.

**Files:**
- Modify: `japanese-invoice-transcriber/app.py`

**Behavior:**
- Text area accepts one SKU per line, commas also tolerated.
- Trim whitespace.
- Remove duplicates.
- Preserve original order.
- Show count and parsed list preview.

**Acceptance criteria:**
- Empty input shows a calm instruction.
- Duplicate SKUs are only looked up once.
- No Shopify calls happen until Kat clicks `Lookup SKUs`.

---

## Task 3: Reuse/add Shopify lookup by SKU

**Objective:** Fetch Shopify products by variant SKU.

**Files:**
- Modify: `japanese-invoice-transcriber/app.py`

**Implementation notes:**
- Search existing Shopify bulk editor helpers before adding new ones.
- Prefer reusing current authenticated Shopify Admin API request helper.
- Add a dedicated helper only if needed:

```python
def lookup_shopify_products_by_skus(skus: list[str]) -> list[DropAuditProduct]:
    ...
```

Returned fields should include:

```text
sku
title
body_html / description
price
status
product_type
tags
first_image_url
product_id
variant_id
admin_url
```

**Acceptance criteria:**
- Works for multiple SKUs.
- Missing SKUs are shown as row-level errors.
- Existing descriptions are detected.

---

## Task 4: Add bag/accessory eligibility checks

**Objective:** Prevent clothing/non-accessory products from entering generation.

**Files:**
- Modify: `japanese-invoice-transcriber/app.py`

**Rules:**
Eligible if Shopify product type/tags/title clearly indicate:

```text
bag
handbag
purse
wallet
accessory
accessories
belt
scarf
sunglasses
jewelry
```

Flag otherwise:

```text
Not eligible: only bags and accessories are supported.
```

**Acceptance criteria:**
- Non-eligible rows remain visible but generation checkbox is disabled.

---

## Task 5: Build the review table

**Objective:** Show pulled Shopify data and generation status clearly.

**Files:**
- Modify: `japanese-invoice-transcriber/app.py`

**Columns/sections:**

```text
SKU
Image
Title
Current description status
Price
Product status
Eligibility
Generation status
Warnings
Admin link
```

**Status values:**

```text
Needs description
Has description
Missing image
Not eligible
Ready to generate
Draft ready
Approved
Pushed to Shopify
Error
```

**Acceptance criteria:**
- Existing descriptions are not selected by default.
- Blank description rows with images and eligible category are selected by default.

---

## Task 6: Add online exact-listing lookup for dimensions/details

**Objective:** Find exact matching online listings for the same bag/accessory before generating final description.

**Files:**
- Modify: `japanese-invoice-transcriber/app.py`
- Optional create: `japanese-invoice-transcriber/drop_audit.py` if `app.py` gets too large.

**Search input:**

```text
product title only
```

**Preferred sources:**

```text
Fashionphile
The RealReal
Rebag
Vestiaire Collective
1stDibs
Brand archive/product pages
Clearly matching resale listings
```

**Output:**

```python
{
    "dimensions": "..." or None,
    "material": "..." or None,
    "details": [...],
    "sources": [{"title": "...", "url": "..."}],
    "warnings": [...]
}
```

**Hard rule:**
If exact measurements cannot be verified, return `None` and show `DIMENSIONS: Needs review`. Do not infer from image.

---

## Task 7: Add Anthropic description generation

**Objective:** Generate Shopify-ready Past Studies descriptions from title, first image, and verified lookup facts.

**Files:**
- Modify: `japanese-invoice-transcriber/app.py`
- Optional create: `japanese-invoice-transcriber/drop_audit.py`

**AI inputs allowed:**

```text
Product title
First product image
Verified online lookup facts/sources for dimensions/details
Past Studies template/tone rules
```

Note: Kat specifically requested title + first image for generation. Online lookup is only to verify exact measurements/details, not to add noisy Shopify metadata.

**Output format:**

```text
DIMENSIONS:
[exact verified dimensions or Needs review]

DETAILS:
[line]
[line]
[line]

MATERIAL:
[verified/visible material or Needs review]

CONDITION NOTES:
[grade] – [specific condition notes]
```

**Acceptance criteria:**
- Output is plain and professional.
- No flowery copy.
- No invented exact measurements.
- Warnings are shown when confidence is low.

---

## Task 8: Draft editing and approval UI

**Objective:** Let Kat review and edit drafts before any Shopify write.

**Files:**
- Modify: `japanese-invoice-transcriber/app.py`

**UI controls:**

```text
Generate selected drafts
Regenerate row
Editable text area per draft
Approve checkbox
Approve selected
```

**Acceptance criteria:**
- Drafts are stored in `st.session_state`.
- Manual edits persist while navigating/rerunning.
- Nothing pushes to Shopify from this task.

---

## Task 9: Push approved descriptions to Shopify

**Objective:** Update only approved product descriptions.

**Files:**
- Modify: `japanese-invoice-transcriber/app.py`

**Rules:**
- Only approved rows can be pushed.
- Default refuses to overwrite nonblank descriptions.
- If overwrite is ever allowed, require an explicit row-level checkbox.
- Use existing product body HTML update helper if available. Prior code around `app.py:5688-5690` uses `update_product_body_html`.

**Acceptance criteria:**
- Push reports succeeded/failed rows.
- Failed rows keep drafts and errors visible.
- Successful rows update status to `Pushed to Shopify`.

---

## Task 10: Add audit log

**Objective:** Save a local record of every approved write.

**Files:**
- Modify: `japanese-invoice-transcriber/app.py`
- Create directory if absent: `japanese-invoice-transcriber/logs/`

**Log fields:**

```text
timestamp
sku
product_id
variant_id
title
old_description_present
new_description
sources_used
warnings
```

**Acceptance criteria:**
- Every Shopify write creates an append-only log entry.
- No API keys/secrets are logged.

---

## Task 11: Final verification

**Objective:** Confirm the feature is safe before Kat uses it on live products.

**Checks:**
1. Lookup known SKU with existing description, confirm it is not selected by default.
2. Lookup known SKU with blank description, confirm draft generation works.
3. Confirm non-bag/accessory SKU is blocked.
4. Confirm missing image blocks generation.
5. Confirm generated copy matches Past Studies template.
6. Confirm pushing only works after approval.
7. Confirm failed Shopify writes show errors and do not clear drafts.
8. Confirm audit log writes without secrets.

---

## Open implementation decision

Before coding the full feature, decide whether online lookup should use:

```text
A. Anthropic web/reverse lookup if available in the existing environment
B. A separate search API/scraper helper
C. Manual "source links" field for v1, then automated lookup in v2
```

Preferred practical v1 if web lookup is not already wired in:

```text
Generate draft + provide Needs review for dimensions when no verified source is available.
```

That avoids fake measurements.

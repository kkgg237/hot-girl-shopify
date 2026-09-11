# Crop-pipeline rules

Log of tuning decisions and behavioral rules for the crop pipeline. Newest at top.

The pipeline is deterministic math + YAML config, not a prompt. "Rules" land in
one of four layers — touch the smallest one that fixes the problem:

| Kind of feedback | Edit |
|---|---|
| Per-slot framing (this shot's headroom, detail zoom) | `templates/*.yaml` |
| Whole-pipeline default (output size, JPEG quality, fallback threshold) | `crop_pipeline/options.py` |
| Crop-window behavior (sliding, aspect enforcement, fallback) | `crop_pipeline/crop.py` |
| Shoot-order assumption (which source frame feeds which slot) | `source_shot:` in template + comment block at top of YAML |

## Memorialization loop

When tuning in response to feedback ("this shot needs more headroom", "we're
losing the hip", "detail is cutting the collar"):

1. **Find the smallest layer that fixes it.** Don't shift a default that
   affects every shot to fix one slot.
2. **Make the change.**
3. **Add or update a test.** Pin the *invariant* ("detail crop includes the
   collar region"), not the magic number — so we can re-tune without churning
   the suite. `tests/test_crop.py` for math; add `tests/test_templates.py` for
   template-level invariants.
4. **Append a one-line entry to this file** (top). Format:
   `YYYY-MM-DD — rule — why`.
5. **Eyeball one re-cropped SKU** against the previous output before calling it
   done.

## Rules

- 2026-08-13 — **CONDENSED ECOMMERCE CROPPING & BACKGROUND RULE SHEET BY CATEGORY.**

  ### 1. TOPS (Shirts, Blouses, Jackets, Sweaters, Coats)
  - **Full-Body Slots (`01_hero`, `02_3/4`, `03_back`)**:
    - **Background**: Pure Studio White Equalization (`#FFFFFF`, `curve_gamma = 0.50`, `highlight_lift = 1.30`).
    - **Headroom & Placement**: **8% Top Headroom** (`headroom = 0.08`, Y = 164px). Model fills **84% canvas height**. Horizontally centered.
    - **Edge Protection**: Zero Model Cutout (`extend_photo_edges_seamless`). Replicates outer background pixels with 30px horizontal seam blend (100% crisp hair & fabric).
  - **Detail Slot (`04_detail`)**:
    - **Framing**: **Garment Item-Focus Crop** (shoulders/collar to bottom hemline with 5% top margin above collar). Full 3:4 canvas (`1536x2048`).

  ### 2. BOTTOMS (Pants, Jeans, Skirts, Shorts)
  - **Full-Body Slots (`01_hero`, `02_3/4`, `03_back`)**:
    - **Background**: Pure Studio White Equalization (`#FFFFFF`).
    - **Placement**: Bottoms Studio Framing (78% fill height, centered vertically in 3:4 canvas).
    - **Edge Protection**: Zero Model Cutout Edge Extension (seamless canvas padding).
  - **Detail Slot (`04_detail`)**:
    - **Framing**: **Garment Item-Focus Crop (Image #1 Standard)** (waistband down to heels/shoes with 5% top margin above waistband). Full 3:4 canvas (`1536x2048`).

  ### 3. DRESSES, JUMPSUITS & ONE-PIECE OUTERWEAR
  - **Full-Body Slots (`01_hero`, `02_3/4`, `03_back`)**:
    - **Background**: Pure Studio White Equalization (`#FFFFFF`).
    - **Headroom & Placement**: **8% Top Headroom** (Y = 164px). Model fills **84% canvas height**. Horizontally centered.
    - **Edge Protection**: Zero Model Cutout Edge Extension.
  - **Detail Slot (`04_detail`)**:
    - **Framing**: **Upper-Torso & Bodice Focus** (neckline/bust to hip line with 5% top headroom). Full 3:4 canvas (`1536x2048`).

  ### 4. HANDBAGS & TABLETOP ACCESSORIES
  - **Mental Model**: **The Retail Display Shelf Rule** (grid functions as a single boutique glass shelf).
  - **All Slots (`01_hero`, `02_angle`, `03_back`, `04_interior`, `05_detail`)**:
    - **Background & Shadow**: Soft 20 Exposure & Shadow Retention (`curve_gamma = 0.78`, `highlight_lift = 1.06`). Preserves 100% of authentic tabletop contact shadow (`L < 160`).
    - **Horizontal Margin**: **20% Side Margins** (Left = 20%, Right = 20%, Subject Width = 60% of canvas width = 921px). Horizontally centered.
    - **Vertical Baseline**: **82% Shelf Baseline Grounding** (bottom base of bag rests at $Y_{base} = 0.82 \times \text{canvas\_h}$).

- 2026-08-13 — **Garment Item-Focus Crop (Detail Slot Framing Rule).**
  Specification for the 4th/detail image slot across apparel listings:
  - **Terminology ("Garment Item-Focus Crop")**: Rather than an extreme thread-level fabric zoom, the 4th slot focuses on the **entirety of the garment piece** in high detail.
  - **Bottoms Framing (Pants / Skirts)**: Crops from just above the waistband (`waistband_y - 5%`) down to the shoes/heels, filling the 3:4 canvas with the complete lower-half pants silhouette (matches Image #1 standard).
  - **Tops Framing (Shirts / Jackets)**: Crops from shoulders/collar down to the bottom hemline (`shoulders_y - 5%` to `hemline_y + 10%`), filling the 3:4 canvas with the top garment.
  - **Background & Sharpness**: 100% full-resolution sensor pixels (zero upscaling loss) combined with Pure White Background Equalization (`#FFFFFF`).
  - Validated on Issey Miyake Pleated Pants (`ISS_2605_02`) and Brunello Cucinelli Top (`BRU_2605_010`).

- 2026-08-13 — **Pure Studio White Background Equalization (#FFFFFF).**
  Standard background fixing specification across all human model & apparel photography:
  - **Pure White Background Target (#FFFFFF)**: Lifts dark studio wall shadows, color casts, and backdrop gradients all the way to **pure #FFFFFF white**.
  - **High-Fidelity Subject Mask Protection**: Segmentation mask (`rembg` / `BiRefNet`) protects 100% of the model, face, skin tones, hair strands, tattoos, and clothing. Zero white curve bleeding onto the subject.
  - **Exposure Curve**: LAB L-channel exposure curve (`curve_gamma = 0.50`, `highlight_lift = 1.30`) applied strictly to background selection (`alpha < 128`).
  - Matches reference standard: `cavalli_orange_seamless_pure_white.jpg`.

- 2026-08-13 — **Human Form / Model Rule: 8% Top Headroom + Soft 30 Background Equalization + Zero Cutout Edge Extension.**
  Universal specification for human model / mannequin clothing listings on 3:4 canvas (`1536x2048`):
  - **8% Top Headroom & 84% Fill Height**: Model's head top aligns at **8% top headroom** (`headroom = 0.08`, Y = 164px). Model height occupies **84% of canvas height** (`fill_h = 0.84`). Horizontally centered.
  - **Soft 30 Background Equalization**: Apply Photoshop LAB L-channel exposure curve (`curve_gamma = 0.85`, `highlight_lift = 0.98`) targeting pure `#FFFFFF`. Smoothly lifts dark studio shadow bands behind the model while keeping natural fabric and skin tones intact.
  - **Zero Model Cutout (Crisp Edge Protection)**: NEVER cut out or mask model/garment edges with segmentation masks. Replicate outer background pixel columns and blend seams horizontally (30px blend zone) to fill 3:4 canvas width (`1536px`) with ZERO box border and ZERO model hair/skin fuzziness.
  - Validated on Cavalli Jeans Orange Mesh Top (`ROB_2604_943`) and Pleats Please Floral Tank.

- 2026-08-13 — **Human Form / Model Rule: Zero-Cutout Outer Edge Extension (No Fuzzy Model Edges).**
  When standardizing 3:4 canvas aspect ratio (1536x2048) on human model / mannequin shots:
  - **Zero Model Cutout**: NEVER cut out or mask the model/garment with segmentation alpha masks — preserves 100% crisp hair strands, skin texture, tattoos, and fabric edges.
  - **Outer Edge Extension**: Replicate the outer left and outer right background pixel columns of the original photo outward into the canvas margin padding.
  - **Seamless Seam Blend**: Apply a 30px horizontal Gaussian gradient blend at the left & right outer photo boundary seams so there is ZERO box border or visible rectangular line.
  - Validated on Cavalli Jeans Orange Mesh Top (`ROB_2604_943`) and Pleats Please Floral Tank.

- 2026-08-13 — **The Retail Display Shelf Rule: 20% Side Margin + Baseline Grounding (Standardized Framing).**
  Universal mental model and framing specification across all handbag shapes (wide flap bags, tall totes, medium hobos):
  - **Mental Model ("The Retail Display Shelf")**: Treats the collection grid as a single physical boutique glass display shelf. Every item rests on the same baseline shelf line with equal horizontal clearance.
  - **Horizontal Centering & 20% Margin**: Fixes left & right side margins to exactly **20% canvas width** (`side_margin_pct = 0.20`, subject width = 60% of 1536px canvas = `921px`). Horizontally centered.
  - **Vertical Baseline Grounding**: Bottom base of every bag rests on a fixed shelf baseline at **82% canvas height** (`base_y_pct = 0.82`). Height scales naturally to preserve true real-life aspect ratio.
  - **Background & Exposure**: Soft 20 Exposure Preset (`curve_gamma = 0.78`, `highlight_lift = 1.06`) targeting pure white `#FFFFFF` while preserving authentic tabletop contact shadows (`L < 160`).
  - Validated across Chanel Pink Flap (`CHA_pink`), LV Sac Plat Tote (`LOU_2604_358`), and Bottega Hobo (`BOT_2606_743`).

- 2026-08-13 — **Photoshop-style background exposure lift (Soft 20) with real tabletop shadow retention.**
  When processing studio tabletop product shots (handbags, leather goods, small accessories) with uneven lighting falloff or vignetting:
  - **Method**: Select subject via AI segmentation (`rembg` / `BiRefNet`), invert mask to target background only, apply a 2px Gaussian feather to mask edges.
  - **Background Curves / Exposure**: Apply a LAB L-channel exposure curve targeting pure `#FFFFFF` with a **20% softer exposure lift** (`curve_gamma = 0.78`, `highlight_lift = 1.06`).
  - **Shadow Retention**: Highlights/midtones (>210) smoothly converge to pure `#FFFFFF`, while dark tabletop contact shadows (L < 160) are 100% protected and preserved under the base of the item.
  - **Canvas & Padding**: Frame the subject at **68% fill height / 70% fill width** on a 3:4 canvas (`1536x2048`), centered horizontally and vertically, extending canvas margins with `#FFFFFF`.
  - **Selected preset**: Soft 20 (`v20_soft`). Validated against Bottega Intrecciato Hobo (`BOT_2606_743`) and Chanel White CC Shoulder Bag (`CHA_2606_172`).

_(Seeded from current values + code comments; dates approximate the file's
last-modified times. Update as new feedback comes in.)_

- 2026-06-02 — **Contiguous shot ranges auto-normalize to start at 1.** If a
  SKU's non-zero shot indices form a contiguous range starting at N>1
  (e.g. ISS_2605_011 numbered 9–17 from Capture One's continuous session
  counter), `group_by_sku` renumbers them so the smallest becomes shot 1
  — the photographer's positional intent (smallest = hero, etc.) is
  preserved. Non-contiguous ranges (e.g. ISS_2605_02 with shots 1–6, 8)
  are NOT normalized — the gap is treated as a real missing shot so the
  back slot legitimately fails to find shot 7. The unnumbered safety file
  (shot 0) is never renumbered. Normalization fires `on_normalize(sku,
  mapping)` and the CLI surfaces it as a `normalized SKU shot indices:
  9->1, 10->2, ...` line — never silent. `tests/test_grouping.py` pins
  the contiguous/non-contiguous distinction; the only thing this rescues
  is the "Capture One didn't reset between SKUs" case, not arbitrary
  reordering. For genuinely non-standardized shoots, pose detection (next
  step) is the right answer; this rule is the cheap fallback.
- 2026-06-02 — **Tops template aligned with listing-standard conventions.**
  Dropped the angled zoom (`03_top_three_quarter_angled`) for the same
  reason listing-standard dropped its side slot — shot 4 turns the same
  direction as shot 1 in the current shoot, so the angled zoom reads as a
  duplicate of the front zoom. Standardized `01_full_body` to `(0.80, 0.0)`
  so it matches listing-standard's hero — the subject now lands in the same
  spot across category templates. New tests in
  `tests/test_templates.py`:
  `test_listing_tops_full_body_matches_listing_standard_framing`,
  `test_listing_tops_has_no_angled_slot`,
  `test_listing_tops_zoom_slots_share_region`. Tune full-body framing in
  both templates together — the cross-template test will catch a drift.
- 2026-06-02 — **Detail slot stays at native crop dimensions.** Don't add
  `allow_upscale: True` to `04_detail` or any future zoom slot. Every output
  pixel comes from real source pixels; if the source crop is smaller than
  1536×2048 (currently 947×1264 for this shoot), the output is smaller too —
  that's intentional. Shopify resizes for display anyway; the bigger concern
  is preserving fabric/texture clarity which interpolation softens. The
  variance in output dimensions across the 4-shot set is a known accepted
  trade-off.
- 2026-05-31 — **Redundant downloads don't shift the shot list.**
  `group_by_sku` now deduplicates files that map to the same `(sku, shot)`
  (e.g. the same shot exported as both `.jpg` and `.jpeg`, or with both
  space and dash separators) — the first file in sorted order is kept,
  the rest emit an `on_duplicate` callback that the CLI surfaces as a
  warning line. Before this, `pipeline.py` did `{p.shot: p for p in items}`
  and the last-inserted file silently won, so re-downloading a SKU could
  flip which file was used without telling the user. Finder/browser dupes
  like `X 1 2.jpg` / `X 1 (1).jpg` parse as orphan SKUs and remain
  harmless. `tests/test_grouping.py` pins the dedup invariants.
- 2026-05-31 — **Dropped `03_side` slot; listing is now 4 shots.** Shots
  3/4/5 in the current shoot all turn the same direction, so pure-side and
  3/4-turn read as redundant orientations. Kept the 3/4 (more informative for
  clothing — shows shape better than a flat profile). Renumbered: hero / 3/4
  / back / detail. If a future shoot captures a true opposite-direction 3/4,
  reintroduce it as `03_three_quarter_right` between 02 and back.
  `tests/test_templates.py::test_listing_standard_has_no_side_slot` guards
  against silent re-addition.
- 2026-05-31 — **Full-body slots share one framing.** Hero, 3/4, and back
  all use `subject_height_fraction: 0.80` and `vertical_bias: 0.0` so the
  subject lands in the same spot across the set — a listing shouldn't feel
  like a grab-bag of different zooms. Previous template tuned each slot
  independently (`0.86 / -0.07` for non-hero) which made the 3/4, side, and
  back read tighter than the hero. Detail (`04_detail`) is exempt — it's a
  zoom, different category. `tests/test_templates.py` asserts the full-body
  slots stay in sync; tune them together or not at all.
- 2026-05-31 — **Don't use `--remove-background` / `--match-reference` on
  these shoots.** rembg leaves a fuzzy halo around loose hair strands and the
  edges of the subject — visually worse than just keeping the raw studio
  background. The `--match-reference` preset and shadow synthesis stay in the
  code for future use (e.g. a real cyc shoot where the alpha mask is clean)
  but are not the default workflow. Default flow is plain crop, no bg
  removal.
- 2026-05-31 — Reference-match recipe = `--match-reference` CLI flag, which
  sets background to warm off-white `(248, 242, 242)` (sampled from the
  reference cyc) and enables a soft synthesized drop shadow under the
  subject's feet — anchors the figure so it doesn't float on pure white.
  Shadow defaults: `opacity=0.18`, `blur=36px`, `squash=0.04` of subject
  height. Thin band + heavy blur reads as a soft anchor halo rather than a
  defined silhouette; the previous tuning (`squash=0.08`, `blur=24`) looked
  like a hovering blob below the feet. **Superseded above — keeping note for
  history; do not enable for the current shoot style.**
- 2026-05-29 — `RULES.md` workflow established — without a log, tuning
  constants drift and the why behind each number is lost; future sessions
  follow the loop above instead of editing constants ad-hoc.
- 2026-05-27 — Detail slot crops `region_of_subject: [0.08, 0.55]` with
  `region_fill: 0.92` — chest/torso zoom showing fabric, collar, button line;
  starts just below the chin, ends mid-thigh.
- 2026-05-27 — Hero (01) frames at `subject_height_fraction: 0.80`,
  `vertical_bias: 0.0` — full body, centered, no downward tuck (hero shot
  reads cleaner balanced in frame).
- 2026-05-27 — 3/4, side, back (02–04) frame at `subject_height_fraction: 0.86`,
  `vertical_bias: -0.07` — tighter and tucked higher so feet land near the
  bottom of the frame; matches the reference shoot.
- 2026-05-27 — Default `rembg_model: isnet-general-use` — cleaner edges on
  people than u2net/u2netp in the reference set.
- 2026-05-27 — Default `allow_upscale: False` — every output pixel must come
  from real source pixels; accept variable output dims when the source is
  smaller than the target rather than upsampling.
- 2026-05-27 — `min_subject_fraction: 0.05` — if the detected subject box is
  less than 5% of frame height, fall back to a centered crop (detection is
  presumed broken).
- 2026-05-27 — Pipeline default `subject_height_fraction: 0.82`,
  `vertical_bias: -0.04` — calibrated from the reference shots, which
  average ~83% subject height with a slight downward bias across
  front/side/back poses. Templates override per-slot.
- 2026-05-27 — Default `output_size: 1536x2048` (3:4) — matches reference
  shoot dimensions.

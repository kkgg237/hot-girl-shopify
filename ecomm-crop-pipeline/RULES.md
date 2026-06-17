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

from pathlib import Path

from crop_pipeline.grouping import group_by_sku, parse_filename


def test_capture_one_space_pattern():
    p = parse_filename(Path("BRU_2605_002 3.jpg"))
    assert p is not None
    assert p.sku == "BRU_2605_002"
    assert p.shot == 3


def test_capture_one_dash_pattern():
    p = parse_filename(Path("BRU_2605_001-1.jpg"))
    assert p is not None
    assert p.sku == "BRU_2605_001"
    assert p.shot == 1


def test_unnumbered_is_shot_zero():
    p = parse_filename(Path("BRU_2605_002.jpg"))
    assert p is not None
    assert p.sku == "BRU_2605_002"
    assert p.shot == 0


# --- redundant-download / dedup invariants ---------------------------------
#
# These pin behavior under the kinds of "extra files in the folder" that
# happen when SKUs are downloaded / synced / re-exported. The rule: the shot
# list stays stable. Same shot exported twice → one wins deterministically
# and the user is told. Junk filenames don't pollute the canonical SKU.


def _make_folder(tmp_path, names):
    for n in names:
        (tmp_path / n).write_bytes(b"")
    return tmp_path


EXTS = (".jpg", ".jpeg", ".png")


def test_duplicate_shot_index_dedupes_and_warns(tmp_path):
    """Same shot exported with both separators -> one ParsedName wins,
    on_duplicate fires for the other."""
    _make_folder(tmp_path, ["BRU_2605_010 1.jpg", "BRU_2605_010-1.jpg"])
    seen = []
    groups = group_by_sku(tmp_path, EXTS, on_duplicate=lambda *a: seen.append(a))
    assert list(groups) == ["BRU_2605_010"]
    items = groups["BRU_2605_010"]
    assert len(items) == 1, f"expected dedup to 1 ParsedName, got {[i.source.name for i in items]}"
    assert items[0].shot == 1
    assert len(seen) == 1
    sku, shot, kept, dropped = seen[0]
    assert (sku, shot) == ("BRU_2605_010", 1)
    # Sort order: space (0x20) < dash (0x2D), so the space-version is kept.
    assert kept.name == "BRU_2605_010 1.jpg"
    assert dropped.name == "BRU_2605_010-1.jpg"


def test_duplicate_extension_dedupes(tmp_path):
    """jpg + jpeg of the same shot -> deduped to one."""
    _make_folder(tmp_path, ["BRU_2605_010 1.jpg", "BRU_2605_010 1.jpeg"])
    groups = group_by_sku(tmp_path, EXTS)
    items = groups["BRU_2605_010"]
    assert len(items) == 1
    assert items[0].shot == 1


def test_finder_dupe_does_not_pollute_canonical_sku(tmp_path):
    """``X 1 2.jpg`` (the macOS Finder duplicate) parses as a different SKU
    (``X 1`` shot 2) and never gets folded into the real SKU's shot list."""
    _make_folder(tmp_path, [
        "BRU_2605_010.jpg",         # shot 0
        "BRU_2605_010 1.jpg",       # shot 1
        "BRU_2605_010 1 2.jpg",     # Finder dupe — different SKU
    ])
    groups = group_by_sku(tmp_path, EXTS)
    canonical = groups["BRU_2605_010"]
    shots = sorted(p.shot for p in canonical)
    assert shots == [0, 1]
    # The dupe forms its own orphan group; harmless because the template's
    # source_shot indices won't line up.
    assert "BRU_2605_010 1" in groups


def test_browser_dupe_does_not_pollute_canonical_sku(tmp_path):
    """``X 1 (1).jpg`` (browser copy) parses as its own orphan SKU."""
    _make_folder(tmp_path, [
        "BRU_2605_010 1.jpg",
        "BRU_2605_010 1 (1).jpg",
    ])
    groups = group_by_sku(tmp_path, EXTS)
    assert sorted(p.shot for p in groups["BRU_2605_010"]) == [1]


def test_extra_real_shot_does_not_break_canonical_set(tmp_path):
    """A future label close-up (e.g. shot 9) stays in the SKU's list but is
    only consumed if a template slot references it."""
    _make_folder(tmp_path, [
        "BRU_2605_010 1.jpg",
        "BRU_2605_010 4.jpg",
        "BRU_2605_010 7.jpg",
        "BRU_2605_010 9.jpg",   # extra
    ])
    groups = group_by_sku(tmp_path, EXTS)
    assert sorted(p.shot for p in groups["BRU_2605_010"]) == [1, 4, 7, 9]


def test_hidden_files_ignored(tmp_path):
    """.DS_Store and other dotfiles never enter the grouping."""
    _make_folder(tmp_path, [".DS_Store", "BRU_2605_010 1.jpg"])
    groups = group_by_sku(tmp_path, EXTS)
    assert list(groups) == ["BRU_2605_010"]


# --- contiguous-range normalization (Capture One counter offset) ----------
#
# When Capture One's session counter doesn't reset per SKU, a later SKU's
# files end up numbered (say) 9–17 instead of 1–9. The positional intent
# is preserved (smallest numbered = hero, etc.) so we normalize. We do NOT
# normalize when the indices have gaps — a missing shot is a real gap, not
# a counter offset.


def test_contiguous_offset_normalized(tmp_path):
    """ISS_011-style case: shots 9..17 -> 1..9, on_normalize fires."""
    names = [f"ISS_2605_011 {i}.jpg" for i in range(9, 18)]
    _make_folder(tmp_path, names)
    seen = []
    groups = group_by_sku(tmp_path, EXTS, on_normalize=lambda sku, m: seen.append((sku, dict(m))))
    items = groups["ISS_2605_011"]
    assert sorted(p.shot for p in items) == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert seen == [("ISS_2605_011", {9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 6, 15: 7, 16: 8, 17: 9})]


def test_canonical_range_is_no_op(tmp_path):
    """Shots 1..8 stay as 1..8, on_normalize does NOT fire."""
    names = [f"BRU_2605_010 {i}.jpg" for i in range(1, 9)]
    _make_folder(tmp_path, names)
    seen = []
    groups = group_by_sku(tmp_path, EXTS, on_normalize=lambda sku, m: seen.append((sku, dict(m))))
    items = groups["BRU_2605_010"]
    assert sorted(p.shot for p in items) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert seen == []


def test_noncontiguous_range_is_not_normalized(tmp_path):
    """ISS_02-style: shots 1..6, 8 (missing 7) stays as-is — the gap is
    real and the back slot must legitimately fail to find shot 7."""
    names = [f"ISS_2605_02 {i}.jpg" for i in [1, 2, 3, 4, 5, 6, 8]]
    _make_folder(tmp_path, names)
    seen = []
    groups = group_by_sku(tmp_path, EXTS, on_normalize=lambda sku, m: seen.append((sku, dict(m))))
    items = groups["ISS_2605_02"]
    assert sorted(p.shot for p in items) == [1, 2, 3, 4, 5, 6, 8]
    assert seen == [], "non-contiguous range must not be silently renumbered"


def test_unnumbered_safety_frame_never_normalized(tmp_path):
    """The unnumbered file (shot 0) stays at shot 0 even when other shots
    are normalized — it conventionally means 'safety frame', not 'first.'"""
    names = ["ISS_2605_011.jpg"] + [f"ISS_2605_011 {i}.jpg" for i in range(9, 18)]
    _make_folder(tmp_path, names)
    groups = group_by_sku(tmp_path, EXTS)
    items = groups["ISS_2605_011"]
    assert sorted(p.shot for p in items) == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    # Shot 0 is the unnumbered original
    zero = next(p for p in items if p.shot == 0)
    assert zero.source.name == "ISS_2605_011.jpg"


def test_single_frame_sku_not_normalized(tmp_path):
    """A SKU with only one non-zero shot has nothing to normalize against —
    no rule fires, no offset is invented."""
    _make_folder(tmp_path, ["SKU_X 5.jpg"])
    groups = group_by_sku(tmp_path, EXTS)
    items = groups["SKU_X"]
    assert [p.shot for p in items] == [5]

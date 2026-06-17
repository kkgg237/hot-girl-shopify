"""Unit tests for bulk_measurements — no network, no Claude.

Exercises the deterministic pieces: column mapping against the real Catchup
header, per-row extraction, template routing for mixed garment types, and the
HTML renderer. Product lookup is injected via a fake so plan_rows is testable.
"""
from __future__ import annotations

import bulk_measurements as bm
from bulk_measurements import ColumnMap, ProductLookup
from heuristics.loader import load_description_templates


# The real header from 2026_Ecomm_Measurements - Catchup.csv (col 0 is unnamed).
REAL_HEADERS = ["", "Barcode", "Tagged Size", "Chest ", "Length",
                "Waist", "Hips", "Inseam", "Rise", "notes"]


def test_heuristic_maps_real_catchup_header():
    cm = bm.map_columns_heuristic(REAL_HEADERS)
    assert cm.barcode_col == "Barcode"
    assert cm.title_col == ""               # unnamed first column = title
    assert cm.tagged_size_col == "Tagged Size"
    assert cm.notes_col == "notes"
    # Trailing-space header still resolves; all six measurements map.
    assert cm.measurement_map["Chest "] == "CHEST"
    assert cm.measurement_map["Length"] == "LENGTH"
    assert cm.measurement_map["Waist"] == "WAIST"
    assert cm.measurement_map["Hips"] == "HIPS"
    assert cm.measurement_map["Inseam"] == "INSEAM"
    assert cm.measurement_map["Rise"] == "RISE"
    ok, reason = cm.is_usable()
    assert ok, reason


def test_alias_variants_map():
    cm = bm.map_columns_heuristic(["UPC", "Bust", "Pit to Pit", "Sleeve Length", "Shoulder Seam"])
    assert cm.barcode_col == "UPC"
    # First chest-like column wins; both Bust and Pit to Pit resolve to CHEST.
    assert cm.measurement_map["Bust"] == "CHEST"
    assert cm.measurement_map["Sleeve Length"] == "SLEEVE"
    assert cm.measurement_map["Shoulder Seam"] == "SHOULDER"


def test_extract_row_pulls_values():
    cm = bm.map_columns_heuristic(REAL_HEADERS)
    raw = {
        "": "Issey Miyake Pleats Please Green Long Sleeve Top",
        "Barcode": "96787476", "Tagged Size": "3",
        "Chest ": "18", "Length": "22", "Waist": "", "Hips": "",
        "Inseam": "", "Rise": "", "notes": "",
    }
    mr = bm.extract_row(raw, cm)
    assert mr.barcode == "96787476"
    assert mr.tagged_size == "3"
    assert mr.measurements == {"CHEST": "18", "LENGTH": "22"}
    assert "Long Sleeve Top" in mr.title


def test_choose_template_routes_by_title():
    templates = load_description_templates()
    top = bm.choose_template("", "Issey Miyake Green Long Sleeve Top", templates)
    skirt = bm.choose_template("", "D&G Light Blue Denim Midi Skirt", templates)
    dress = bm.choose_template("", "Issey Miyake Pleats Please Grey Midi Dress", templates)
    assert top and top.name == "Tops"
    assert skirt and skirt.name == "Bottoms"
    assert dress and dress.name == "Dresses"


def test_choose_template_prefers_category():
    templates = load_description_templates()
    # Title says "Top" but the Shopify category says Skirts -> category wins.
    tpl = bm.choose_template("Skirts", "Mislabeled Top", templates)
    assert tpl and tpl.name == "Bottoms"


def test_render_tops_fills_only_its_fields():
    templates = load_description_templates()
    tops = next(t for t in templates if t.name == "Tops")
    html = bm.render_template(
        tops, tagged_size="3",
        measurements={"CHEST": "18", "LENGTH": "22", "WAIST": "99"},  # WAIST ignored
        notes="small mark on hem",
    )
    assert "<strong>TAGGED SIZE:</strong> 3</p>" in html
    assert '<td><strong>CHEST</strong></td><td>18"</td>' in html
    assert '<td><strong>LENGTH</strong></td><td>22"</td>' in html
    assert "WAIST" not in html              # Tops template has no WAIST row
    assert "small mark on hem" in html


def test_render_bottoms_fields():
    templates = load_description_templates()
    bottoms = next(t for t in templates if t.name == "Bottoms")
    html = bm.render_template(
        bottoms, tagged_size="44",
        measurements={"WAIST": "22", "HIPS": "30", "INSEAM": "40"},
    )
    assert '<td><strong>WAIST</strong></td><td>22"</td>' in html
    assert '<td><strong>HIPS</strong></td><td>30"</td>' in html
    assert '<td><strong>INSEAM</strong></td><td>40"</td>' in html
    # Untouched fields stay blank.
    assert '<td><strong>RISE</strong></td><td></td></tr>' in html


def test_render_no_unit_for_non_numeric_and_escapes():
    templates = load_description_templates()
    tops = next(t for t in templates if t.name == "Tops")
    html = bm.render_template(
        tops, tagged_size="M & L",
        measurements={"CHEST": "approx 20"}, unit='"',
    )
    assert "M &amp; L" in html               # escaped
    assert '<td><strong>CHEST</strong></td><td>approx 20</td>' in html  # no unit appended


def test_render_decimal_keeps_unit():
    templates = load_description_templates()
    tops = next(t for t in templates if t.name == "Tops")
    html = bm.render_template(tops, measurements={"LENGTH": "21.5"})
    assert '<td><strong>LENGTH</strong></td><td>21.5"</td>' in html


def _fake_lookup(by_barcode: dict[str, ProductLookup]):
    def _fn(barcode="", sku="", **_):
        return by_barcode.get(barcode.strip(), ProductLookup(found=False))
    return _fn


def test_plan_rows_actions():
    templates = load_description_templates()
    headers = REAL_HEADERS
    rows = [
        {"": "Issey Miyake Long Sleeve Top", "Barcode": "111", "Tagged Size": "3",
         "Chest ": "18", "Length": "22", "Waist": "", "Hips": "", "Inseam": "",
         "Rise": "", "notes": ""},
        {"": "Unknown Item", "Barcode": "999", "Tagged Size": "M",
         "Chest ": "10", "Length": "", "Waist": "", "Hips": "", "Inseam": "",
         "Rise": "", "notes": ""},
        {"": "Has Description Already", "Barcode": "222", "Tagged Size": "S",
         "Chest ": "16", "Length": "20", "Waist": "", "Hips": "", "Inseam": "",
         "Rise": "", "notes": ""},
    ]
    cm = bm.map_columns_heuristic(headers)
    lookups = {
        "111": ProductLookup(found=True, product_id=111, title="Top",
                             status="ACTIVE", description_html="", category_name="Tops"),
        "222": ProductLookup(found=True, product_id=222, title="Top",
                             status="ACTIVE",
                             description_html="<p>already written</p>",
                             category_name="Tops"),
    }
    plans = bm.plan_rows(headers, rows, cm, templates=templates,
                         lookup_fn=_fake_lookup(lookups))
    assert plans[0].action == "write"
    assert plans[0].template_name == "Tops"
    assert '18"' in plans[0].body_html
    assert plans[1].action == "skip-not-found"
    assert plans[2].action == "skip-has-desc"

    # With override, the has-desc row flips to write.
    plans_ovr = bm.plan_rows(headers, rows, cm, templates=templates,
                             overwrite_existing=True,
                             lookup_fn=_fake_lookup(lookups))
    assert plans_ovr[2].action == "write"


def test_audit_gate_blocks_banned_phrase_then_overridable():
    templates = load_description_templates()
    headers = REAL_HEADERS
    # "perfect" is a banned phrase in every template — injected via notes.
    rows = [
        {"": "Issey Miyake Long Sleeve Top", "Barcode": "111", "Tagged Size": "3",
         "Chest ": "18", "Length": "22", "Waist": "", "Hips": "", "Inseam": "",
         "Rise": "", "notes": "perfect condition"},
    ]
    cm = bm.map_columns_heuristic(headers)
    lookups = {
        "111": ProductLookup(found=True, product_id=111, title="Top",
                             status="ACTIVE", description_html="", category_name="Tops"),
    }
    plans = bm.plan_rows(headers, rows, cm, templates=templates,
                         lookup_fn=_fake_lookup(lookups))
    assert plans[0].action == "skip-failed-audit"
    assert "banned phrase" in plans[0].reason

    # With ignore_audit the same row is allowed through.
    plans_ovr = bm.plan_rows(headers, rows, cm, templates=templates,
                             ignore_audit=True, lookup_fn=_fake_lookup(lookups))
    assert plans_ovr[0].action == "write"


def test_parse_measurement_csv_roundtrip():
    csv_text = (
        ",Barcode,Tagged Size,Chest ,Length,Waist,Hips,Inseam,Rise,notes\n"
        "Green Top,96787476,3,18,22,,,,,\n"
        "Blue Skirt,97673236,44,,22,30,40,,,\n"
    )
    headers, rows = bm.parse_measurement_csv(csv_text)
    assert headers[0] == ""
    assert headers[1] == "Barcode"
    assert len(rows) == 2
    assert rows[0]["Barcode"] == "96787476"
    assert rows[1]["Hips"] == "40"

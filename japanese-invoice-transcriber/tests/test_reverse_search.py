"""Unit tests for reverse_search.py module."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reverse_search import (
    analyze_garment_image_with_ai,
    build_search_urls,
    format_shopify_title,
    generate_manifest_csv,
    get_directory_info,
)


def test_build_search_urls():
    query = "roberto cavalli 2003 mon amour"
    urls = build_search_urls(query)

    assert "Google Lens" in urls
    assert "Grailed" in urls
    assert "1stDibs" in urls
    assert "eBay" in urls
    assert "roberto+cavalli" in urls["Grailed"]


def test_build_search_urls_with_image_url():
    query = "blumarine floral skirt"
    img_url = "https://example.com/skirt.jpg"
    urls = build_search_urls(query, image_url=img_url)

    assert "https://lens.google.com/uploadbyurl?url=https%3A%2F%2Fexample.com%2Fskirt.jpg" in urls["Google Lens"]


def test_format_shopify_title():
    title = format_shopify_title(
        designer="Roberto Cavalli",
        year_era="2003",
        collection="Mon Amour",
        print_color="White Tiger Tattoo Graphic Denim",
        garment_type="Jacket Skirt",
        is_set=True,
    )
    assert title == "roberto cavalli 2003 mon amour white tiger tattoo graphic denim jacket skirt set"


def test_get_directory_info(tmp_path: Path):
    # Create test directory structure
    sub_folder = tmp_path / "sub_folder"
    sub_folder.mkdir()

    img1 = tmp_path / "look1.jpg"
    img1.write_bytes(b"fake_jpg")

    img2 = tmp_path / "look2.png"
    img2.write_bytes(b"fake_png")

    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("hello")

    subdirs, images = get_directory_info(tmp_path)

    assert len(subdirs) == 1
    assert subdirs[0].name == "sub_folder"
    assert len(images) == 2
    assert [i.name for i in images] == ["look1.jpg", "look2.png"]


def test_generate_manifest_csv():
    results = [
        {
            "filename": "look1.jpg",
            "item_type": "Set",
            "designer": "Roberto Cavalli",
            "year_era": "2003",
            "collection": "Mon Amour",
            "print_color": "White Tiger Tattoo",
            "garment_type": "Jacket Skirt",
            "fabric": "Denim",
            "suggested_title": "roberto cavalli 2003 mon amour white tiger tattoo denim jacket skirt set",
            "min_price_usd": 650,
            "max_price_usd": 1200,
            "search_query": "roberto cavalli 2003 mon amour white tiger tattoo denim jacket skirt set",
            "image_url": "",
        }
    ]

    csv_out = generate_manifest_csv(results)
    reader = list(csv.reader(io.StringIO(csv_out)))

    assert len(reader) == 2
    assert reader[0][0] == "Source"
    assert reader[1][0] == "look1.jpg"
    assert reader[1][1] == "Set"
    assert reader[1][2] == "Roberto Cavalli"
    assert reader[1][9] == "$650 - $1200"


def test_analyze_garment_image_with_ai_mock():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_message = MagicMock()

    mock_json = {
        "item_type": "Set",
        "designer": "Roberto Cavalli",
        "year_era": "2003",
        "collection": "Mon Amour",
        "print_color": "white tiger tattoo graphic denim",
        "garment_type": "jacket skirt set",
        "fabric": "denim",
        "suggested_title": "roberto cavalli 2003 mon amour white tiger tattoo graphic denim jacket skirt set",
        "min_price_usd": 650,
        "max_price_usd": 1200,
        "search_query": "roberto cavalli 2003 mon amour tattoo denim set",
        "notes": "Verified match",
    }

    mock_message.text = f"```json\n{json.dumps(mock_json)}\n```"
    mock_response.content = [mock_message]
    mock_client.messages.create.return_value = mock_response

    fake_bytes = b"fake_image_bytes"
    res = analyze_garment_image_with_ai(
        image_bytes=fake_bytes,
        filename="test.jpg",
        mime_type="image/jpeg",
        client=mock_client,
    )

    assert res["designer"] == "Roberto Cavalli"
    assert res["item_type"] == "Set"
    assert res["min_price_usd"] == 650
    assert res["max_price_usd"] == 1200
    assert res["filename"] == "test.jpg"

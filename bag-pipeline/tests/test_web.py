"""Tests for the FastAPI web endpoints (manual per-bag flow)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from pipeline import web  # noqa: E402  (importing web triggers load_dotenv)
from pipeline.schema import BagListing
from tests.test_analyze import SchemaTests as _SchemaTests

# web's load_dotenv(.env, override=True) clobbers env vars from the local
# .env file. Reset them to test values *after* the import.
os.environ["BAG_PIPELINE_PASSWORD"] = "testpass"
os.environ["BAG_PIPELINE_NO_AUTH"] = ""


AUTH = ("team", "testpass")
SAMPLE_LISTING_DICT = _SchemaTests.SAMPLE


def _photo(name: str) -> tuple[str, tuple[str, bytes, str]]:
    return ("files", (name, b"\xff\xd8\xff\xe0fakejpg", "image/jpeg"))


class WebTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_storage = Path(__file__).resolve().parent / "_tmp_storage"
        self._orig_storage = web.STORAGE_ROOT
        web.STORAGE_ROOT = self._tmp_storage
        self.client = TestClient(web.app)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self._tmp_storage, ignore_errors=True)
        web.STORAGE_ROOT = self._orig_storage

    def test_health_is_public(self) -> None:
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True})

    def test_homepage_requires_auth(self) -> None:
        self.assertEqual(self.client.get("/").status_code, 401)

    def test_homepage_serves_html_when_authed(self) -> None:
        resp = self.client.get("/", auth=AUTH)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("bag-pipeline", resp.text)
        self.assertIn("bag-pipeline", resp.text)
        self.assertIn("Pick folder", resp.text)
        self.assertIn("Upload photos", resp.text)

    def test_create_bag_requires_auth(self) -> None:
        resp = self.client.post("/api/bags", files=[_photo("a.jpg")])
        self.assertEqual(resp.status_code, 401)

    def test_upload_creates_one_bag_per_photo(self) -> None:
        resp = self.client.post(
            "/api/bags",
            files=[_photo("LOU_0226_817.jpg"), _photo("PRA_0001.jpg"), _photo("CHA_0042.jpg")],
            auth=AUTH,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        skus = [b["sku"] for b in data["created"]]
        self.assertEqual(set(skus), {"LOU_0226_817", "PRA_0001", "CHA_0042"})
        self.assertEqual(data["errors"], [])

    def test_sku_collisions_get_numeric_suffix(self) -> None:
        self.client.post("/api/bags", files=[_photo("bag.jpg")], auth=AUTH)
        resp = self.client.post("/api/bags", files=[_photo("bag.jpg"), _photo("bag.jpg")], auth=AUTH)
        skus = [b["sku"] for b in resp.json()["created"]]
        self.assertEqual(skus, ["bag_2", "bag_3"])

    def test_upload_persists_file_under_sku_dir(self) -> None:
        self.client.post("/api/bags", files=[_photo("hero.jpg")], auth=AUTH)
        self.assertTrue((self._tmp_storage / "hero" / "hero.jpg").exists())

    def test_upload_skips_non_jpgs(self) -> None:
        resp = self.client.post(
            "/api/bags",
            files=[_photo("good.jpg"), ("files", ("notes.txt", b"x", "text/plain"))],
            auth=AUTH,
        )
        data = resp.json()
        self.assertEqual(len(data["created"]), 1)
        self.assertEqual(len(data["errors"]), 1)

    def test_upload_sanitizes_messy_filename_to_sku(self) -> None:
        resp = self.client.post(
            "/api/bags",
            files=[("files", ("My Bag 1.jpg", b"x", "image/jpeg"))],
            auth=AUTH,
        )
        self.assertEqual(resp.status_code, 200)
        sku = resp.json()["created"][0]["sku"]
        # spaces collapse to underscores
        self.assertEqual(sku, "My_Bag_1")
        self.assertTrue((self._tmp_storage / sku).exists())

    def test_list_bags_empty(self) -> None:
        resp = self.client.get("/api/bags", auth=AUTH)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"bags": []})

    def test_list_bags_reflects_active_uploads(self) -> None:
        self.client.post("/api/bags", files=[_photo("alpha.jpg"), _photo("beta.jpg")], auth=AUTH)
        resp = self.client.get("/api/bags", auth=AUTH)
        data = resp.json()
        skus = sorted(b["sku"] for b in data["bags"])
        self.assertEqual(skus, ["alpha", "beta"])
        self.assertEqual(data["bags"][0]["shot_count"], 1)

    def test_delete_bag_removes_session(self) -> None:
        self.client.post("/api/bags", files=[_photo("gone.jpg")], auth=AUTH)
        self.assertTrue((self._tmp_storage / "gone").exists())
        resp = self.client.delete("/api/bags/gone", auth=AUTH)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse((self._tmp_storage / "gone").exists())

    def test_delete_unknown_bag_404s(self) -> None:
        resp = self.client.delete("/api/bags/never-existed", auth=AUTH)
        self.assertEqual(resp.status_code, 404)

    def test_delete_requires_auth(self) -> None:
        self.assertEqual(self.client.delete("/api/bags/anything").status_code, 401)


class AnalyzeEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_storage = Path(__file__).resolve().parent / "_tmp_storage_analyze"
        self._orig_storage = web.STORAGE_ROOT
        self._orig_analyze = web._analyze_fn
        web.STORAGE_ROOT = self._tmp_storage
        web._analyze_fn = lambda hero_path: BagListing.model_validate(SAMPLE_LISTING_DICT)
        self.client = TestClient(web.app)
        self.client.post(
            "/api/bags",
            files=[("files", ("TESTBAG.jpg", b"\xff\xd8\xff\xe0fakejpg", "image/jpeg"))],
            auth=AUTH,
        )

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self._tmp_storage, ignore_errors=True)
        web.STORAGE_ROOT = self._orig_storage
        web._analyze_fn = self._orig_analyze

    def test_analyze_requires_auth(self) -> None:
        resp = self.client.post("/api/bags/TESTBAG/analyze")
        self.assertEqual(resp.status_code, 401)

    def test_analyze_returns_listing_with_sku(self) -> None:
        resp = self.client.post("/api/bags/TESTBAG/analyze", auth=AUTH)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["sku"], "TESTBAG")
        self.assertEqual(data["brand"], "Prada")
        self.assertEqual(data["condition_grade"], 8.0)

    def test_analyze_persists_listing_in_bag_dir(self) -> None:
        self.client.post("/api/bags/TESTBAG/analyze", auth=AUTH)
        self.assertTrue((self._tmp_storage / "TESTBAG" / "_listing.json").exists())

    def test_analyze_unknown_bag_404s(self) -> None:
        resp = self.client.post("/api/bags/NOPE/analyze", auth=AUTH)
        self.assertEqual(resp.status_code, 404)

    def test_get_listing_404_before_analyze(self) -> None:
        resp = self.client.get("/api/bags/TESTBAG/listing", auth=AUTH)
        self.assertEqual(resp.status_code, 404)

    def test_get_listing_returns_saved_result(self) -> None:
        self.client.post("/api/bags/TESTBAG/analyze", auth=AUTH)
        resp = self.client.get("/api/bags/TESTBAG/listing", auth=AUTH)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["sku"], "TESTBAG")

    def test_list_bags_marks_analyzed(self) -> None:
        before = self.client.get("/api/bags", auth=AUTH).json()
        self.assertFalse(before["bags"][0]["analyzed"])
        self.client.post("/api/bags/TESTBAG/analyze", auth=AUTH)
        after = self.client.get("/api/bags", auth=AUTH).json()
        self.assertTrue(after["bags"][0]["analyzed"])
        self.assertEqual(after["bags"][0]["shot_count"], 1)

    def test_get_photo_returns_image_bytes(self) -> None:
        resp = self.client.get("/api/bags/TESTBAG/photo", auth=AUTH)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "image/jpeg")
        self.assertTrue(resp.content.startswith(b"\xff\xd8\xff"))

    def test_get_photo_unknown_bag_404s(self) -> None:
        resp = self.client.get("/api/bags/NOPE/photo", auth=AUTH)
        self.assertEqual(resp.status_code, 404)

    def test_put_listing_saves_edited_fields(self) -> None:
        self.client.post("/api/bags/TESTBAG/analyze", auth=AUTH)
        edited = dict(SAMPLE_LISTING_DICT)
        edited["title"] = "Edited title"
        edited["condition_grade"] = 9.0
        resp = self.client.put("/api/bags/TESTBAG/listing", json=edited, auth=AUTH)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["title"], "Edited title")
        self.assertEqual(data["condition_grade"], 9.0)
        self.assertEqual(data["sku"], "TESTBAG")
        # Reload from disk
        fresh = self.client.get("/api/bags/TESTBAG/listing", auth=AUTH).json()
        self.assertEqual(fresh["title"], "Edited title")

    def test_put_listing_rejects_invalid_payload(self) -> None:
        bad = dict(SAMPLE_LISTING_DICT)
        bad["condition_grade"] = 99  # out of range
        resp = self.client.put("/api/bags/TESTBAG/listing", json=bad, auth=AUTH)
        self.assertEqual(resp.status_code, 400)

    def test_put_listing_requires_auth(self) -> None:
        resp = self.client.put("/api/bags/TESTBAG/listing", json=SAMPLE_LISTING_DICT)
        self.assertEqual(resp.status_code, 401)

    def test_analyze_propagates_api_failure_as_502(self) -> None:
        def boom(_path):
            raise RuntimeError("Anthropic returned 500")

        web._analyze_fn = boom
        resp = self.client.post("/api/bags/TESTBAG/analyze", auth=AUTH)
        self.assertEqual(resp.status_code, 502)
        self.assertIn("Anthropic returned 500", resp.json()["detail"])


class CsvExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_storage = Path(__file__).resolve().parent / "_tmp_storage_csv"
        self._orig_storage = web.STORAGE_ROOT
        self._orig_analyze = web._analyze_fn
        web.STORAGE_ROOT = self._tmp_storage
        web._analyze_fn = lambda hero_path: BagListing.model_validate(SAMPLE_LISTING_DICT)
        self.client = TestClient(web.app)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self._tmp_storage, ignore_errors=True)
        web.STORAGE_ROOT = self._orig_storage
        web._analyze_fn = self._orig_analyze

    def test_export_returns_404_when_nothing_analyzed(self) -> None:
        resp = self.client.get("/api/export.csv", auth=AUTH)
        self.assertEqual(resp.status_code, 404)

    def test_export_returns_csv_for_analyzed_bags(self) -> None:
        self.client.post(
            "/api/bags",
            files=[("files", ("BAG_A.jpg", b"\xff\xd8\xff\xe0fake", "image/jpeg"))],
            auth=AUTH,
        )
        self.client.post("/api/bags/BAG_A/analyze", auth=AUTH)
        resp = self.client.get("/api/export.csv", auth=AUTH)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("text/csv", resp.headers["content-type"])
        self.assertIn("attachment", resp.headers["content-disposition"])
        self.assertIn("shopify-products.csv", resp.headers["content-disposition"])
        body = resp.text
        self.assertIn("Handle,Title,Body (HTML)", body)
        self.assertIn("BAG_A", body)
        for token in ("DIMENSIONS:", "DETAILS:", "MATERIAL:", "CONDITION:"):
            self.assertIn(token, body)

    def test_export_requires_auth(self) -> None:
        resp = self.client.get("/api/export.csv")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()

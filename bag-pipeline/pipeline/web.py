"""FastAPI app for the hosted bag pipeline.

Manual workflow for now: each upload is one bag. First file is the hero
shot. No filename parsing; SKU is whatever the user types (or auto-generated).
Files are held in an ephemeral session dir and discarded on bag delete or
after the Shopify push.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials

# Load .env from the repo root, overriding any empty shell vars that
# would otherwise mask values (the shell exports ANTHROPIC_API_KEY="").
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)

from pipeline.ingest import manual_bag
from pipeline.analyze import analyze_hero
from pipeline.export import to_csv
from pipeline.schema import BagListing


REPO_ROOT = Path(__file__).resolve().parents[1]
STORAGE_ROOT = Path(tempfile.gettempdir()) / "bag-pipeline-sessions"
TEMPLATES_DIR = REPO_ROOT / "templates"
SKU_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_\-. ]+$")


app = FastAPI(title="bag-pipeline")
_basic = HTTPBasic()


def _expected_password() -> str:
    pw = os.environ.get("BAG_PIPELINE_PASSWORD", "").strip()
    if not pw:
        raise HTTPException(
            status_code=500,
            detail="server misconfigured: BAG_PIPELINE_PASSWORD not set",
        )
    return pw


def _auth_disabled() -> bool:
    return os.environ.get("BAG_PIPELINE_NO_AUTH", "").strip() in ("1", "true", "yes")


def require_auth(credentials: Optional[HTTPBasicCredentials] = Depends(HTTPBasic(auto_error=False))) -> str:
    if _auth_disabled():
        return "dev"
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="credentials required",
            headers={"WWW-Authenticate": "Basic"},
        )
    expected_user = os.environ.get("BAG_PIPELINE_USER", "team").strip() or "team"
    user_ok = secrets.compare_digest(credentials.username, expected_user)
    pass_ok = secrets.compare_digest(credentials.password, _expected_password())
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _validate_sku(sku: str) -> None:
    if not SKU_RE.match(sku):
        raise HTTPException(
            status_code=400,
            detail="sku must contain only letters, digits, dashes, and underscores",
        )


def _safe_filename(name: str) -> str:
    base = Path(name).name
    if not base or not SAFE_FILENAME_RE.match(base):
        raise HTTPException(status_code=400, detail=f"unsafe filename: {name!r}")
    return base


def _new_sku() -> str:
    return "bag_" + uuid.uuid4().hex[:8]


def _sku_from_filename(filename: str) -> str:
    """Derive a SKU from a filename: strip extension + sanitize."""
    base = Path(filename).stem
    cleaned = re.sub(r"[^A-Za-z0-9_\-]", "_", base)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_-")
    return cleaned or _new_sku()


def _unique_sku(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def homepage(_user: str = Depends(require_auth)) -> str:
    return (TEMPLATES_DIR / "index.html").read_text()


@app.post("/api/bags")
async def create_bags(
    files: list[UploadFile] = File(...),
    _user: str = Depends(require_auth),
) -> dict:
    """Create one bag per uploaded photo.

    SKU is derived from each filename (extension stripped, sanitized).
    Collisions get a numeric suffix.
    """
    if not files:
        raise HTTPException(status_code=400, detail="no files uploaded")

    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    taken = {d.name for d in STORAGE_ROOT.iterdir() if d.is_dir()}

    created: list[dict] = []
    errors: list[dict] = []

    for upload in files:
        try:
            filename = _safe_filename(upload.filename or "")
            if Path(filename).suffix.lower() not in (".jpg", ".jpeg"):
                errors.append({"file": filename, "error": "not a jpg"})
                continue

            base_sku = _sku_from_filename(filename)
            sku = _unique_sku(base_sku, taken)
            taken.add(sku)

            bag_dir = STORAGE_ROOT / sku
            bag_dir.mkdir(parents=True)
            (bag_dir / filename).write_bytes(await upload.read())
            created.append({"sku": sku, "filename": filename})
        except HTTPException as e:
            errors.append({"file": upload.filename, "error": e.detail})
        except Exception as e:
            errors.append({"file": upload.filename, "error": str(e)})

    return {
        "created": created,
        "errors": errors,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _is_photo(p: Path) -> bool:
    return p.is_file() and not p.name.startswith("_") and p.suffix.lower() in (".jpg", ".jpeg")


@app.get("/api/bags")
def list_bags(_user: str = Depends(require_auth)) -> dict:
    if not STORAGE_ROOT.exists():
        return {"bags": []}
    items = []
    for d in sorted(STORAGE_ROOT.iterdir()):
        if not d.is_dir():
            continue
        shots = sorted(p.name for p in d.iterdir() if _is_photo(p))
        analyzed = (d / "_listing.json").exists()
        items.append(
            {"sku": d.name, "shot_count": len(shots), "shots": shots, "analyzed": analyzed}
        )
    return {"bags": items}


@app.delete("/api/bags/{sku}")
def delete_bag(sku: str, _user: str = Depends(require_auth)) -> dict:
    _validate_sku(sku)
    bag_dir = STORAGE_ROOT / sku
    if not bag_dir.exists():
        raise HTTPException(status_code=404, detail=f"bag '{sku}' not found")
    shutil.rmtree(bag_dir)
    return {"deleted": sku}


# Pluggable for tests — overridden to skip the real Claude call.
_analyze_fn = analyze_hero


@app.post("/api/bags/{sku}/analyze")
def analyze_bag(sku: str, _user: str = Depends(require_auth)) -> dict:
    _validate_sku(sku)
    bag_dir = STORAGE_ROOT / sku
    if not bag_dir.exists():
        raise HTTPException(status_code=404, detail=f"bag '{sku}' not found")

    photos = sorted(p for p in bag_dir.iterdir() if _is_photo(p))
    if not photos:
        raise HTTPException(status_code=400, detail=f"bag '{sku}' has no photos")
    hero = photos[0]

    try:
        listing: BagListing = _analyze_fn(hero)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"analyze failed: {e}")

    result = listing.model_dump()
    result["sku"] = sku
    (bag_dir / "_listing.json").write_text(json.dumps(result, indent=2))
    return result


@app.get("/api/bags/{sku}/listing")
def get_listing(sku: str, _user: str = Depends(require_auth)) -> dict:
    _validate_sku(sku)
    listing_path = STORAGE_ROOT / sku / "_listing.json"
    if not listing_path.exists():
        raise HTTPException(status_code=404, detail="no analyze result yet")
    return json.loads(listing_path.read_text())


@app.put("/api/bags/{sku}/listing")
def update_listing(sku: str, body: dict, _user: str = Depends(require_auth)) -> dict:
    _validate_sku(sku)
    bag_dir = STORAGE_ROOT / sku
    if not bag_dir.exists():
        raise HTTPException(status_code=404, detail=f"bag '{sku}' not found")
    payload = {k: v for k, v in body.items() if k != "sku"}
    try:
        listing = BagListing.model_validate(payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid listing: {e}")
    out = listing.model_dump()
    out["sku"] = sku
    (bag_dir / "_listing.json").write_text(json.dumps(out, indent=2))
    return out


@app.get("/api/export.csv")
def export_csv(_user: str = Depends(require_auth)) -> Response:
    rows: list[tuple[str, BagListing]] = []
    if STORAGE_ROOT.exists():
        for d in sorted(STORAGE_ROOT.iterdir()):
            if not d.is_dir():
                continue
            listing_path = d / "_listing.json"
            if not listing_path.exists():
                continue
            data = json.loads(listing_path.read_text())
            data.pop("sku", None)
            try:
                rows.append((d.name, BagListing.model_validate(data)))
            except Exception:
                continue
    if not rows:
        raise HTTPException(status_code=404, detail="no analyzed bags to export")
    return Response(
        content=to_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="shopify-products.csv"'},
    )


@app.get("/api/bags/{sku}/photo")
def get_bag_photo(sku: str, _user: str = Depends(require_auth)):
    _validate_sku(sku)
    bag_dir = STORAGE_ROOT / sku
    if not bag_dir.exists():
        raise HTTPException(status_code=404, detail=f"bag '{sku}' not found")
    photos = sorted(p for p in bag_dir.iterdir() if _is_photo(p))
    if not photos:
        raise HTTPException(status_code=404, detail="no photo")
    return FileResponse(photos[0], media_type="image/jpeg")

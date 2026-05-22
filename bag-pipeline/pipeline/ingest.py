"""Stage 1: scan a Capture One export folder and group images by SKU."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path


FILENAME_RE = re.compile(r"^(?P<sku>.+)-(?P<shot>\d+)\.jpe?g$", re.IGNORECASE)


@dataclass
class Bag:
    sku: str
    hero: str
    shots: list[str]
    flags: list[str] = field(default_factory=list)


@dataclass
class Manifest:
    shoot_id: str
    source_folder: str
    created_at: str
    bags: list[Bag]

    def to_dict(self) -> dict:
        return {
            "shoot_id": self.shoot_id,
            "source_folder": self.source_folder,
            "created_at": self.created_at,
            "bags": [asdict(b) for b in self.bags],
        }


def _iter_jpgs(folder: Path, recursive: bool = False):
    if recursive:
        candidates = sorted(folder.rglob("*"))
    else:
        candidates = sorted(folder.iterdir())
    for entry in candidates:
        if not entry.is_file():
            continue
        if any(part.startswith(".") for part in entry.relative_to(folder).parts):
            continue
        if entry.suffix.lower() not in (".jpg", ".jpeg"):
            continue
        yield entry


def _parse(name: str) -> tuple[str, int] | None:
    match = FILENAME_RE.match(name)
    if not match:
        return None
    return match.group("sku"), int(match.group("shot"))


def scan_folder(folder: Path, recursive: bool = False) -> list[Bag]:
    """Group jpg files in folder by SKU. Returns bags sorted by SKU."""
    groups: dict[str, list[tuple[int, Path]]] = {}
    for path in _iter_jpgs(folder, recursive=recursive):
        parsed = _parse(path.name)
        if parsed is None:
            continue
        sku, shot = parsed
        groups.setdefault(sku, []).append((shot, path))

    bags: list[Bag] = []
    for sku in sorted(groups):
        shots = sorted(groups[sku], key=lambda pair: pair[0])
        shot_numbers = [n for n, _ in shots]
        shot_paths = [str(p) for _, p in shots]

        flags: list[str] = []
        if 1 in shot_numbers:
            hero = shot_paths[shot_numbers.index(1)]
        else:
            hero = shot_paths[0]
            flags.append("missing_hero_shot_01")

        bags.append(Bag(sku=sku, hero=hero, shots=shot_paths, flags=flags))

    return bags


def build_manifest(folder: Path, shoot_id: str, recursive: bool = False) -> Manifest:
    folder = folder.resolve()
    bags = scan_folder(folder, recursive=recursive)
    return Manifest(
        shoot_id=shoot_id,
        source_folder=str(folder),
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        bags=bags,
    )


def write_manifest(manifest: Manifest, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest.to_dict(), indent=2))
    return output_path


def ingest(folder: Path, shoot_id: str, output_dir: Path) -> Path:
    """Run Stage 1: scan folder, build manifest, write to output_dir."""
    manifest = build_manifest(folder, shoot_id)
    out = output_dir / f"{shoot_id}.json"
    return write_manifest(manifest, out)


def manual_bag(sku: str, file_paths: list[Path]) -> Bag:
    """Build a bag from an ordered list of files. First file is the hero.

    Used by the web flow where the user uploads images for one bag at a time
    rather than relying on the {SKU}-NN.jpg filename convention.
    """
    if not file_paths:
        raise ValueError("manual_bag requires at least one file")
    shots = [str(p) for p in file_paths]
    return Bag(sku=sku, hero=shots[0], shots=shots, flags=[])

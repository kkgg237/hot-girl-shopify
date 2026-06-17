"""Template loading + per-slot framing.

A template is a YAML file declaring a fixed list of named output slots and
the framing rule for each. The pipeline applies the same template to every
SKU so output sets are uniform across hundreds of items.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"


@dataclass(frozen=True)
class ShotSpec:
    slot: str
    source_shot: int
    # Full-body framing (used when region_of_subject is None).
    subject_height_fraction: Optional[float] = None
    vertical_bias: float = 0.0
    # Detail framing: a [top, bottom] pair of fractions of the subject's
    # height to zoom into. region_fill says what fraction of the output that
    # zoomed region should fill.
    region_of_subject: Optional[Tuple[float, float]] = None
    region_fill: float = 0.92


@dataclass(frozen=True)
class Template:
    name: str
    description: str
    output_size: Tuple[int, int]
    shots: List[ShotSpec] = field(default_factory=list)


def load_template(name_or_path: str | Path) -> Template:
    """Load a template by name (``listing-standard``) or by explicit path."""
    p = Path(name_or_path)
    if p.suffix.lower() not in (".yaml", ".yml"):
        p = TEMPLATES_DIR / f"{name_or_path}.yaml"
    if not p.is_file():
        raise FileNotFoundError(f"template not found: {p}")

    data = yaml.safe_load(p.read_text())

    shots = []
    for raw in data.get("shots", []):
        region = raw.get("region_of_subject")
        shots.append(
            ShotSpec(
                slot=raw["slot"],
                source_shot=int(raw["source_shot"]),
                subject_height_fraction=raw.get("subject_height_fraction"),
                vertical_bias=float(raw.get("vertical_bias", 0.0)),
                region_of_subject=tuple(region) if region else None,
                region_fill=float(raw.get("region_fill", 0.92)),
            )
        )

    return Template(
        name=data["name"],
        description=data.get("description", ""),
        output_size=tuple(data.get("output_size", [1536, 2048])),
        shots=shots,
    )

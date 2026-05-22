"""Pydantic schemas for the Stage 2 analyze output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Confidence = Literal["high", "medium", "low"]


class ConditionNotes(BaseModel):
    exterior: str = Field(..., description="Exterior leather/canvas condition")
    hardware: str = Field(..., description="Hardware condition")
    stitching: str = Field(..., description="Stitching condition")
    strap: str = Field(..., description="Strap condition (or 'not applicable' if no strap)")


class BagListing(BaseModel):
    """Drafted Shopify listing for one bag, generated from the hero shot."""

    brand: str
    brand_confidence: Confidence
    model: str = Field(..., description="Model/silhouette name as a single string")
    model_confidence: Confidence
    model_candidates: list[str] = Field(
        default_factory=list,
        description="Top 3 candidate models when confidence is medium/low. Empty when high.",
    )

    era: str = Field(..., description="Decade as a label like \"90's\" or \"00's\"")
    colorway: str
    material_primary: str = Field(..., description="Primary material as a noun phrase")
    silhouette: str = Field(..., description="Silhouette type, e.g. 'Shoulder Bag', 'Tote', 'Pochette'")

    title: str = Field(
        ...,
        description="Format: {Era}'s {Brand} {Color} {Material} {Silhouette}",
    )

    details_bullets: list[str] = Field(
        ...,
        min_length=3,
        max_length=6,
        description=(
            "3-4 descriptive phrases about the bag (material, hardware, closure, "
            "silhouette). Up to one may reference the collection if confident. "
            "Schema allows up to 6 for backward compatibility with older listings."
        ),
    )

    material_line: str = Field(
        ...,
        description="Comma-separated noun list, e.g. 'Leather, Gold-Tone Hardware'",
    )

    condition_grade: float = Field(
        ...,
        ge=1.0,
        le=10.0,
        description="1-10 with half-point granularity (e.g. 8.0, 8.5).",
    )
    condition_notes: ConditionNotes
    condition_unverifiable: list[str] = Field(
        ...,
        description="Things that cannot be verified from the hero shot alone.",
    )
    condition_text: str = Field(
        ...,
        description="Full formatted condition paragraph, 2-4 sentences, factual.",
    )

    dimensions: str = Field(
        default="[measure in hand]",
        description="Dimensions string. Measured by hand — Claude leaves the placeholder.",
    )

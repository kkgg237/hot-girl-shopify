#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0", "pydantic>=2.0"]
# ///
"""Terminal CLI for browsing rules + managing feedback notes.

Usage:
    uv run python -m heuristics view              # dump every section
    uv run python -m heuristics view --section model_era
    uv run python -m heuristics search burberry   # find rules + notes mentioning "burberry"
    uv run python -m heuristics note "feedback text" --topic titles
    uv run python -m heuristics feedback                    # list all
    uv run python -m heuristics feedback --status pending   # filter
    uv run python -m heuristics resolve fb-2026-04-23-001 applied --resolution "fixed in pricing.py"

Run from the project root so `heuristics` resolves as a package.
"""
from __future__ import annotations

import argparse
import sys

from . import loader


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_meta(r: loader.Rules) -> None:
    print("== Title format ==")
    print(f"  {r.meta.get('title_format', '(unset)')}")
    if r.meta.get("notes"):
        for line in r.meta["notes"].rstrip().splitlines():
            print(f"  | {line}")
    print()


def _render_model_era(r: loader.Rules) -> None:
    total = sum(len(v) for v in r.model_era.values())
    print(f"== Model → era ({total} entries, WIRED) ==")
    for brand, models in r.model_era.items():
        print(f"  {brand}")
        for m, era in models.items():
            print(f"    {m:<30s} {era}")
    print()


def _render_archetypes(r: loader.Rules) -> None:
    total = sum(len(v) for v in r.brand_archetypes.values())
    print(f"== Brand archetypes ({total} pairs, MIRROR) ==")
    for brand, types in r.brand_archetypes.items():
        for t, defaults in types.items():
            tags = " ".join(f"{k}={v}" for k, v in defaults.items())
            print(f"  ({brand}, {t}) → {tags}")
    print()


def _render_titles(r: loader.Rules) -> None:
    print("== Title rules ==")
    print(f"  era_policy.allow_in_title_regex: {r.titles.era_policy.allow_in_title_regex}")
    decades = r.titles.era_policy.allow_decades_for or "(none)"
    print(f"  era_policy.allow_decades_for:    {decades}")
    print(f"  silhouette_categorical (move to END of style chain):")
    print("    " + ", ".join(r.titles.silhouette_categorical))
    print(f"  acronyms_uppercase ({len(r.titles.acronyms_uppercase)}):")
    print("    " + ", ".join(r.titles.acronyms_uppercase))
    print()


def _render_tiers(r: loader.Rules) -> None:
    print("== Brand tiers (MIRROR) ==")
    print(f"  luxury ({len(r.tier_brands.get('luxury', []))}): " +
          ", ".join(r.tier_brands.get("luxury", [])))
    print(f"  mid    ({len(r.tier_brands.get('mid', []))}): " +
          ", ".join(r.tier_brands.get("mid", [])))
    print()


def _render_canon(r: loader.Rules) -> None:
    brands = r.canonicalize.get("brands", {})
    types = r.canonicalize.get("types", {})
    print(f"== Canonicalization (MIRROR) ==")
    print(f"  brands: {len(brands)} aliases")
    print(f"  types:  {len(types)} aliases")
    print(f"  (run with --section canonicalize for full list)")
    print()


def _render_canon_full(r: loader.Rules) -> None:
    print("== Canonicalize.brands ==")
    for k, v in r.canonicalize.get("brands", {}).items():
        print(f"  {k:<30s} → {v}")
    print()
    print("== Canonicalize.types ==")
    for k, v in r.canonicalize.get("types", {}).items():
        print(f"  {k:<30s} → {v}")
    print()


def _render_anchors(r: loader.Rules) -> None:
    print(f"== Regression anchors ({len(r.regression_anchors)}) ==")
    for a in r.regression_anchors:
        title = a.expected_title or "(empty — fill in once confirmed)"
        print(f"  {a.source_id:<20s} → {title}")
        if a.note:
            print(f"  {' ':<20s}   {a.note}")
    print()


SECTIONS = {
    "meta": _render_meta,
    "model_era": _render_model_era,
    "brand_archetypes": _render_archetypes,
    "titles": _render_titles,
    "tier_brands": _render_tiers,
    "canonicalize": _render_canon_full,
    "regression_anchors": _render_anchors,
}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_view(args: argparse.Namespace) -> int:
    r = loader.load_rules()
    if args.section:
        if args.section not in SECTIONS:
            print(f"Unknown section: {args.section}")
            print(f"Available: {', '.join(SECTIONS.keys())}")
            return 1
        SECTIONS[args.section](r)
        return 0
    # Full dump (canon shown abbreviated, full available via --section)
    _render_meta(r)
    _render_titles(r)
    _render_model_era(r)
    _render_archetypes(r)
    _render_tiers(r)
    _render_canon(r)
    _render_anchors(r)
    print(f"Source: {loader.RULES_PATH}")
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    n = loader.append_feedback(args.text, topic=args.topic)
    print(f"Saved {n.id}")
    print(f"  topic:  {n.topic}")
    print(f"  status: {n.status}")
    print(f"  quote:  {n.quote[:80]}{'...' if len(n.quote) > 80 else ''}")
    return 0


def cmd_feedback(args: argparse.Namespace) -> int:
    notes = loader.load_feedback()
    if args.status:
        notes = [n for n in notes if n.status == args.status]
    if args.topic:
        notes = [n for n in notes if n.topic == args.topic]
    if not notes:
        print("(no matching notes)")
        return 0
    for n in notes:
        print(f"[{n.status:<8s}] {n.id}  topic={n.topic}  date={n.date}")
        print(f"  quote: {n.quote}")
        if n.resolution:
            first_line = n.resolution.strip().splitlines()[0]
            print(f"  → {first_line}")
        if n.related_rules:
            print(f"  rules: {', '.join(n.related_rules)}")
        print()
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    ok = loader.update_feedback_status(
        args.note_id,
        status=args.status,
        resolution=args.resolution,
    )
    if ok:
        print(f"Updated {args.note_id} → {args.status}")
        return 0
    print(f"Not found: {args.note_id}")
    return 1


def cmd_search(args: argparse.Namespace) -> int:
    q = args.query.lower()
    r = loader.load_rules()
    notes = loader.load_feedback()
    hits = 0

    for brand, models in r.model_era.items():
        for m, era in models.items():
            if q in brand.lower() or q in m.lower():
                print(f"  model_era    {brand} / {m} → {era}")
                hits += 1

    for brand, types in r.brand_archetypes.items():
        for t, defaults in types.items():
            if q in brand.lower() or q in t.lower():
                print(f"  archetype    ({brand}, {t}) → {defaults}")
                hits += 1

    for tier, brands in r.tier_brands.items():
        for b in brands:
            if q in b.lower():
                print(f"  tier         {b} ({tier})")
                hits += 1

    for kind, mapping in r.canonicalize.items():
        for k, v in mapping.items():
            if q in k.lower() or q in str(v).lower():
                print(f"  canon.{kind:<6s} {k} → {v}")
                hits += 1

    for a in r.regression_anchors:
        if q in a.source_id.lower() or q in a.expected_title.lower():
            print(f"  anchor       {a.source_id} → {a.expected_title}")
            hits += 1

    for n in notes:
        if q in n.quote.lower() or q in n.topic.lower() or q in (n.resolution or "").lower():
            print(f"  feedback     [{n.status}] {n.id}  {n.quote[:80]}")
            hits += 1

    print(f"\n{hits} hit(s) for '{args.query}'")
    return 0


def cmd_paths(args: argparse.Namespace) -> int:
    print(f"rules.yaml:    {loader.RULES_PATH}")
    print(f"feedback.yaml: {loader.FEEDBACK_PATH}")
    return 0


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="heuristics",
        description="View and manage invoice transcriber rules + feedback.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("view", help="Print all rules grouped by section")
    pv.add_argument("--section", choices=list(SECTIONS.keys()),
                    help="Show only one section in full")
    pv.set_defaults(func=cmd_view)

    pn = sub.add_parser("note", help="Append a new pending feedback note")
    pn.add_argument("text", help="The feedback text (use quotes if it contains spaces)")
    pn.add_argument("--topic", default="general",
                    help="One of: titles, costs, aesthetic, data_quality, scope, general")
    pn.set_defaults(func=cmd_note)

    pf = sub.add_parser("feedback", help="List feedback notes, optionally filtered")
    pf.add_argument("--status", choices=["pending", "applied", "rejected", "deferred"])
    pf.add_argument("--topic")
    pf.set_defaults(func=cmd_feedback)

    pr = sub.add_parser("resolve", help="Mark a feedback note as applied / rejected / deferred")
    pr.add_argument("note_id")
    pr.add_argument("status", choices=["applied", "rejected", "deferred", "pending"])
    pr.add_argument("--resolution", help="Free-text resolution explanation")
    pr.set_defaults(func=cmd_resolve)

    ps = sub.add_parser("search", help="Search rules and notes for a substring")
    ps.add_argument("query")
    ps.set_defaults(func=cmd_search)

    pp = sub.add_parser("paths", help="Print absolute paths to the YAML files")
    pp.set_defaults(func=cmd_paths)

    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())

"""Heuristics — single source of truth for invoice transcriber rules.

Public API:
    from heuristics import RULES, load_rules, load_feedback,
                           append_feedback, update_feedback_status

`RULES` is a module-level singleton loaded at import time. Call `load_rules()`
to get a fresh copy after the YAML file has been edited at runtime.
"""
from .loader import (
    RULES,
    Rules,
    FeedbackNote,
    load_rules,
    load_feedback,
    append_feedback,
    update_feedback_status,
    stale_pending_notes,
    format_digest,
    mark_reminded,
    RULES_PATH,
    FEEDBACK_PATH,
)

__all__ = [
    "RULES",
    "Rules",
    "FeedbackNote",
    "load_rules",
    "load_feedback",
    "append_feedback",
    "update_feedback_status",
    "stale_pending_notes",
    "format_digest",
    "mark_reminded",
    "RULES_PATH",
    "FEEDBACK_PATH",
]

"""Heuristics — single source of truth for invoice transcriber rules.

Public API:
    from heuristics import RULES, load_rules, load_feedback,
                           append_feedback, update_feedback_status,
                           load_description_templates, save_description_templates

`RULES` is a module-level singleton loaded at import time. Call `load_rules()`
to get a fresh copy after the YAML file has been edited at runtime.
"""
from .loader import (
    RULES,
    Rules,
    FeedbackNote,
    DescriptionTemplate,
    load_rules,
    load_feedback,
    append_feedback,
    update_feedback_status,
    stale_pending_notes,
    format_digest,
    mark_reminded,
    load_description_templates,
    save_description_templates,
    find_template_for_category,
    audit_description,
    suggest_template_from_product,
    RULES_PATH,
    FEEDBACK_PATH,
    DESCRIPTION_TEMPLATES_PATH,
)

__all__ = [
    "RULES",
    "Rules",
    "FeedbackNote",
    "DescriptionTemplate",
    "load_rules",
    "load_feedback",
    "append_feedback",
    "update_feedback_status",
    "stale_pending_notes",
    "format_digest",
    "mark_reminded",
    "load_description_templates",
    "save_description_templates",
    "find_template_for_category",
    "audit_description",
    "suggest_template_from_product",
    "RULES_PATH",
    "FEEDBACK_PATH",
    "DESCRIPTION_TEMPLATES_PATH",
]

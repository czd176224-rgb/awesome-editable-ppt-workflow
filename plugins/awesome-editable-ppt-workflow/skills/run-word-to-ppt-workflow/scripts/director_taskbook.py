"""Validate and seal the seven-field presentation director taskbook."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


TASKBOOK_FIELDS = (
    "use_scenario",
    "presenter",
    "primary_audience",
    "audience_prior_knowledge",
    "desired_outcome",
    "emphasis",
    "deemphasis",
)

DIRECTOR_TEMPLATE_IDS = frozenset({
    "company-business-introduction",
    "investment-committee",
    "project-initiation",
    "corporate-planning",
    "investment-project-bp",
})

TASKBOOK_AUTHORITY_BOUNDARY = (
    "This taskbook is a user-confirmed presentation constraint, not factual source material. "
    "It may change emphasis, hierarchy, reading path, evidence framing, and takeaway selection "
    "only; it must not add, omit, rewrite, or move Word content."
)

_TASKBOOK_LABELS = {
    "use_scenario": "Use scenario",
    "presenter": "Presenter",
    "primary_audience": "Primary audience",
    "audience_prior_knowledge": "Audience prior knowledge",
    "desired_outcome": "Desired understanding, discussion, or decision",
    "emphasis": "Word content to emphasize",
    "deemphasis": "Content to keep lower prominence",
}


def validate_taskbook(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(TASKBOOK_FIELDS):
        raise ValueError("director taskbook must contain exactly the seven confirmed fields")
    result: dict[str, str] = {}
    for field in TASKBOOK_FIELDS:
        text = value[field]
        if not isinstance(text, str) or not text.strip() or len(text) > 2_000:
            raise ValueError(f"director taskbook {field} must be nonblank text up to 2000 characters")
        result[field] = text.strip()
    return result


def taskbook_digest(value: Mapping[str, Any]) -> str:
    normalized = validate_taskbook(value)
    payload = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_confirmed_taskbook(project: Path) -> dict[str, str]:
    from workflow_v6_state import load

    state = load(Path(project))
    confirmation = state.get("director_confirmation")
    if not isinstance(confirmation, Mapping):
        raise ValueError("confirmed director taskbook is missing")
    taskbook = validate_taskbook(confirmation.get("taskbook"))
    if confirmation.get("taskbook_digest") != taskbook_digest(taskbook):
        raise ValueError("confirmed director taskbook digest is invalid")
    return taskbook


def confirmed_taskbook_prompt(project: Path) -> str:
    taskbook = load_confirmed_taskbook(project)
    fields = "\n".join(
        f"- {_TASKBOOK_LABELS[field]}: {taskbook[field]}" for field in TASKBOOK_FIELDS
    )
    return (
        f"{TASKBOOK_AUTHORITY_BOUNDARY}\n"
        f"{fields}\n"
        "Apply the use scenario to business_proposition and analytical_backbone. "
        "Use the presenter to calibrate explanatory_lead and speaking stance. "
        "Use the primary audience to shape content_hierarchy; use prior knowledge to shape "
        "reading_path_and_density. Use the desired outcome to shape takeaway_statement and "
        "evidence_interpretation_conclusion. Use emphasis to guide content_hierarchy and "
        "supporting_visual_policy. Keep deemphasized Word content present but visually subordinate; "
        "never omit it."
    )

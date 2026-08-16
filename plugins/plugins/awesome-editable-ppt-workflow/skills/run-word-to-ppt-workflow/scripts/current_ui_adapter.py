"""Pure versioned adapter for the current Confirm UI result payload."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator


CURRENT_UI_PAYLOAD_VERSION = "confirm-ui-result-v1"
CURRENT_UI_CONTRACT_STATUS = "provisional"

CONFIRMATION_FIELDS = (
    "canvas",
    "page_count",
    "pagination_mode",
    "one_page_to_one_slide",
    "direction",
    "template_selection",
    "visual_style",
    "color",
    "icons",
    "typography",
    "image_rendering",
    "style_axes",
    "layout_preferences",
    "information_density",
    "regional_style",
    "background_system",
    "image_role",
    "evidence_strength",
    "composition_tendency",
    "brand_device",
    "production_profile",
    "additional_requirements",
    "formula_policy",
    "generation_mode",
    "refine_spec",
    "image_quality",
    "max_concurrency",
    "automatic_repair_budget",
    "editable_output",
    "start_generation",
)


def _current_result_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("stage") != "final" or payload.get("status") != "confirmed":
        raise ValueError("style result must be the final confirmed Confirm UI result")
    missing = [field for field in CONFIRMATION_FIELDS if field not in payload]
    if missing:
        raise ValueError(f"confirmed style result is missing: {', '.join(missing)}")
    result = {
        "stage": "final",
        "status": "confirmed",
        "confirmed_at": payload.get("confirmed_at"),
    }
    result.update({field: payload[field] for field in CONFIRMATION_FIELDS})
    return copy.deepcopy(result)


_ADAPTERS = {CURRENT_UI_PAYLOAD_VERSION: _current_result_v1}


def adapt_current_ui_payload(
    payload: Mapping[str, Any],
    *,
    payload_version: str = CURRENT_UI_PAYLOAD_VERSION,
) -> dict[str, Any]:
    """Map a known UI payload version to the existing style-confirmation projection."""
    if not isinstance(payload, Mapping):
        raise ValueError("Confirm UI payload must be an object")
    # V6 freezes a complete UI revision.  Only its global visual contract is
    # a style input; page materials stay separate and are never reinterpreted
    # by the style compiler.
    if payload.get("status") == "confirmed" and isinstance(payload.get("global_visual_contract"), Mapping):
        schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "style_confirmation.schema.json").read_text(encoding="utf-8"))
        errors = sorted(Draft202012Validator(schema).iter_errors(dict(payload)), key=lambda error: list(error.path))
        if errors:
            raise ValueError(f"V6 confirmed UI result validation failed: {errors[0].message}")
        return _current_result_v1(payload["global_visual_contract"])
    embedded = payload.get("ui_payload_version")
    effective_version = embedded if embedded is not None else payload_version
    if embedded is not None and embedded != payload_version:
        effective_version = embedded
    adapter = _ADAPTERS.get(effective_version)
    if adapter is None:
        raise ValueError(f"unsupported Confirm UI payload version: {effective_version}")
    if embedded is not None and embedded != payload_version:
        raise ValueError(
            "unsupported Confirm UI payload version: "
            f"embedded {embedded!r} does not match dispatch {payload_version!r}"
        )
    return adapter(payload)

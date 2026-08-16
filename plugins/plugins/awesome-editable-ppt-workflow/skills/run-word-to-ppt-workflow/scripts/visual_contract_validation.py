"""Single strict runtime validator for the effective nested visual contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator


_AUTHORITY_SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "schemas"
        / "effective_page_authority_v3.schema.json"
    ).read_text(encoding="utf-8")
)
_VISUAL_SCHEMA = _AUTHORITY_SCHEMA["$defs"]["visualContract"]
_VALIDATOR = Draft202012Validator(_VISUAL_SCHEMA)


def validate_strict_visual_contract(value: Mapping[str, Any]) -> None:
    """Reject missing, extra, or mistyped nested visual-contract fields."""
    if not isinstance(value, Mapping):
        raise ValueError("visual contract must be an object")
    errors = sorted(_VALIDATOR.iter_errors(value), key=lambda error: list(error.path))
    if errors:
        location = ".".join(str(item) for item in errors[0].path) or "<root>"
        raise ValueError(
            f"visual contract validation failed at {location}: {errors[0].message}"
        )

"""Compile the sealed V6 UI result into the only Image2 prompt authority."""

from __future__ import annotations

import copy
import json
from typing import Any, Mapping, Sequence


SYSTEM_GENERATION_CONSTRAINTS = (
    "Generate only the slide body from the confirmed material below. Do not reinterpret "
    "comments, recover omitted source material, add facts, classifications, conclusions, "
    "labels, brands, identities, events, products, or evidence.",
    "For edit requests, input images are independent reference materials, not a base "
    "canvas. Compose a new unified 17:8 body image from the confirmed materials.",
    "Preserve confirmed Logo and screenshot text, identity, aspect ratio, and visual content "
    "as a high-fidelity best effort, not pixel-perfect reproduction.",
    "Never fabricate an unreferenced real identity, brand, event, product, or evidence.",
)

GEOMETRY_AND_FIXED_LAYER_EXCLUSIONS = {
    "canvas_pixels": "1904x896",
    "aspect_ratio": "17:8",
    "body_only": True,
    "prohibited_output": [
        "main title",
        "fixed SVG logo",
        "footer",
        "page number",
    ],
    "main_title_rule": (
        "The fixed page main title is identification-only and must not be rendered, repeated, "
        "paraphrased, or replaced anywhere inside the 17:8 body."
    ),
}

_PAGE_MATERIAL_FIELDS = (
    "effective_body",
    "attachment_extracts",
    "chart_facts",
    "image_requirements",
    "degradations",
)

_VISUAL_CONTRACT_FIELDS = (
    "canvas",
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
    "additional_requirements",
    "image_usage_policy",
)


def _stable_json_copy(value: Any) -> Any:
    """Deep-copy JSON deterministically without changing its keys or values."""
    if isinstance(value, Mapping):
        return {
            str(key): _stable_json_copy(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_stable_json_copy(item) for item in value]
    return copy.deepcopy(value)


def _usable_reference_record(reference: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return prompt semantics only; paths, URLs, hashes, and lifecycle state stay local."""
    if reference.get("status") != "available":
        return None
    reference_id = reference.get("reference_id")
    role = reference.get("purpose", reference.get("role"))
    if not isinstance(reference_id, str) or not reference_id.strip():
        return None
    if not isinstance(role, str) or not role.strip():
        return None
    result: dict[str, Any] = {
        "reference_id": reference_id,
        "role": role,
    }
    for key in ("preservation", "allow_crop", "allow_restyle"):
        if key in reference:
            result[key] = copy.deepcopy(reference[key])
    return result


def filter_confirmed_page_for_prompt(confirmed_page: Mapping[str, Any]) -> dict[str, Any]:
    """Project one frozen page to the exact material shared by Image2 and lightweight QA."""
    filtered = {
        field: _stable_json_copy(confirmed_page.get(field, "" if field == "effective_body" else []))
        for field in _PAGE_MATERIAL_FIELDS
    }
    filtered["reference_images"] = [
        record
        for item in confirmed_page.get("reference_images", [])
        if isinstance(item, Mapping)
        for record in [_usable_reference_record(item)]
        if record is not None
    ]
    return filtered


def filter_global_visual_contract(global_visual_contract: Mapping[str, Any]) -> dict[str, Any]:
    """Project the mixed UI result contract to its schema-defined visual fields."""
    if not isinstance(global_visual_contract, Mapping) or not global_visual_contract:
        raise ValueError("V6 global visual contract must be nonempty")
    filtered = {
        field: _stable_json_copy(global_visual_contract[field])
        for field in _VISUAL_CONTRACT_FIELDS
        if field in global_visual_contract
    }
    if not filtered:
        raise ValueError("V6 global visual contract must contain visual instructions")
    return filtered


def compile_confirmed_page_prompt(
    global_visual_contract: Mapping[str, Any],
    confirmed_page: Mapping[str, Any],
    qa_feedback: Sequence[str] = (),
) -> str:
    """Serialize the ordered, sealed UI authority without truncation or reinterpretation."""
    visual_contract = filter_global_visual_contract(global_visual_contract)
    page = filter_confirmed_page_for_prompt(confirmed_page)
    payload: dict[str, Any] = {
        "system_generation_constraints": list(SYSTEM_GENERATION_CONSTRAINTS),
        "frozen_global_visual_contract": visual_contract,
        "geometry_and_fixed_layer_exclusions": copy.deepcopy(
            GEOMETRY_AND_FIXED_LAYER_EXCLUSIONS
        ),
        "confirmed_effective_body": page["effective_body"],
        "confirmed_attachment_extracts": page["attachment_extracts"],
        "confirmed_chart_facts": page["chart_facts"],
        "confirmed_image_requirements": page["image_requirements"],
        "confirmed_usable_references": page["reference_images"],
        "confirmed_degradation_expressions": page["degradations"],
    }
    if qa_feedback:
        payload["actionable_qa_feedback"] = [str(item) for item in qa_feedback]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def estimate_frozen_page_chars(
    global_contract: Mapping[str, Any], page: Mapping[str, Any],
) -> int:
    """Count the complete final prompt without truncating user material."""
    return len(compile_confirmed_page_prompt(global_contract, page))

"""Layered, non-blocking QA for V6 Image2 body candidates."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image

from codex_subscription_runtime import invoke_structured
from workflow_v6_prompt_contract import (
    filter_confirmed_page_for_prompt,
    filter_global_visual_contract,
)
import workflow_v6_media as v6_media


EXPECTED_SIZE = (1904, 896)
PROMPT_LIMIT = 32_000
SEMANTIC_CHECKS = (
    "confirmed_content_and_requirements",
    "global_style_followed",
    "body_region_composition",
    "fixed_layers_absent",
    "confirmed_references_recognizable_and_fused",
    "no_fabricated_real_world_evidence",
    "reference_high_fidelity_best_effort",
)
CORRECTION_ACTIONS = (
    "preserve", "keep", "maintain", "restore", "remove", "avoid", "ensure",
    "increase", "reduce", "improve", "align", "use", "replace", "correct",
)
CORRECTION_TARGETS = (
    "confirmed_reference", "screenshot", "logo", "photo", "fixed_title",
    "fixed_logo", "footer", "page_number", "body_region", "confirmed_body",
    "image_requirement", "color_palette", "typography", "spacing", "aspect_ratio",
    "legibility", "crop", "recognizable_identity", "contrast_relation",
    "body_geometry", "fabricated_identity", "brand", "event", "product",
    "institution", "evidence",
)
CORRECTION_CONSTRAINTS = (
    "aspect_ratio", "legibility", "recognizable", "uncropped", "remove_fixed_layer",
    "match_confirmed_body", "match_image_requirement", "match_style_property",
    "no_fabricated_identity", "body_region_only", "contrast_relation", "align_geometry",
    "high_fidelity_best_effort",
)
_SAFE_CORRECTION_TUPLES = frozenset({
    ("fixed_layers_absent", "remove", target, "remove_fixed_layer")
    for target in ("fixed_title", "fixed_logo", "footer", "page_number")
} | {
    ("confirmed_references_recognizable_and_fused", action, target, constraint)
    for action, target, constraint in (
        ("preserve", "confirmed_reference", "recognizable"),
        ("keep", "confirmed_reference", "recognizable"),
        ("preserve", "screenshot", "recognizable"),
        ("keep", "screenshot", "uncropped"),
        ("preserve", "logo", "aspect_ratio"),
        ("keep", "logo", "recognizable"),
        ("preserve", "photo", "recognizable"),
        ("keep", "photo", "legibility"),
    )
} | {
    ("no_fabricated_real_world_evidence", "remove", target, "no_fabricated_identity")
    for target in ("fabricated_identity", "brand", "event", "product", "institution", "evidence")
} | {
    ("global_style_followed", "use", "color_palette", "match_style_property"),
    ("global_style_followed", "align", "typography", "match_style_property"),
    ("global_style_followed", "use", "spacing", "match_style_property"),
    ("global_style_followed", "increase", "contrast_relation", "contrast_relation"),
    ("confirmed_content_and_requirements", "restore", "confirmed_body", "match_confirmed_body"),
    ("confirmed_content_and_requirements", "use", "image_requirement", "match_image_requirement"),
    ("body_region_composition", "correct", "aspect_ratio", "aspect_ratio"),
    ("body_region_composition", "align", "body_geometry", "align_geometry"),
    ("reference_high_fidelity_best_effort", "preserve", "screenshot", "legibility"),
    ("reference_high_fidelity_best_effort", "keep", "screenshot", "uncropped"),
    ("reference_high_fidelity_best_effort", "preserve", "logo", "aspect_ratio"),
    ("reference_high_fidelity_best_effort", "keep", "logo", "aspect_ratio"),
    ("reference_high_fidelity_best_effort", "preserve", "photo", "recognizable"),
})


def _issue(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _trace_value(value: Any) -> Mapping[str, Any] | None:
    try:
        if isinstance(value, Mapping):
            return value
        if isinstance(value, (str, Path)):
            parsed = json.loads(Path(value).read_text(encoding="utf-8"))
            return parsed if isinstance(parsed, Mapping) else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return None


def _has_visual_style(contract: Any) -> bool:
    if not isinstance(contract, Mapping) or not contract:
        return False
    visual_style = contract.get("visual_style")
    if "visual_style" in contract:
        return isinstance(visual_style, str) and bool(visual_style.strip())
    return any(isinstance(value, str) and value.strip() for value in contract.values())


def mechanical_review(
    *, request: Any, output: Path, receipt_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate free local evidence before spending a semantic-model call."""
    issues: list[dict[str, str]] = []
    output = Path(output)
    data: bytes | None = None
    mime_type: str | None = None
    dimensions: tuple[int, int] | None = None

    if len(request.prompt) > PROMPT_LIMIT:
        issues.append(_issue("prompt_over_limit", "compiled prompt exceeds 32000 characters"))
    if not _has_visual_style(receipt_inputs.get("visual_contract")):
        issues.append(_issue("visual_style_empty", "frozen visual style is empty"))
    if (
        request.operation not in {"generate", "edit"}
        or (request.operation == "generate" and request.input_images)
        or (request.operation == "edit" and not request.input_images)
        or len(request.input_images) != len(request.image_roles)
        or len(request.input_images) != len(request.input_sha256s)
    ):
        issues.append(_issue("operation_input_mismatch", "operation and confirmed inputs do not align"))

    input_records: list[dict[str, str]] = []
    for path, role, expected in zip(
        request.input_images, request.image_roles, request.input_sha256s,
    ):
        try:
            current = hashlib.sha256(
                v6_media._read_file_limited(output.parents[2], Path(path))
            ).hexdigest()
        except (OSError, ValueError):
            current = ""
        if not re.fullmatch(r"[0-9a-f]{64}", str(expected)) or current != expected:
            issues.append(_issue("input_digest_mismatch", "a confirmed input no longer matches its digest"))
        input_records.append({"role": str(role), "path": str(Path(path)), "sha256": str(expected)})

    if not output.is_file():
        issues.append(_issue("output_missing", "candidate output is missing"))
    else:
        try:
            data = v6_media._read_file_limited(output.parents[2], output)
            opened, mime_type = v6_media._open_raster(data)
            try:
                dimensions = opened.size
            finally:
                opened.close()
        except (OSError, ValueError, Image.UnidentifiedImageError):
            issues.append(_issue("output_undecodable", "candidate output cannot be decoded"))
        if dimensions is not None and dimensions != EXPECTED_SIZE:
            issues.append(_issue(
                "output_wrong_size",
                f"candidate is {dimensions[0]}x{dimensions[1]}, expected 1904x896",
            ))
        if mime_type is not None and mime_type != "image/png":
            issues.append(_issue("output_wrong_mime", f"candidate MIME is {mime_type}, expected image/png"))

    trace = _trace_value(receipt_inputs.get("trace", receipt_inputs.get("trace_path")))
    trace_matches = trace is not None and data is not None
    if trace_matches:
        expected_digest = hashlib.sha256(data).hexdigest()
        outputs = trace.get("outputs")
        canonical = str(output.resolve())
        bound_output = next(
            (
                item for item in outputs
                if isinstance(item, Mapping) and item.get("path") == canonical
            ),
            None,
        ) if isinstance(outputs, list) else None
        trace_matches = bool(
            trace.get("operation") == request.operation
            and trace.get("model") == "gpt-image-2"
            and trace.get("quality") == request.quality
            and trace.get("size") == "1904x896"
            and trace.get("input_images") == input_records
            and isinstance(bound_output, Mapping)
            and bound_output.get("sha256") == expected_digest
            and bound_output.get("mime_type") == "image/png"
            and all(
                bound_output.get(key) == "image/png"
                for key in ("mime", "content_type") if key in bound_output
            )
        )
    if not trace_matches:
        issues.append(_issue("output_trace_mismatch", "candidate output does not match its generation trace"))

    return {
        "artifact_version": "mechanical-qa-v6",
        "accepted": not issues,
        "checks": {"local_contract": "pass" if not issues else "fail"},
        "issues": issues,
    }


def output_schema() -> dict[str, Any]:
    correction = {
        "type": "object",
        "additionalProperties": False,
        "required": ["check", "action", "target", "constraint", "correction"],
        "properties": {
            "check": {"type": "string", "enum": list(SEMANTIC_CHECKS)},
            "action": {"type": "string", "enum": list(CORRECTION_ACTIONS)},
            "target": {"type": "string", "enum": list(CORRECTION_TARGETS)},
            "constraint": {"type": "string", "enum": list(CORRECTION_CONSTRAINTS)},
            "correction": {"type": "string", "minLength": 1, "maxLength": 500},
        },
    }
    check = {
        "type": "object",
        "additionalProperties": False,
        "required": ["result", "detail", "correction"],
        "properties": {
            "result": {"type": "string", "enum": ["pass", "fail"]},
            "detail": {"type": "string", "minLength": 1},
            "correction": {"anyOf": [{"type": "null"}, correction]},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["checks", "issues"],
        "properties": {
            "checks": {
                "type": "object",
                "additionalProperties": False,
                "required": list(SEMANTIC_CHECKS),
                "properties": {name: check for name in SEMANTIC_CHECKS},
            },
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["code", "correction"],
                    "properties": {
                        "code": {"type": "string", "minLength": 1},
                        "correction": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    }


def semantic_review(
    *, image: Path, confirmed_page: Mapping[str, Any],
    visual_contract: Mapping[str, Any], reference_roles: Sequence[str],
    timeout: float = 180,
) -> dict[str, Any]:
    """Perform exactly one lightweight semantic review of a valid candidate."""
    prompt = (
        "Review Image 1 only against the frozen confirmed V6 body contract below. Check: "
        "the rendered body is consistent with the confirmed effective body and explicit image "
        "requirements; the frozen global style is followed; the composition respects the "
        "17:8 1904x896 body region; Image2 did not generate the fixed main title, fixed SVG Logo, "
        "footer, or page number; every required confirmed real reference remains recognizable "
        "and is fused according to its confirmed role; no unreferenced real identity, brand, "
        "event, product, institution, or evidence was fabricated; and Logo or screenshot "
        "references are not severely distorted beyond high-fidelity best effort. A brand or "
        "background-board text naturally present in a confirmed news or meeting photo is allowed. "
        "Explicit exclusions: do not demand pixel identity, exact original hash appearance, "
        "exhaustive word/number/person/institution coverage, exact-material placement, "
        "reconstruction suitability, editable-object suitability, post-reconstruction comparison, "
        "overlay repair, or re-explanation of the design. For each failed check, emit correction "
        "as a structured object with that exact check code, an allowed action, typed target, "
        "concrete constraint, and a concise natural-language correction. For passed checks use "
        "null. Classification uses only the structured fields; correction language is not parsed.\n"
        + json.dumps({
            "confirmed_page": filter_confirmed_page_for_prompt(confirmed_page),
            "global_visual_contract": filter_global_visual_contract(visual_contract),
            "confirmed_reference_roles": list(reference_roles),
        }, ensure_ascii=False, sort_keys=True)
    )
    project = Path(image).parents[2]
    semantic_timeout = min(float(timeout), 180.0)
    if semantic_timeout <= 0:
        raise ValueError("semantic QA timeout must be positive")
    result = invoke_structured(
        project,
        role="v6-light-semantic-qa",
        prompt=prompt,
        images=[Path(image)],
        output_schema=output_schema(),
        timeout=semantic_timeout,
    )
    checks = {
        name: dict(result.value["checks"][name]) for name in SEMANTIC_CHECKS
    }
    issues = [dict(item) for item in result.value["issues"]]
    failed = [name for name, value in checks.items() if value.get("result") != "pass"]
    return {
        "artifact_version": "semantic-qa-v6",
        "accepted": not failed and not issues,
        "checks": checks,
        "issues": issues,
        "score": len(SEMANTIC_CHECKS) - len(failed),
    }


def _correction(check_code: str, value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    structured = value.get("correction")
    if not isinstance(structured, Mapping) or set(structured) != {
        "check", "action", "target", "constraint", "correction",
    }:
        return None
    if structured.get("check") != check_code:
        return None
    action = structured.get("action")
    target = structured.get("target")
    constraint = structured.get("constraint")
    text = structured.get("correction")
    identity = (check_code, action, target, constraint)
    if (
        identity not in _SAFE_CORRECTION_TUPLES
        or not isinstance(text, str)
        or not text.strip()
        or len(text.strip()) > 500
    ):
        return None
    return text.strip()


def actionable_retry_feedback(
    current: Mapping[str, Any], previous: Mapping[str, Any] | None,
) -> list[str]:
    """Return only new, nonempty, structured corrections, preserving their text."""
    previous_items = set(actionable_retry_feedback(previous, None)) if previous else set()
    feedback: list[str] = []
    checks = current.get("checks")
    if isinstance(checks, Mapping):
        for code in SEMANTIC_CHECKS:
            check = checks.get(code)
            if not isinstance(check, Mapping) or check.get("result") != "fail":
                continue
            text = _correction(code, check)
            if text:
                feedback.append(text)
    unique: list[str] = []
    seen = set(previous_items)
    for item in feedback:
        identity = item.casefold()
        if identity not in {value.casefold() for value in seen}:
            seen.add(item)
            unique.append(item)
    return unique


def review_candidate(
    project: Path,
    *,
    image: Path,
    effective_page: Mapping[str, Any],
    style_contract: Mapping[str, Any],
    fixed_logo_name: str,
    timeout: float,
) -> dict[str, Any]:
    """Compatibility wrapper; fixed-logo identity is a native-layer exclusion."""
    del project, fixed_logo_name
    roles = [
        str(item.get("purpose", item.get("role", "")))
        for item in effective_page.get("reference_images", [])
        if isinstance(item, Mapping)
    ]
    return semantic_review(
        image=image,
        confirmed_page=effective_page,
        visual_contract=style_contract,
        reference_roles=roles,
        timeout=timeout,
    )


def improved(previous: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    previous_score = int(previous.get("score", 0))
    candidate_score = int(candidate.get("score", 0))
    if candidate_score != previous_score:
        return candidate_score > previous_score
    return len(candidate.get("issues", [])) < len(previous.get("issues", []))

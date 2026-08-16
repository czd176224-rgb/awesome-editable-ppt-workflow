"""Build the closed, sealed effective authority for one Word page."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from visual_contract_validation import validate_strict_visual_contract


ARTIFACT_VERSION = "effective-page-authority-v3"
PRECEDENCE = (
    "fixed_hard_rules",
    "word_facts",
    "page_comments",
    "ui_global_soft_style",
    "evidence_material",
    "model_creativity",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DIRECTIVE_KINDS = {
    "visual_override",
    "material_requirement",
    "content_override",
    "fixed_override",
    "mixed",
    "note",
}
_VISUAL_TARGETS = {
    "visual.image_rendering": ("soft_preferences", "image_rendering", "rendering"),
    "visual.image_ratio": ("soft_preferences", "image_role", "proportion"),
    "visual.layout": ("soft_preferences", "layout_preferences"),
}
_WORD_TARGETS = {"word.body_text", "word.facts", "word.tables"}
_FIXED_TARGETS = {
    "fixed.body_geometry",
    "fixed.page_title",
    "fixed.logo",
    "fixed.footer",
    "fixed.page_number",
}
_MATERIAL_TARGETS = {
    "material.page_image": ("page_images", ("material_id", "asset_id")),
    "material.attachment": ("attachment_evidence", ("material_id", "evidence_id", "asset_id")),
    "material.search_evidence": ("search_evidence", ("material_id", "asset_id", "evidence_id")),
}
_AUTHORITY_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "schemas" / "effective_page_authority_v3.schema.json").read_text(
        encoding="utf-8"
    )
)
_AUTHORITY_VALIDATOR = Draft202012Validator(_AUTHORITY_SCHEMA)


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _seal_digest(value: Mapping[str, Any]) -> str:
    unsealed = copy.deepcopy(dict(value))
    unsealed.pop("sealed_sha256", None)
    return hashlib.sha256(_canonical_json_bytes(unsealed)).hexdigest()


def verify_effective_page_authority_seal(value: Mapping[str, Any]) -> bool:
    """Return whether ``value`` is a valid authority artifact with an intact seal."""
    if not isinstance(value, Mapping):
        return False
    recorded = value.get("sealed_sha256")
    if not isinstance(recorded, str) or not _SHA256.fullmatch(recorded):
        return False
    try:
        return recorded == _seal_digest(value) and not any(_AUTHORITY_VALIDATOR.iter_errors(value))
    except (TypeError, ValueError):
        return False


def _visual_contract(style_execution: Mapping[str, Any]) -> dict[str, Any]:
    contract: dict[str, Any] = {}
    for section in ("hard_constraints", "soft_preferences", "creative_freedom"):
        value = style_execution.get(section)
        if not isinstance(value, Mapping):
            raise ValueError(f"style_execution {section} must be an object")
        contract[section] = copy.deepcopy(dict(value))
    return contract


def _apply_visual_overrides(
    contract: dict[str, Any], overrides: Mapping[tuple[str, ...], str]
) -> None:
    for path, value in overrides.items():
        parent: dict[str, Any] = contract
        for segment in path[:-1]:
            child = parent.get(segment)
            if not isinstance(child, dict):
                raise ValueError(f"style_execution path {'.'.join(path[:-1])} must be an object")
            parent = child
        parent[path[-1]] = [value] if path == ("soft_preferences", "layout_preferences") else value


def _normalized_directives(
    directives: list[Mapping[str, Any]],
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]],
    dict[tuple[str, ...], str],
]:
    approved: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    visual_overrides: dict[tuple[str, ...], str] = {}
    seen_ids: set[str] = set()
    for index, directive in enumerate(directives, start=1):
        if not isinstance(directive, Mapping):
            raise ValueError("each directive must be an object")
        if set(directive) != {"directive_id", "kind", "text", "decisions"}:
            raise ValueError("resolved directive must contain only directive_id, kind, text, and decisions")
        text = directive.get("text")
        kind = directive.get("kind")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("directive text is required")
        if kind not in _DIRECTIVE_KINDS:
            raise ValueError("directive kind is not supported")
        directive_id = directive.get("directive_id", f"directive-{index}")
        if not isinstance(directive_id, str) or not directive_id.strip():
            raise ValueError("directive_id must be a non-empty string")
        directive_id = directive_id.strip()
        if directive_id in seen_ids:
            raise ValueError(f"duplicate directive_id: {directive_id}")
        seen_ids.add(directive_id)
        decisions = directive.get("decisions")
        if not isinstance(decisions, list):
            raise ValueError("resolved directive decisions must be an array")
        for decision in decisions:
            if not isinstance(decision, Mapping):
                raise ValueError("each resolved decision must be an object")
            target = decision.get("target")
            action = decision.get("action")
            if not isinstance(target, str) or not isinstance(action, str):
                raise ValueError("resolved decision target and action are required")
            if target in _FIXED_TARGETS or target in _WORD_TARGETS:
                if not set(decision).issubset({"target", "action", "value"}):
                    raise ValueError("protected-layer decision has unsupported fields")
                rejected.append(
                    {
                        "code": (
                            "fixed_layer_override_rejected"
                            if target in _FIXED_TARGETS
                            else "word_fact_override_rejected"
                        ),
                        "directive_id": directive_id,
                        "target": target,
                        "detail": "page comments cannot change fixed layers or authoritative Word content",
                    }
                )
                continue
            if target in _VISUAL_TARGETS:
                if kind not in {"visual_override", "mixed"}:
                    raise ValueError("visual decisions require a visual_override or mixed directive")
                if set(decision) != {"target", "action", "value"} or action != "set":
                    raise ValueError("visual decisions require target, action=set, and value")
                value = decision.get("value")
                if not isinstance(value, str) or not value:
                    raise ValueError("visual decision value must be a non-empty string")
                normalized = {
                    "directive_id": directive_id,
                    "target": target,
                    "action": "set",
                    "value": value,
                }
                approved.append(normalized)
                visual_overrides[_VISUAL_TARGETS[target]] = value
                continue
            if target in _MATERIAL_TARGETS:
                if kind not in {"material_requirement", "mixed"}:
                    raise ValueError("material decisions require a material_requirement or mixed directive")
                enterprise_fields = {
                    "target", "action", "material_id", "directive_id", "parent_directive_id",
                    "entity", "query", "material_role",
                }
                decision_fields = set(decision)
                if decision_fields not in ({"target", "action", "material_id"}, enterprise_fields) or action != "require":
                    raise ValueError("material decisions require target, action=require, and material_id")
                if decision_fields == enterprise_fields and (
                    target != "material.search_evidence" or decision.get("material_role") != "enterprise_logo"
                ):
                    raise ValueError("enterprise Logo decisions require the enterprise search role")
                material_id = decision.get("material_id")
                if not isinstance(material_id, str) or not material_id:
                    raise ValueError("material_id must be a non-empty string")
                normalized = {
                        "directive_id": decision.get("directive_id", directive_id),
                        "target": target,
                        "action": "require",
                        "material_id": material_id,
                    }
                if decision_fields == enterprise_fields:
                    normalized.update({
                        "parent_directive_id": decision["parent_directive_id"],
                        "entity": decision["entity"],
                        "query": decision["query"],
                        "material_role": decision["material_role"],
                    })
                approved.append(normalized)
                continue
            raise ValueError(f"unsupported resolved decision target: {target}")
    final_visual_indexes: dict[str, int] = {}
    for index, item in enumerate(approved):
        if item["target"] in _VISUAL_TARGETS:
            final_visual_indexes[item["target"]] = index
    effective: list[dict[str, Any]] = []
    superseded: list[dict[str, Any]] = []
    for index, item in enumerate(approved):
        final_index = final_visual_indexes.get(item["target"])
        if final_index is not None and final_index != index:
            superseded.append({
                **copy.deepcopy(item),
                "superseded_by_directive_id": approved[final_index]["directive_id"],
            })
        else:
            effective.append(item)
    return effective, superseded, rejected, visual_overrides


def _material_readiness(
    approved: list[dict[str, Any]],
    evidence: Mapping[str, list[Mapping[str, Any]]],
) -> dict[str, Any]:
    blocking: list[dict[str, str]] = []
    for item in approved:
        target = item["target"]
        if target not in _MATERIAL_TARGETS:
            continue
        collection_name, identity_fields = _MATERIAL_TARGETS[target]
        available_ids = {
            candidate[field]
            for candidate in evidence[collection_name]
            if isinstance(candidate, Mapping)
            for field in identity_fields
            if isinstance(candidate.get(field), str) and candidate[field]
        }
        if item["material_id"] not in available_ids:
            blocking.append(
                {
                    "code": "required_material_missing",
                    "directive_id": item["directive_id"],
                    "target": target,
                    "material_id": item["material_id"],
                }
            )
    return {
        "status": "blocked" if blocking else "ready",
        "blocking_reasons": blocking,
    }


def build_effective_page_authority(
    *,
    page_contract: Mapping[str, Any],
    style_execution: Mapping[str, Any],
    directives: list[Mapping[str, Any]],
    page_images: list[Mapping[str, Any]],
    attachment_evidence: list[Mapping[str, Any]],
    search_evidence: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return and seal effective-page-authority-v3 without mutating Word facts."""
    if not isinstance(page_contract, Mapping):
        raise ValueError("page_contract must be an object")
    if not isinstance(style_execution, Mapping):
        raise ValueError("style_execution must be an object")
    for label, value in (
        ("directives", directives),
        ("page_images", page_images),
        ("attachment_evidence", attachment_evidence),
        ("search_evidence", search_evidence),
    ):
        if not isinstance(value, list):
            raise ValueError(f"{label} must be an array")

    page_number = page_contract.get("page_number")
    body_text = page_contract.get("body_text")
    tables = page_contract.get("tables", page_contract.get("source_tables"))
    if type(page_number) is not int or page_number < 1:
        raise ValueError("page_contract page_number must be a positive integer")
    if not isinstance(body_text, str):
        raise ValueError("page_contract body_text must be a string")
    if not isinstance(tables, list):
        raise ValueError("page_contract tables must be an array")

    authoritative_content = {
        "body_text": body_text,
        "tables": copy.deepcopy(tables),
    }
    required, superseded, rejected, visual_overrides = _normalized_directives(directives)
    effective_visual = _visual_contract(style_execution)
    _apply_visual_overrides(effective_visual, visual_overrides)
    validate_strict_visual_contract(effective_visual)
    evidence_material = {
        "page_images": copy.deepcopy(page_images),
        "attachment_evidence": copy.deepcopy(attachment_evidence),
        "search_evidence": copy.deepcopy(search_evidence),
    }

    authority: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "page_number": page_number,
        "precedence": list(PRECEDENCE),
        "fixed_hard_rules": {
            "body_width_px": 1904,
            "body_height_px": 896,
            "logo_in_body": False,
            "fixed_layers_outside_body": ["page_title", "logo", "footer", "page_number"],
        },
        "authoritative_content": authoritative_content,
        "required_directives": required,
        "superseded_directives": superseded,
        "effective_visual_contract": effective_visual,
        "evidence_material": evidence_material,
        "rejected_overrides": rejected,
        "readiness": _material_readiness(required, evidence_material),
    }
    authority["sealed_sha256"] = _seal_digest(authority)
    return authority

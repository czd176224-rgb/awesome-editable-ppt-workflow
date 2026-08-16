"""Authoritative native/hybrid reconstruction plan from Word facts, never OCR text."""

from __future__ import annotations

import re
from typing import Any, Mapping

from page_fact_plan import apply_field_overrides, factual_attachment_supplement_value
from page_coverage import build_coverage_contract


def build_native_page_plan(
    contract: Mapping[str, Any], fact_plan: Mapping[str, Any], route: Mapping[str, Any],
    coverage_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    route_name = route.get("route")
    if route_name not in {"native", "hybrid", "image"}:
        raise ValueError("native page plan requires native, hybrid, or image route")
    if coverage_contract is None:
        coverage_contract = build_coverage_contract(contract, fact_plan)
    images = []
    for binding in contract.get("asset_bindings", []):
        if not isinstance(binding, Mapping) or binding.get("asset_role") != "mandatory_inline_image":
            continue
        generation = binding.get("generation_input") if isinstance(binding.get("generation_input"), Mapping) else {}
        relative = generation.get("relative_path")
        if isinstance(relative, str):
            images.append({"asset_id": binding.get("asset_id"), "relative_path": relative})
    semantic_coverage_ids = {
        item.get("coverage_id")
        for item in (coverage_contract or {}).get("required_items", [])
        if isinstance(item, Mapping) and item.get("kind") == "semantic_unit"
    }
    authoritative_body = apply_field_overrides(str(contract.get("body_text", "")), fact_plan)
    authoritative_tables = [
        apply_field_overrides(str(value), fact_plan) for value in contract.get("source_tables", [])
    ]
    supplement_coverage_ids = {
        str(item.get("coverage_id"))
        for item in (coverage_contract or {}).get("required_items", [])
        if isinstance(item, Mapping) and item.get("kind") == "attachment_supplement"
    }
    supplements: list[dict[str, Any]] = []
    for item in fact_plan.get("attachment_supplements", []):
        if not isinstance(item, Mapping) or item.get("authorization") != "supplement_only":
            continue
        text, evidence_id, source = item.get("text"), item.get("evidence_id"), item.get("source")
        if not isinstance(text, str) or not text.strip() or not isinstance(evidence_id, str) or not evidence_id:
            raise ValueError("approved attachment supplement is incomplete")
        field = item.get("field")
        if factual_attachment_supplement_value(field, text) is None:
            raise ValueError("attachment supplement must contain an approved factual field value")
        if re.search(r"(?:https?://|file://|www\.|[A-Za-z][A-Za-z0-9+.-]*://)", text, re.IGNORECASE):
            raise ValueError("approved attachment supplement cannot contain a URL")
        if not isinstance(source, Mapping) or not all(
            isinstance(source.get(key), str) and source.get(key) for key in ("file", "locator", "sha256")
        ):
            raise ValueError("approved attachment supplement requires exact provenance")
        coverage_id = f"supplement:{evidence_id}"
        if coverage_contract and coverage_id not in supplement_coverage_ids:
            raise ValueError("approved attachment supplement is absent from the coverage contract")
        supplements.append({
            "coverage_id": coverage_id,
            "text": text.strip(),
            "field": field,
            "source": dict(source),
            "evidence_id": evidence_id,
        })
    return {
        "schema_version": "1.0", "page_number": int(contract["page_number"]), "route": route_name,
        "page_title": str(contract.get("page_title", "")), "body_text": authoritative_body,
        "tables": authoritative_tables, "required_images": images,
        "attachment_supplements": supplements,
        "semantic_units": [
            {
                "coverage_id": f"semantic:{item.get('unit_id')}",
                "text": apply_field_overrides(str(item.get("text")), fact_plan),
            }
            for item in contract.get("semantic_units", [])
            if isinstance(item, Mapping) and item.get("kind") == "sentence" and item.get("unit_id") and item.get("text")
            and (not coverage_contract or f"semantic:{item.get('unit_id')}" in semantic_coverage_ids)
        ],
        "mandatory_anchors": list(fact_plan.get("mandatory_anchors", [])),
        "coverage_contract": dict(coverage_contract or {"required_items": []}),
        "text_authority": "word_and_fact_plan_only",
    }

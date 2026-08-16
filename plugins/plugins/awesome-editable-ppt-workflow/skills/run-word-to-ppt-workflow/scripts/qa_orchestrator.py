"""Deterministic QA policy for the native-authority page contract."""

from __future__ import annotations

from typing import Any, Mapping


def risk_score(contract: Mapping[str, Any], route: Mapping[str, Any], observations: Mapping[str, Any]) -> int:
    score = 0
    native_text_authority = route.get("text_authority") == "native_overlay"
    if route.get("route") == "image" and not native_text_authority:
        score += 1
    if not native_text_authority:
        score += min(len(contract.get("detected_numbers", [])) + len(contract.get("detected_dates", [])), 2)
    score += 1 if contract.get("asset_bindings") else 0
    score += 4 if observations.get("uncertain") else 0
    score += 3 if observations.get("ocr_available") is False and route.get("route") == "image" and not native_text_authority else 0
    score += 4 if observations.get("deterministic_mismatch") else 0
    score += 3 if observations.get("unsupported_claim_uncertain") else 0
    return score


def decide_qa_path(contract: Mapping[str, Any], route: Mapping[str, Any], observations: Mapping[str, Any]) -> dict[str, Any]:
    score = risk_score(contract, route, observations)
    return {
        "schema_version": "1.0", "path": "deterministic_only", "risk_score": score,
        "max_semantic_calls": 0, "max_image_repairs": 1,
        "trigger_reason": "native_authority_contract",
    }

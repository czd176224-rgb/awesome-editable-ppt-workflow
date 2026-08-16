from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _contract() -> dict:
    return {"page_title": "主标题", "asset_bindings": [], "detected_numbers": [], "detected_dates": []}


def test_hybrid_background_scan_does_not_repeat_authoritative_anchor_qa() -> None:
    from deterministic_qa import run_deterministic_qa

    result = run_deterministic_qa(
        None,
        _contract(),
        {"mandatory_anchors": ["100亿元", "2026年"]},
        {"route": "hybrid"},
        {
            "background_text_detection_available": True,
            "background_text_detected": False,
        },
    )

    assert result["issues"] == []
    assert result["status"] == "pass"


def test_contain_mapping_is_advisory_and_never_requests_image_repair() -> None:
    from deterministic_qa import run_deterministic_qa

    result = run_deterministic_qa(
        None,
        _contract(),
        {"mandatory_anchors": []},
        {"route": "image"},
        {
            "background_text_detection_available": True,
            "background_text_detected": False,
            "aspect_mapping": "contain",
        },
    )

    assert result["status"] == "pass_with_advisory"
    assert result["repair_scope"] == "none"
    assert [item["code"] for item in result["issues"]] == ["contained_body_image"]

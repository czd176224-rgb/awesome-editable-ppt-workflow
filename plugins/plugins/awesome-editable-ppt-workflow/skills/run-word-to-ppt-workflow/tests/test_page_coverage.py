from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _contract() -> dict:
    return {
        "page_number": 3,
        "page_title": "完成第一阶段建设。",
        "body_text": "形成三项成果。2026年完成验收。",
        "semantic_units": [
            {"unit_id": "unit_001", "kind": "sentence", "text": "完成第一阶段建设。", "source_block_index": 2},
            {"unit_id": "unit_002", "kind": "sentence", "text": "形成三项成果。", "source_block_index": 3},
            {"unit_id": "unit_003", "kind": "table_row", "text": "|收入|100亿元|", "source_block_index": 4},
        ],
        "source_tables": ["|指标|数值|\n|---|---|\n|收入|100亿元|", "|阶段|状态|\n|---|---|\n|一期|完成|"],
        "asset_bindings": [
            {"asset_id": "word_asset_001", "asset_role": "mandatory_inline_image"},
            {"asset_id": "word_asset_002", "asset_role": "document_source"},
        ],
    }


def test_coverage_contract_requires_every_sentence_table_anchor_and_inline_image() -> None:
    from page_coverage import build_coverage_contract

    coverage = build_coverage_contract(_contract(), {"mandatory_anchors": ["100亿元", "2026年"]})

    assert [item["coverage_id"] for item in coverage["required_items"]] == [
        "semantic:unit_002", "table:001", "table:002",
        "anchor:001", "anchor:002", "image:word_asset_001",
    ]
    assert all(item["required"] is True for item in coverage["required_items"])
    assert coverage["fixed_title"]["text"] == "完成第一阶段建设。"


def test_title_only_anchor_is_owned_by_the_fixed_title_not_the_body_builder() -> None:
    from page_coverage import build_coverage_contract

    contract = _contract()
    contract["page_title"] = "持有12.43%的股权"
    coverage = build_coverage_contract(contract, {"mandatory_anchors": ["12.43%"]})

    assert coverage["fixed_title"]["anchors"] == ["12.43%"]
    assert not any(item["kind"] == "mandatory_anchor" for item in coverage["required_items"])


def test_render_receipt_rejects_missing_invisible_duplicate_and_unknown_items() -> None:
    from page_coverage import CoverageValidationError, build_coverage_contract, validate_render_receipt

    coverage = build_coverage_contract(_contract(), {"mandatory_anchors": ["100亿元"]})
    valid_ids = [item["coverage_id"] for item in coverage["required_items"]]
    receipts = []
    for index, item in enumerate(coverage["required_items"]):
        receipt = {
            "coverage_id": item["coverage_id"], "object_id": f"shape-{index}", "visible": True,
            "expected_sha256": item["expected"]["sha256"],
        }
        if item["expected"]["kind"] == "asset_id":
            receipt["observed_asset_id"] = item["expected"]["value"]
        else:
            receipt["observed_text"] = item["expected"]["value"]
        receipts.append(receipt)
    assert validate_render_receipt(coverage, receipts)["passed"] is True

    invalid = receipts[:-1] + [receipts[0], {"coverage_id": "unknown:x", "object_id": "x", "visible": False}]
    with pytest.raises(CoverageValidationError) as caught:
        validate_render_receipt(coverage, invalid)
    report = caught.value.report
    assert report["missing"] == [valid_ids[-1]]
    assert report["duplicates"] == [valid_ids[0]]
    assert report["unknown"] == ["unknown:x"]
    assert report["invisible"] == ["unknown:x"]


def test_coverage_identity_rejects_tampering() -> None:
    from page_coverage import build_coverage_contract, verify_coverage_contract

    coverage = build_coverage_contract(_contract(), {"mandatory_anchors": ["100亿元"]})
    assert verify_coverage_contract(coverage, expected_page_number=3) == coverage

    coverage["required_items"][0]["source"]["text"] = "被篡改"
    with pytest.raises(ValueError, match="SHA-256"):
        verify_coverage_contract(coverage, expected_page_number=3)

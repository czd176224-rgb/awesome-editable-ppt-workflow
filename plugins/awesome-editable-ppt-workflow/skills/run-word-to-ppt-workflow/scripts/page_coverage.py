"""Route-independent content coverage contract and deterministic receipt gate."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from page_fact_plan import (
    apply_field_overrides,
    extract_typed_field_value,
    factual_attachment_supplement_value,
    normalize_typed_field_value,
)


COVERAGE_VERSION = "page-coverage-v1"


class CoverageValidationError(ValueError):
    def __init__(self, report: Mapping[str, Any]):
        self.report = dict(report)
        super().__init__("render receipt does not cover every required Word item")


def normalize_coverage_text(value: Any) -> str:
    return "".join(str(value).split()).replace("％", "%").casefold()


def table_visible_text(markdown: str) -> str:
    rows = []
    for line in str(markdown).splitlines():
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and not all(set(cell) <= {"-", ":"} for cell in cells):
            rows.append("|".join(cells))
    return "\n".join(rows)


def _expected(
    value: str, *, field_values: list[Mapping[str, Any]] | None = None, kind: str = "text",
) -> dict[str, Any]:
    normalized = normalize_coverage_text(value)
    return {
        "kind": kind,
        "value": value,
        "sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "field_values": [dict(item) for item in (field_values or [])],
    }


def _item(
    coverage_id: str, kind: str, source: Mapping[str, Any], expected: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "coverage_id": coverage_id,
        "kind": kind,
        "required": True,
        "source": dict(source),
        "expected": dict(expected),
    }


def _override_field_values(raw: str, fact_plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for override in fact_plan.get("field_overrides", []):
        if not isinstance(override, Mapping):
            continue
        field, old, new = override.get("field"), override.get("word_value"), override.get("attachment_value")
        if not all(isinstance(value, str) and value for value in (field, old, new)):
            continue
        raw_value = extract_typed_field_value(raw, field)
        if raw_value is None or (
            normalize_typed_field_value(raw_value, field) != normalize_typed_field_value(old, field)
        ):
            continue
        values.append({"field": field, "value": new, "forbidden_values": [old]})
    return values


def _anchor_field_values(anchor: str, fact_plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for override in fact_plan.get("field_overrides", []):
        if not isinstance(override, Mapping):
            continue
        field, old, new = override.get("field"), override.get("word_value"), override.get("attachment_value")
        if not all(isinstance(value, str) and value for value in (field, old, new)):
            continue
        if normalize_typed_field_value(anchor, field) == normalize_typed_field_value(new, field):
            values.append({"field": field, "value": new, "forbidden_values": [old]})
    return values


def _identity(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "version": value.get("coverage_version", value.get("version")),
        "page_number": value.get("page_number"),
        "fixed_title": value.get("fixed_title", {}),
        "required_items": value.get("required_items", []),
    }


def coverage_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(_identity(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def verify_coverage_contract(value: Mapping[str, Any], *, expected_page_number: int | None = None) -> dict[str, Any]:
    if value.get("coverage_version") != COVERAGE_VERSION:
        raise ValueError("unsupported coverage contract version")
    if expected_page_number is not None and value.get("page_number") != expected_page_number:
        raise ValueError("coverage contract page number mismatch")
    if not isinstance(value.get("required_items"), list):
        raise ValueError("coverage contract required_items must be an array")
    if value.get("sha256") != coverage_sha256(value):
        raise ValueError("coverage contract SHA-256 mismatch")
    return dict(value)


def build_coverage_contract(contract: Mapping[str, Any], fact_plan: Mapping[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    page_title = str(contract.get("page_title", "")).strip()
    body_text = apply_field_overrides(str(contract.get("body_text", "")), fact_plan)
    table_text = "\n".join(
        apply_field_overrides(value, fact_plan)
        for value in contract.get("source_tables", []) if isinstance(value, str)
    )
    normalize = normalize_coverage_text
    title_key, body_key = normalize(page_title), normalize(body_text + "\n" + table_text)
    title_semantic_key = title_key.lstrip("，,。.;；：:!?！？")
    title_unit_ids: set[str] = set()
    accumulated_title = ""
    for unit in contract.get("semantic_units", []):
        if not isinstance(unit, Mapping) or unit.get("kind") != "sentence" or not title_semantic_key:
            continue
        unit_id, unit_text = unit.get("unit_id"), normalize(unit.get("text", ""))
        candidate = accumulated_title + unit_text
        if isinstance(unit_id, str) and unit_text and (
            title_semantic_key.startswith(candidate) or candidate.startswith(title_semantic_key)
        ):
            title_unit_ids.add(unit_id)
            accumulated_title = candidate
            if len(accumulated_title) >= len(title_semantic_key):
                break
        else:
            break
    for unit in contract.get("semantic_units", []):
        if not isinstance(unit, Mapping) or unit.get("kind") != "sentence":
            continue
        unit_id, text = unit.get("unit_id"), unit.get("text")
        if (
            isinstance(unit_id, str)
            and isinstance(text, str)
            and text.strip()
            and unit_id not in title_unit_ids
        ):
            approved_text = apply_field_overrides(text.strip(), fact_plan)
            items.append(_item(
                f"semantic:{unit_id}",
                "semantic_unit",
                {"text": approved_text, "source_block_index": unit.get("source_block_index")},
                _expected(approved_text, field_values=_override_field_values(text, fact_plan)),
            ))
    for index, table in enumerate(contract.get("source_tables", []), start=1):
        if isinstance(table, str) and table.strip():
            approved_table = apply_field_overrides(table.strip(), fact_plan)
            visible = table_visible_text(approved_table)
            items.append(_item(
                f"table:{index:03d}",
                "source_table",
                {"markdown": approved_table, "table_index": index},
                _expected(visible, field_values=_override_field_values(table, fact_plan)),
            ))
    fixed_title_anchors: list[str] = []
    for index, anchor in enumerate(fact_plan.get("mandatory_anchors", []), start=1):
        if isinstance(anchor, str) and anchor.strip():
            anchor = anchor.strip()
            key = normalize(anchor)
            if key and key in body_key:
                items.append(_item(
                    f"anchor:{index:03d}", "mandatory_anchor", {"value": anchor},
                    _expected(anchor, field_values=_anchor_field_values(anchor, fact_plan)),
                ))
            elif key and key in title_key:
                fixed_title_anchors.append(anchor)
            else:
                raise ValueError(f"mandatory anchor is absent from the locked Word page: {anchor}")
    for binding in contract.get("asset_bindings", []):
        if not isinstance(binding, Mapping) or binding.get("asset_role") != "mandatory_inline_image":
            continue
        asset_id = binding.get("asset_id")
        if isinstance(asset_id, str) and asset_id:
            items.append(_item(
                f"image:{asset_id}", "mandatory_inline_image", {"asset_id": asset_id},
                _expected(asset_id, kind="asset_id"),
            ))
    supplement_ids: set[str] = set()
    for supplement in fact_plan.get("attachment_supplements", []):
        if not isinstance(supplement, Mapping) or supplement.get("authorization") != "supplement_only":
            continue
        text = supplement.get("text")
        evidence_id = supplement.get("evidence_id")
        source = supplement.get("source")
        if not isinstance(text, str) or not text.strip() or not isinstance(evidence_id, str) or not evidence_id:
            raise ValueError("approved attachment supplement is incomplete")
        field = supplement.get("field")
        if factual_attachment_supplement_value(field, text) is None:
            raise ValueError("attachment supplement must contain an approved factual field value")
        if re.search(r"(?:https?://|file://|www\.|[A-Za-z][A-Za-z0-9+.-]*://)", text, re.IGNORECASE):
            raise ValueError("approved attachment supplement cannot contain a URL")
        if not isinstance(source, Mapping) or not all(
            isinstance(source.get(key), str) and source.get(key) for key in ("file", "locator", "sha256")
        ):
            raise ValueError("approved attachment supplement requires exact provenance")
        coverage_id = f"supplement:{evidence_id}"
        if coverage_id in supplement_ids:
            raise ValueError("approved attachment supplement evidence id must be unique")
        supplement_ids.add(coverage_id)
        items.append(_item(
            coverage_id,
            "attachment_supplement",
            {"text": text.strip(), "field": field, "provenance": dict(source)},
            _expected(text.strip()),
        ))
    fixed_title = {
        "text": page_title,
        "sha256": hashlib.sha256(page_title.encode("utf-8")).hexdigest(),
        "anchors": fixed_title_anchors,
    }
    identity = {"version": COVERAGE_VERSION, "page_number": contract.get("page_number"), "fixed_title": fixed_title, "required_items": items}
    digest = coverage_sha256(identity)
    return {"schema_version": "1.0", "coverage_version": COVERAGE_VERSION, "page_number": contract.get("page_number"), "fixed_title": fixed_title, "required_items": items, "sha256": digest}


def validate_render_receipt(coverage: Mapping[str, Any], receipts: list[Mapping[str, Any]]) -> dict[str, Any]:
    required = [str(item.get("coverage_id")) for item in coverage.get("required_items", []) if isinstance(item, Mapping)]
    actual = [str(item.get("coverage_id")) for item in receipts if isinstance(item, Mapping)]
    counts = Counter(actual)
    required_set = set(required)
    expected_by_id = {
        str(item.get("coverage_id")): item.get("expected")
        for item in coverage.get("required_items", [])
        if isinstance(item, Mapping) and isinstance(item.get("expected"), Mapping)
    }
    mismatched: list[str] = []
    for receipt in receipts:
        if not isinstance(receipt, Mapping):
            continue
        coverage_id = str(receipt.get("coverage_id"))
        expected = expected_by_id.get(coverage_id)
        if not isinstance(expected, Mapping):
            continue
        if receipt.get("expected_sha256") != expected.get("sha256"):
            mismatched.append(coverage_id)
            continue
        if expected.get("kind") == "asset_id":
            matched = receipt.get("observed_asset_id") == expected.get("value")
        else:
            observed_text = str(receipt.get("observed_text", ""))
            observed = normalize_coverage_text(observed_text)
            matched = normalize_coverage_text(expected.get("value", "")) in observed
            for field_value in expected.get("field_values", []):
                if not matched or not isinstance(field_value, Mapping):
                    break
                field, current = field_value.get("field"), field_value.get("value")
                if not isinstance(field, str) or not isinstance(current, str):
                    matched = False
                    break
                observed_field_value = extract_typed_field_value(observed_text, field)
                if observed_field_value is None:
                    matched = False
                    break
                actual_normalized = normalize_typed_field_value(observed_field_value, field)
                matched = actual_normalized == normalize_typed_field_value(current, field) and not any(
                    actual_normalized == normalize_typed_field_value(old, field)
                    for old in field_value.get("forbidden_values", [])
                    if isinstance(old, str) and old
                )
        if not matched:
            mismatched.append(coverage_id)
    report = {
        "passed": False,
        "missing": sorted(value for value in required if counts[value] == 0),
        "duplicates": sorted(value for value, count in counts.items() if count > 1 and value in required_set),
        "unknown": sorted(value for value in actual if value not in required_set),
        "invisible": sorted(str(item.get("coverage_id")) for item in receipts if isinstance(item, Mapping) and item.get("visible") is not True),
        "mismatched": sorted(set(mismatched)),
    }
    report["passed"] = not any(
        report[key] for key in ("missing", "duplicates", "unknown", "invisible", "mismatched")
    )
    if not report["passed"]:
        raise CoverageValidationError(report)
    return report


def verified_coverage_result(
    coverage: Mapping[str, Any], receipt_path: Path,
) -> dict[str, Any]:
    """Derive the persisted coverage result only from a verified receipt file."""
    verified = verify_coverage_contract(coverage)
    raw = Path(receipt_path).read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    receipts = payload.get("receipts") if isinstance(payload, Mapping) else None
    if not isinstance(receipts, list) or any(not isinstance(item, Mapping) for item in receipts):
        raise ValueError("coverage receipt entries are missing or invalid")
    report = validate_render_receipt(verified, receipts)
    return {
        "passed": bool(report["passed"]),
        "missing": list(report["missing"]),
        "count": len(verified.get("required_items", [])),
        "revision": verified["sha256"],
        "receipt_sha256": hashlib.sha256(raw).hexdigest(),
    }

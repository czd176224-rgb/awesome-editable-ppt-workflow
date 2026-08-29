"""Confirmed V6 page material records for Image2 body generation."""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from workflow_v6_contract import canonical_sha256
from natural_comment_resolver import resolve_comment_deterministically
from workflow_v6_media import NormalizedReference, normalize_reference


PAGE_MATERIALS_VERSION = "page-materials-v6"
_ATTACHMENT_EXTRACTION_CACHE: dict[str, dict[str, Any]] = {}
_SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
_REFERENCE_IMAGE_SCHEMA = json.loads(
    (_SCHEMAS / "reference_image_v6.schema.json").read_text(encoding="utf-8")
)
_PAGE_MATERIALS_SCHEMA = json.loads(
    (_SCHEMAS / "page_materials_v6.schema.json").read_text(encoding="utf-8")
)
_SCHEMA_REGISTRY = Registry().with_resources((
    (
        _REFERENCE_IMAGE_SCHEMA["$id"],
        Resource.from_contents(_REFERENCE_IMAGE_SCHEMA),
    ),
))
_PAGE_MATERIALS_VALIDATOR = Draft202012Validator(
    _PAGE_MATERIALS_SCHEMA, registry=_SCHEMA_REGISTRY
)


@dataclass(frozen=True)
class CommentResolution:
    """The only comment-derived material inputs that may reach V6 confirmation."""

    effective_body: str
    attachment_requirements: tuple[dict[str, Any], ...]
    image_requirements: tuple[dict[str, Any], ...]
    degradations: tuple[dict[str, Any], ...]


_FACT_REPLACEMENT = re.compile(
    r"(?:change|replace)\s+(?:the\s+)?(?:key\s+)?(?:fact|data)\s+(?:to|with)\s+(.+)$",
    re.IGNORECASE,
)
_FACT_FROM_TO = re.compile(
    r"(?:change|replace)\s+(?:the\s+)?(?:[\w-]+\s+)?(?:fact|data)\s+"
    r"(?P<old>.+?)\s+(?:to|with)\s+(?P<new>.+)$",
    re.IGNORECASE,
)
_CHINESE_FROM_TO = re.compile(r"将(?P<old>.+?)(?:改为|修改为|替换为)(?P<new>.+)$")
_FINAL_BODY_REPLACEMENT = re.compile(
    r"(?:replace)\s+(?:the\s+)?final\s+body\s+paragraph\s+with\s+(?P<new>.+)$",
    re.IGNORECASE,
)
_CHINESE_FINAL_BODY_REPLACEMENT = re.compile(r"正文最后一段替换为(?P<new>.+)$")
_BODY_REPLACEMENT = re.compile(
    r"(?:change|replace)\s+(?:the\s+)?body\s+(?:to|with)\s+(?P<new>.+)$",
    re.IGNORECASE,
)
_TABLE_REPLACEMENT = re.compile(r"(?:replace)\s+(?:the\s+)?table\s+with\s+(?P<new>.+)$", re.IGNORECASE | re.DOTALL)
_CHINESE_TABLE_REPLACEMENT = re.compile(r"(?:将)?(?:表格).{0,24}(?:替换为|改为)(?P<new>.+)$", re.DOTALL)
_PERSON_PHOTO = re.compile(
    r"(?:real\s+(?:photo|photograph)|(?:photo|photograph)\s+of)\s+(?:of\s+)?"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})",
    re.IGNORECASE,
)
_BRAND_LOGO = re.compile(
    r"(?:use|add|show) +(?>the +)?([A-Z][A-Za-z0-9&.-]{1,}) +logo", re.IGNORECASE,
)
_ATTACHMENT_ROWS = re.compile(
    r"(?:selected\s+)?attachment\s+rows|(?:selected\s+)?rows\s+(?:from|in)\s+(?:the\s+)?attachment",
    re.IGNORECASE,
)
_NAMED_ATTACHMENT = re.compile(r"attachment +([A-Za-z0-9._-]+)", re.IGNORECASE)
_ATTACHMENT_ROW_NUMBERS = re.compile(r"\brows?\s+([0-9][0-9,\s]*)", re.IGNORECASE)
_ATTACHMENT_FIELDS = re.compile(r"\bfields?\s+([A-Za-z][A-Za-z0-9 _,-]*?)(?:[.;]|$)", re.IGNORECASE)
_FIXED_TITLE_CHANGE = re.compile(
    r"(?:change|replace).{0,24}(?:title)|(?:title).{0,24}(?:change|replace)",
    re.IGNORECASE,
)


def _comment_id(comment: Mapping[str, Any], position: int) -> str:
    value = comment.get("comment_id", position)
    return str(value)


def _fact_replacement(text: str) -> tuple[str | None, str] | None:
    from_to = _FACT_FROM_TO.search(text.strip()) or _CHINESE_FROM_TO.search(text.strip())
    if from_to:
        old = from_to.group("old").strip()
        replacement = from_to.group("new").strip()
        if old and replacement:
            return old, replacement
    match = _FACT_REPLACEMENT.search(text.strip())
    if not match:
        return None
    replacement = match.group(1).strip()
    return (None, replacement) if replacement else None


def _real_person_photo(text: str) -> str | None:
    match = _PERSON_PHOTO.search(text.strip())
    if not match:
        return None
    return match.group(1).strip().rstrip(".,;:") or None


def _brand_logo(text: str) -> str | None:
    match = _BRAND_LOGO.search(text.strip())
    if not match:
        return None
    return match.group(1) or None


def _paragraphs(value: str) -> list[str]:
    return [paragraph for paragraph in re.split(r"\n\s*\n", value) if paragraph.strip()]


def _replace_word_content(*, body: str, target: str, text: str) -> tuple[str | None, bool]:
    """Apply a deterministic change and flag source-target ambiguity separately."""
    if target == "word.facts":
        replacement = _fact_replacement(text)
        if replacement is None:
            return None, False
        old, new = replacement
        if old:
            source_target = old + new[-1] if new[-1:] in ".。!?！？" and old + new[-1] in body else old
            if body.count(source_target) != 1:
                return None, True
            return body.replace(source_target, new, 1), False
        paragraphs = _paragraphs(body)
        return (new, False) if len(paragraphs) == 1 else (None, False)
    if target == "word.body_text":
        final = _FINAL_BODY_REPLACEMENT.search(text) or _CHINESE_FINAL_BODY_REPLACEMENT.search(text)
        if final:
            paragraphs = _paragraphs(body)
            replacement = final.group("new").strip()
            if not paragraphs or not replacement:
                return None, False
            paragraphs[-1] = replacement
            return "\n\n".join(paragraphs), False
        whole = _BODY_REPLACEMENT.search(text)
        return (
            (whole.group("new").strip(), False)
            if whole and whole.group("new").strip() else (None, False)
        )
    if target == "word.tables":
        match = _TABLE_REPLACEMENT.search(text) or _CHINESE_TABLE_REPLACEMENT.search(text)
        if not match or not match.group("new").strip():
            return None, False
        paragraphs = _paragraphs(body)
        table_indexes = [index for index, paragraph in enumerate(paragraphs) if "|" in paragraph]
        if len(table_indexes) != 1:
            return None, True
        paragraphs[table_indexes[0]] = match.group("new").strip()
        return "\n\n".join(paragraphs), False
    return None, False


def _available_attachments(
    *,
    available_attachment_ids: Sequence[str] | None,
    available_attachments: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    if available_attachments is not None:
        if any(not isinstance(item, Mapping) for item in available_attachments):
            raise ValueError("available attachments must be objects")
        normalized = [dict(item) for item in available_attachments]
    else:
        normalized = [{"attachment_id": value} for value in (available_attachment_ids or ())]
    for item in normalized:
        attachment_id = item.get("attachment_id")
        if not isinstance(attachment_id, str) or not attachment_id:
            raise ValueError("available attachment ids must be non-empty strings")
    return normalized


def _attachment_requirement(
    *, text: str, comment_id: str, available_attachments: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    normalized = text.lower()
    explicit_ids = [str(item["attachment_id"]) for item in available_attachments if str(item["attachment_id"]).lower() in normalized]
    named = _NAMED_ATTACHMENT.search(text)
    if named and named.group(1) not in explicit_ids:
        explicit_ids.append(named.group(1))
    attachment_id = next(
        (
            str(item["attachment_id"])
            for item in available_attachments
            if str(item["attachment_id"]) in explicit_ids
        ),
        None,
    )
    if attachment_id is None and len(available_attachments) == 1:
        attachment_id = str(available_attachments[0]["attachment_id"])
    if attachment_id is None:
        return None
    rows_match = _ATTACHMENT_ROW_NUMBERS.search(text)
    rows = [int(value) for value in re.findall(r"\d+", rows_match.group(1))] if rows_match else []
    fields_match = _ATTACHMENT_FIELDS.search(text)
    fields = (
        [value.strip() for value in fields_match.group(1).split(",") if value.strip()]
        if fields_match else []
    )
    return {
        "comment_id": comment_id,
        "attachment_id": attachment_id,
        "selector": "selected_rows",
        "rows": rows,
        "fields": fields,
    }


def resolve_page_comments(
    *, word_original: str, fixed_page_title: str,
    comments: Sequence[Mapping[str, Any]],
    available_attachment_ids: Sequence[str] | None = None,
    available_attachments: Sequence[Mapping[str, Any]] | None = None,
) -> CommentResolution:
    """Compile Word comments into body changes and concrete material requirements.

    The legacy resolver supplies deterministic closed classifications.  This
    adapter deliberately discards its reviewer prose and exposes only values
    that a later Image2 confirmation boundary may consume.
    """
    if not isinstance(word_original, str) or not isinstance(fixed_page_title, str):
        raise ValueError("Word content and fixed page title must be strings")
    if not isinstance(comments, Sequence) or isinstance(comments, (str, bytes)):
        raise ValueError("comments must be a sequence")
    attachments = _available_attachments(
        available_attachment_ids=available_attachment_ids,
        available_attachments=available_attachments,
    )

    effective_body = _remove_duplicated_title(
        fixed_page_title=fixed_page_title.strip(),
        word_original=word_original,
        effective_body=word_original,
    )
    attachment_requirements: list[dict[str, Any]] = []
    image_requirements: list[dict[str, Any]] = []
    degradations: list[dict[str, Any]] = []
    page_context = {
        "page_title": fixed_page_title,
        "body_text": effective_body,
        "source_text": word_original,
    }
    for position, comment in enumerate(comments, start=1):
        if not isinstance(comment, Mapping):
            raise ValueError("page comment must be an object")
        comment_id = _comment_id(comment, position)
        text = comment.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"page comment {comment_id} text is required")
        normalized = text.strip()
        person = _real_person_photo(normalized)
        if person:
            image_requirements.append({
                "kind": "reference_acquisition",
                "mode": "one_shot",
                "subject": person,
                "visual": "photo",
            })
            continue
        brand = _brand_logo(normalized)
        if brand:
            image_requirements.append({
                "kind": "reference_acquisition",
                "mode": "one_shot",
                "subject": brand,
                "visual": "logo",
            })
            continue
        if _ATTACHMENT_ROWS.search(normalized) or (
            "attachment" in normalized.lower() and "row" in normalized.lower()
        ):
            requirement = _attachment_requirement(
                text=normalized, comment_id=comment_id, available_attachments=attachments,
            )
            if requirement is None:
                degradations.append({"code": "attachment_unavailable", "comment_id": comment_id})
            else:
                attachment_requirements.append(requirement)
            continue
        if _FIXED_TITLE_CHANGE.search(normalized):
            degradations.append({"code": "unsupported_fixed_layer_request", "comment_id": comment_id})
            continue

        directive = resolve_comment_deterministically(
            normalized, page_context, source_comment_id=comment_id,
        )
        if directive is None:
            generic = (
                "timeline" if "timeline" in normalized.lower()
                else "icon" if "icon" in normalized.lower()
                else "diagram" if "diagram" in normalized.lower()
                else None
            )
            if generic:
                image_requirements.append({"kind": "text_only", "concept": generic})
            else:
                degradations.append({"code": "unsupported_comment", "comment_id": comment_id})
            continue

        targets = {str(decision.get("target", "")) for decision in directive.decisions}
        if any(target.startswith("fixed.") for target in targets):
            degradations.append({"code": "unsupported_fixed_layer_request", "comment_id": comment_id})
            continue
        word_target = next(
            (target for target in ("word.facts", "word.body_text", "word.tables") if target in targets),
            None,
        )
        if word_target:
            replacement, ambiguous = _replace_word_content(
                body=effective_body, target=word_target, text=normalized,
            )
            if replacement:
                effective_body = replacement
            else:
                degradations.append({
                    "code": "ambiguous_word_modification" if ambiguous else "unsupported_word_modification",
                    "comment_id": comment_id,
                })
            continue
        if directive.kind == "attachment_reference":
            requirement = _attachment_requirement(
                text=normalized, comment_id=comment_id, available_attachments=attachments,
            )
            if requirement is None:
                degradations.append({"code": "attachment_unavailable", "comment_id": comment_id})
            else:
                attachment_requirements.append(requirement)
            continue
        if directive.kind == "external_image":
            material_id = next(
                (
                    str(decision["material_id"])
                    for decision in directive.decisions
                    if decision.get("target") == "material.search_evidence"
                    and isinstance(decision.get("material_id"), str)
                ),
                None,
            )
            if not material_id or not directive.search_query:
                degradations.append({"code": "unsupported_evidence_request", "comment_id": comment_id})
                continue
            image_requirements.append({
                "kind": "reference_acquisition",
                "mode": "one_shot",
                "purpose": "source_backed_evidence",
                "request_id": material_id,
                "material_id": material_id,
                "search_query": directive.search_query,
            })
            continue
        if directive.visual_overrides:
            image_requirements.append({
                "kind": "text_only",
                "visual": dict(directive.visual_overrides),
            })
            continue
        if "icon" in normalized.lower():
            image_requirements.append({"kind": "text_only", "concept": "icon"})
            continue
        degradations.append({"code": "unsupported_comment", "comment_id": comment_id})

    return CommentResolution(
        effective_body=effective_body,
        attachment_requirements=tuple(attachment_requirements),
        image_requirements=tuple(image_requirements),
        degradations=tuple(degradations),
    )


def canonical_json(value: Any) -> str:
    """Serialize JSON values deterministically for local artifact digests."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def confirmed_revision_digest(result: Mapping[str, Any]) -> str:
    """Return the local-only digest of a confirmed UI revision payload."""
    if not isinstance(result, Mapping):
        raise ValueError("confirmed revision result must be an object")
    payload = copy.deepcopy(dict(result))
    payload.pop("confirmed_revision_digest", None)
    # Canonical serialization makes this boundary explicit; canonical_sha256 owns hashing.
    return canonical_sha256(json.loads(canonical_json(payload)))


def _remove_duplicated_title(
    *, fixed_page_title: str, word_original: str, effective_body: str,
) -> str:
    word_lines = word_original.splitlines()
    first_word_line = next((line.strip() for line in word_lines if line.strip()), "")
    body_lines = effective_body.splitlines()
    first_body_index = next(
        (index for index, line in enumerate(body_lines) if line.strip()), None
    )
    if (
        first_word_line == fixed_page_title
        and first_body_index is not None
        and body_lines[first_body_index].strip() == fixed_page_title
    ):
        body_lines.pop(first_body_index)
        while body_lines and not body_lines[0].strip():
            body_lines.pop(0)
        return "\n".join(body_lines).strip()
    return effective_body.strip()


def new_page_materials(
    *, page_number: int, fixed_page_title: str, word_original: str,
    effective_body: str,
) -> dict[str, Any]:
    """Create the single material authority for one V6 page."""
    if type(page_number) is not int or page_number < 1:
        raise ValueError("page_number must be a positive integer")
    if not isinstance(fixed_page_title, str) or not fixed_page_title.strip():
        raise ValueError("fixed_page_title is required")
    if not isinstance(word_original, str) or not isinstance(effective_body, str):
        raise ValueError("Word content and effective body must be strings")
    title = fixed_page_title.strip()
    return {
        "artifact_version": PAGE_MATERIALS_VERSION,
        "page_number": page_number,
        "fixed_page_title": title,
        "word_original": word_original,
        "effective_body": _remove_duplicated_title(
            fixed_page_title=title,
            word_original=word_original,
            effective_body=effective_body,
        ),
        "attachment_extracts": [],
        "chart_facts": [],
        "image_requirements": [],
        "degradations": [],
        "reference_images": [],
    }


def extract_attachment_material(
    *, attachment: Path, requirement: Mapping[str, Any], project: Path | None = None,
) -> dict[str, Any]:
    """Extract only the rows and fields a pre-UI attachment request selected."""
    attachment_id = requirement.get("attachment_id")
    if not isinstance(attachment_id, str) or not attachment_id:
        raise ValueError("attachment requirement requires attachment_id")
    path = Path(attachment)
    if not path.is_file():
        return {
            "attachment_id": attachment_id,
            "status": "unavailable",
            "degradation": "Attachment unavailable; keep the page editable without its requested evidence.",
        }
    rows = requirement.get("rows", [])
    fields = requirement.get("fields", [])
    if not isinstance(rows, list) or any(type(row) is not int or row < 1 for row in rows):
        raise ValueError("attachment rows must be positive integers")
    if not isinstance(fields, list) or any(not isinstance(field, str) or not field for field in fields):
        raise ValueError("attachment fields must be non-empty strings")
    source_bytes = path.read_bytes()
    requirement_digest = canonical_sha256({
        "attachment_id": attachment_id,
        "selector": requirement.get("selector", "selected_rows"),
        "rows": rows,
        "fields": fields,
    })
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    receipt = canonical_sha256({
        "source_sha256": source_sha256,
        "requirement_digest": requirement_digest,
    })
    receipt_path = None
    if project is not None:
        receipt_path = Path(project).resolve() / "02_v6" / "attachment_extracts" / f"{receipt}.json"
        if receipt_path.is_file():
            cached_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if (
                isinstance(cached_receipt, Mapping)
                and cached_receipt.get("receipt") == receipt
                and cached_receipt.get("source_sha256") == source_sha256
                and cached_receipt.get("requirement_digest") == requirement_digest
                and isinstance(cached_receipt.get("result"), Mapping)
            ):
                return copy.deepcopy(dict(cached_receipt["result"]))
    cached = _ATTACHMENT_EXTRACTION_CACHE.get(receipt)
    if cached is not None:
        return copy.deepcopy(cached)
    if path.suffix.lower() == ".csv":
        records = list(csv.DictReader(source_bytes.decode("utf-8-sig").splitlines()))
        chosen = [records[index - 1] for index in rows if index <= len(records)] if rows else []
        content = [
            {field: record[field] for field in fields if field in record}
            for record in chosen
        ]
    elif path.suffix.lower() == ".json":
        value = json.loads(source_bytes.decode("utf-8"))
        records = value if isinstance(value, list) else []
        chosen = [records[index - 1] for index in rows if index <= len(records)] if rows else []
        content = [
            {field: record[field] for field in fields if isinstance(record, Mapping) and field in record}
            for record in chosen
        ]
    else:
        lines = source_bytes.decode("utf-8", errors="replace").splitlines()
        content = [lines[index - 1] for index in rows if index <= len(lines)] if rows else []
    result = {
        "attachment_id": attachment_id,
        "status": "available",
        "selector": requirement.get("selector", "selected_rows"),
        "content": content,
        "receipt": receipt,
    }
    _ATTACHMENT_EXTRACTION_CACHE[receipt] = copy.deepcopy(result)
    if receipt_path is not None:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = receipt_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "receipt": receipt,
            "source_sha256": source_sha256,
            "requirement_digest": requirement_digest,
            "result": result,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(receipt_path)
    return result


def chart_to_facts(chart: Mapping[str, Any]) -> dict[str, Any]:
    """Keep a chart's literal facts as text, never as a reference-image input."""
    title = chart.get("title")
    if not isinstance(title, str) or not title:
        raise ValueError("chart title is required")
    series = chart.get("series")
    if not isinstance(series, list) or any(not isinstance(item, Mapping) for item in series):
        raise ValueError("chart series must be objects")
    factual_series: list[dict[str, Any]] = []
    for item in series:
        entry: dict[str, Any] = {}
        for key in (
            "series", "name", "unit", "value", "values", "time", "times",
            "categories", "basis", "trend", "relationship", "source_wording",
            "category_indices", "value_indices",
            "x_values", "x_label", "x_unit", "x_basis",
            "x_indices", "y_indices", "size_indices",
            "y_values", "y_label", "y_unit", "y_basis",
            "size_values", "size_label", "size_unit", "size_basis",
            "start", "changes", "end", "start_dates", "end_dates",
            "width_values", "width_label", "width_unit", "width_basis",
            "share_values", "share_label", "share_unit", "share_basis",
            "share_denominator", "target_value", "actual_value",
        ):
            if key in item:
                entry[key] = copy.deepcopy(item[key])
        factual_series.append(entry)
    result: dict[str, Any] = {"title": title, "series": factual_series}
    for key in (
        "unit", "basis", "period", "source_page", "relationship", "source_wording",
        "x_label", "x_unit", "x_basis", "y_label", "y_unit", "y_basis",
        "size_label", "size_unit", "size_basis", "target_value", "actual_value",
        "rendering_primitive", "chart_variant", "disabled_primitive", "fallback", "table_rows",
    ):
        if key in chart:
            result[key] = copy.deepcopy(chart[key])
    return result


def _numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _numeric_list(value: Any, *, non_negative: bool = False) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(_numeric(item) and (not non_negative or float(item) >= 0) for item in value)
    )


def _labels(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, (str, int, float)) and not isinstance(item, bool) and str(item).strip()
        for item in value
    )


def _text(record: Mapping[str, Any], chart: Mapping[str, Any], key: str) -> bool:
    value = record.get(key, chart.get(key))
    return isinstance(value, str) and bool(value.strip())


def _text_value(record: Mapping[str, Any], chart: Mapping[str, Any], key: str) -> str | None:
    value = record.get(key, chart.get(key))
    return value.strip() if isinstance(value, str) and value.strip() else None


def _series(chart: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = chart.get("series")
    if not isinstance(value, list) or not value or any(not isinstance(item, Mapping) for item in value):
        return []
    return value


def _aligned_indices(item: Mapping[str, Any], dimensions: Sequence[tuple[str, str]]) -> bool:
    index_values = [item.get(index_key) for _value_key, index_key in dimensions]
    if not any(value is not None for value in index_values):
        return True
    if any(not isinstance(value, list) for value in index_values):
        return False
    for value_key, index_key in dimensions:
        values = item.get(value_key)
        indices = item.get(index_key)
        if (
            not isinstance(values, list)
            or not isinstance(indices, list)
            or len(values) != len(indices)
            or any(type(index) is not int or index < 0 for index in indices)
            or len(indices) != len(set(indices))
        ):
            return False
    return all(value == index_values[0] for value in index_values[1:])


def _one_dimensional_complete(chart: Mapping[str, Any]) -> bool:
    primitive = chart.get("rendering_primitive")
    variants = {"column_bar": {"column", "bar"}, "line_point": {"line", "dot"}}
    if chart.get("chart_variant") not in variants.get(primitive, set()):
        return False
    series = _series(chart)
    if not series:
        return False
    comparison_basis: set[tuple[str, str]] = set()
    for item in series:
        categories = item.get("categories")
        values = item.get("values")
        if (
            not _text(item, {}, "name") and not _text(item, {}, "series")
            or not _labels(categories)
            or not _numeric_list(values)
            or len(categories) != len(values)
            or not _aligned_indices(item, (("categories", "category_indices"), ("values", "value_indices")))
        ):
            return False
        unit = _text_value(item, chart, "unit")
        basis = _text_value(item, chart, "basis")
        if unit is None or basis is None:
            return False
        comparison_basis.add((unit, basis))
    if len(comparison_basis) != 1:
        return False
    target = chart.get("target_value")
    actual = chart.get("actual_value")
    return (target is None and actual is None) or (_numeric(target) and _numeric(actual))


def _xy_complete(chart: Mapping[str, Any]) -> bool:
    variant = chart.get("chart_variant")
    if variant not in {"scatter", "bubble"}:
        return False
    for prefix in ("x", "y"):
        if not all(_text({}, chart, f"{prefix}_{suffix}") for suffix in ("label", "unit", "basis")):
            return False
    if variant == "bubble" and not all(
        _text({}, chart, f"size_{suffix}") for suffix in ("label", "unit", "basis")
    ):
        return False
    series = _series(chart)
    for item in series:
        x_values = item.get("x_values")
        y_values = item.get("y_values")
        if not _numeric_list(x_values) or not _numeric_list(y_values) or len(x_values) != len(y_values):
            return False
        dimensions = [("x_values", "x_indices"), ("y_values", "y_indices")]
        if variant == "bubble":
            sizes = item.get("size_values")
            if not _numeric_list(sizes, non_negative=True) or len(sizes) != len(x_values):
                return False
            dimensions.append(("size_values", "size_indices"))
        if not _aligned_indices(item, dimensions):
            return False
    return bool(series)


def _cumulative_complete(chart: Mapping[str, Any]) -> bool:
    series = _series(chart)
    if len(series) != 1 or not _text(series[0], chart, "unit") or not _text(series[0], chart, "basis"):
        return False
    item = series[0]
    changes = item.get("changes")
    categories = item.get("categories")
    if (
        not _numeric(item.get("start"))
        or not _numeric_list(changes)
        or not _numeric(item.get("end"))
        or not _labels(categories)
        or len(changes) != len(categories)
    ):
        return False
    return math.isclose(
        float(item["start"]) + sum(float(value) for value in changes),
        float(item["end"]), rel_tol=1e-9, abs_tol=1e-9,
    )


def _time_interval_complete(chart: Mapping[str, Any]) -> bool:
    series = _series(chart)
    for item in series:
        categories = item.get("categories")
        starts = item.get("start_dates")
        ends = item.get("end_dates")
        if not _labels(categories) or not isinstance(starts, list) or not isinstance(ends, list):
            return False
        if not starts or len(categories) != len(starts) or len(starts) != len(ends):
            return False
        try:
            intervals = [(date.fromisoformat(str(start)), date.fromisoformat(str(end))) for start, end in zip(starts, ends)]
        except ValueError:
            return False
        if any(start > end for start, end in intervals):
            return False
    return bool(series)


def _variable_rectangle_complete(chart: Mapping[str, Any]) -> bool:
    series = _series(chart)
    if len(series) != 1:
        return False
    item = series[0]
    categories = item.get("categories")
    widths = item.get("width_values")
    shares = item.get("share_values")
    denominator = item.get("share_denominator")
    if (
        not _labels(categories)
        or not _numeric_list(widths, non_negative=True)
        or len(categories) != len(widths)
        or not all(_text(item, chart, f"{prefix}_{suffix}") for prefix in ("width", "share") for suffix in ("label", "unit", "basis"))
        or not _numeric(denominator)
        or float(denominator) <= 0
        or not isinstance(shares, list)
        or len(shares) != len(widths)
    ):
        return False
    return all(
        _numeric_list(values, non_negative=True)
        and math.isclose(sum(float(value) for value in values), float(denominator), rel_tol=1e-9, abs_tol=1e-9)
        for values in shares
    )


def _complete_numeric_chart(chart: Mapping[str, Any]) -> bool:
    primitive = chart.get("rendering_primitive")
    if primitive in {"column_bar", "line_point"}:
        return _one_dimensional_complete(chart)
    if primitive == "xy":
        return _xy_complete(chart)
    if primitive == "cumulative_bridge":
        return _cumulative_complete(chart)
    if primitive == "time_interval":
        return _time_interval_complete(chart)
    if primitive == "variable_rectangle":
        return _variable_rectangle_complete(chart)
    return False


def select_numeric_authority(chart_facts: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Seal exactly one complete explicit chart; ambiguity or missing dimensions refuses."""
    if not isinstance(chart_facts, Sequence) or isinstance(chart_facts, (str, bytes)):
        return None
    eligible = [chart for chart in chart_facts if isinstance(chart, Mapping) and _complete_numeric_chart(chart)]
    return chart_to_facts(eligible[0]) if len(eligible) == 1 else None


def validate_page_materials(value: Mapping[str, Any], *, confirmed: bool) -> None:
    """Validate a material record and its confirmation boundary."""
    if not isinstance(value, Mapping):
        raise ValueError("page materials must be an object")
    errors = sorted(
        _PAGE_MATERIALS_VALIDATOR.iter_errors(copy.deepcopy(dict(value))),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise ValueError(f"page materials validation failed at {location}: {errors[0].message}")
    title = value["fixed_page_title"]
    word_first_line = next(
        (line.strip() for line in value["word_original"].splitlines() if line.strip()),
        "",
    )
    body_first_line = next(
        (line.strip() for line in value["effective_body"].splitlines() if line.strip()),
        "",
    )
    if word_first_line == title and body_first_line == title:
        raise ValueError("effective_body must exclude a duplicated fixed page title")
    revision = value.get("confirmed_revision")
    digest = value.get("confirmed_revision_digest")
    if confirmed and (revision is None or digest is None):
        raise ValueError("confirmed revision and digest are required")
    if digest is not None and digest != confirmed_revision_digest(value):
        raise ValueError("confirmed revision digest is invalid")


def reference_image_from_source(
    source: Mapping[str, Any], *, page_number: int, position: int,
    project: Path | None = None,
) -> dict[str, Any]:
    """Normalize an extracted Word image into a stable V6 reference record."""
    original_path = source.get("original_path")
    model_input_path = source.get("model_input_path")
    thumbnail_path = source.get("thumbnail_path")
    available = (
        source.get("status") == "available"
        and isinstance(original_path, str)
        and isinstance(model_input_path, str)
    )
    reference_id = str(source.get("asset_id") or f"page-{page_number:03d}-reference-{position:02d}")
    if available and project is not None:
        original = Path(project).resolve() / original_path
        kind = "screenshot" if source.get("media_type") == "image/png" else "photo"
        normalized = normalize_reference(
            project, original, reference_id=reference_id, kind=kind,
        )
        return reference_image_from_normalized(
            normalized,
            reference_id=reference_id,
            source="word_embedded",
            purpose=str(source.get("purpose") or "Word embedded image"),
            source_url=None,
            thumbnail_sha256=hashlib.sha256(
                (Path(project).resolve() / normalized.thumbnail_path).read_bytes()
            ).hexdigest(),
        )
    return {
        "reference_id": reference_id,
        "source": "word_embedded",
        "purpose": str(source.get("purpose") or "Word embedded image"),
        "preservation": "reference_only",
        "allow_crop": True,
        "allow_restyle": False,
        "status": "available" if available else "unavailable",
        "original_path": original_path if isinstance(original_path, str) else None,
        "model_input_path": model_input_path if isinstance(model_input_path, str) else None,
        "thumbnail_path": thumbnail_path if isinstance(thumbnail_path, str) else None,
        "source_url": None,
        "integrity": {
            "original_sha256": source.get("original_sha256") if isinstance(source.get("original_sha256"), str) else None,
            "model_input_sha256": source.get("model_input_sha256") if isinstance(source.get("model_input_sha256"), str) else None,
            "thumbnail_sha256": source.get("thumbnail_sha256") if isinstance(source.get("thumbnail_sha256"), str) else None,
        },
    }


def reference_image_from_normalized(
    normalized: NormalizedReference, *, reference_id: str, source: str,
    purpose: str, source_url: str | None, thumbnail_sha256: str,
) -> dict[str, Any]:
    """Translate bounded local media into the V6 reference-image schema."""
    return {
        "reference_id": reference_id,
        "source": source,
        "purpose": purpose,
        "preservation": "reference_only",
        "allow_crop": True,
        "allow_restyle": False,
        "status": "available",
        "original_path": normalized.original_path,
        "model_input_path": normalized.model_input_path,
        "thumbnail_path": normalized.thumbnail_path,
        "source_url": source_url,
        "integrity": {
            "original_sha256": normalized.original_sha256,
            "model_input_sha256": normalized.model_input_sha256,
            "thumbnail_sha256": thumbnail_sha256,
        },
    }

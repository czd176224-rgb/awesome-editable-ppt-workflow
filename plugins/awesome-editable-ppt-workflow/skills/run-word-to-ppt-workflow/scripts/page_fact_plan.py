"""Compile conservative page facts with Word authority and exact provenance."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Mapping


DATE_PATTERN = r"\d{4}(?:[-/.]\d{1,2}[-/.]\d{1,2}|年\s*\d{1,2}月\s*\d{1,2}日)"
NUMBER_PATTERN = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
AMOUNT_PATTERN = rf"{NUMBER_PATTERN}\s*(?:亿元|万元|亿|万|元)"
PERCENT_PATTERN = rf"{NUMBER_PATTERN}\s*[%％]"
VALUE_PATTERN = rf"(?:{DATE_PATTERN}|{AMOUNT_PATTERN}|{PERCENT_PATTERN}|{NUMBER_PATTERN})"
VALUE_RE = re.compile(VALUE_PATTERN)
FIELD_TYPES = {
    "投资额": "amount",
    "金额": "amount",
    "日期": "date",
    "数量": "number",
    "比例": "percent",
}
FIELD_ALIASES = {"投资额": ("投资额", "投资")}
FIELD_LABEL_RE = re.compile(r"(?m)(?:^|[|；;。\n])\s*(?P<field>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_（）()]{0,11})\s*[：:]")
TEXT_VALUE_PATTERN = r"[^，,。；;\n|]+"
URL_RE = re.compile(r"(?:https?://|file://|www\.|[A-Za-z][A-Za-z0-9+.-]*://)", re.IGNORECASE)
AUTHORITY_SOURCE_RE = re.compile(
    r"(?:以\s*)?(?:Word正文|Word|原文|正文)(?:内容)?\s*(?:为准|优先)", re.IGNORECASE,
)
NEGATIVE_ATTACHMENT_RE = re.compile(
    r"(?:附件[^，,；;。\n]{0,20}仅供参考|(?:不得|禁止|不应|不要)\s*(?:采用|覆盖))",
    re.IGNORECASE,
)
SOURCE_PARTICLES = (
    "中的最新数据", "中的最新值", "的最新数据", "的最新值", "中最新数据", "中最新值",
    "最新数据", "最新值", "最新信息", "中的", "的", "中",
)
CONCLUSION_FIELD_TOKENS = (
    "结论", "建议", "决策", "策略", "判断", "意见", "推荐", "行动", "方案", "目标",
)
FACTUAL_FIELD_TOKENS = (
    "主体", "客户", "公司", "单位", "机构", "名称", "日期", "时间", "金额", "投资额", "比例",
    "数量", "状态", "地点", "地址", "负责人", "人员", "数据", "指标", "阶段", "进度", "期限",
    "范围", "类型", "收入", "成本", "价格", "规模", "份额", "表格",
)
DECISION_VALUE_RE = re.compile(
    r"(?:建议|推荐|应当|应该|必须|务必|优先|决定|决策|立即|尽快|拟(?:收购|出售|退出|并购)|"
    r"收购|出售|退出|并购|维持现状|采取|推进|实施|启动|停止|暂停|调整策略|制定方案)",
)


def _comments(contract: Mapping[str, Any]) -> list[str]:
    return [
        str(item.get("text", "")).strip()
        for item in contract.get("page_comments", [])
        if isinstance(item, Mapping) and str(item.get("text", "")).strip()
    ]


def _anchors(contract: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("detected_dates", "detected_amounts", "detected_numbers"):
        for item in contract.get(key, []):
            value = item.get("value") if isinstance(item, Mapping) else item
            if isinstance(value, str) and value and value not in values:
                values.append(value)
    return values


def _evidence_content(text: str) -> str:
    """Remove extractor locator labels before detecting business values."""
    lines = text.replace("\r", "").splitlines()
    if lines and re.fullmatch(r"\[(?:Paragraph|Table|Page|Slide|sheet)[^\]]*\]", lines[0], re.IGNORECASE):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _candidate_fields(word_text: str) -> tuple[str, ...]:
    values: list[str] = []
    for field in FIELD_TYPES:
        if any(label in word_text for label in FIELD_ALIASES.get(field, (field,))):
            values.append(field)
    for match in FIELD_LABEL_RE.finditer(word_text):
        field = match.group("field").strip()
        if field and field not in values:
            values.append(field)
    return tuple(sorted(values, key=len, reverse=True))


def _normalize_asset_token(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    normalized = "".join(normalized.split())
    return normalized.strip("'\"“”‘’《》〈〉【】[]()（）")


def _bound_aliases(contract: Mapping[str, Any], source_file: str) -> frozenset[str]:
    source_key = _normalize_asset_token(source_file)
    aliases: set[str] = set()
    for binding in contract.get("asset_bindings", []):
        if not isinstance(binding, Mapping):
            continue
        filename = binding.get("original_filename")
        if not isinstance(filename, str) or _normalize_asset_token(filename) != source_key:
            continue
        for value in (filename, Path(filename).stem, binding.get("asset_id")):
            if isinstance(value, str) and value.strip():
                aliases.add(_normalize_asset_token(value))
    return frozenset(aliases)


def _comment_rejects_attachment_authority(comment: str) -> bool:
    normalized = unicodedata.normalize("NFKC", comment)
    return bool(AUTHORITY_SOURCE_RE.search(normalized) or NEGATIVE_ATTACHMENT_RE.search(normalized))


def _explicit_comment_fields(comment: str, candidates: tuple[str, ...]) -> set[str]:
    fields: set[str] = set()
    for field in candidates:
        if re.search(rf"(?:仅\s*)?覆盖\s*{re.escape(field)}\s*字段", comment):
            fields.add(field)
    return fields


def _field_before(comment: str, position: int, candidates: tuple[str, ...]) -> str | None:
    clause_start = max(comment.rfind(mark, 0, position) for mark in "，,；;。\n") + 1
    prefix = comment[clause_start:position].strip()
    return next((field for field in candidates if prefix.endswith(field)), None)


def _source_token_and_field(
    phrase: str, candidates: tuple[str, ...],
) -> tuple[str, str | None]:
    value = phrase.strip()
    inline_field: str | None = None
    for field in candidates:
        if not value.endswith(field):
            continue
        source = value[:-len(field)].rstrip()
        for particle in ("中的", "的", "中"):
            if source.endswith(particle):
                source = source[:-len(particle)].rstrip()
                inline_field = field
                value = source
                break
        if inline_field is not None:
            break
    for particle in SOURCE_PARTICLES:
        if value.endswith(particle):
            value = value[:-len(particle)].rstrip()
            break
    return value, inline_field


def _parse_override_directives(
    comment: str, candidates: tuple[str, ...],
) -> tuple[tuple[str, str, str], ...]:
    """Parse sentence-local source/field/intent triples without free-text alias matching."""
    directives: list[tuple[str, str, str]] = []
    for clause in re.split(r"[；;。！!\n]+", comment):
        clause = clause.strip()
        if not clause:
            continue
        explicit_fields = _explicit_comment_fields(clause, candidates)
        clause_directives: list[tuple[str, str, str]] = []
        for match in re.finditer(r"以\s*(?P<source>[^，,]+?)\s*为准", clause):
            source, inline_field = _source_token_and_field(match.group("source"), candidates)
            fields = ({inline_field} if inline_field else set()) | explicit_fields
            before = _field_before(clause, match.start(), candidates)
            if before:
                fields.add(before)
            clause_directives.extend((source, field, "为准") for field in fields if source)
        for match in re.finditer(r"采用\s*(?P<source>[^，,]+)", clause):
            phrase = re.sub(r"\s*为准\s*$", "", match.group("source").strip())
            source, inline_field = _source_token_and_field(phrase, candidates)
            fields = ({inline_field} if inline_field else set()) | explicit_fields
            before = _field_before(clause, match.start(), candidates)
            if before:
                fields.add(before)
            clause_directives.extend((source, field, "采用") for field in fields if source)
        if len({(_normalize_asset_token(source), field) for source, field, _ in clause_directives}) == 1:
            directives.extend(clause_directives)
    return tuple(directives)


def _authorization_map(contract: Mapping[str, Any], word_text: str) -> tuple[dict[str, str], set[str]]:
    """Resolve each field to exactly one bound source; ambiguous fields have no authority."""
    candidates = _candidate_fields(word_text)
    alias_owners: dict[str, set[str]] = {}
    for binding in contract.get("asset_bindings", []):
        if not isinstance(binding, Mapping) or not isinstance(binding.get("original_filename"), str):
            continue
        filename = str(binding["original_filename"])
        for value in (filename, Path(filename).stem, binding.get("asset_id")):
            if isinstance(value, str) and value.strip():
                alias_owners.setdefault(_normalize_asset_token(value), set()).add(filename)
    field_sources: dict[str, set[str]] = {}
    for comment in _comments(contract):
        if _comment_rejects_attachment_authority(comment):
            continue
        for source_token, field, _intent in _parse_override_directives(comment, candidates):
            owners = alias_owners.get(_normalize_asset_token(source_token), set())
            if len(owners) != 1:
                continue
            field_sources.setdefault(field, set()).update(owners)
    ambiguous = {field for field, owners in field_sources.items() if len(owners) != 1}
    authorized = {
        field: next(iter(owners))
        for field, owners in field_sources.items()
        if len(owners) == 1
    }
    return authorized, ambiguous


def _field_pattern(field: str) -> str:
    kind = FIELD_TYPES.get(field, "text")
    return {
        "date": DATE_PATTERN,
        "amount": AMOUNT_PATTERN,
        "percent": PERCENT_PATTERN,
        "number": NUMBER_PATTERN,
        "text": TEXT_VALUE_PATTERN,
    }[kind]


def normalize_typed_field_value(value: str, field: str) -> str:
    normalized = "".join(str(value).strip().split()).replace("％", "%")
    if FIELD_TYPES.get(field) in {"amount", "number", "percent"}:
        normalized = normalized.replace(",", "")
    if FIELD_TYPES.get(field) == "date":
        match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", normalized)
        if match:
            normalized = f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        normalized = normalized.replace("/", "-").replace(".", "-")
    return normalized.casefold()


def extract_typed_field_value(text: str, field: str) -> str | None:
    content = _evidence_content(text)
    value_pattern = _field_pattern(field)
    separator = r"(?:[：:]|为|是|[|])" if FIELD_TYPES.get(field, "text") == "text" else r"(?:[：:]|为|是|[|])?"
    for label in FIELD_ALIASES.get(field, (field,)):
        match = re.search(
            rf"{re.escape(label)}\s*{separator}\s*(?P<value>{value_pattern})",
            content,
        )
        if match:
            return match.group("value").strip()
    return None


def apply_field_overrides(text: str, fact_plan: Mapping[str, Any]) -> str:
    """Apply only explicitly authorized complete named-field value replacements."""
    result = str(text)
    for override in fact_plan.get("field_overrides", []):
        if not isinstance(override, Mapping):
            continue
        field, old, new = override.get("field"), override.get("word_value"), override.get("attachment_value")
        if not all(isinstance(value, str) and value for value in (field, old, new)):
            continue
        separator = r"(?:[：:]|为|是|[|])" if FIELD_TYPES.get(field, "text") == "text" else r"(?:[：:]|为|是|[|])?"
        for label in FIELD_ALIASES.get(field, (field,)):
            pattern = re.compile(
                rf"(?P<prefix>{re.escape(label)}\s*{separator}\s*)"
                rf"(?P<value>{_field_pattern(field)})"
            )

            def replace(match: re.Match[str]) -> str:
                if normalize_typed_field_value(match.group("value"), field) != normalize_typed_field_value(old, field):
                    return match.group(0)
                return match.group("prefix") + new

            result, count = pattern.subn(replace, result)
            if count and normalize_typed_field_value(old, field) not in normalize_typed_field_value(result, field):
                break
    return result


def _ordinary_supplement(content: str, word_text: str) -> tuple[str, str] | None:
    if URL_RE.search(content):
        return None
    word_fields = set(_candidate_fields(word_text))
    for segment in re.split(r"[\n；;]", content):
        match = re.fullmatch(
            r"\s*(?P<field>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_（）()]{0,11})\s*[：:]\s*(?P<value>[^，,。；;\n|]+)\s*[。.]?\s*",
            segment,
        )
        if match and match.group("field") in word_fields:
            field, value = match.group("field"), match.group("value").strip()
            if is_factual_attachment_supplement(field, value):
                return field, f"{field}：{value}"
    return None


def is_factual_attachment_supplement(field: Any, value: Any) -> bool:
    if not isinstance(field, str) or not isinstance(value, str):
        return False
    normalized_field, normalized_value = field.strip(), value.strip()
    if not normalized_field or not normalized_value or URL_RE.search(normalized_value):
        return False
    if any(token in normalized_field for token in CONCLUSION_FIELD_TOKENS):
        return False
    if normalized_field not in FIELD_TYPES and not any(
        token in normalized_field for token in FACTUAL_FIELD_TOKENS
    ):
        return False
    return DECISION_VALUE_RE.search(normalized_value) is None


def factual_attachment_supplement_value(field: Any, text: Any) -> str | None:
    if not isinstance(field, str) or not isinstance(text, str):
        return None
    value = extract_typed_field_value(text, field)
    return value if value is not None and is_factual_attachment_supplement(field, value) else None


def build_fact_plan(contract: Mapping[str, Any], selected_evidence: Mapping[str, Any]) -> dict[str, Any]:
    word_text = str(contract.get("body_text", contract.get("source_text", "")))
    word_values = set(VALUE_RE.findall(word_text))
    candidate_fields = _candidate_fields(word_text)
    authorization, ambiguous_fields = _authorization_map(contract, word_text)
    supplements: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    field_overrides: list[dict[str, Any]] = []
    for chunk in selected_evidence.get("selected_chunks", []):
        if not isinstance(chunk, Mapping):
            continue
        text = str(chunk.get("text", ""))
        source = dict(chunk.get("source", {})) if isinstance(chunk.get("source"), Mapping) else {}
        if not all(isinstance(source.get(key), str) and source.get(key) for key in ("file", "locator", "sha256")):
            raise ValueError("attachment-derived facts require exact file, locator, and sha256 provenance")
        content = _evidence_content(text)
        evidence_values = set(VALUE_RE.findall(content))
        changed_values = sorted(evidence_values - word_values)
        typed_difference = False
        accepted_override = False
        for field in candidate_fields:
            word_value = extract_typed_field_value(word_text, field)
            attachment_value = extract_typed_field_value(content, field)
            if not word_value or not attachment_value:
                continue
            if normalize_typed_field_value(word_value, field) == normalize_typed_field_value(attachment_value, field):
                continue
            typed_difference = True
            source_is_authorized = (
                field not in ambiguous_fields
                and _normalize_asset_token(authorization.get(field, "")) == _normalize_asset_token(source["file"])
            )
            hard_policy_allows = is_factual_attachment_supplement(field, attachment_value)
            if source_is_authorized and hard_policy_allows and not any(
                item["field"] == field for item in field_overrides
            ):
                accepted_override = True
                field_overrides.append({
                    "field": field,
                    "word_value": word_value,
                    "attachment_value": attachment_value,
                    "source": source,
                    "evidence_id": chunk.get("evidence_id"),
                })
                supplements.append({
                    "text": f"{field}：{attachment_value}",
                    "field": field,
                    "source": source,
                    "evidence_id": chunk.get("evidence_id"),
                    "authorization": "explicit_named_field_override",
                })
                continue
            conflicts.append({
                "field": field,
                "word_values": [word_value],
                "attachment_values": [attachment_value],
                "source": source,
                "resolution": "word_wins",
                "qa_advisory": True,
                "reason": "ambiguous_or_unauthorized_field_override",
            })
        if accepted_override or typed_difference:
            continue
        if changed_values and word_values:
            conflicts.append({
                "word_values": sorted(word_values),
                "attachment_values": changed_values,
                "source": source,
                "resolution": "word_wins",
                "qa_advisory": True,
            })
            continue
        ordinary = _ordinary_supplement(content, word_text)
        if ordinary is not None:
            field, approved_text = ordinary
            if extract_typed_field_value(word_text, field) is not None:
                continue
            supplements.append({
                "text": approved_text,
                "field": field,
                "source": source,
                "evidence_id": chunk.get("evidence_id"),
                "authorization": "supplement_only",
            })
    anchors = _anchors(contract)
    for override in field_overrides:
        old, new = override["word_value"], override["attachment_value"]
        anchors = [new if value == old or value in old else value for value in anchors]
    anchors = list(dict.fromkeys(anchors))
    return {
        "schema_version": "1.0",
        "word_claims": [
            str(item.get("text"))
            for item in contract.get("semantic_units", [])
            if isinstance(item, Mapping) and item.get("text")
        ],
        "attachment_supplements": supplements,
        "conflicts": conflicts,
        "field_overrides": field_overrides,
        "mandatory_anchors": anchors,
        "forbidden_new_conclusions": True,
    }

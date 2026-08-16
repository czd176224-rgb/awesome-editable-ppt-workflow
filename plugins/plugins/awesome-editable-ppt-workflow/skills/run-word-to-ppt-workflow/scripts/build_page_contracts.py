"""Build immutable, page-local source contracts from pages.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from page_assets import binding_metadata
from workflow_contract import WORKFLOW_VERSION


DATE_RE = re.compile(r"(?:19|20)\d{2}年\d{1,2}月\d{1,2}日|(?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}")
AMOUNT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:万|亿|元|万元|亿元|%|％)")
NUMBER_RE = re.compile(r"(?<![\d.])\d+(?:\.\d+)?(?:%|％)?")
SENTENCE_RE = re.compile(r"(?:[^。！？；.!?;\n]|(?<=\d)\.(?=\d))+[。！？；.!?;]?")
PAGE_MARKER_RE = re.compile(r"^第\s*(\d+)\s*页\s*[：:]?\s*$")
COMMENT_TITLE_RE = re.compile(
    r"(?:\[title\s*[：:]\s*([^\]\r\n]+)\]|(?:PPT标题|页面标题|本页标题|标题)\s*[：:]\s*([^\r\n]+))",
    re.IGNORECASE,
)
HEADING_RE = re.compile(
    r"^(?:[一二三四五六七八九十]+、|"
    r"[（(][一二三四五六七八九十]+[）)]|"
    r"\d+(?:\.\d+)*[、.)）])\s*"
)
CONTINUATION_PREFIX_RE = re.compile(r"^[，。；：,.;:]")
def normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()


def source_text(blocks: list[dict]) -> str:
    return normalize("\n\n".join(block.get("text") or block.get("markdown") or "" for block in blocks))


def _comment_title(page_comments: list[dict] | None) -> str | None:
    for comment in page_comments or []:
        if not isinstance(comment, dict):
            continue
        match = COMMENT_TITLE_RE.search(str(comment.get("text", "")))
        if match:
            title = " ".join(next(group for group in match.groups() if group).split()).strip("，。；：,.;: ")
            if title:
                return title[:40]
    return None


def _looks_like_explicit_physical_title(line: str) -> bool:
    value = line.strip()
    if not value or CONTINUATION_PREFIX_RE.match(value) or "|" in value:
        return False
    if len(value) > 34 or value.endswith(("，", "。", "；", "：", ",", ".", ";", ":")):
        return False
    if HEADING_RE.match(value):
        return len(value) <= 26
    return len(value) <= 24 and bool(re.search(r"(报告|进展|方案|计划|安排|情况|分析|总结|建议|任务)$", value))


def _looks_like_ordered_marker_title(line: str, line_count: int) -> bool:
    """Use the first short block's ordered-page role, not prose vocabulary, as title evidence."""
    value = line.strip()
    return bool(
        line_count > 1
        and value
        and len(value) <= 34
        and not CONTINUATION_PREFIX_RE.match(value)
        and "|" not in value
        and not value.endswith(("，", "。", "；", ",", ".", ";"))
    )


def _derived_physical_title(lines: list[str], page_number: int) -> str:
    for raw in lines:
        if not HEADING_RE.match(raw.strip()):
            continue
        value = HEADING_RE.sub("", raw.strip()).lstrip("，。；：,.;: ")
        clause = re.split(r"[，。；：,;:]", value, maxsplit=1)[0].strip()
        if 2 <= len(clause) <= 28:
            return clause
    for raw in lines:
        if re.search(r"\s{2,}", raw) or ("牵头方" in raw and "方案" in raw):
            continue
        value = HEADING_RE.sub("", raw.strip()).lstrip("，。；：,.;: ")
        if not value or "|" in value:
            continue
        clause = re.split(r"[，。；：,;:]", value, maxsplit=1)[0].strip()
        if len(clause) < 6:
            clause = value
        clause = clause[:28].rstrip("，。；：,.;: ")
        if clause:
            return clause
    return f"第{page_number}页工作进展"


def derive_short_page_title(text: str, page_number: int) -> str:
    """Derive a compact fixed-layer title without consuming authoritative body text."""
    value = normalize(text)
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if lines:
        lines[0] = re.sub(r"^图\s*\d+\s*[：:]\s*", "", lines[0], flags=re.IGNORECASE)
    derived = _derived_physical_title(lines, page_number)
    entities = re.findall(r"[一二三四五六七八九十百\d]+(?:地市场|种增长模式)", value)
    action = re.match(r"^(聚焦|围绕|推动|加快|深化|提升|推进|建设|实现|形成)", value)
    if action and len(entities) >= 2:
        title = f"{action.group(1)}{entities[0]}与{entities[1]}"
        if len(title) <= 28:
            return title
    return derived


def validate_source_body_coverage(
    source: str,
    page_title: str,
    body_text: str,
    title_origin: str,
    *,
    page_number: int,
) -> None:
    """Reject any body-role projection that omits or changes non-heading Word text."""
    normalized_source = normalize(source)
    normalized_body = normalize(body_text)
    if title_origin == "explicit_word_heading":
        source_lines = normalized_source.splitlines()
        heading_index = next((index for index, line in enumerate(source_lines) if line.strip()), None)
        if heading_index is None or source_lines[heading_index].strip() != normalize(page_title):
            raise ValueError(f"page {page_number} explicit title does not match Word source")
        expected_body = normalize("\n".join(source_lines[heading_index + 1:]))
    else:
        expected_body = normalized_source
    if normalized_body != expected_body:
        raise ValueError(f"page {page_number} title extraction loses non-heading Word text")


def _split_page_title_body_with_origin(
    text: str,
    page_number: int,
    *,
    pagination_mode: str | None = None,
    page_comments: list[dict] | None = None,
) -> tuple[str, str, str]:
    content_text = normalize(text)
    raw_lines = content_text.splitlines()
    lines = [line.strip() for line in raw_lines if line.strip()]
    marker_removed = False
    if lines:
        marker = PAGE_MARKER_RE.fullmatch(lines[0])
        if marker:
            if int(marker.group(1)) != page_number:
                raise ValueError(f"page {page_number} contains a mismatched page marker")
            marker_index = next(index for index, line in enumerate(raw_lines) if line.strip())
            content_text = normalize("\n".join(raw_lines[marker_index + 1:]))
            raw_lines = content_text.splitlines()
            lines = [line.strip() for line in raw_lines if line.strip()]
            marker_removed = True
    if not lines:
        raise ValueError(f"page {page_number} has no title or body content")

    overridden = _comment_title(page_comments)
    if overridden:
        return overridden, content_text, "comment_override"
    ordered_page = pagination_mode in {"ordered_markers", "explicit_text_markers"} or marker_removed
    if _looks_like_explicit_physical_title(lines[0]) or (
        ordered_page and _looks_like_ordered_marker_title(lines[0], len(lines))
    ):
        heading_index = next(index for index, line in enumerate(raw_lines) if line.strip())
        body_text = normalize("\n".join(raw_lines[heading_index + 1:]))
        return lines[0], body_text, "explicit_word_heading"
    return derive_short_page_title(content_text, page_number), content_text, "derived_from_body"


def split_page_title_body(
    text: str,
    page_number: int,
    *,
    pagination_mode: str | None = None,
    page_comments: list[dict] | None = None,
) -> tuple[str, str]:
    """Lock a usable page title while preserving every non-heading Word block."""
    page_title, body_text, _ = _split_page_title_body_with_origin(
        text,
        page_number,
        pagination_mode=pagination_mode,
        page_comments=page_comments,
    )
    return page_title, body_text


def detection(value: str, page_number: int) -> dict:
    return {
        "value": value,
        "source_trace": [{
            "source_type": "word_page",
            "source_page": page_number,
            "source_locator": f"page_{page_number:03d}",
            "text_span": value,
            "excerpt": value,
        }],
    }


def split_sentences(text: str) -> list[str]:
    return [match.group(0).strip() for match in SENTENCE_RE.finditer(normalize(text)) if match.group(0).strip()]


def semantic_units(blocks: list[dict], page_number: int) -> list[dict]:
    raw_units: list[tuple[str, str, int]] = []
    for block_index, block in enumerate(blocks, start=1):
        if block.get("type") == "paragraph":
            raw_units.extend(("sentence", text, block_index) for text in split_sentences(block.get("text", "")))
        elif block.get("type") == "table":
            rows = [line.strip() for line in block.get("markdown", "").splitlines() if line.strip()]
            rows = [row for row in rows if not re.fullmatch(r"\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?", row)]
            raw_units.extend(("table_row", row, block_index) for row in rows)
    return [
        {
            "unit_id": f"unit_{index:03d}",
            "kind": kind,
            "text": text,
            "source_block_index": block_index,
            "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "source_trace": detection(text, page_number)["source_trace"],
        }
        for index, (kind, text, block_index) in enumerate(raw_units, start=1)
    ]


def explicit_relations(units: list[dict], page_number: int) -> list[dict]:
    """Rebuild source-explicit relations with a bounded, conservative state machine."""
    relations: list[dict] = []
    pending_cause: tuple[int, str] | None = None
    pending_condition: tuple[int, str, bool] | None = None
    pending_parallel: tuple[int, str] | None = None
    sequence_previous: tuple[int, str] | None = None

    def starts(text: str, cue: str, denied: tuple[str, ...] = ()) -> bool:
        stripped = text.strip()
        return stripped.startswith(cue) and not any(stripped.startswith(cue + suffix) for suffix in denied)

    def add(kind: str, scope: str, indexes: list[int], cue: str) -> None:
        ids = [units[index]["unit_id"] for index in indexes]
        excerpt = "\n".join(units[index]["text"] for index in indexes)
        relation = {"relation_id": f"relation_{len(relations)+1:03d}", "type": kind, "scope": scope,
                    "source_unit_ids": ids, "cue": cue, "source_trace": [{"source_type": "word_page", "source_page": page_number,
                    "source_locator": f"page_{page_number:03d}", "text_span": cue, "excerpt": excerpt}]}
        canonical = json.dumps(relation, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        relation["source_sha256"] = hashlib.sha256(canonical).hexdigest()
        relations.append(relation)

    cause_starts = ("由于", "因为")
    cause_results = ("因此", "所以", "从而")
    condition_starts = ("如果", "假如", "若", "只要")
    condition_results = ("即可", "可以", "方可", "才能", "则", "就", "即")
    sequence_cues = ("首先", "其次", "再次", "最后", "随后", "然后", "接着", "继而")
    sequence_continuations = {"随后", "然后", "接着", "继而"}
    evidence_intra_cues = (("数据显示", ("器",)), ("事实表明", ("书",)), ("研究表明", ("书",)))
    evidence_cross_cues = (("这表明", ("书",)), ("这说明", ("书",)), ("由此可见", ("光",)))
    condition_predicates = r"通过|完成|达到|符合|满足|存在|发生|需要|解除|已|未|到位|增长|成熟|充足|回暖|下降|上升|中断|恢复|改善|恶化|获批|生效"
    condition_subjects = r"条件|政策|风险|审批|资金|需求|市场|项目|方案|企业|系统|申请|平台|技术|产能|供应|订单"

    def embedded_literal(text: str, cues: tuple[str, ...], denied: dict[str, tuple[str, ...]] | None = None) -> tuple[str, int, int] | None:
        matches: list[tuple[str, int, int]] = []
        denied = denied or {}
        for cue in cues:
            position = text.find(cue)
            while position >= 0 and any(text.startswith(cue + suffix, position) for suffix in denied.get(cue, ())):
                position = text.find(cue, position + 1)
            if position >= 0:
                matches.append((cue, position, position + len(cue)))
        return min(matches, key=lambda item: (item[1], -len(item[0]))) if matches else None

    def embedded_condition(text: str) -> tuple[str, int, int, bool] | None:
        matches: list[tuple[str, int, int, bool]] = []
        literal = embedded_literal(text, ("如果", "假如", "只要"))
        if literal:
            matches.append((*literal, True))
        report_contexts = ("显示", "表明", "规定", "明确", "判断", "认为", "评审", "评估", "测算", "分析")
        organization_suffixes = (
            "客户", "团队", "单位", "主体", "人员", "个人", "组织", "集团", "园区", "部门", "机构",
            "企业", "公司", "项目", "方案", "系统", "平台", "设备", "产品", "业务", "市场", "政府",
            "基金", "国资", "学校", "医院", "银行", "中心",
        )
        organization_name_suffixes = (
            "科技", "集团", "公司", "企业", "银行", "医院", "学校", "大学", "学院", "研究院", "中心",
            "园区", "部门", "机构", "单位", "组织", "政府", "基金", "股份", "控股", "实业", "资本",
        )
        generic_organization_subjects = (
            "企业", "公司", "基金", "集团", "平台", "机构", "单位", "组织", "银行", "医院", "学校",
        )
        business_modifiers = (
            "科技型", "民营", "国有", "政府", "产业", "项目", "本地", "当地", "上市", "中小", "大型", "小微",
        )

        def is_controlled_generic_subject(prefix: str) -> bool:
            for subject in generic_organization_subjects:
                if prefix == subject:
                    return True
                if not prefix.endswith(subject):
                    continue
                modifiers = prefix[:-len(subject)]
                while modifiers:
                    modifier = next((item for item in business_modifiers if modifiers.startswith(item)), None)
                    if not modifier:
                        break
                    modifiers = modifiers[len(modifier):]
                if not modifiers:
                    return True
            return False

        for single_ruo in re.finditer(r"若(?!干)", text):
            result = following_match(text, condition_results, single_ruo.end())
            segment_end = result[1] if result else len(text)
            before = text[:single_ruo.start()].rstrip()
            segment = text[single_ruo.end():segment_end]
            direct_condition = re.match(condition_predicates, segment)
            subject_condition = re.match(rf"(?:(?:{condition_subjects}))+(?:{condition_predicates})", segment)
            boundary = re.match(rf"(?:(?:{condition_subjects}))+[，,；;：:]", segment)
            first_predicate = re.search(condition_predicates, segment)
            subject_prefix = segment[:first_predicate.start()].rstrip(" \t\r\n，,；;：:") if first_predicate else ""
            ambiguous_organization_name = any(
                subject_prefix.endswith(suffix) and len(subject_prefix) > len(suffix)
                for suffix in organization_name_suffixes
            ) and not is_controlled_generic_subject(subject_prefix)
            general_result_boundary = bool(
                result
                and re.search(r"[，,；;：:]", segment)
                and re.search(r"[A-Za-z\u4e00-\u9fff]", segment)
                and not ambiguous_organization_name
            )
            sentence_initial = single_ruo.start() == 0 and bool(
                direct_condition or subject_condition or boundary or general_result_boundary
            )
            organization_condition = (
                single_ruo.start() > 0
                and any(before.endswith(suffix) for suffix in organization_suffixes)
                and direct_condition
            )
            report_subjects = rf"(?:{condition_subjects}|{'|'.join(organization_suffixes)})"
            report_condition = re.search(rf"(?:{report_subjects})(?:{condition_predicates})", segment)
            report_pattern = any(before.endswith(context) for context in report_contexts) and bool(
                report_condition
                or (result and re.search(r"[A-Za-z\u4e00-\u9fff]", segment))
            ) and not ambiguous_organization_name
            if sentence_initial or organization_condition or report_pattern:
                matches.append(("若", single_ruo.start(), single_ruo.end(), True))
                break
        timed = re.search(r"当(?!地|前|今|年|月|日).+?时", text)
        if timed:
            matches.append(("当…时", timed.start(), timed.end(), True))
        return min(matches, key=lambda item: (item[1], -len(item[0]))) if matches else None

    def following_match(text: str, cues: tuple[str, ...], start: int) -> tuple[str, int] | None:
        matches: list[tuple[str, int]] = []
        for cue in cues:
            position = text.find(cue, start)
            while position >= 0 and cue == "就" and text.startswith("就绪", position):
                position = text.find(cue, position + 1)
            if position >= 0:
                matches.append((cue, position))
        return min(matches, key=lambda item: (item[1], -len(item[0]))) if matches else None

    def following_cue(text: str, cues: tuple[str, ...], start: int) -> str | None:
        match = following_match(text, cues, start)
        return match[0] if match else None

    def only_result_cue(text: str, start: int) -> str | None:
        lexical_non_cues = ("人才", "才华", "才干", "才智", "才学", "才艺", "才貌", "才俊", "才情", "才识", "才气",
                            "天才", "英才", "奇才", "专才", "通才")
        lexical_spans: list[tuple[int, int]] = []
        for word in lexical_non_cues:
            position = text.find(word, start)
            while position >= 0:
                lexical_spans.append((position, position + len(word)))
                position = text.find(word, position + 1)

        def inside_lexical_word(position: int) -> bool:
            return any(span_start <= position < span_end for span_start, span_end in lexical_spans)

        def has_actual_content(fragment: str) -> bool:
            return bool(re.search(r"[A-Za-z\u4e00-\u9fff]", fragment))

        complete_matches = []
        for cue in ("才能", "才可", "方可", "即可"):
            position = text.find(cue, start)
            while position >= 0:
                condition_segment = text[start:position]
                result_segment = text[position + len(cue):]
                if (
                    not inside_lexical_word(position)
                    and has_actual_content(condition_segment)
                    and has_actual_content(result_segment)
                ):
                    complete_matches.append((cue, position))
                position = text.find(cue, position + 1)
        if complete_matches:
            return min(complete_matches, key=lambda item: (item[1], -len(item[0])))[0]
        for bare in re.finditer("才", text[start:]):
            position = start + bare.start()
            if inside_lexical_word(position):
                continue
            condition_segment = text[start:position]
            result_segment = re.sub(r"^[\s，,；;：:。！？!?、]+", "", text[position + len("才"):])
            if has_actual_content(condition_segment) and has_actual_content(result_segment):
                return "才"
        return None

    def sequence_start(text: str) -> str | None:
        denied_by_cue = {"最后": ("期限", "日期", "时间"), "随后": ("者",), "然后": ("续",),
                         "接着": ("剂",), "继而": ("性",)}
        return next((cue for cue in sequence_cues if starts(text, cue, denied_by_cue.get(cue, ()))), None)

    def has_conflicting_leading_relation(text: str) -> bool:
        return bool(
            next((cue for cue in cause_starts if starts(text, cue)), None)
            or next((cue for cue in cause_results if starts(text, cue, ("类",))), None)
            or next((cue for cue in condition_starts if starts(text, cue, ("干",) if cue == "若" else ())), None)
            or starts(text, "只有")
            or starts(text, "除非")
            or re.match(r"^当(?!地|前|今|年|月|日).+?时", text)
            or next((cue for cue in condition_results if starts(text, cue, ("业", "任", "诊", "近", "是", "此", "地", "座", "餐") if cue == "就" else ())), None)
            or next((cue for cue in ("但是", "然而", "相比之下", "一方面", "另一方面", "此外", "另外", "同时", "以及") if starts(text, cue)), None)
            or sequence_start(text)
            or next((cue for cue, denied in (*evidence_intra_cues, *evidence_cross_cues) if starts(text, cue, denied)), None)
            or next((cue for cue in ("通过", "依托", "借助") if starts(text, cue, ("率",) if cue == "通过" else ())), None)
            or re.match(r"^与.+?相比", text)
        )
    for index, unit in enumerate(units):
        text = unit["text"].strip()
        handled = False

        for first, second in (("不仅", "而且"), ("不但", "而且"), ("一方面", "另一方面")):
            if starts(text, first) and re.search(re.escape(first) + r".+" + re.escape(second), text):
                add("parallel", "intra_unit", [index], f"{first}…{second}")
                handled = True
                break
        if handled:
            pending_cause = pending_condition = pending_parallel = None
            continue

        paired_condition = None
        only_position = text.find("只有")
        unless_position = text.find("除非")
        if only_position >= 0:
            result = only_result_cue(text, only_position + len("只有"))
            if result:
                paired_condition = f"只有…{result}"
        elif unless_position >= 0 and text.find("否则", unless_position + len("除非")) >= 0:
            paired_condition = "除非…否则"
        if paired_condition:
            add("condition", "intra_unit", [index], paired_condition)
            pending_cause = pending_condition = pending_parallel = None
            continue

        prior_pending_cause = pending_cause
        cause_match = embedded_literal(text, cause_starts)
        cause_first = cause_match[0] if cause_match else None
        cause_result_any = following_cue(text, cause_results, cause_match[2]) if cause_match else None
        if cause_first and cause_result_any:
            add("cause", "intra_unit", [index], f"{cause_first}…{cause_result_any}")
            handled = True
            pending_cause = None
        else:
            leading_result = next((cue for cue in cause_results if starts(text, cue, ("类",))), None)
            if leading_result and index > 0:
                if prior_pending_cause and prior_pending_cause[0] == index - 1:
                    add("cause", "cross_unit", [prior_pending_cause[0], index], f"{prior_pending_cause[1]}…{leading_result}")
                else:
                    add("cause", "cross_unit", [index - 1, index], leading_result)
                handled = True
                pending_cause = None
            else:
                if (
                    prior_pending_cause and prior_pending_cause[0] == index - 1
                    and units[prior_pending_cause[0]].get("kind") == units[index].get("kind") == "sentence"
                    and units[prior_pending_cause[0]].get("source_block_index") == units[index].get("source_block_index")
                    and not has_conflicting_leading_relation(text)
                ):
                    add("cause", "cross_unit", [prior_pending_cause[0], index], prior_pending_cause[1])
                pending_cause = (index, cause_first) if cause_first else None

        prior_pending_condition = pending_condition
        condition_match = embedded_condition(text)
        condition_first = condition_match[0] if condition_match else None
        first_end = condition_match[2] if condition_match else 0
        condition_allows_plain = condition_match[3] if condition_match else False
        condition_result_any = following_cue(text, condition_results, first_end) if condition_match else None
        if condition_first and condition_result_any:
            add("condition", "intra_unit", [index], f"{condition_first}…{condition_result_any}")
            handled = True
            pending_condition = None
        else:
            leading_condition_result = next((cue for cue in condition_results if starts(
                text, cue, ("业", "任", "诊", "近", "是", "此", "地", "座", "餐") if cue == "就" else ()
            )), None)
            if leading_condition_result and prior_pending_condition and prior_pending_condition[0] == index - 1:
                add("condition", "cross_unit", [prior_pending_condition[0], index], f"{prior_pending_condition[1]}…{leading_condition_result}")
                handled = True
                pending_condition = None
            else:
                if (
                    prior_pending_condition and prior_pending_condition[0] == index - 1
                    and prior_pending_condition[2]
                    and units[prior_pending_condition[0]].get("kind") == units[index].get("kind") == "sentence"
                    and units[prior_pending_condition[0]].get("source_block_index") == units[index].get("source_block_index")
                    and not has_conflicting_leading_relation(text)
                ):
                    add("condition", "cross_unit", [prior_pending_condition[0], index], prior_pending_condition[1])
                pending_condition = (index, condition_first, condition_allows_plain) if condition_first else None

        if re.match(r"^与.+?相比(?:[，,:：]|.+)", text):
            add("contrast", "intra_unit", [index], "与…相比")
            handled = True
        elif index > 0 and starts(text, "相比之下"):
            add("contrast", "cross_unit", [index - 1, index], "相比之下")
            handled = True
        elif index > 0:
            for cue in ("但是", "然而"):
                if starts(text, cue):
                    add("contrast", "cross_unit", [index - 1, index], cue)
                    handled = True
                    break

        if starts(text, "一方面"):
            pending_parallel = (index, "一方面")
        elif starts(text, "另一方面") and pending_parallel and pending_parallel[0] == index - 1:
            add("parallel", "cross_unit", [pending_parallel[0], index], "一方面…另一方面")
            handled = True
            pending_parallel = None
        else:
            pending_parallel = None
            if index > 0:
                for cue, denied in (("此外", ()), ("另外", ("费用", "一个", "一项")), ("同时", ("性",)), ("以及", ())):
                    if starts(text, cue, denied):
                        add("parallel", "cross_unit", [index - 1, index], cue)
                        handled = True
                        break

        sequence_cue = sequence_start(text)
        if sequence_cue:
            if sequence_previous and sequence_previous[0] == index - 1:
                add("sequence", "cross_unit", [sequence_previous[0], index], f"{sequence_previous[1]}…{sequence_cue}")
            elif sequence_cue in sequence_continuations and index > 0:
                add("sequence", "cross_unit", [index - 1, index], sequence_cue)
            sequence_previous = (index, sequence_cue)
            handled = True
        elif sequence_previous and sequence_previous[0] != index - 1:
            sequence_previous = None

        for first in ("通过", "依托", "借助"):
            if starts(text, first, ("率",) if first == "通过" else ()):
                result = next((cue for cue in ("实现", "促进", "提升") if text.find(cue, len(first)) > len(first)), None)
                if result:
                    add("support", "intra_unit", [index], f"{first}…{result}")
                    handled = True
                break

        for cue, denied in evidence_intra_cues:
            if starts(text, cue, denied):
                add("support", "intra_unit", [index], cue)
                break
        for cue, denied in evidence_cross_cues:
            if index > 0 and starts(text, cue, denied):
                add("support", "cross_unit", [index - 1, index], cue)
                break
    return relations


def _asset_bindings(page: dict, manifest: dict | None, page_text: str) -> list[dict]:
    """Return only the original Word assets explicitly bound to this page."""
    if manifest is None:
        return []
    page_number = int(page["page_number"])
    page_block_indexes = {
        block.get("source_block_index")
        for block in page.get("blocks", [])
        if isinstance(block.get("source_block_index"), int) and not isinstance(block.get("source_block_index"), bool)
    }
    page_block_indexes.update(
        index for index in page.get("source_block_indexes", [])
        if isinstance(index, int) and not isinstance(index, bool)
    )
    bindings = []
    for asset in manifest.get("assets", []):
        if page_number not in asset.get("page_numbers", []):
            continue
        source_block_indexes = sorted(set(asset.get("source_block_indexes", [])) & page_block_indexes)
        if not source_block_indexes:
            raise ValueError(f"source asset {asset.get('asset_id')} is not bound to page {page_number} blocks")
        binding = {
            "asset_id": asset["asset_id"],
            "sha256": asset["sha256"],
            "media_type": asset["media_type"],
            "relative_path": asset["relative_path"],
            "original_filename": asset["original_filename"],
            "source_block_indexes": source_block_indexes,
            "provenance": {
                "source_type": "word_inline_image" if str(asset.get("media_type", "")).startswith("image/") else "word_page_attachment",
                "source_page": page_number,
                "source_file": asset["original_filename"],
                "source_sha256": asset["sha256"],
                "source_block_indexes": source_block_indexes,
            },
        }
        if isinstance(asset.get("generation_input"), dict):
            binding["generation_input"] = asset["generation_input"]
        binding.update(binding_metadata(page_text, asset))
        bindings.append(binding)
    return bindings


def build(source: Path, output: Path, source_asset_manifest: dict | None = None) -> int:
    payload = json.loads(source.read_text(encoding="utf-8"))
    pages = payload.get("pages") if isinstance(payload, dict) else payload
    if not isinstance(pages, list) or not pages:
        raise ValueError("source must contain a non-empty pages array")
    output.mkdir(parents=True, exist_ok=True)
    numbers = [int(page["page_number"]) for page in pages]
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError("page numbers must start at 1 and be consecutive")
    locked_pages = []
    for page in pages:
        number = int(page["page_number"])
        blocks = page.get("blocks", [])
        if not blocks:
            raise ValueError(f"page {number} has no blocks")
        text = source_text(blocks)
        if not text:
            raise ValueError(f"page {number} has no source text")
        page_title, body_text, title_origin = _split_page_title_body_with_origin(
            text,
            number,
            pagination_mode=payload.get("pagination_mode"),
            page_comments=page.get("page_comments") or [],
        )
        validate_source_body_coverage(
            text,
            page_title,
            body_text,
            title_origin,
            page_number=number,
        )
        tables = [block["markdown"] for block in blocks if block.get("type") == "table" and block.get("markdown")]
        units = semantic_units(blocks, number)
        contract = {
            "schema_version": "2.0",
            "workflow_contract_version": WORKFLOW_VERSION,
            "page_number": number,
            "page_title": page_title,
            "title_origin": title_origin,
            "body_text": body_text,
            "body_hash": hashlib.sha256(body_text.encode("utf-8")).hexdigest(),
            "source_text": text,
            "source_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "source_blocks": blocks,
            "page_comments": page.get("page_comments") or [],
            "requirement_precedence": [
                "fixed_hard_rules",
                "ui_global_soft_preferences",
                "model_creative_freedom",
                "word_page_comments",
            ],
            "asset_bindings": _asset_bindings(page, source_asset_manifest, text),
            "source_tables": tables,
            "semantic_units": units,
            "explicit_relations": explicit_relations(units, number),
            "relationship_contract_sha256": "",
            "must_keep": page.get("must_keep") or [],
            "key_facts": [],
            "key_data": [],
            "forbidden_cross_page_content": page.get("forbidden_cross_page_content") or [],
            "page_purpose": page.get("page_purpose") or "待人工填写",
            "detected_dates": [detection(v, number) for v in dict.fromkeys(DATE_RE.findall(text))],
            "detected_numbers": [detection(v, number) for v in dict.fromkeys(NUMBER_RE.findall(text))],
            "detected_amounts": [detection(v, number) for v in dict.fromkeys(AMOUNT_RE.findall(text))],
            "human_review_status": "pending",
            "human_review_notes": [],
        }
        contract["relationship_contract_sha256"] = hashlib.sha256(json.dumps(contract["explicit_relations"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        target = output / f"page_{number:03d}.json"
        target.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        locked_pages.append({
            "page_number": number,
            "contract_file": target.name,
            "contract_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "relationship_contract_sha256": contract["relationship_contract_sha256"],
        })
    lock = {
        "schema_version": "2.0",
        "workflow_contract_version": WORKFLOW_VERSION,
        "source_file": str(source.name),
        "page_count": len(pages),
        "pages": locked_pages,
    }
    (output / "source_lock.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(pages)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    count = build(args.source.resolve(), args.out.resolve())
    print(f"contracts={count} output={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

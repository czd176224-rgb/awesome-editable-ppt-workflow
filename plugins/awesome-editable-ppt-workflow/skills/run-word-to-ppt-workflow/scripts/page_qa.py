"""Relaxed QA for one generated page, independently of every other page.

The assessor makes only three decisions: approved-style match, preservation of
the source page's content and logic (excluding the separately rendered page
main title), and absence of that main title in the generated image. It is not a
coverage scorer or cross-page comparator.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping


_ADVISORY_STYLE = frozenset({"match", "minor_variance"})
_ADVISORY_CONTENT = frozenset(
    {"match", "paraphrase", "reordered_blocks", "different_valid_layout", "minor_variance"}
)
_MATERIAL_STYLE = frozenset({"material_mismatch", "mismatch"})
_MATERIAL_CONTENT = frozenset({"material_mismatch", "mismatch"})
_STRUCTURAL_ISSUES = frozenset(
    {
        "invented_important_conclusion",
        "omitted_core_meaning",
        "reversed_major_relation",
        "invented_attachment_conclusion",
        "unsupported_new_claim",
        "unprovenanced_attachment_fact",
    }
)
_LOCAL_ISSUES = frozenset(
    {"wrong_key_fact", "wrong_number", "wrong_date", "wrong_entity", "missing_inline_image"}
)
_MINOR_ISSUES = frozenset({
    "minor_color_variance", "minor_typography_variance", "minor_decoration",
    "material_style_mismatch",
    "unreadable_crop", "unreadable_overlap", "word_attachment_conflict",
    "unmet_page_comment", "attachment_fact_with_recorded_source",
})
_IGNORED_TITLE_ISSUES = frozenset({"omitted_page_main_title"})
ISSUE_FIELDS = frozenset({"code", "message", "severity", "trigger", "evidence", "confidence"})
ISSUE_SEVERITIES = frozenset({"advisory", "local", "structural"})


def qa_issue(
    code: str, message: str, severity: str, trigger: str, evidence: str | Mapping[str, Any],
    confidence: str = "high",
) -> dict[str, Any]:
    return validate_qa_issue({
        "code": code,
        "message": " ".join(message.split())[:280],
        "severity": severity,
        "trigger": trigger,
        "evidence": dict(evidence) if isinstance(evidence, Mapping) else evidence,
        "confidence": confidence,
    })


def validate_qa_issue(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != ISSUE_FIELDS:
        raise ValueError("QA issues must use the structured issue contract")
    code, message, severity = value.get("code"), value.get("message"), value.get("severity")
    trigger, evidence, confidence = value.get("trigger"), value.get("evidence"), value.get("confidence")
    if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", code):
        raise ValueError("QA issue code is invalid")
    if not isinstance(message, str) or not message.strip() or len(message) > 280:
        raise ValueError("QA issue message is invalid")
    if severity not in ISSUE_SEVERITIES:
        raise ValueError("QA issue severity is invalid")
    if not isinstance(trigger, str) or not trigger.strip():
        raise ValueError("QA issue trigger is invalid")
    if isinstance(evidence, str) and evidence:
        pass
    elif isinstance(evidence, Mapping) and all(
        isinstance(evidence.get(field), str) and evidence.get(field)
        for field in ("file", "locator", "sha256")
    ):
        pass
    else:
        raise ValueError("QA issue evidence must be a label or exact provenance")
    if confidence not in {"low", "medium", "high"}:
        raise ValueError("QA issue confidence is invalid")
    return {
        "code": code, "message": message.strip(), "severity": severity, "trigger": trigger.strip(),
        "evidence": dict(evidence) if isinstance(evidence, Mapping) else evidence,
        "confidence": confidence,
    }


def issue_message(value: Mapping[str, Any]) -> str:
    return validate_qa_issue(value)["message"]


@dataclass(frozen=True)
class PageQAResult:
    """A compact result that can be passed directly to the repair builder."""

    status: str
    repair_scope: str
    issues: tuple[Mapping[str, Any], ...] = ()
    evidence: tuple[Mapping[str, Any], ...] = ()
    confidence: str = "high"
    trigger_reason: str | None = None
    checked_scope: str = "full"

    def __post_init__(self) -> None:
        if self.status not in {"pass", "pass_with_advisory", "repair"}:
            raise ValueError("status must be pass, pass_with_advisory, or repair")
        if self.repair_scope not in {"none", "local", "structural"}:
            raise ValueError("repair_scope must be none, local, or structural")
        if self.status == "repair":
            if self.repair_scope == "none" or not self.issues:
                raise ValueError("repair results need a local or structural scope and an issue")
        elif self.repair_scope != "none":
            raise ValueError("non-repair results must have repair_scope none")
        if self.confidence not in {"low", "medium", "high"}:
            raise ValueError("confidence must be low, medium, or high")
        if self.checked_scope not in {"full", "targeted"}:
            raise ValueError("checked_scope must be full or targeted")
        normalized_issues = tuple(validate_qa_issue(item) for item in self.issues)
        object.__setattr__(self, "issues", normalized_issues)
        if self.status == "pass" and normalized_issues:
            raise ValueError("pass results cannot contain issues")
        for record in self.evidence:
            if not isinstance(record, Mapping) or not isinstance(record.get("fact"), str):
                raise ValueError("each evidence record must include fact and source")
            source = record.get("source")
            if isinstance(source, str) and source:
                continue
            if isinstance(source, Mapping) and all(
                isinstance(source.get(field), str) and source.get(field)
                for field in ("file", "locator", "sha256")
            ):
                continue
            raise ValueError("each evidence source must be a label or exact file/locator/sha256 provenance")

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status,
            "repair_scope": self.repair_scope,
            "issues": [dict(item) for item in self.issues],
        }
        if self.evidence:
            result["evidence"] = [dict(record) for record in self.evidence]
        if self.confidence != "high":
            result["confidence"] = self.confidence
        if self.trigger_reason:
            result["trigger_reason"] = self.trigger_reason
        if self.checked_scope != "full":
            result["checked_scope"] = self.checked_scope
        return result


def assess_page(style_observation: dict, source_observation: dict) -> PageQAResult:
    """Assess only frozen-style match and current-page meaning preservation.

    Observations are deliberately qualitative. ``overall_match`` describes the
    frozen user-style decision, and ``main_content_match`` describes the
    current source-page decision.  Their issue lists may identify the concise,
    visible reason for a material failure; no score, completeness inventory, or
    comparison with another generated page is accepted or needed.
    """

    style = _observation(style_observation, "overall_match")
    source = _observation(source_observation, "main_content_match")
    style_status = style["overall_match"]
    content_status = source["main_content_match"]
    if _only_ignored_title_issues(source):
        content_status = "match"
    _validate_status(style_status, _ADVISORY_STYLE | _MATERIAL_STYLE, "overall_match")
    _validate_status(content_status, _ADVISORY_CONTENT | _MATERIAL_CONTENT, "main_content_match")
    categorized = _categorized_issues(style, source)

    title_visible = source.get("top_level_duplicate_title_visible")
    if title_visible is True:
        categorized["local"].append(qa_issue(
            "top_level_duplicate_title", "生成图片中出现了页面主标题。", "local",
            "semantic_page_observation", "ocr_text_and_position",
        ))
    elif title_visible is None and source.get("page_main_title_visible") is True:
        categorized["advisory"].append(qa_issue(
            "title_position_uncertain", "OCR检测到标题文字，但缺少顶层位置证据，未触发修复。", "advisory",
            "semantic_page_observation", "ocr_text_without_top_level_position", "medium",
        ))

    structural = categorized["structural"]
    local = categorized["local"]
    if style_status in _MATERIAL_STYLE:
        categorized["advisory"].append(qa_issue(
            "material_style_mismatch", "Overall style differs from the frozen user preference.", "advisory",
            "semantic_page_observation", "style_observation",
        ))
    if content_status in _MATERIAL_CONTENT and not structural and not local:
        structural.append(qa_issue(
            "main_content_material_mismatch",
            "Main content, key facts, or logic materially differs from the source page.",
            "structural", "semantic_page_observation", "content_observation",
        ))
    if structural:
        return PageQAResult("repair", "structural", tuple(_unique(structural)))
    if local:
        return PageQAResult("repair", "local", tuple(_unique(local)))

    advisory = categorized["advisory"]
    if style_status != "match" or content_status != "match" or advisory:
        return PageQAResult("pass_with_advisory", "none", tuple(_unique(advisory)))
    return PageQAResult("pass", "none")


def _observation(value: Any, status_key: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{status_key} observation must be an object")
    status = value.get(status_key)
    if not isinstance(status, str) or not status.strip():
        raise ValueError(f"observation must include {status_key}")
    return value


def _validate_status(value: str, allowed: frozenset[str], field: str) -> None:
    if value not in allowed:
        raise ValueError(f"{field} is not a supported qualitative observation")


def _categorized_issues(style: Mapping[str, Any], source: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    categorized = {"structural": [], "local": [], "advisory": []}
    for observation in (style, source):
        raw_issues = observation.get("issues", [])
        if raw_issues is None:
            continue
        if not isinstance(raw_issues, list):
            raise ValueError("issues must be a list")
        for issue in raw_issues:
            if not isinstance(issue, Mapping):
                raise ValueError("each issue must be an object")
            kind = issue.get("kind")
            detail = _concise_detail(issue.get("detail"))
            if not isinstance(kind, str) or not detail:
                raise ValueError("each issue must include kind and detail")
            normalized_kind = kind.strip().lower().replace("-", "_").replace(" ", "_")
            if normalized_kind in _STRUCTURAL_ISSUES:
                categorized["structural"].append(qa_issue(
                    normalized_kind, detail, "structural", "semantic_page_observation", normalized_kind,
                ))
            elif normalized_kind in _LOCAL_ISSUES:
                categorized["local"].append(qa_issue(
                    normalized_kind, detail, "local", "semantic_page_observation", normalized_kind,
                ))
            elif normalized_kind in _MINOR_ISSUES:
                categorized["advisory"].append(qa_issue(
                    normalized_kind, detail, "advisory", "semantic_page_observation", normalized_kind,
                ))
            elif normalized_kind in _IGNORED_TITLE_ISSUES:
                continue
            else:
                raise ValueError(f"unsupported relaxed QA issue kind: {kind}")
    return categorized


def _only_ignored_title_issues(source: Mapping[str, Any]) -> bool:
    issues = source.get("issues")
    if not isinstance(issues, list) or not issues:
        return False
    kinds = {
        str(issue.get("kind", "")).strip().lower().replace("-", "_").replace(" ", "_")
        for issue in issues
        if isinstance(issue, Mapping)
    }
    return bool(kinds) and kinds <= _IGNORED_TITLE_ISSUES


def _concise_detail(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:280] or None


def _unique(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item["code"], item["message"])
        if key not in seen:
            seen.add(key)
            values.append(item)
    return values

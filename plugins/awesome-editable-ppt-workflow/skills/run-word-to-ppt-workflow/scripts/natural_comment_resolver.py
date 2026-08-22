"""Resolve Word page comments into closed structured directives.

Comments are resolved by the closed deterministic vocabulary consumed by
the V6 materials compiler. Ambiguous required comments block the page.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence



SemanticKind = Literal[
    "visual_expression",
    "external_image",
    "page_image",
    "attachment_reference",
    "layout_override",
    "advisory",
]

_SEARCH = re.compile(r"^\[search-evidence:([^\]\r\n]+)\]$", re.IGNORECASE)
_REQUIREMENT = re.compile(r"^\[requirement:([^\]\r\n]+)\]$", re.IGNORECASE)
_PAGE_IMAGE = re.compile(
    r"^\[require-page-image:([A-Za-z0-9][A-Za-z0-9._-]{0,127})\]$",
    re.IGNORECASE,
)
_NOTE = re.compile(r"^\[note:([^\]\r\n]+)\]$", re.IGNORECASE)
_NEGATED_SEARCH = re.compile(
    r"(?:不要|无需|不必|禁止|避免)\s*(?:搜索|查找|使用)?.{0,8}(?:新闻|外部).{0,4}(?:图片|照片)|"
    r"(?:do not|don't|must not|no need to)\s+(?:search|find|use).{0,24}(?:news|external).{0,12}(?:image|photo)",
    re.IGNORECASE,
)
_EXTERNAL_IMAGE = re.compile(
    r"(?:新闻稿?\s*(?:图片|照片)|新闻(?:图片|照片)|搜索.{0,40}(?:新闻|照片|图片)|"
    r"(?:search|find|use|need|require).{0,40}(?:news|press|external).{0,20}(?:image|photo))",
    re.IGNORECASE,
)
_PAGE_IMAGE_NATURAL = re.compile(
    r"(?:必须|务必|请)?\s*(?:使用|采用|放入).{0,12}(?:本页|页面).{0,10}(?:第一张|首张|第\s*1\s*张)?.{0,4}(?:图片|照片)|"
    r"(?:must|required to|please)\s+use.{0,24}(?:first\s+)?(?:page|supplied).{0,12}(?:image|photo)",
    re.IGNORECASE,
)
_ATTACHMENT = re.compile(
    r"(?:参考|使用|采用).{0,12}(?:附件|报告).{0,24}(?:背景图|图片|素材)|"
    r"(?:use|reference).{0,24}(?:attachment|attached|report).{0,24}(?:background|image|material)",
    re.IGNORECASE,
)
_VISUAL_EXPRESSION = re.compile(
    r"(?:文字表达图片化|文字.{0,8}(?:图像化|可视化)|图解(?:文字|内容)|"
    r"(?:visuali[sz]e|turn).{0,24}(?:text|copy).{0,16}(?:visual|image))",
    re.IGNORECASE,
)
_COMPANY_LOGO_REQUEST = re.compile(
    r"(?=.*(?:企业|公司|品牌))(?=.*(?:Logo|标志|徽标))"
    r"(?=.*(?:都要|全部|均需|均要|每个|添加|加入|展示|放入))",
    re.IGNORECASE,
)
_NEGATION = re.compile(
    r"(?:不要|不得|不需要|不使用|不采用|无需|不必|禁止|避免|别|"
    r"do\s+not|don't|must\s+not|no\s+need|\bno\b|\bnot\b|\bavoid\b)",
    re.IGNORECASE,
)
_MATERIAL_INTENT_CONCEPT = re.compile(
    r"(?:搜索|查找|新闻.{0,4}(?:图片|照片)|本页.{0,12}(?:图片|照片)|附件|"
    r"报告.{0,20}(?:背景|图片|素材)|search|find|news.{0,12}(?:image|photo)|"
    r"page.{0,12}(?:image|photo)|attached|attachment|report.{0,20}(?:background|image|material))",
    re.IGNORECASE,
)

_VISUAL_TARGETS = {"visual.image_rendering", "visual.image_ratio", "visual.layout"}
_VISUAL_OVERRIDE_KEYS = {
    "visual.image_rendering": "image_rendering",
    "visual.image_ratio": "image_ratio",
    "visual.layout": "layout",
}
_MATERIAL_TARGETS = {
    "material.page_image",
    "material.attachment",
    "material.search_evidence",
}
_PROTECTED_TARGETS = {
    "word.body_text",
    "word.facts",
    "word.tables",
    "fixed.body_geometry",
    "fixed.page_title",
    "fixed.logo",
    "fixed.footer",
    "fixed.page_number",
}
_ALL_TARGETS = sorted(_VISUAL_TARGETS | _MATERIAL_TARGETS | _PROTECTED_TARGETS)


class CommentResolutionBlocked(RuntimeError):
    """A required Word comment could not be resolved safely."""


@dataclass(frozen=True)
class SearchRequest:
    """One authenticated visual lookup required by a parent Word comment."""

    directive_id: str
    parent_directive_id: str
    source_comment_id: str
    entity: str
    query: str
    material_id: str
    material_role: str = "enterprise_logo"
    required: bool = True
    max_results: int = 1

    @property
    def search_query(self) -> str:
        return self.query

    @property
    def search_required(self) -> bool:
        return True

    @property
    def decisions(self) -> tuple[Mapping[str, Any], ...]:
        return ({
            "target": "material.search_evidence",
            "action": "require",
            "material_id": self.material_id,
        },)


@dataclass(frozen=True)
class ResolvedDirective:
    directive_id: str
    source_comment_id: str
    raw_text: str
    kind: SemanticKind
    required: bool
    search_required: bool
    search_query: str | None
    visual_overrides: Mapping[str, Any]
    authority_kind: str
    decisions: tuple[Mapping[str, Any], ...]
    resolution_receipt: Mapping[str, Any] = field(default_factory=dict)
    search_requests: tuple[SearchRequest, ...] = field(default_factory=tuple)

    def authority_directive(self) -> dict[str, Any]:
        """Return the exact closed directive shape accepted by Task 1."""
        return {
            "directive_id": self.directive_id,
            "kind": self.authority_kind,
            "text": self.raw_text,
            "decisions": [dict(decision) for decision in self.decisions],
        }


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _directive_id(source_comment_id: str, text: str) -> str:
    digest = hashlib.sha256(_canonical({"source_comment_id": source_comment_id, "text": text})).hexdigest()
    return f"comment_{digest[:16]}"


def _child_search_directive_id(parent_directive_id: str, entity: str, material_id: str) -> str:
    digest = hashlib.sha256(_canonical({
        "parent_directive_id": parent_directive_id,
        "entity": entity,
        "material_id": material_id,
    })).hexdigest()
    return f"{parent_directive_id}__search_{digest[:12]}"


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _receipt_base(
    directive: ResolvedDirective, *, raw_comment: str, resolution_mode: str,
) -> dict[str, Any]:
    return {
        "receipt_version": "comment-resolution-receipt-v1",
        "source_comment_id": directive.source_comment_id,
        "raw_comment_sha256": hashlib.sha256(raw_comment.encode("utf-8")).hexdigest(),
        "directive_id": directive.directive_id,
        "closed_directive_sha256": _digest(directive.authority_directive()),
        "resolution_mode": resolution_mode,
        "role": "comment-resolution",
    }


def _bounded(value: str, limit: int = 240) -> str:
    return " ".join(value.split())[:limit].strip()


def _asset_identity(asset: Mapping[str, Any], fields: Sequence[str]) -> str | None:
    for field in fields:
        value = asset.get(field)
        if isinstance(value, str) and value:
            return value
    return None


def _is_image(asset: Mapping[str, Any]) -> bool:
    media_type = asset.get("media_type")
    if not isinstance(media_type, str):
        generation = asset.get("generation_input")
        media_type = generation.get("media_type") if isinstance(generation, Mapping) else None
    return isinstance(media_type, str) and media_type.startswith("image/")


def _search_query(page_context: Mapping[str, Any], explicit: str | None = None) -> str:
    if explicit:
        return _bounded(explicit)
    facts = page_context.get("key_facts", [])
    terms: list[str] = []
    if isinstance(facts, list):
        for fact in facts:
            if isinstance(fact, str):
                clean = _bounded(fact, 80).strip("，。；、:：")
                if clean and clean not in terms:
                    terms.append(clean)
    authority_text = " ".join(
        value
        for value in (page_context.get("page_title"), page_context.get("body_text"))
        if isinstance(value, str)
    )
    terms.sort(key=lambda term: authority_text.find(term) if term in authority_text else len(authority_text))
    if not terms:
        title = page_context.get("page_title")
        if isinstance(title, str) and title.strip():
            terms.append(_bounded(title, 160))
    terms.extend(term for term in ("新闻", "图片") if term not in terms)
    return _bounded(" ".join(terms))


def search_material_id(query: str) -> str:
    """Return the stable request/material identity shared by resolver and evidence."""
    bounded = _bounded(query)
    if not bounded:
        raise ValueError("search query is required")
    return f"search-request-{hashlib.sha256(bounded.encode('utf-8')).hexdigest()[:16]}"


def _enumerated_company_entities(page_context: Mapping[str, Any]) -> list[str]:
    """Extract only an exact, explicitly structured company set from locked Word text."""
    locked_source = page_context.get("source_text")
    source = (
        locked_source
        if isinstance(locked_source, str) and locked_source.strip()
        else page_context.get("body_text")
    )
    if not isinstance(source, str) or not source.strip():
        return []
    title = page_context.get("page_title")
    title = title.strip() if isinstance(title, str) else ""
    company_shape = r"[\u3400-\u9fffA-Za-z0-9·&＋+()（）-]{2,24}"

    def clean(value: str) -> str | None:
        candidate = value.strip().strip(" \t。.!！?？：:（）()[]【】《》\"'“”")
        if candidate == title or re.fullmatch(company_shape, candidate) is None:
            return None
        return candidate

    lead_matches = [
        item for item in re.findall(
            rf"(?:^|[\r\n])\s*围绕\s*({company_shape})\s*等链主企业",
            source,
            flags=re.M,
        )
        if clean(item)
    ]
    bullet_matches = [
        item for item in re.findall(
            rf"^[ \t]*[·•●▪]\s*({company_shape})\s*[：:]",
            source,
            flags=re.M,
        )
        if clean(item)
    ]
    if lead_matches or bullet_matches:
        # "X等" is not a closed set by itself. It becomes exact only when the
        # remaining named companies are explicitly enumerated as bullets.
        if lead_matches and not bullet_matches:
            return []
        ordered: list[str] = []
        for raw in [*lead_matches, *bullet_matches]:
            candidate = clean(raw)
            if candidate and candidate not in ordered:
                ordered.append(candidate)
        return ordered if len(ordered) >= 2 else []

    # Backward-compatible closed list form: one complete line consisting only
    # of company-name tokens separated by enumeration punctuation.
    for line in source.splitlines():
        stripped = line.strip()
        if stripped == title or not re.search(r"[、；;|｜]", stripped):
            continue
        raw_items = re.split(r"[、；;|｜]", stripped)
        items = [clean(item) for item in raw_items]
        if len(items) >= 2 and all(items) and len(set(items)) == len(items):
            return [str(item) for item in items]
    return []


def _is_wholly_negated_intent(text: str) -> bool:
    relevant = [
        clause.strip()
        for clause in re.split(r"[，,；;。.!！]+", text)
        if _MATERIAL_INTENT_CONCEPT.search(clause)
    ]
    return bool(relevant) and all(_NEGATION.search(clause) for clause in relevant)


def _visual_decisions(text: str) -> tuple[list[dict[str, Any]], bool]:
    decisions: list[dict[str, Any]] = []
    saw_negated = False
    candidates = (
        ("visual.image_rendering", r"(?:真实照片|实拍|新闻照片|photographic|real photo)", "photographic"),
        ("visual.image_rendering", r"(?:水墨|ink[- ]?illustration)", "ink-illustration"),
        ("visual.image_rendering", r"(?:手绘|hand[- ]?drawn)", "hand-drawn"),
        ("visual.image_ratio", _VISUAL_EXPRESSION.pattern, "medium-high"),
        (
            "visual.image_ratio",
            r"(?:图片|图像|image|visual).{0,18}(?:一半|50%|half)|"
            r"(?:一半|50%|half).{0,18}(?:图片|图像|image|visual)",
            "medium",
        ),
        ("visual.layout", r"(?:时间轴|timeline)", "timeline"),
    )
    clauses = re.split(
        r"[，,；;。.!！]+|(?:但是|不过|然而|并且|而且|同时|但|并|而)|\b(?:but|however|and)\b",
        text,
        flags=re.I,
    )
    selected_targets: set[str] = set()
    for clause in clauses:
        matches: list[tuple[int, int, str, str, str]] = []
        for target, pattern, value in candidates:
            matches.extend(
                (match.start(), match.end(), target, value, match.group(0))
                for match in re.finditer(pattern, clause, re.I)
            )
        matches.sort(key=lambda item: (item[0], item[1]))
        for index, (start, end, target, value, matched_text) in enumerate(matches):
            previous_end = matches[index - 1][1] if index else 0
            next_start = matches[index + 1][0] if index + 1 < len(matches) else len(clause)
            local_text = clause[previous_end:start] + matched_text + clause[end:next_start]
            if _NEGATION.search(local_text):
                saw_negated = True
                continue
            if target in selected_targets:
                continue
            decisions.append({"target": target, "action": "set", "value": value})
            selected_targets.add(target)
    return decisions, saw_negated


def _protected_decision(text: str) -> dict[str, Any] | None:
    def changes(target_pattern: str) -> bool:
        verb = r"(?:改成|改为|替换|修改|change|replace)"
        return bool(
            re.search(
                rf"(?:{target_pattern}).{{0,30}}{verb}|{verb}.{{0,30}}(?:{target_pattern})",
                text,
                re.I,
            )
        )

    if re.search(r"(?:Logo|标志|徽标).{0,16}(?:正文|主体|body)|(?:正文|主体|body).{0,16}(?:Logo|标志|徽标)", text, re.I):
        return {"target": "fixed.logo", "action": "set", "value": text}
    if re.search(r"(?:页码|page number).{0,20}(?:移|改|放|位置|move|place|change)", text, re.I):
        return {"target": "fixed.page_number", "action": "set", "value": text}
    if re.search(r"(?:标题|title).{0,16}(?:改|替换|change|replace)", text, re.I):
        return {"target": "fixed.page_title", "action": "set", "value": text}
    if changes(r"表格|table"):
        return {"target": "word.tables", "action": "replace", "value": text}
    if changes(r"正文|body"):
        return {"target": "word.body_text", "action": "replace", "value": text}
    if changes(r"事实|数据|利润|收入|fact|data"):
        return {"target": "word.facts", "action": "replace", "value": text}
    return None


def _resolved(
    text: str,
    source_comment_id: str,
    *,
    kind: SemanticKind,
    required: bool = True,
    search_required: bool = False,
    search_query: str | None = None,
    authority_kind: str = "note",
    decisions: Sequence[Mapping[str, Any]] = (),
    search_requests: Sequence[SearchRequest] = (),
) -> ResolvedDirective:
    visual = {
        _VISUAL_OVERRIDE_KEYS[str(item["target"])]: item["value"]
        for item in decisions
        if item.get("target") in _VISUAL_TARGETS and isinstance(item.get("value"), str)
    }
    return ResolvedDirective(
        directive_id=_directive_id(source_comment_id, text),
        source_comment_id=source_comment_id,
        raw_text=text,
        kind=kind,
        required=required,
        search_required=search_required,
        search_query=search_query,
        visual_overrides=visual,
        authority_kind=authority_kind,
        decisions=tuple(dict(item) for item in decisions),
        search_requests=tuple(search_requests),
    )


def resolve_comment_deterministically(
    text: str,
    page_context: Mapping[str, Any],
    assets: list[Mapping[str, Any]] | None = None,
    *,
    source_comment_id: str = "comment",
) -> ResolvedDirective | None:
    """Resolve known Chinese/English forms, returning ``None`` only for ambiguity."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("comment text is required")
    if not isinstance(page_context, Mapping):
        raise ValueError("page_context must be an object")
    if assets is not None and not isinstance(assets, list):
        raise ValueError("assets must be an array")
    normalized = text.strip()
    material = [item for item in (assets or []) if isinstance(item, Mapping)]

    note = _NOTE.fullmatch(normalized)
    if note:
        return _resolved(normalized, source_comment_id, kind="advisory", required=False)

    search = _SEARCH.fullmatch(normalized)
    if search:
        query = _search_query(page_context, search.group(1).strip())
        material_id = search_material_id(query)
        return _resolved(
            normalized,
            source_comment_id,
            kind="external_image",
            search_required=True,
            search_query=query,
            authority_kind="material_requirement",
            decisions=[{"target": "material.search_evidence", "action": "require", "material_id": material_id}],
        )

    page_image = _PAGE_IMAGE.fullmatch(normalized)
    if page_image:
        asset_id = page_image.group(1)
        return _resolved(
            normalized,
            source_comment_id,
            kind="page_image",
            authority_kind="material_requirement",
            decisions=[{"target": "material.page_image", "action": "require", "material_id": asset_id}],
        )

    requirement = _REQUIREMENT.fullmatch(normalized)
    if requirement:
        inner = requirement.group(1).strip()
        nested = resolve_comment_deterministically(
            inner,
            page_context,
            assets,
            source_comment_id=source_comment_id,
        )
        if nested is not None:
            return _resolved(
                normalized,
                source_comment_id,
                kind=nested.kind,
                search_required=nested.search_required,
                search_query=nested.search_query,
                authority_kind=nested.authority_kind,
                decisions=nested.decisions,
                search_requests=nested.search_requests,
            )
        return _resolved(normalized, source_comment_id, kind="layout_override", authority_kind="note")

    if _is_wholly_negated_intent(normalized):
        return _resolved(normalized, source_comment_id, kind="advisory")

    if _NEGATED_SEARCH.search(normalized):
        return _resolved(normalized, source_comment_id, kind="advisory")

    if re.search(r"(?:不要|不得|禁止|避免|无需|不必|do not|don't|must not)", normalized, re.I) and re.search(
        r"(?:正文|事实|数据|表格|Logo|标志|徽标|页码|标题|body|fact|table|page number|title)",
        normalized,
        re.I,
    ):
        return _resolved(normalized, source_comment_id, kind="advisory")

    if _COMPANY_LOGO_REQUEST.search(normalized):
        entities = _enumerated_company_entities(page_context)
        if entities:
            parent_directive_id = _directive_id(source_comment_id, normalized)
            requests: list[SearchRequest] = []
            decisions: list[dict[str, Any]] = []
            for entity in entities:
                query = _bounded(f"{entity} 官方 Logo")
                material_id = search_material_id(query)
                child_directive_id = _child_search_directive_id(
                    parent_directive_id, entity, material_id,
                )
                requests.append(SearchRequest(
                    directive_id=child_directive_id,
                    parent_directive_id=parent_directive_id,
                    source_comment_id=source_comment_id,
                    entity=entity,
                    query=query,
                    material_id=material_id,
                ))
                decisions.append({
                    "target": "material.search_evidence",
                    "action": "require",
                    "material_id": material_id,
                    "directive_id": child_directive_id,
                    "parent_directive_id": parent_directive_id,
                    "entity": entity,
                    "query": query,
                    "material_role": "enterprise_logo",
                })
            return _resolved(
                normalized,
                source_comment_id,
                kind="external_image",
                search_required=True,
                search_query=None,
                authority_kind="material_requirement",
                decisions=decisions,
                search_requests=requests,
            )
        raise CommentResolutionBlocked(
            "material_blocked: locked Word text does not provide an exact enterprise Logo set"
        )

    protected = _protected_decision(normalized)
    if protected:
        return _resolved(
            normalized,
            source_comment_id,
            kind="layout_override",
            authority_kind="mixed",
            decisions=[protected],
        )

    explicit_external_request = bool(
        normalized == "新闻稿图片"
        or (
            re.search(
                r"(?:搜索|查找|需要|必须|务必|注明来源|search|find|need|require|source|citation)",
                normalized,
                re.I,
            )
            and _EXTERNAL_IMAGE.search(normalized)
        )
    )
    if explicit_external_request:
        explicit = None
        match = re.search(r"搜索(.+?)(?:作为|用作|$)", normalized)
        if match:
            explicit = match.group(1).strip()
        query = _search_query(page_context, explicit)
        material_id = search_material_id(query)
        return _resolved(
            normalized,
            source_comment_id,
            kind="external_image",
            search_required=True,
            search_query=query,
            authority_kind="material_requirement",
            decisions=[{"target": "material.search_evidence", "action": "require", "material_id": material_id}],
        )

    visual, saw_negated_visual = _visual_decisions(normalized)
    if visual:
        visual_kind: SemanticKind = (
            "layout_override"
            if all(decision["target"] == "visual.layout" for decision in visual)
            else "visual_expression"
        )
        return _resolved(
            normalized,
            source_comment_id,
            kind=visual_kind,
            authority_kind="visual_override",
            decisions=visual,
        )
    if saw_negated_visual:
        return None

    if _PAGE_IMAGE_NATURAL.search(normalized):
        selected = next((item for item in material if _is_image(item)), None)
        asset_id = _asset_identity(selected, ("asset_id", "material_id")) if selected else None
        decisions = (
            [{"target": "material.page_image", "action": "require", "material_id": asset_id}]
            if asset_id
            else []
        )
        return _resolved(
            normalized,
            source_comment_id,
            kind="page_image",
            authority_kind="material_requirement",
            decisions=decisions,
        )

    if _ATTACHMENT.search(normalized):
        selected = next((item for item in material if not _is_image(item)), None)
        evidence_id = _asset_identity(selected, ("evidence_id", "material_id", "asset_id")) if selected else None
        decisions = (
            [{"target": "material.attachment", "action": "require", "material_id": evidence_id}]
            if evidence_id
            else []
        )
        return _resolved(
            normalized,
            source_comment_id,
            kind="attachment_reference",
            authority_kind="material_requirement",
            decisions=decisions,
        )

    if _EXTERNAL_IMAGE.search(normalized):
        explicit = None
        match = re.search(r"搜索(.+?)(?:作为|用作|$)", normalized)
        if match:
            explicit = match.group(1).strip()
        query = _search_query(page_context, explicit)
        material_id = search_material_id(query)
        return _resolved(
            normalized,
            source_comment_id,
            kind="external_image",
            search_required=True,
            search_query=query,
            authority_kind="material_requirement",
            decisions=[{"target": "material.search_evidence", "action": "require", "material_id": material_id}],
        )
    return None


def resolve_page_comments(
    project: Path,
    page_contract: Mapping[str, Any],
    assets: list[Mapping[str, Any]],
    timeout: float,
) -> list[ResolvedDirective]:
    """Resolve all page-local comments; unresolved required comments block."""
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if not isinstance(page_contract, Mapping):
        raise ValueError("page_contract must be an object")
    if not isinstance(assets, list):
        raise ValueError("assets must be an array")
    comments = page_contract.get("page_comments", [])
    if not isinstance(comments, list):
        raise ValueError("page comments must be an array")
    resolved: list[ResolvedDirective] = []
    seen: set[str] = set()
    for index, comment in enumerate(comments, start=1):
        if not isinstance(comment, Mapping):
            raise ValueError("page comment must be an object")
        source_id = comment.get("comment_id", f"comment-{index}")
        text = comment.get("text")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("page comment_id is required")
        if source_id in seen:
            raise ValueError(f"duplicate page comment_id: {source_id}")
        seen.add(source_id)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"page comment {source_id} text is required")
        directive = resolve_comment_deterministically(
            text,
            page_contract,
            assets,
            source_comment_id=source_id,
        )
        resolution_mode = "deterministic"
        if directive is None:
            visual_choices, saw_negated_visual = _visual_decisions(text)
            if saw_negated_visual and not visual_choices:
                raise CommentResolutionBlocked(
                    f"comment {source_id} contains a required visual exclusion that the closed authority cannot represent"
                )
            raise CommentResolutionBlocked(
                f"comment {source_id} is unresolved by the deterministic comment authority"
            )
        if resolution_mode == "deterministic":
            directive = replace(
                directive,
                resolution_receipt=_receipt_base(
                    directive, raw_comment=text, resolution_mode="deterministic",
                ),
            )
        if directive.kind in {"page_image", "attachment_reference"}:
            target = (
                "material.page_image"
                if directive.kind == "page_image"
                else "material.attachment"
            )
            required_ids = {
                str(item["material_id"])
                for item in directive.decisions
                if item.get("target") == target and isinstance(item.get("material_id"), str)
            }
            available_ids = {
                identity
                for asset in assets
                if isinstance(asset, Mapping)
                and ((directive.kind == "page_image") == _is_image(asset))
                for identity in [
                    _asset_identity(asset, ("asset_id", "material_id", "evidence_id"))
                ]
                if identity
            }
            if not required_ids or not required_ids.issubset(available_ids):
                raise CommentResolutionBlocked(
                    f"comment {source_id} has required page material that is not available"
                )
        resolved.append(directive)
    return resolved

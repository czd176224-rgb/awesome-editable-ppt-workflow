"""Compile the consulting-report page director into the sole Image2 prompt shape."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


SECTION_SPECS = (
    ("Task and Canvas", "task_and_canvas"),
    ("Core Proposition and Content", "core_proposition_and_content"),
    ("Consulting Information Architecture", "consulting_information_architecture"),
    ("Visual Style and Color", "visual_style_and_color"),
    ("Text and Typography", "text_and_typography"),
    ("Strict Prohibitions", "strict_prohibitions"),
)

_CUSTODY_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/]"),
    re.compile(r"(?:^|\s)/(?:[^/\s]+/)*[^/\s]+"),
    re.compile(r"(?:^|\s)(?:\.\.?[\\/]|\\\\)"),
    re.compile(r"\b(?:00_source|01_source_assets|02_v6)[\\/]", re.IGNORECASE),
    re.compile(r"\b[0-9a-f]{64}\b", re.IGNORECASE),
    re.compile(r"\b(?:sha-?256|digest|receipt[_ -]?id)\b", re.IGNORECASE),
)
_SENTENCE_PARTS = re.compile(r"[^.!?;。！？；\r\n]+[.!?;。！？；]?")
_FIXED_TERMS = (
    re.compile(r"\btitle\b", re.IGNORECASE),
    re.compile(r"\blogo\b", re.IGNORECASE),
    re.compile(r"\bfooter\b", re.IGNORECASE),
    re.compile(r"\bpage(?:[_ -]+)number\b", re.IGNORECASE),
)
_SAFE_REGION = re.compile(r"\b(?:central|largest|safe)\b.*\b17:8\b", re.IGNORECASE)
_CANVAS_BACKGROUND = re.compile(
    r"(?:\bcanvas\s+background\b|"
    r"\b(?:canvas|background)\b.*\b(?:color|grid|texture|gradient|glow)\b|"
    r"\b(?:color|grid|texture|gradient|glow)\b.*\b(?:canvas|background)\b)",
    re.IGNORECASE,
)
_BACKGROUND_EFFECT = re.compile(
    r"\b(?:texture|gradient|glow|fog|vortex|burlap|linen|paper)\b",
    re.IGNORECASE,
)
_FOREGROUND_CONTEXT = re.compile(
    r"\b(?:foreground|subject|evidence|person|people|object|arrange|arrangement|relationship|garment)\b",
    re.IGNORECASE,
)

_TASK_CONSTRAINT = (
    "Generate a 1904x896 slide body. At any output ratio, keep all meaningful content inside the "
    "central largest 17:8 content region with a visibly empty perimeter on all four sides. "
    "Give the source-supported main relationship the strongest visual priority."
)

LOCAL_CHART_SCOPE = (
    "Compose the whole slide first around its argument, facts, mechanisms, relationships, and "
    "source-supported conclusions where present. Freely combine faithful prose, process or hierarchy diagrams, optional local "
    "charts, and conclusions in one reading path. Choose a professional chart only when its local "
    "data has complete, compatible dimensions and improves communication; even complete data does "
    "not require a chart. The chart footprint follows the argument, not the presence of numbers. "
    "Standalone KPIs, dates, and counts may remain exact text or labels. The eight relationship "
    "examples are an optional local toolkit, not a page template or compulsory substitution. "
    "Keep source-grounded qualitative relationships visible without requiring a named substitute. "
    "Data-scale restrictions apply to data marks, not ordinary layout size, position, or hierarchy; "
    "visual emphasis must not masquerade as measured magnitude."
)

_QUANTITATIVE_CONTENT_CONSTRAINT = (
    "Data marks need complete compatible source values and labels: subject, unit, period, basis, "
    "categories, actual/forecast status, source-stated assumptions and total-to-component relationships; "
    "do not calculate new metrics or infer missing values."
)

LOCAL_VISUAL_SCOPE = (
    "Compose the whole slide first: mix prose, tables, diagrams and optional local charts. "
    "Use charts only when they help; complete data does not require a chart. KPIs, dates and counts may stay text. "
    "Measured-scale restrictions govern data marks, not ordinary layout size, position, or hierarchy."
)

VISIBLE_COPY_BOUNDARY = (
    "Role labels, prompt section headings, and quote introducers are instruction metadata, "
    "not visible copy: render only the source wording, never its instruction introducer. "
    "Source-authored headings and labels remain allowed; separate layout instructions from visible words."
)

SEMANTIC_FIDELITY = (
    "Word is the semantic authority. Preserve every distinct fact, explanation, relationship, "
    "and conclusion, including subjects, names, numbers, dates, units, bases, conditions, "
    "exceptions, degree of certainty, and their scope. Lossless rewording, regrouping, and "
    "text-to-diagram conversion are allowed; this is not a word-for-word copying rule. Shared "
    "labels may remove repetition only when their applicability stays explicit and no distinct "
    "information is lost. Visual semantic expansion exposes source-supported relationships, "
    "not new claims, causal links, ranks, or measured values. Follow the frozen page composition: "
    "do not split source body pages for layout density. Existing TOC continuations and source-backed "
    "section/closing additions remain authoritative. Do not move additional information across "
    "assigned pages or hide it in notes or appendices. Planning "
    "instructions, not visible slide copy, describe the design; they are not factual authority."
)

FRONTEND_FIDELITY = (
    "Preserve every distinct fact, explanation, relationship, "
    "and conclusion with its subject, names, numbers, dates, units, bases, conditions, exceptions, "
    "degree of certainty, and scope. Lossless rewording, regrouping, and text-to-diagram conversion "
    "are allowed with explicit shared-label scope. Preserve meaningful sequence, membership and "
    "ownership; parallel rows and paragraphs may be reordered. Keep facts, assumptions, calculated "
    "results, analytical judgments and recommendations distinct. Follow the frozen page composition: "
    "do not split source body pages for layout density; existing TOC continuations and source-backed "
    "section/closing additions stand. Keep all assigned information visible, not in notes or other pages."
)

_ARCHITECTURE_CONSTRAINT = (
    "Communicate one source-supported main message in a coherent reading path. Let relationships "
    "shape space; attach complete explanations and scoped qualifiers to their subjects without "
    "duplicating full prose. Visual focus is not authority rank or measured magnitude. "
    "Use a source-supported conclusion where present, no invented takeaway."
)

_TYPOGRAPHY_CONSTRAINT = (
    "Render accurate, legible Simplified Chinese with presentation-scale hierarchy and complete data labels."
)

_QUALITATIVE_PROHIBITION = (
    "Do not invent causal links, loops, ranks, or values, or add claims. Qualitative geometry is "
    "not measured magnitude: data marks must not imply unsupported numeric axes, proportions, "
    "size ranking or differences."
)

_FIXED_AND_SOURCE_PROHIBITION = (
    "Word is the semantic authority. Do not generate title, logo, footer, or page number; those "
    "are supplied as fixed PowerPoint layers."
)


def _material_value(material_view: object) -> Mapping[str, Any]:
    value = getattr(material_view, "value", material_view)
    if not isinstance(value, Mapping):
        raise ValueError("complete material view must expose a mapping value")
    return value


def _visual_contract_colors(material_view: object) -> dict[str, str]:
    contract = _material_value(material_view).get("visual_contract")
    if not isinstance(contract, Mapping):
        raise ValueError("complete material view visual contract is missing")
    colors: dict[str, str] = {}
    for key, label in (
        ("background_color", "background"),
        ("primary_color", "primary"),
        ("secondary_color", "secondary"),
    ):
        value = contract.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"complete material view {label} color is missing")
        colors[key] = value.strip()
    return colors


def _mix_hex(color: str, target: tuple[int, int, int], amount: float) -> str:
    source = tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
    mixed = tuple(
        round(value * (1 - amount) + target_value * amount)
        for value, target_value in zip(source, target)
    )
    return "#" + "".join(f"{value:02X}" for value in mixed)


def _color_constraints(
    material_view: object, *, font_accent_allowed: bool | None = False,
) -> tuple[str, str]:
    if font_accent_allowed is not None and type(font_accent_allowed) is not bool:
        raise ValueError("font accent permission must be boolean")
    colors = _visual_contract_colors(material_view)
    secondary = colors["secondary_color"]
    strong = _mix_hex(secondary, (0, 0, 0), 0.20)
    support = _mix_hex(secondary, (255, 255, 255), 0.40)
    soft = _mix_hex(secondary, (255, 255, 255), 0.70)
    wash = _mix_hex(secondary, (255, 255, 255), 0.88)
    positive = (
        f"Use primary color {colors['primary_color']} for primary text and neutral structure. "
        f"Use derived shades of secondary color {secondary}: strong {strong}, base, support {support}, "
        f"soft {soft}, wash {wash}. Allow large fills, wide paths, and structural geometry for "
        "grouping and visual hierarchy, not measured magnitude. For source relationships use at "
        "least two visibly distinct tones: parallel peers keep the same tone; their group or focal "
        "structure may differ. No color duty: neutrals suffice."
    )
    if font_accent_allowed is True:
        positive += (
            " This is a user-confirmed emphasis page: secondary-color-family text is optional and restrained."
        )
    elif font_accent_allowed is False:
        positive += (
            " This is not a user-confirmed emphasis page. Do not use any secondary-color-family "
            "shade for any text object; use primary or neutral text. Secondary-color-family shades "
            "remain allowed for text-box fills, shapes, borders, nodes, and connectors."
        )
    prohibited = (
        "Do not use color as the sole carrier of a fact or relationship, rank parallel peers by "
        "shade, or invent cross-hue business semantics."
    )
    return positive, prohibited


def _background_constraint(material_view: object) -> str:
    background = _visual_contract_colors(material_view)["background_color"]
    return (
        "Fill the entire canvas with the source-authoritative background color "
        f"{background}; do not add any background scene, gradient, pattern, or texture."
    )


def _validated_sections(value: Mapping[str, object]) -> dict[str, str]:
    raw = value.get("prompt_sections")
    if not isinstance(raw, Mapping):
        raise ValueError("consulting prompt sections are missing")
    expected = {key for _heading, key in SECTION_SPECS}
    if set(raw) != expected:
        raise ValueError("prompt sections must have the approved consulting six-part shape")
    result: dict[str, str] = {}
    for _heading, key in SECTION_SPECS:
        text = raw.get(key)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"consulting prompt section {key} must be non-empty natural language")
        if any(pattern.search(text) for pattern in _CUSTODY_PATTERNS):
            raise ValueError("compiled prompt contains a local custody path, digest, or receipt ID")
        result[key] = " ".join(text.split())
    return result


def _render_complete_source_block(block: object) -> str:
    if not isinstance(block, Mapping):
        raise ValueError("complete Word content block must be a mapping")
    block_type = block.get("type")
    if block_type in {"paragraph", "list"}:
        text = block.get("text")
        if not isinstance(text, str):
            raise ValueError(f"complete Word {block_type} block text is missing")
        if block_type == "paragraph":
            return text
        marker = "1." if block.get("list_kind") == "number" else "-"
        level = block.get("level", 0)
        indent = "  " * level if isinstance(level, int) and level > 0 else ""
        return f"{indent}{marker} {text}"
    if block_type == "table":
        rows = block.get("rows")
        if not isinstance(rows, list) or any(not isinstance(row, list) for row in rows):
            raise ValueError("complete Word table rows are missing")
        if any(any(not isinstance(cell, str) for cell in row) for row in rows):
            raise ValueError("complete Word table cells must be text")
        return "\n".join(" | ".join(row) for row in rows)
    raise ValueError(f"unsupported complete Word block type: {block_type}")


def _complete_fact_content(material_view: object) -> str:
    blocks = _material_value(material_view).get("complete_word_content")
    if not isinstance(blocks, list):
        raise ValueError("complete material view Word content is missing")
    ordered = sorted(
        enumerate(blocks),
        key=lambda item: (
            item[1].get("source_order", item[0])
            if isinstance(item[1], Mapping)
            else item[0]
        ),
    )
    return "\n".join(_render_complete_source_block(block) for _index, block in ordered)


def _canonical_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _page_plan_architecture(value: Mapping[str, object]) -> str:
    plan = value.get("page_plan")
    if not isinstance(plan, Mapping):
        raise ValueError("consulting page plan is missing")
    lines = [f"Page purpose: {plan.get('page_purpose', '')}"]
    for label, key in (
        ("Primary relationship", "primary_relationship"),
        ("Core exhibit", "core_exhibit"),
        ("Support groups", "support_groups"),
        ("Reading path", "reading_path"),
        ("Local visuals", "local_visuals"),
    ):
        lines.append(f"{label}: {_canonical_text(plan.get(key))}")
    selected = value.get("selected_references")
    if not isinstance(selected, list):
        raise ValueError("selected references are missing")
    lines.extend(
        f"Reference {reference['material_id']}: use: {reference['use']}; preserve: {reference['preserve']}"
        for reference in selected
        if isinstance(reference, Mapping)
    )
    return "\n".join(lines)


def _without_compiler_owned_clauses(text: str, *, task_section: bool) -> str:
    kept: list[str] = []
    for match in _SENTENCE_PARTS.finditer(text):
        clause = match.group(0).strip()
        if not clause:
            continue
        task_background_effect = (
            task_section
            and _BACKGROUND_EFFECT.search(clause)
            and not _FOREGROUND_CONTEXT.search(clause)
        )
        if (
            any(pattern.search(clause) for pattern in _FIXED_TERMS)
            or _SAFE_REGION.search(clause)
            or _CANVAS_BACKGROUND.search(clause)
            or task_background_effect
        ):
            continue
        kept.append(clause)
    return " ".join(kept)


def _join(parts: Sequence[str]) -> str:
    return "\n".join(part for part in parts if part)


def compile_consulting_six_part_prompt(
    value: Mapping[str, object], material_view: object, *,
    font_accent_allowed: bool | None = False,
) -> str:
    """Compile exactly six consulting-report sections in their sealed order."""
    schema_version = value.get("schema_version")
    if schema_version not in {
        "awesome-consulting-page-director-v3",
        "awesome-page-correction-v2",
    }:
        raise ValueError("consulting prompt requires a v3 director or v2 correction authority")
    positive_color, prohibited_color = _color_constraints(
        material_view, font_accent_allowed=font_accent_allowed
    )
    if schema_version == "awesome-page-correction-v2":
        sections = _validated_sections(value)
        for _heading, key in SECTION_SPECS:
            original = sections[key]
            sections[key] = _without_compiler_owned_clauses(
                original, task_section=key == "task_and_canvas"
            )
            sections[key] = sections[key].replace(positive_color, "").replace(
                prohibited_color, ""
            ).strip()
        sections["task_and_canvas"] = _join(
            (sections["task_and_canvas"], _background_constraint(material_view), _TASK_CONSTRAINT)
        )
        sections["core_proposition_and_content"] = _join(
            (VISIBLE_COPY_BOUNDARY, sections["core_proposition_and_content"], FRONTEND_FIDELITY)
        )
        sections["consulting_information_architecture"] = _join(
            (sections["consulting_information_architecture"], _ARCHITECTURE_CONSTRAINT, LOCAL_VISUAL_SCOPE)
        )
    else:
        architecture = _page_plan_architecture(value).replace(
            positive_color, ""
        ).replace(prohibited_color, "")
        sections = {
            "task_and_canvas": _TASK_CONSTRAINT,
            "core_proposition_and_content": _join(
                (VISIBLE_COPY_BOUNDARY, _complete_fact_content(material_view), FRONTEND_FIDELITY)
            ),
            "consulting_information_architecture": _join(
                (architecture, _ARCHITECTURE_CONSTRAINT, LOCAL_VISUAL_SCOPE)
            ),
            "visual_style_and_color": "",
            "text_and_typography": "",
            "strict_prohibitions": "",
        }
    sections["visual_style_and_color"] = _join(
        (
            sections["visual_style_and_color"],
            _background_constraint(material_view) if schema_version != "awesome-page-correction-v2" else "",
            positive_color,
        )
    )
    sections["text_and_typography"] = _join(
        (sections["text_and_typography"], _TYPOGRAPHY_CONSTRAINT)
    )
    sections["strict_prohibitions"] = _join(
        (
            sections["strict_prohibitions"],
            _FIXED_AND_SOURCE_PROHIBITION,
            _QUANTITATIVE_CONTENT_CONSTRAINT,
            prohibited_color,
            _QUALITATIVE_PROHIBITION,
        )
    )
    return "\n\n".join(
        f"## {heading}\n{sections[key]}" for heading, key in SECTION_SPECS
    )

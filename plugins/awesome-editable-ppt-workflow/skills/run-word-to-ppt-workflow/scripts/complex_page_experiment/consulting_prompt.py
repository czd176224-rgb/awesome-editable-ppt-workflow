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

_QUANTITATIVE_CONTENT_CONSTRAINT = (
    "Use a quantitative form only when source evidence is complete and unambiguous for the "
    "subject, unit, period, basis, categories, and values required by that form. Preserve every "
    "source value and label exactly; do not calculate new metrics, infer missing values, or mix "
    "incompatible subjects, units, periods, or bases. When those dimensions are incomplete, use "
    "the named qualitative substitute and make the source-stated relationship visibly legible. "
    "When a page contains financial, valuation, investment, or operating data, preserve subject, "
    "unit, period, basis, actual/forecast status, source-stated assumptions, and total-to-component "
    "relationships. Keep facts, assumptions, calculated results, analytical judgments, and "
    "recommendations distinct."
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
    "Render source-authorized Simplified Chinese accurately and legibly, with presentation-scale "
    "hierarchy for explanatory lead, analytical labels, evidence, interpretation, and takeaway. "
    "Apply TTS-style quantitative label discipline: every quantitative mark must identify its "
    "subject and keep its unit, period, and basis explicit through the chart heading, secondary heading, axis, "
    "legend, data label, or adjacent source-exact annotation."
)

_QUALITATIVE_PROHIBITION = (
    "Without complete source values, do not use numeric axes, proportional geometry, bubble-size "
    "ranking, target-line magnitude, or difference magnitude. A qualitative substitute must not "
    "masquerade as measured scale; use labels, equal sizing, sequence, grouping, connectors, and "
    "source wording to signal the relationship visibly."
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
        "Treat the confirmed background color as the canvas base; "
        f"use primary color {colors['primary_color']} for primary text and neutral structure. "
        f"Use derived shades of secondary color {secondary} when color has a source-grounded "
        f"structural duty: strong {strong}, the confirmed secondary as base, support {support}, "
        f"soft {soft}, and wash {wash}. "
        "The same hue communicates parallel items, the same "
        "category, or common membership. Ordered light-to-dark shades may communicate only a "
        "source-explicit process, hierarchy, stage, or visual focus. Cross-hue colors may be used "
        "only for source-explicit risk, status, rating, or positive/negative business meaning. "
        "Labels, legends, numbering, and spatial structure remain primary; color is supporting "
        "evidence and never invents meaning. When the source contains any of these relationships, "
        "use at least two visibly distinct tones from this family across the analytical backbone, "
        "not merely one accent line or colored text; parallel peers keep the same tone while their "
        "shared group, axis, stage, or focal node may use another tone. A page with no color duty "
        "may remain black, white, and gray."
    )
    if font_accent_allowed is True:
        positive += (
            " This is a user-confirmed emphasis page: secondary-color-family shades may be used "
            "selectively for important text, but they are optional and must remain restrained."
        )
    elif font_accent_allowed is False:
        positive += (
            " This is not a user-confirmed emphasis page. Do not use any secondary-color-family "
            "shade for any text object. Use primary or neutral text color plus weight, size, "
            "position, shape, or hierarchy for local emphasis. Secondary-color-family shades "
            "remain allowed for text-box fills, shapes, borders, nodes, and connectors."
        )
    prohibited = (
        "Do not use color as the sole carrier of a fact or relationship, assign ordered color "
        "depth to merely parallel categories, or introduce cross-hue business semantics absent "
        "from complete_word_content."
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


def _source_fact_references(material_view: object) -> dict[str, str]:
    blocks = _material_value(material_view).get("complete_word_content")
    if not isinstance(blocks, list):
        raise ValueError("complete material view Word content is missing")
    references: dict[str, str] = {}
    for block in blocks:
        if not isinstance(block, Mapping):
            raise ValueError("complete Word content block must be a mapping")
        source_id = str(block.get("source_block_id", "unknown"))
        if block.get("type") == "table":
            rows = block.get("rows")
            if not isinstance(rows, list):
                raise ValueError("complete Word table rows are missing")
            for cell in (
                cell
                for row in rows
                if isinstance(row, list)
                for cell in row
                if isinstance(cell, str) and cell
            ):
                references.setdefault(cell, source_id)
        else:
            text = block.get("text")
            if isinstance(text, str) and text:
                references.setdefault(text, source_id)
    return references


def _source_fact_matches(
    text: str, references: Mapping[str, str],
) -> list[tuple[int, int, str]]:
    def token_character(character: str) -> bool:
        return character.isalnum() or character in "_-"

    candidates: list[tuple[int, int, str]] = []
    for fact, source_id in references.items():
        start = text.find(fact)
        while start >= 0:
            end = start + len(fact)
            left_bound = (
                not token_character(fact[0])
                or start == 0
                or not token_character(text[start - 1])
            )
            right_bound = (
                not token_character(fact[-1])
                or end == len(text)
                or not token_character(text[end])
            )
            if left_bound and right_bound:
                candidates.append((start, end, source_id))
            start = text.find(fact, start + 1)
    matches: list[tuple[int, int, str]] = []
    cursor = 0
    for start, end, source_id in sorted(
        candidates, key=lambda item: (item[0], -(item[1] - item[0]))
    ):
        if start >= cursor:
            matches.append((start, end, source_id))
            cursor = end
    return matches


def _source_id_only_text(text: str, references: Mapping[str, str]) -> str:
    matches = _source_fact_matches(text, references)
    if not matches:
        return text
    parts: list[str] = []
    cursor = 0
    for start, end, source_id in matches:
        parts.extend((text[cursor:start], f"[source fact {source_id} in section 2]"))
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def _source_id_only_plan(value: object, references: Mapping[str, str]) -> object:
    if isinstance(value, Mapping):
        natural_language_fields = {
            "page_purpose", "description", "label", "visual_instruction",
            "reading_path", "instruction",
        }
        return {
            key: (
                _source_id_only_text(child, references)
                if key in natural_language_fields
                and isinstance(child, str)
                else _source_id_only_plan(child, references)
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_source_id_only_plan(child, references) for child in value]
    return value


def _page_plan_architecture(
    value: Mapping[str, object], material_view: object,
) -> str:
    plan = value.get("page_plan")
    if not isinstance(plan, Mapping):
        raise ValueError("consulting page plan is missing")
    references = _source_fact_references(material_view)
    normalized = _source_id_only_plan(plan, references)
    assert isinstance(normalized, Mapping)
    lines = [f"Page purpose: {normalized.get('page_purpose', '')}"]
    for label, key in (
        ("Primary relationship", "primary_relationship"),
        ("Core exhibit", "core_exhibit"),
        ("Support groups", "support_groups"),
        ("Reading path", "reading_path"),
        ("Local visuals", "local_visuals"),
    ):
        lines.append(f"{label}: {_canonical_text(normalized.get(key))}")
    selected = value.get("selected_references")
    if not isinstance(selected, list):
        raise ValueError("selected references are missing")
    reference_lines: list[str] = []
    for reference in selected:
        if not isinstance(reference, Mapping):
            raise ValueError("selected reference must be a mapping")
        instructions = (reference.get("use"), reference.get("preserve"))
        if any(not isinstance(instruction, str) for instruction in instructions):
            raise ValueError("selected reference use and preserve must be text")
        if any(_source_fact_matches(instruction, references) for instruction in instructions):
            raise ValueError("selected reference instructions must not repeat complete Word facts")
        reference_lines.append(
            f"Reference {reference['material_id']}: use: {reference['use']}; preserve: {reference['preserve']}"
        )
    return "\n".join(("\n".join(lines), *reference_lines))


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
            fixed_or_safe_restatement = any(
                pattern.search(original) for pattern in _FIXED_TERMS
            ) or _SAFE_REGION.search(original)
            if not sections[key] and (
                key != "task_and_canvas" or fixed_or_safe_restatement
            ):
                raise ValueError(
                    f"consulting prompt section {key} contains only compiler-owned boundary clauses"
                )
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
        architecture = _page_plan_architecture(value, material_view).replace(
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

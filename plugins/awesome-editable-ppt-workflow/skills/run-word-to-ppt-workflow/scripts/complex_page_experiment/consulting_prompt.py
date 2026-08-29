"""Compile the consulting-report page director into the sole Image2 prompt shape."""

from __future__ import annotations

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
    "Generate one coherent consulting-report slide body at 1904x896. Regardless of the provider's "
    "eventual canvas aspect ratio, place all body text, charts, diagrams, tables, annotations, "
    "connectors, references, and key decoration inside the central largest 17:8 content region; "
    "leave a visibly empty perimeter on all four sides. Do not generate title, logo, footer, or "
    "page number; those are supplied as fixed PowerPoint layers."
)

_ARGUMENT_CONSTRAINT = (
    "The page must communicate one business proposition and retain explanatory copy that "
    "connects evidence, interpretation, and conclusion, ending with an explicit takeaway."
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

_VISIBLE_TEXT_CUSTODY = (
    "business_proposition, explanatory_lead, and takeaway_statement are planning instructions, "
    "not visible slide copy. Every visible word or label must use exact contiguous spans from "
    "complete_word_content. Do not paraphrase, summarize, expand, or invent "
    "visible wording. The prohibition is against text expansion, not visual semantic expansion: "
    "use color, spatial position, shapes, connectors, and visual hierarchy to make relationships "
    "already explicit in complete_word_content visible without adding explanatory wording."
)

_ARCHITECTURE_CONSTRAINT = (
    "Choose one content-driven analytical backbone, build the page skeleton before placing text, "
    "and make every module participate in the "
    "same reading path; do not substitute disconnected cards or one decorative panorama for "
    "the page argument. Make source-explicit process, hierarchy, parallelism, membership, "
    "comparison, and causality visible as a complete page skeleton. Labels, legends, numbering, "
    "and spatial structure remain the primary information carriers. Color may make an existing "
    "relationship explicit but must not create a relationship that complete_word_content does not state."
)

_DUAL_MODE_RELATIONSHIP_CONSTRAINT = """Apply a row below only when complete_word_content explicitly contains that relationship. If none of the eight relationships is source-explicit, do not introduce a chart or any named qualitative substitute. Apply this exact eight-row dual-mode relationship mapping:
increase_decrease_drivers: use a scaled cumulative bridge/waterfall only with verified start, changes, and end; otherwise use an equal-weight positive/negative driver bridge with no cumulative baseline or computed end value.
change_over_time: use a line or column chart only with explicit periods and values; otherwise use a timeline or stage-evolution roadmap with no implied slope or magnitude.
two_variable_relationship: use a scatter plot only with numeric x/y values; use a clearly labelled qualitative quadrant only when the source supplies the two qualitative axes and item classifications; otherwise use a comparison table.
third_variable_size: encode bubble size only from a real non-negative third numeric variable; otherwise use uniform-size nodes with no size ranking.
market_size_share: use a Mekko/variable rectangle only with complete width and share values; otherwise use an equal-width hierarchy or portfolio matrix with no area-based claim.
project_stage_time: use a Gantt only with explicit start/end or start/duration; otherwise use an ordered roadmap or milestone sequence when dates/durations are absent.
option_comparison: use a bar/dot plot only when comparable values or source ratings exist; otherwise use a native comparison table with source-backed criteria and wording.
target_actual_variance: use bar/dot plus target line or difference arrow only when both values share a unit/basis; otherwise use a goal-current-gap narrative structure with no target line, arrow magnitude, or calculated variance."""

_TYPOGRAPHY_CONSTRAINT = (
    "Render source-authorized Simplified Chinese accurately and legibly, with presentation-scale "
    "hierarchy for explanatory lead, analytical labels, evidence, interpretation, and takeaway. "
    "Apply TTS-style quantitative label discipline: every quantitative mark must identify its "
    "subject and keep its unit, period, and basis explicit through the title, subtitle, axis, "
    "legend, data label, or adjacent source-exact annotation."
)

_QUALITATIVE_PROHIBITION = (
    "Without complete source values, do not use numeric axes, proportional geometry, bubble-size "
    "ranking, target-line magnitude, or difference magnitude. A qualitative substitute must not "
    "masquerade as measured scale; use labels, equal sizing, sequence, grouping, connectors, and "
    "source wording to signal the relationship visibly."
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
    if value.get("schema_version") not in {
        "awesome-consulting-page-director-v2",
        "awesome-page-correction-v2",
    }:
        raise ValueError("consulting prompt requires a v2 director or correction authority")
    sections = _validated_sections(value)
    positive_color, prohibited_color = _color_constraints(
        material_view, font_accent_allowed=font_accent_allowed
    )
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
        (
            sections["core_proposition_and_content"],
            _ARGUMENT_CONSTRAINT,
            _VISIBLE_TEXT_CUSTODY,
            _QUANTITATIVE_CONTENT_CONSTRAINT,
        )
    )
    sections["consulting_information_architecture"] = _join(
        (
            sections["consulting_information_architecture"],
            _ARCHITECTURE_CONSTRAINT,
            _DUAL_MODE_RELATIONSHIP_CONSTRAINT,
        )
    )
    sections["visual_style_and_color"] = _join(
        (sections["visual_style_and_color"], positive_color)
    )
    sections["text_and_typography"] = _join(
        (sections["text_and_typography"], _TYPOGRAPHY_CONSTRAINT)
    )
    sections["strict_prohibitions"] = _join(
        (sections["strict_prohibitions"], prohibited_color, _QUALITATIVE_PROHIBITION)
    )
    return "\n\n".join(
        f"## {heading}\n{sections[key]}" for heading, key in SECTION_SPECS
    )

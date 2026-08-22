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

_TASK_CONSTRAINT = (
    "Generate one coherent consulting-report slide body at 1904x896 with a 17:8 aspect ratio. "
    "Keep every body text block, chart, diagram, table, annotation, connector, and supporting "
    "visual inside the central safe region. Do not generate title, logo, footer, or page number; "
    "those are supplied as fixed PowerPoint layers."
)

_ARGUMENT_CONSTRAINT = (
    "The page must communicate one business proposition and retain explanatory copy that "
    "connects evidence, interpretation, and conclusion, ending with an explicit takeaway."
)

_ARCHITECTURE_CONSTRAINT = (
    "Choose one content-driven analytical backbone and make every module participate in the "
    "same reading path; do not substitute disconnected cards or one decorative panorama for "
    "the page argument."
)

_TYPOGRAPHY_CONSTRAINT = (
    "Render source-authorized Simplified Chinese accurately and legibly, with presentation-scale "
    "hierarchy for explanatory lead, analytical labels, evidence, interpretation, and takeaway."
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


def _color_constraints(material_view: object) -> tuple[str, str]:
    colors = _visual_contract_colors(material_view)
    positive = (
        f"Fill the entire canvas with confirmed background color {colors['background_color']}. "
        f"Use confirmed primary color {colors['primary_color']} for body text and structural "
        f"hierarchy. Use confirmed secondary color {colors['secondary_color']} only as a semantic "
        "accent for decisive terms, numbers, numbering, core nodes, arrow tips, local underlines, "
        "status markers, and key transitions. Keep background and light neutral areas dominant, "
        "dark text and structure secondary, and visible accent coverage restrained."
    )
    prohibited = (
        "Do not use the accent color for full-width solid headers, full-column fills, card "
        "backgrounds, wide bands or paths, large tinted regions, repeated solid icons, or a "
        "colored border around every module."
    )
    return positive, prohibited


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


def _join(parts: Sequence[str]) -> str:
    return "\n".join(part for part in parts if part)


def compile_consulting_six_part_prompt(
    value: Mapping[str, object], material_view: object
) -> str:
    """Compile exactly six consulting-report sections in their sealed order."""
    if value.get("schema_version") != "awesome-consulting-page-director-v2":
        raise ValueError("consulting prompt requires awesome-consulting-page-director-v2")
    sections = _validated_sections(value)
    positive_color, prohibited_color = _color_constraints(material_view)
    sections["task_and_canvas"] = _join(
        (sections["task_and_canvas"], _TASK_CONSTRAINT)
    )
    sections["core_proposition_and_content"] = _join(
        (sections["core_proposition_and_content"], _ARGUMENT_CONSTRAINT)
    )
    sections["consulting_information_architecture"] = _join(
        (sections["consulting_information_architecture"], _ARCHITECTURE_CONSTRAINT)
    )
    sections["visual_style_and_color"] = _join(
        (sections["visual_style_and_color"], positive_color)
    )
    sections["text_and_typography"] = _join(
        (sections["text_and_typography"], _TYPOGRAPHY_CONSTRAINT)
    )
    sections["strict_prohibitions"] = _join(
        (sections["strict_prohibitions"], prohibited_color)
    )
    return "\n\n".join(
        f"## {heading}\n{sections[key]}" for heading, key in SECTION_SPECS
    )

"""Validate and seal the seven-field presentation director taskbook."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


TASKBOOK_FIELDS = (
    "use_scenario",
    "presenter",
    "primary_audience",
    "audience_prior_knowledge",
    "desired_outcome",
    "emphasis",
    "deemphasis",
)

DIRECTOR_TEMPLATE_IDS = frozenset({
    "company-business-introduction",
    "investment-committee",
    "project-initiation",
    "corporate-planning",
    "investment-project-bp",
})

TASKBOOK_AUTHORITY_BOUNDARY = (
    "This taskbook is a user-confirmed presentation constraint, not factual source material. "
    "It may guide only how Word information maps to the compact page plan. Lossless within-page "
    "rewording and regrouping are "
    "allowed; the taskbook cannot authorize new facts, omitted information, altered meaning, "
    "or moving content between pages."
)

_TASKBOOK_LABELS = {
    "use_scenario": "Use scenario",
    "presenter": "Presenter",
    "primary_audience": "Primary audience",
    "audience_prior_knowledge": "Audience prior knowledge",
    "desired_outcome": "Desired understanding, discussion, or decision",
    "emphasis": "Word content to emphasize",
    "deemphasis": "Content to keep lower prominence",
}


def validate_taskbook(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(TASKBOOK_FIELDS):
        raise ValueError("director taskbook must contain exactly the seven confirmed fields")
    result: dict[str, str] = {}
    for field in TASKBOOK_FIELDS:
        text = value[field]
        if (
            not isinstance(text, str)
            or (field != "emphasis" and not text.strip())
            or len(text) > 2_000
        ):
            raise ValueError(f"director taskbook {field} must be nonblank text up to 2000 characters")
        result[field] = text.strip()
    return result


def _normalized_match_text(value: str) -> str:
    return "".join(
        character.casefold()
        for character in unicodedata.normalize("NFKC", value)
        if character.isalnum()
    )


def _block_text(block: Mapping[str, Any]) -> str:
    text = block.get("text")
    if isinstance(text, str):
        return text
    rows = block.get("rows")
    if isinstance(rows, list):
        return "\n".join(
            str(cell)
            for row in rows
            if isinstance(row, list)
            for cell in row
        )
    return ""


def _emphasis_parts(emphasis: str) -> tuple[str, ...]:
    parts = [part for part in re.split(r"[\r\n,，、;；。!?！？]+", emphasis) if part.strip()]
    parts.extend(
        item
        for part in tuple(parts)
        for item in re.split(r"(?:以及|和|与|及)", part)
        if item.strip() and item != part
    )
    return tuple(dict.fromkeys(parts))


def identify_emphasis_pages(
    emphasis: str, pages: Sequence[Mapping[str, Any]],
) -> set[int]:
    """Conservatively match confirmed emphasis against the complete paginated Word."""
    fragments = {
        normalized
        for part in _emphasis_parts(emphasis)
        if len(normalized := _normalized_match_text(part)) >= 2
    }
    if not fragments:
        return set()
    matches: set[int] = set()
    for page in pages:
        number = page.get("page_number")
        blocks = page.get("blocks")
        if type(number) is not int or not isinstance(blocks, list):
            continue
        page_text = _normalized_match_text(
            "\n".join(_block_text(block) for block in blocks if isinstance(block, Mapping))
        )
        if any(fragment in page_text for fragment in fragments):
            matches.add(number)
    return matches


def identify_semantic_emphasis_pages(
    emphasis: str,
    pages: Sequence[Mapping[str, Any]],
    composition_pages: Sequence[Mapping[str, Any]],
) -> set[int]:
    """Match paraphrased emphasis with multiple page-local lexical signals."""
    canonical = {"路径": "计划", "成效": "成果"}

    def normalize(value: str) -> str:
        result = _normalized_match_text(value)
        for source, target in canonical.items():
            result = result.replace(source, target)
        return result

    phrases = [
        normalized for part in _emphasis_parts(emphasis)
        if len(normalized := normalize(part)) >= 4
    ]
    if not phrases:
        return set()
    page_text = {
        page["page_number"]: _normalized_match_text(
            "\n".join(_block_text(block) for block in page.get("blocks", []) if isinstance(block, Mapping))
        )
        for page in pages
        if type(page.get("page_number")) is int and isinstance(page.get("blocks"), list)
    }
    matches: set[int] = set()
    for page in composition_pages:
        output = page.get("output_page_number")
        source = page.get("source_page_number")
        if type(output) is not int:
            continue
        heading = normalize(
            " ".join(str(page.get(field, "")) for field in ("chapter_title", "fixed_page_title"))
            + page_text.get(source, "")
        )
        heading_pairs = {heading[index:index + 2] for index in range(len(heading) - 1)}
        for phrase in phrases:
            phrase_pairs = {phrase[index:index + 2] for index in range(len(phrase) - 1)}
            if phrase in heading or len(heading_pairs & phrase_pairs) >= 2:
                matches.add(output)
                break
    # ponytail: page-local phrase evidence is a precision-first semantic proxy; use a
    # sealed classifier only if future documents need deeper paraphrase recall.
    return matches


def expand_emphasis_sections(
    matches: set[int], composition_pages: Sequence[Mapping[str, Any]],
) -> set[int]:
    """Include a contiguous section only when repeated direct matches prove shared emphasis."""
    expanded = set(matches)
    boundaries = [
        index
        for index, page in enumerate(composition_pages)
        if page.get("page_role") in {"section", "closing"}
    ]
    for position, start in enumerate(boundaries):
        if composition_pages[start].get("page_role") != "section":
            continue
        stop = boundaries[position + 1] if position + 1 < len(boundaries) else len(composition_pages)
        numbers = {
            int(page["output_page_number"])
            for page in composition_pages[start:stop]
            if type(page.get("output_page_number")) is int
        }
        if composition_pages[start].get("output_page_number") in matches or len(numbers & matches) >= 2:
            expanded.update(numbers)
    return expanded


def project_emphasis_pages(project: Path) -> set[int]:
    """Recompute the internal emphasis-page set from current Word pagination and taskbook."""
    from workflow_v6_state import load

    state = load(Path(project))
    if not isinstance(state.get("director_confirmation"), Mapping):
        return set()
    try:
        taskbook = load_confirmed_taskbook(project)
        manifest = json.loads(
            (Path(project) / "02_v6" / "paginated_word_source.json").read_text(
                encoding="utf-8-sig"
            )
        )
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ValueError("paginated Word source is missing or invalid") from exc
    pages = manifest.get("pages") if isinstance(manifest, Mapping) else None
    if not isinstance(pages, list):
        raise ValueError("paginated Word source has no page list")
    matches = identify_emphasis_pages(
        taskbook["emphasis"], [page for page in pages if isinstance(page, Mapping)]
    )
    composition_path = Path(project) / "02_v6" / "page_composition.json"
    if not composition_path.is_file():
        return matches
    try:
        composition = json.loads(composition_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError("page composition is invalid") from exc
    composition_pages = composition.get("pages") if isinstance(composition, Mapping) else None
    if not isinstance(composition_pages, list):
        raise ValueError("page composition has no page list")
    clean_pages = [page for page in pages if isinstance(page, Mapping)]
    clean_composition = [page for page in composition_pages if isinstance(page, Mapping)]
    return expand_emphasis_sections(
        matches | identify_semantic_emphasis_pages(
            taskbook["emphasis"], clean_pages, clean_composition,
        ),
        clean_composition,
    )


def taskbook_digest(value: Mapping[str, Any]) -> str:
    normalized = validate_taskbook(value)
    payload = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_confirmed_taskbook(project: Path) -> dict[str, str]:
    from workflow_v6_state import load

    state = load(Path(project))
    confirmation = state.get("director_confirmation")
    if not isinstance(confirmation, Mapping):
        raise ValueError("confirmed director taskbook is missing")
    taskbook = validate_taskbook(confirmation.get("taskbook"))
    if confirmation.get("taskbook_digest") != taskbook_digest(taskbook):
        raise ValueError("confirmed director taskbook digest is invalid")
    return taskbook


def confirmed_taskbook_prompt(project: Path) -> str:
    taskbook = load_confirmed_taskbook(project)
    fields = "\n".join(
        f"- {_TASKBOOK_LABELS[field]}: {taskbook[field]}" for field in TASKBOOK_FIELDS
    )
    return (
        f"{TASKBOOK_AUTHORITY_BOUNDARY}\n"
        f"{fields}\n"
        "Map the use scenario and desired outcome to page_purpose. Select primary_relationship "
        "only from source-supported relationships. Use the presenter, primary audience, and prior "
        "knowledge to calibrate reading_path. Use emphasis and deemphasis only to allocate source "
        "blocks among core_exhibit and support_groups and to suggest local_visuals. A conclusion is "
        "optional and may appear only when the Word source supplies it. Keep deemphasized Word "
        "content present but visually subordinate; never omit it."
    )

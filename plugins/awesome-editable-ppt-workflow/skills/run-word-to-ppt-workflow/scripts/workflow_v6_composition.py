"""Build and validate the ordered V6 page composition manifest."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

import workflow_v6_secure_io as secure_io


PAGE_ROLES = frozenset({"cover", "toc", "section", "content", "closing", "appendix"})
TOC_ENTRY_CAPACITY = 12
_ARTIFACT_VERSION = "page-composition-v1"
_ROLE_MAP = {
    "封面": "cover",
    "首页": "cover",
    "目录": "toc",
    "章节": "section",
    "正文": "content",
    "尾页": "closing",
    "附录": "appendix",
}
_CONTROL_RE = re.compile(r"^\s*PPT\s*页型\s*[：:]\s*([^\s]+)\s*$", re.IGNORECASE)
_PART_RE = re.compile(r"^\s*PART\s*([0-9]+)\s*[|｜]\s*(.+?)\s*$", re.IGNORECASE)
_CHAPTER_RE = re.compile(r"^\s*第\s*([0-9一二三四五六七八九十百]+)\s*章\s*[：:]?\s*(.+?)\s*$")
_TOC_RE = re.compile(r"^\s*(?:PART\b|第\s*[0-9一二三四五六七八九十百]+\s*章|章节)", re.IGNORECASE)
_COVER_TERMS = ("呈报对象", "汇报主线", "联合", "报告", "建议")
_CLOSING_TERMS = ("尾页", "结束语", "致谢", "谢谢")
_CLOSING_PREFIXES = ("最终目标：", "结论：", "结束语：")
_PAGE_FIELDS = {
    "output_page_number",
    "source_page_id",
    "page_role",
    "role_source",
    "chapter_title",
    "fixed_page_title",
    "source_page_number",
    "material_source_block_ids",
    "visible_page_number",
}
_OPTIONAL_PAGE_FIELDS = {"composition_page_id"}


def _block_text(block: Mapping[str, Any]) -> str:
    if block.get("type") in {"paragraph", "list"}:
        value = block.get("text")
        return value.strip() if isinstance(value, str) else ""
    if block.get("type") == "table" and isinstance(block.get("rows"), list):
        return "\n".join(
            " | ".join(str(cell) for cell in row)
            for row in block["rows"]
            if isinstance(row, list)
        ).strip()
    value = block.get("markdown")
    return value.strip() if isinstance(value, str) else ""


def _lines(blocks: Sequence[Mapping[str, Any]]) -> list[tuple[str, str | None]]:
    values: list[tuple[str, str | None]] = []
    for block in blocks:
        text = _block_text(block)
        block_id = block.get("source_block_id")
        if text:
            values.extend(
                (line.strip(), block_id if isinstance(block_id, str) and block_id else None)
                for line in text.splitlines()
                if line.strip()
            )
    return values


def _chapter(line: str) -> str:
    match = _PART_RE.match(line) or _CHAPTER_RE.match(line)
    return match.group(2).strip() if match else ""


def _chapter_entries(blocks: Sequence[Mapping[str, Any]]) -> list[tuple[str, str, str | None]]:
    entries: list[tuple[str, str, str | None]] = []
    seen: set[str] = set()
    for line, block_id in _lines(blocks):
        title = _chapter(line)
        if title and title not in seen:
            entries.append((title, line, block_id))
            seen.add(title)
    return entries


def _material_ids(blocks: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        block_id
        for block in blocks
        if _block_text(block)
        and isinstance((block_id := block.get("source_block_id")), str)
        and block_id
    ]


def explicit_role(
    blocks: Sequence[Mapping[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    role = None
    renderable: list[dict[str, Any]] = []
    for block in blocks:
        text = _block_text(block)
        marker = _CONTROL_RE.match(text) if block.get("type") in {"paragraph", "list"} else None
        if marker:
            role = role or _ROLE_MAP.get(marker.group(1))
            continue
        renderable.append(dict(block))
    return role, renderable


def explicit_comment_role(
    comments: Sequence[Mapping[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    role = None
    renderable = []
    for comment in comments:
        text = comment.get("text")
        marker = _CONTROL_RE.match(text) if isinstance(text, str) else None
        if marker:
            role = role or _ROLE_MAP.get(marker.group(1))
            continue
        renderable.append(dict(comment))
    return role, renderable


def _automatic_role(
    blocks: Sequence[Mapping[str, Any]], *, first_logical_page: bool
) -> str:
    lines = [line for line, _block_id in _lines(blocks)]
    paragraphs = [
        _block_text(block)
        for block in blocks
        if block.get("type") in {"paragraph", "list"} and _block_text(block)
    ]
    if first_logical_page and len(paragraphs) <= 12 and any(
        term in line for line in lines for term in _COVER_TERMS
    ):
        return "cover"
    if len({line for line in lines if _TOC_RE.match(line)}) >= 2:
        return "toc"
    first_three = lines[:3]
    if any("附录" in line for line in first_three):
        return "appendix"
    if any(term in line for line in first_three for term in _CLOSING_TERMS):
        return "closing"
    return "content"


def _source_record(
    raw_page: Mapping[str, Any], *, first_logical_page: bool
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    explicit, blocks = explicit_role(raw_page.get("blocks", []))
    comment_explicit, _comments = explicit_comment_role(raw_page.get("page_comments", []))
    role = explicit or comment_explicit or _automatic_role(
        blocks, first_logical_page=first_logical_page,
    )
    lines = _lines(blocks)
    chapters = _chapter_entries(blocks)
    chapter_title = chapters[0][0] if chapters and role in {"section", "content"} else ""
    if role == "section" and not chapter_title and lines:
        chapter_title = lines[0][0]
    resolved_title = raw_page.get("resolved_title")
    fixed_title = resolved_title.strip() if isinstance(resolved_title, str) else (lines[0][0] if lines else "")
    return ({
        "output_page_number": 0,
        "source_page_id": raw_page.get("source_page_id"),
        "page_role": role,
        "role_source": "explicit" if explicit or comment_explicit else "automatic",
        "chapter_title": chapter_title,
        "fixed_page_title": fixed_title,
        "source_page_number": raw_page.get("page_number"),
        "material_source_block_ids": _material_ids(blocks),
        "visible_page_number": role not in {"cover", "closing"},
    }, blocks)


def _synthesized_record(
    *, role: str, chapter_title: str, fixed_title: str, source_block_id: str,
    composition_page_id: str | None = None,
) -> dict[str, Any]:
    record = {
        "output_page_number": 0,
        "source_page_id": None,
        "page_role": role,
        "role_source": "synthesized",
        "chapter_title": chapter_title,
        "fixed_page_title": fixed_title,
        "source_page_number": None,
        "material_source_block_ids": [source_block_id],
        "visible_page_number": role != "closing",
    }
    if composition_page_id is not None:
        record["composition_page_id"] = composition_page_id
    return record


def compose_pages(pages_payload: Mapping[str, Any]) -> dict[str, Any]:
    raw_pages = pages_payload.get("pages", [])
    if not isinstance(raw_pages, list):
        raise ValueError("pages payload is invalid")

    source_records: list[dict[str, Any]] = []
    blocks_by_page: list[list[dict[str, Any]]] = []
    for index, raw_page in enumerate(raw_pages):
        if not isinstance(raw_page, Mapping):
            raise ValueError("source page is invalid")
        record, blocks = _source_record(raw_page, first_logical_page=index == 0)
        source_records.append(record)
        blocks_by_page.append(blocks)

    split_records: list[dict[str, Any]] = []
    split_blocks: list[list[dict[str, Any]]] = []
    for record, blocks in zip(source_records, blocks_by_page):
        material_ids = record["material_source_block_ids"]
        if record["page_role"] != "toc" or len(material_ids) <= TOC_ENTRY_CAPACITY + 1:
            split_records.append(record)
            split_blocks.append(blocks)
            continue
        title_id, entries = material_ids[0], material_ids[1:]
        chunks = [entries[index:index + TOC_ENTRY_CAPACITY] for index in range(0, len(entries), TOC_ENTRY_CAPACITY)]
        first = copy.deepcopy(record)
        first["material_source_block_ids"] = [title_id, *chunks[0]]
        split_records.append(first)
        split_blocks.append([block for block in blocks if block.get("source_block_id") in set(first["material_source_block_ids"])])
        for part_number, chunk in enumerate(chunks[1:], start=2):
            continuation = copy.deepcopy(record)
            continuation.update({
                "source_page_id": None,
                "source_page_number": None,
                "role_source": "synthesized",
                "material_source_block_ids": chunk,
                "composition_page_id": f"toc-continuation:{title_id}:{part_number}",
            })
            split_records.append(continuation)
            split_blocks.append([block for block in blocks if block.get("source_block_id") in set(chunk)])
    source_records, blocks_by_page = split_records, split_blocks

    toc_chapters: list[tuple[str, str, str | None]] = []
    for record, blocks in zip(source_records, blocks_by_page):
        if record["page_role"] == "toc":
            toc_chapters.extend(_chapter_entries(blocks))

    composed: list[dict[str, Any]] = []
    inserted_sections: set[str] = set()
    toc_by_title = {}
    block_occurrences: dict[str | None, int] = {}
    for title, line, block_id in toc_chapters:
        occurrence = block_occurrences.get(block_id, 0) + 1
        block_occurrences[block_id] = occurrence
        toc_by_title[title] = (line, block_id, occurrence)
    for record in source_records:
        chapter_title = record["chapter_title"]
        if record["page_role"] == "section" and chapter_title:
            inserted_sections.add(chapter_title)
        if record["page_role"] == "content" and chapter_title in toc_by_title:
            if chapter_title not in inserted_sections:
                line, block_id, occurrence = toc_by_title[chapter_title]
                if not block_id:
                    block_id = next(iter(record["material_source_block_ids"]), None)
                if block_id:
                    composed.append(_synthesized_record(
                        role="section",
                        chapter_title=chapter_title,
                        fixed_title=line,
                        source_block_id=block_id,
                        composition_page_id=(
                            f"synthesized-section:{block_id}:{occurrence}"
                        ),
                    ))
                    inserted_sections.add(chapter_title)
        composed.append(record)

    if not any(record["page_role"] == "closing" for record in composed):
        closing = None
        for blocks in blocks_by_page:
            lines = _lines(blocks)
            for line, block_id in lines:
                if line.startswith(_CLOSING_PREFIXES) and block_id:
                    closing = (line, block_id)
                    break
            if closing:
                break
        if closing:
            composed.append(_synthesized_record(
                role="closing",
                chapter_title="",
                fixed_title=closing[0],
                source_block_id=closing[1],
            ))

    for output_page_number, record in enumerate(composed, start=1):
        record["output_page_number"] = output_page_number
    result = {
        "artifact_version": _ARTIFACT_VERSION,
        "page_count": len(composed),
        "pages": composed,
        "warnings": copy.deepcopy(pages_payload.get("pagination_warnings", [])),
    }
    validate_composition(result)
    return result


def validate_composition(value: Mapping[str, Any], *, confirmed: bool = False) -> None:
    if set(value) != {"artifact_version", "page_count", "pages", "warnings"}:
        raise ValueError("composition fields are invalid")
    if value["artifact_version"] != _ARTIFACT_VERSION:
        raise ValueError("composition artifact version is invalid")
    pages = value["pages"]
    if not isinstance(pages, list) or type(value["page_count"]) is not int:
        raise ValueError("composition page collection is invalid")
    if value["page_count"] != len(pages):
        raise ValueError("composition page count is invalid")
    if not isinstance(value["warnings"], list):
        raise ValueError("composition warnings are invalid")
    if [page.get("output_page_number") for page in pages if isinstance(page, Mapping)] != list(
        range(1, len(pages) + 1)
    ):
        raise ValueError("composition output positions must be continuous")
    if sum(page.get("page_role") == "cover" for page in pages if isinstance(page, Mapping)) > 1:
        raise ValueError("composition cannot contain two cover pages")

    page_ids = []
    for page in pages:
        if (
            not isinstance(page, Mapping)
            or not _PAGE_FIELDS.issubset(page)
            or not set(page).issubset(_PAGE_FIELDS | _OPTIONAL_PAGE_FIELDS)
        ):
            raise ValueError("composition page fields are invalid")
        page_id = page.get("composition_page_id")
        if page_id is not None and (not isinstance(page_id, str) or not page_id):
            raise ValueError("composition page identity is invalid")
        if page_id is not None:
            if page["source_page_id"] is not None or page["source_page_number"] is not None:
                raise ValueError("composition page identity is not producer-owned")
            if not confirmed and page["role_source"] != "synthesized":
                raise ValueError("composition page identity requires synthesized provenance")
            page_ids.append(page_id)
        if page["page_role"] not in PAGE_ROLES:
            raise ValueError("composition page role is invalid")
        if page["role_source"] not in {"explicit", "automatic", "synthesized"}:
            raise ValueError("composition role source is invalid")
        if page["page_role"] == "section" and not str(page["chapter_title"]).strip():
            raise ValueError("composition section title is required")
        if not isinstance(page["chapter_title"], str) or not isinstance(page["fixed_page_title"], str):
            raise ValueError("composition page titles are invalid")
        if not page["fixed_page_title"].strip():
            raise ValueError("composition fixed page title is required")
        if page["source_page_id"] is not None and type(page["source_page_id"]) is not int:
            raise ValueError("composition source page id is invalid")
        if page["source_page_number"] is not None and type(page["source_page_number"]) is not int:
            raise ValueError("composition source page number is invalid")
        block_ids = page["material_source_block_ids"]
        if not isinstance(block_ids, list) or any(not isinstance(item, str) or not item for item in block_ids):
            raise ValueError("composition source block ids are invalid")
        if page["role_source"] == "synthesized" and not block_ids:
            raise ValueError("synthesized composition page requires a source block id")
        if type(page["visible_page_number"]) is not bool or page["visible_page_number"] != (
            page["page_role"] not in {"cover", "closing"}
        ):
            raise ValueError("composition visible page number is invalid")
    if len(page_ids) != len(set(page_ids)):
        raise ValueError("composition page identity must be unique")


def freeze_composition(
    value: Mapping[str, Any], submitted_pages: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    frozen = copy.deepcopy(dict(value))
    frozen["pages"] = [copy.deepcopy(dict(page)) for page in submitted_pages]
    frozen["page_count"] = len(frozen["pages"])
    validate_composition(frozen, confirmed=True)
    return frozen


def legacy_content_only_confirmation(root: Path) -> bool:
    try:
        payload = secure_io.read_bytes(root, Path("confirm_ui/result.json"))
    except FileNotFoundError:
        return False
    except OSError as exc:
        if exc.errno == -1073741772:  # Windows STATUS_OBJECT_NAME_NOT_FOUND
            return False
        raise
    value = json.loads(payload.decode("utf-8"))
    expected = {
        "status", "revision", "confirmed_at", "production_profile",
        "global_visual_contract", "confirmed_pages",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        return False
    pages = value.get("confirmed_pages")
    return (
        value.get("status") == "confirmed"
        and type(value.get("revision")) is int
        and isinstance(value.get("production_profile"), str)
        and isinstance(value.get("global_visual_contract"), Mapping)
        and isinstance(pages, list) and bool(pages)
        and all(
            isinstance(page, Mapping)
            and type(page.get("page_number")) is int
            and page["page_number"] > 0
            and "output_page_number" not in page
            and "page_role" not in page
            for page in pages
        )
    )


def load_composition_authority(root: Path) -> dict[str, Any] | None:
    try:
        payload = secure_io.read_bytes(root, Path("02_v6/page_composition.json"))
    except FileNotFoundError:
        payload = None
    except OSError as exc:
        if exc.errno != -1073741772:  # Windows STATUS_OBJECT_NAME_NOT_FOUND
            raise
        payload = None
    if payload is None:
        if legacy_content_only_confirmation(root):
            return None
        raise ValueError("current V6 project is missing frozen page composition authority")
    composition = json.loads(payload.decode("utf-8"))
    validate_composition(composition, confirmed=True)
    return composition

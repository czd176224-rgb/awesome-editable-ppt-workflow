"""Create V6 page sources, effective content, and non-blocking references."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

from docx import Document
from docx.oxml.ns import qn

from extract_docx_pages import extract_auto, iter_blocks
from source_assets import extract_source_assets
from workflow_v6_contract import new_page, new_project
from workflow_v6_materials import (
    chart_to_facts, extract_attachment_material, new_page_materials, reference_image_from_normalized, reference_image_from_source, resolve_page_comments,
    validate_page_materials,
)
from workflow_v6_media import normalize_reference
from workflow_v6_state import create, load, mutation_lock
from style_recommendations import _recommendations


V6_PAGE_MARKER = r"^第\s*(\d+)\s*页(?:\s*PPT)?$"
_LEGACY_PROJECT_MARKERS = frozenset({
    "workflow_run.json",
    "workflow_state.json",
    "workflow_v4.json",
    "workflow_v5.json",
})
_SEARCH_TERMS = re.compile(r"(?:搜索|查找|检索|新闻|资料|公开材料|网络材料)")
_ATTACHMENT_TERMS = re.compile(r"(?:附件|附带文件|链接材料|链接附件)")
_PAGE_MARKER_RE = re.compile(r"^第\s*(\d+)\s*页\s*[：:]?\s*$")
_COMMENT_TITLE_RE = re.compile(
    r"(?:\[title\s*[：:]\s*([^\]\r\n]+)\]|(?:PPT标题|页面标题|本页标题|标题)\s*[：:]\s*([^\r\n]+))",
    re.IGNORECASE,
)
_HEADING_RE = re.compile(
    r"^(?:[一二三四五六七八九十]+、|[（(][一二三四五六七八九十]+[）)]|\d+(?:\.\d+)*[、.)）])\s*"
)
_CONTINUATION_PREFIX_RE = re.compile(r"^[，。；：,.;:]")


def _normalize_page_text(text: str) -> str:
    return "\n".join(
        line.rstrip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ).strip()


def _comment_title(page_comments: list[dict] | None) -> str | None:
    for comment in page_comments or []:
        if not isinstance(comment, dict):
            continue
        match = _COMMENT_TITLE_RE.search(str(comment.get("text", "")))
        if match:
            title = " ".join(next(group for group in match.groups() if group).split()).strip(
                "，。；：,.;: "
            )
            if title:
                return title[:40]
    return None


def _looks_like_explicit_physical_title(line: str) -> bool:
    value = line.strip()
    if not value or _CONTINUATION_PREFIX_RE.match(value) or "|" in value:
        return False
    if len(value) > 34 or value.endswith(("，", "。", "；", "：", ",", ".", ";", ":")):
        return False
    if _HEADING_RE.match(value):
        return len(value) <= 26
    return len(value) <= 24 and bool(
        re.search(r"(报告|进展|方案|计划|安排|情况|分析|总结|建议|任务)$", value)
    )


def _looks_like_ordered_marker_title(line: str, line_count: int) -> bool:
    value = line.strip()
    return bool(
        line_count > 1
        and value
        and len(value) <= 34
        and not _CONTINUATION_PREFIX_RE.match(value)
        and "|" not in value
        and not value.endswith(("，", "。", "；", ",", ".", ";"))
    )


def _derive_short_page_title(text: str, page_number: int) -> str:
    value = _normalize_page_text(text)
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if lines:
        lines[0] = re.sub(r"^图\s*\d+\s*[：:]\s*", "", lines[0], flags=re.IGNORECASE)
    derived = f"第{page_number}页工作进展"
    for raw in lines:
        if not _HEADING_RE.match(raw.strip()):
            continue
        candidate = _HEADING_RE.sub("", raw.strip()).lstrip("，。；：,.;: ")
        clause = re.split(r"[，。；：,;:]", candidate, maxsplit=1)[0].strip()
        if 2 <= len(clause) <= 28:
            derived = clause
            break
    else:
        for raw in lines:
            if re.search(r"\s{2,}", raw) or ("牵头方" in raw and "方案" in raw):
                continue
            candidate = _HEADING_RE.sub("", raw.strip()).lstrip("，。；：,.;: ")
            if not candidate or "|" in candidate:
                continue
            clause = re.split(r"[，。；：,;:]", candidate, maxsplit=1)[0].strip()
            if len(clause) < 6:
                clause = candidate
            derived = clause[:28].rstrip("，。；：,.;: ") or derived
            break
    entities = re.findall(r"[一二三四五六七八九十百\d]+(?:地市场|种增长模式)", value)
    action = re.match(r"^(聚焦|围绕|推动|加快|深化|提升|推进|建设|实现|形成)", value)
    if action and len(entities) >= 2:
        title = f"{action.group(1)}{entities[0]}与{entities[1]}"
        if len(title) <= 28:
            return title
    return derived


def _split_page_title_body_with_origin(
    text: str,
    page_number: int,
    *,
    pagination_mode: str | None = None,
    page_comments: list[dict] | None = None,
) -> tuple[str, str, str]:
    content_text = _normalize_page_text(text)
    raw_lines = content_text.splitlines()
    lines = [line.strip() for line in raw_lines if line.strip()]
    marker_removed = False
    if lines:
        marker = _PAGE_MARKER_RE.fullmatch(lines[0])
        if marker:
            if int(marker.group(1)) != page_number:
                raise ValueError(f"page {page_number} contains a mismatched page marker")
            marker_index = next(index for index, line in enumerate(raw_lines) if line.strip())
            content_text = _normalize_page_text("\n".join(raw_lines[marker_index + 1 :]))
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
        body_text = _normalize_page_text("\n".join(raw_lines[heading_index + 1 :]))
        return lines[0], body_text, "explicit_word_heading"
    return _derive_short_page_title(content_text, page_number), content_text, "derived_from_body"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_materials_mutable(project: Path) -> None:
    """Reference selection is allowed only before the one final UI freeze."""
    state = load(project)
    result_path = Path(project).resolve() / "confirm_ui" / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {}
    if state.get("page_materials_status") == "confirmed" or (
        result.get("status") == "confirmed" and type(result.get("revision")) is int
    ):
        raise ValueError("confirmed V6 page materials are frozen and cannot be changed downstream")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _page_text(page: Mapping[str, Any]) -> str:
    values: list[str] = []
    for block in page.get("blocks", []):
        if not isinstance(block, Mapping):
            continue
        if block.get("type") in {"paragraph", "list"}:
            value = block.get("text")
        elif block.get("type") == "table" and isinstance(block.get("rows"), list):
            value = "\n".join(" | ".join(str(cell) for cell in row) for row in block["rows"])
        else:
            value = block.get("markdown")
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return "\n\n".join(values)


def _explicit_title_source_block_id(page: Mapping[str, Any], title_origin: str) -> str | None:
    """Bind a title only when its complete source block is structurally proven."""
    if title_origin != "explicit_word_heading":
        return None
    for block in page.get("blocks", []):
        if not isinstance(block, Mapping):
            continue
        block_type = block.get("type")
        if block_type in {"paragraph", "list"}:
            value = block.get("text")
            has_content = isinstance(value, str) and bool(value.strip())
        elif block_type == "table":
            rows = block.get("rows")
            has_content = isinstance(rows, list) and any(
                str(cell).strip() for row in rows if isinstance(row, list) for cell in row
            )
        else:
            has_content = False
        if not has_content:
            continue
        if block_type != "paragraph":
            return None
        lines = [line for line in str(block.get("text", "")).splitlines() if line.strip()]
        source_id = block.get("source_block_id")
        return source_id if len(lines) == 1 and isinstance(source_id, str) else None
    return None


def _hyperlinks_by_block(docx_path: Path) -> dict[int, list[str]]:
    document = Document(docx_path)
    values: dict[int, list[str]] = {}
    for index, block in enumerate(iter_blocks(document)):
        targets: list[str] = []
        for element in block._element.iter():
            if element.tag.rsplit("}", 1)[-1] != "hyperlink":
                continue
            relationship_id = element.get(qn("r:id"))
            if not relationship_id:
                continue
            relationship = document.part.rels.get(relationship_id)
            target = getattr(relationship, "target_ref", None)
            if isinstance(target, str) and target and target not in targets:
                targets.append(target)
        if targets:
            values[index] = targets
    return values


def _links_for_page(page: Mapping[str, Any], links_by_block: Mapping[int, list[str]]) -> list[str]:
    indexes = {
        int(block["source_block_index"])
        for block in page.get("blocks", [])
        if isinstance(block, Mapping) and type(block.get("source_block_index")) is int
    }
    return list(dict.fromkeys(
        link for index in sorted(indexes) for link in links_by_block.get(index, [])
    ))


def _asset_references(
    page_number: int, manifest: Mapping[str, Any], *, project: Path
) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for asset in manifest.get("assets", []):
        if not isinstance(asset, Mapping) or page_number not in asset.get("page_numbers", []):
            continue
        generation_input = asset.get("generation_input")
        status = "available" if isinstance(generation_input, Mapping) else "unavailable"
        reference = {
            "kind": (
                "word_image" if str(asset.get("media_type", "")).startswith("image/")
                else "chart" if str(asset.get("media_type", "")).endswith("drawingml.chart+xml")
                else "attachment"
            ),
            "status": status,
            "purpose": "本页 Word 自带材料",
            "asset_id": asset.get("asset_id"),
            "media_type": asset.get("media_type"),
        }
        original_relative = asset.get("relative_path")
        if isinstance(original_relative, str):
            reference["original_path"] = (
                project / "01_source_assets" / original_relative
            ).relative_to(project).as_posix()
        if isinstance(asset.get("sha256"), str):
            reference["original_sha256"] = asset["sha256"]
        if isinstance(generation_input, Mapping):
            relative = generation_input.get("relative_path")
            if isinstance(relative, str):
                model_input_path = (
                    project / "01_source_assets" / relative
                ).relative_to(project).as_posix()
                reference["path"] = model_input_path
                reference["model_input_path"] = model_input_path
            if isinstance(generation_input.get("sha256"), str):
                reference["model_input_sha256"] = generation_input["sha256"]
        references.append(reference)
    return references


def compile_effective_page(
    *,
    page_number: int,
    word_text: str,
    comments: Sequence[Mapping[str, Any]],
    references: Sequence[Mapping[str, Any]],
    attachment_links: Sequence[str],
    fixed_page_title: str | None = None,
    body_render_content: str | None = None,
) -> dict[str, Any]:
    directives = [
        {
            "comment_id": str(item.get("comment_id", index)),
            "text": str(item.get("text", "")).strip(),
            "precedence": "overrides_word_content",
        }
        for index, item in enumerate(comments, start=1)
        if isinstance(item, Mapping) and str(item.get("text", "")).strip()
    ]
    has_attachment = bool(attachment_links) or any(
        item.get("kind") == "attachment" and item.get("status") == "available"
        for item in references
    )
    invalidated = []
    search_requests = []
    for directive in directives:
        text = directive["text"]
        if _ATTACHMENT_TERMS.search(text) and not has_attachment:
            invalidated.append({
                "comment_id": directive["comment_id"],
                "kind": "attachment_reference",
                "reason": "attachment_unavailable",
            })
        if _SEARCH_TERMS.search(text):
            search_requests.append({"page_number": page_number, "purpose": text})
    return {
        "artifact_version": "effective-page-v6",
        "page_number": page_number,
        "word_original": word_text,
        "fixed_page_title": fixed_page_title or f"第{page_number}页",
        "body_render_content": body_render_content if body_render_content is not None else word_text,
        "title_render_policy": "fixed_layer_only_never_render_in_body",
        "comment_directives": directives,
        "authority_order": ["page_comments", "word_original", "global_style", "references"],
        "effective_content_policy": (
            "Apply every active page comment as an authoritative modification of the Word text; "
            "comments may replace Word facts. Without comments, preserve the Word text."
        ),
        "invalidated_requirements": invalidated,
        "search_requests": search_requests,
    }


def _material_path(project: Path, page_number: int) -> Path:
    return Path(project).resolve() / "02_v6" / "page_materials" / f"page_{page_number:03d}.json"


def _reference_material_path(project: Path, page_number: int) -> Path:
    return Path(project).resolve() / "02_v6" / "reference_materials" / f"page_{page_number:03d}.json"


def _chart_records(manifest: Mapping[str, Any], page_number: int) -> list[dict[str, Any]]:
    records = manifest.get("chart_records", manifest.get("charts", []))
    if not isinstance(records, list):
        return []
    return [
        dict(record) for record in records
        if isinstance(record, Mapping) and page_number in record.get("page_numbers", [])
    ]


def _load_reference_materials(project: Path, page_number: int) -> tuple[dict[str, Any], dict[str, Any]]:
    material_path = _material_path(project, page_number)
    receipt_path = _reference_material_path(project, page_number)
    if not material_path.is_file() or not receipt_path.is_file():
        raise ValueError("V6 page reference materials are unavailable")
    materials = json.loads(material_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not isinstance(materials, dict) or not isinstance(receipt, dict):
        raise ValueError("V6 page reference materials are invalid")
    if materials.get("page_number") != page_number or receipt.get("page_number") != page_number:
        raise ValueError("V6 page reference material identity is invalid")
    return materials, receipt


def _acquisition(receipt: dict[str, Any], request_id: str) -> dict[str, Any]:
    acquisitions = receipt.get("reference_acquisitions")
    if not isinstance(acquisitions, list):
        raise ValueError("V6 reference acquisition records are unavailable")
    item = next((value for value in acquisitions if isinstance(value, dict) and value.get("request_id") == request_id), None)
    if item is None:
        raise ValueError("V6 reference request is unknown")
    return item


def _append_degradation(materials: dict[str, Any], *, code: str, detail: str) -> None:
    degradation = {"code": code, "detail": detail}
    if degradation not in materials["degradations"]:
        materials["degradations"].append(degradation)


def initialize_v6_project(word: Path, logo: Path, project: Path) -> dict[str, Any]:
    word = Path(word).resolve()
    logo = Path(logo).resolve()
    project = Path(project).resolve()
    if not word.is_file() or word.suffix.lower() != ".docx":
        raise ValueError("V6 requires an existing .docx Word source")
    if not logo.is_file() or logo.suffix.lower() != ".svg":
        raise ValueError("V6 requires an existing .svg logo source")
    existing_markers = sorted(
        marker for marker in {"workflow_v6.json", *_LEGACY_PROJECT_MARKERS}
        if (project / marker).exists()
    )
    if existing_markers:
        if any(marker in _LEGACY_PROJECT_MARKERS for marker in existing_markers):
            raise ValueError(
                "Project is from an older workflow. Create a new project from the original Word document, SVG logo, and attachments."
            )
        raise FileExistsError("V6 project already exists")

    source_dir = project / "00_source"
    source_dir.mkdir(parents=True, exist_ok=True)
    locked_word = source_dir / "source.docx"
    locked_logo = source_dir / "logo.svg"
    shutil.copy2(word, locked_word)
    shutil.copy2(logo, locked_logo)

    pages_payload = extract_auto(locked_word, marker_pattern=V6_PAGE_MARKER)
    assets = extract_source_assets(locked_word, pages_payload, project / "01_source_assets")
    links_by_block = _hyperlinks_by_block(locked_word)
    state_pages = []
    for raw_page in pages_payload["pages"]:
        page_number = int(raw_page["page_number"])
        text = _page_text(raw_page)
        page_comments = raw_page.get("page_comments", [])
        title, body_render_content, title_origin = _split_page_title_body_with_origin(
            text,
            page_number,
            pagination_mode=str(pages_payload.get("pagination_mode", "")),
            page_comments=page_comments,
        )
        raw_page["fixed_page_title"] = title
        raw_page["fixed_page_title_source_block_id"] = _explicit_title_source_block_id(
            raw_page, title_origin,
        )
        references = _asset_references(page_number, assets, project=project)
        links = _links_for_page(raw_page, links_by_block)
        for link in links:
            references.append({
                "kind": "attachment_link",
                "status": "available",
                "purpose": "本页 Word 附带链接",
                "url": link,
            })
        page_source = {
            "artifact_version": "page-source-v6",
            "page_number": page_number,
            "word_original": text,
            "fixed_page_title": title,
            "body_render_content": body_render_content,
            "comments": page_comments,
            "references": references,
        }
        effective = compile_effective_page(
            page_number=page_number,
            word_text=text,
            comments=page_source["comments"],
            references=references,
            attachment_links=links,
            fixed_page_title=title,
            body_render_content=body_render_content,
        )
        attachments = [
            {
                "attachment_id": str(reference.get("asset_id") or f"attachment-{index:02d}"),
                "source_kind": reference.get("kind"),
                "original_path": reference.get("original_path"),
                "original_sha256": reference.get("original_sha256"),
                "model_input_path": reference.get("model_input_path"),
                "model_input_sha256": reference.get("model_input_sha256"),
            }
            for index, reference in enumerate(references, start=1)
            if reference.get("kind") in {"attachment", "attachment_link"} and reference.get("status") == "available"
        ]
        comment_resolution = resolve_page_comments(
            word_original=text,
            fixed_page_title=title,
            comments=page_source["comments"],
            available_attachments=attachments,
        )
        materials = new_page_materials(
            page_number=page_number,
            fixed_page_title=title,
            word_original=text,
            effective_body=comment_resolution.effective_body,
        )
        image_sources = [
            reference for reference in references
            if reference.get("kind") == "word_image"
        ]
        materials["reference_images"] = [
            reference_image_from_source(
                reference, page_number=page_number, position=index, project=project,
            )
            for index, reference in enumerate(image_sources[:16], start=1)
        ]
        attachments_by_id = {
            str(item["attachment_id"]): item for item in attachments
        }
        for requirement in comment_resolution.attachment_requirements:
            attachment = attachments_by_id.get(str(requirement["attachment_id"]))
            path = attachment.get("model_input_path") if attachment else None
            try:
                extracted = extract_attachment_material(
                    attachment=project / path if isinstance(path, str) else project / "__unavailable_attachment__",
                    requirement=requirement, project=project,
                )
            except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
                extracted = {
                    "attachment_id": requirement["attachment_id"],
                    "status": "unavailable",
                    "degradation": "Attachment unavailable; keep the page editable without its requested evidence.",
                }
            extracted = {**dict(requirement), **extracted}
            if attachment:
                extracted["source_identity"] = {
                    "original_path": attachment.get("original_path"),
                    "original_sha256": attachment.get("original_sha256"),
                }
            materials["attachment_extracts"].append(extracted)
            if extracted["status"] == "unavailable":
                _append_degradation(
                    materials, code="attachment_unavailable",
                    detail=f"Attachment {requirement['attachment_id']} could not be extracted.",
                )
        materials["image_requirements"] = [
            dict(requirement) for requirement in comment_resolution.image_requirements
        ]
        materials["chart_facts"] = [
            chart_to_facts(chart) for chart in _chart_records(assets, page_number)
        ]
        materials["degradations"].extend(
            dict(degradation) for degradation in comment_resolution.degradations
        )
        if len(image_sources) > 16:
            materials["degradations"].append({
                "code": "reference_image_limit_exceeded",
                "detail": "Only the first 16 source images are available to Image2.",
            })
        validate_page_materials(materials, confirmed=False)
        _write_json(project / "02_v6" / "page_sources" / f"page_{page_number:03d}.json", page_source)
        _write_json(project / "02_v6" / "effective_pages" / f"page_{page_number:03d}.json", effective)
        _write_json(project / "02_v6" / "page_materials" / f"page_{page_number:03d}.json", materials)
        state_pages.append(new_page(page_number, title=title))

    _write_json(project / "02_v6" / "paginated_word_source.json", pages_payload)

    state = new_project(
        word_source={"path": "00_source/source.docx", "sha256": _sha256(locked_word)},
        logo_source={"path": "00_source/logo.svg", "sha256": _sha256(locked_logo)},
        pages=state_pages,
    )
    create(project, state)
    _write_json(project / "02_v6" / "source_assets.json", assets)
    _write_json(
        project / "confirm_ui" / "recommendations.json",
        _recommendations([
            {"source_text": _page_text(page), "page_purpose": "", "asset_bindings": []}
            for page in pages_payload["pages"]
        ]),
    )
    return state

"""V6 reconstruction requests, fixed-layer finalization, and deck assembly."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
import zipfile
import copy
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping

from PIL import Image
from pptx import Presentation
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.opc.package import Part
from pptx.opc.packuri import PackURI

from fixed_frame import apply_fixed_frame, inspect_fixed_frame
from fixed_region_contract import fixed_frame_execution
from workflow_v6_contract import geometry_contract, transition_page, validate_project
from workflow_v6_state import mutation_lock, save
import workflow_v6_secure_io as secure_io


_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_RELATIONSHIP_ATTRIBUTES = {f"{{{_R}}}embed", f"{{{_R}}}id", f"{{{_R}}}link"}


def _copy_image_relationship(relationship, destination_slide) -> str:
    source_part = relationship.target_part
    if source_part.content_type != "image/svg+xml":
        _part, new_id = destination_slide.part.get_or_add_image_part(BytesIO(source_part.blob))
        return new_id
    digest = hashlib.sha256(source_part.blob).hexdigest()[:16]
    partname = PackURI(f"/ppt/media/fixed-logo-{digest}.svg")
    package = destination_slide.part.package
    target = next((part for part in package.iter_parts() if part.partname == partname), None)
    if target is None:
        target = Part(partname, "image/svg+xml", package, source_part.blob)
    return destination_slide.part.relate_to(target, RT.IMAGE)


def _copy_page_slide(source_path: Path, destination: Presentation, destination_layout, page_number: int) -> None:
    source = Presentation(source_path)
    if len(source.slides) != 1:
        raise ValueError("V6 reconstructed page package must contain one slide")
    if source.slide_width != destination.slide_width or source.slide_height != destination.slide_height:
        raise ValueError("all V6 reconstructed pages must share one slide size")
    source_slide = source.slides[0]
    destination_slide = destination.slides.add_slide(destination_layout)
    mapping: dict[str, str] = {}
    for relationship in source_slide.part.rels.values():
        if relationship.reltype == RT.SLIDE_LAYOUT:
            continue
        if relationship.reltype == RT.IMAGE:
            mapping[relationship.rId] = _copy_image_relationship(relationship, destination_slide)
        elif relationship.is_external:
            mapping[relationship.rId] = destination_slide.part.relate_to(
                relationship.target_ref, relationship.reltype, is_external=True
            )
        else:
            raise ValueError(f"unsupported V6 slide relationship: {relationship.reltype}")
    copied_content = copy.deepcopy(source_slide.element.cSld)
    copied_content.set("name", f"editable-ppt-v6-page:{page_number}")
    for node in copied_content.iter():
        for attribute, old_id in tuple(node.attrib.items()):
            if attribute in _RELATIONSHIP_ATTRIBUTES:
                if old_id not in mapping:
                    raise ValueError(f"unresolved V6 slide relationship: {old_id}")
                node.set(attribute, mapping[old_id])
    destination_slide.element.replace(destination_slide.element.cSld, copied_content)
    source_color_map = source_slide.element.clrMapOvr
    if source_color_map is not None:
        destination_color_map = destination_slide.element.clrMapOvr
        copied_color_map = copy.deepcopy(source_color_map)
        if destination_color_map is None:
            destination_slide.element.append(copied_color_map)
        else:
            destination_slide.element.replace(destination_color_map, copied_color_map)


def _sha256(path: Path) -> str:
    try:
        root = _project_for_artifact(path)
    except ValueError:
        data = path.read_bytes()
    else:
        data = secure_io.read_bytes(root, path.relative_to(root))
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    root = _project_for_artifact(path)
    value = json.loads(secure_io.read_bytes(root, path.relative_to(root)).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    root = _project_for_artifact(path)
    secure_io.atomic_write_bytes(
        root, path.relative_to(root),
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        replace=path.exists(),
    )


def _project_for_artifact(path: Path) -> Path:
    for marker in ("02_v6", "04_v6", "05_v6", "06_v6", "08_final"):
        if marker in path.parts:
            return Path(*path.parts[:path.parts.index(marker)])
    raise ValueError("reconstruction artifact is outside canonical project storage")


def _load_reconstruction_state(root: Path) -> dict[str, Any]:
    value = json.loads(
        secure_io.read_bytes(root, Path("workflow_v6.json")).decode("utf-8")
    )
    if not isinstance(value, dict):
        raise ValueError("V6 state root must be an object")
    validate_project(value)
    return value


def _update_reconstruction_page(
    root: Path, page_number: int, page: Mapping[str, Any],
) -> Path:
    with mutation_lock(root):
        state = _load_reconstruction_state(root)
        if page_number < 1 or page_number > len(state["pages"]):
            raise ValueError("V6 page number is out of range")
        if page.get("page_number") != page_number:
            raise ValueError("V6 page update identity is invalid")
        state["pages"][page_number - 1] = dict(page)
        return save(root, state)


def _fixed_frame_style(style: Mapping[str, Any]) -> Mapping[str, Any]:
    """Map the current confirmed UI contract onto the existing fixed-frame input."""
    if isinstance(style.get("fixed_frame"), Mapping) and isinstance(
        style.get("hard_constraints"), Mapping
    ):
        return style
    cjk_font = style.get("cjk_font")
    title_size = style.get("title_size_pt")
    title_color = style.get("primary_color")
    if (
        not isinstance(cjk_font, str)
        or not cjk_font.strip()
        or not isinstance(title_size, (int, float))
        or float(title_size) <= 0
        or not isinstance(title_color, str)
    ):
        raise ValueError("V6 confirmed UI contract cannot drive the fixed frame")
    return {
        **dict(style),
        "fixed_frame": {"title_color": title_color, **fixed_frame_execution()},
        "hard_constraints": {
            "title_color": title_color,
            "typography": {
                "heading": {"cjk": cjk_font.strip()},
                "type_scale_pt": {"page_title": float(title_size)},
            },
        },
    }


def build_reconstruction_request(project: Path, *, page_number: int) -> dict[str, Any]:
    secure_io.reject_reparse_chain(Path(project))
    root = Path(project).resolve()
    state = _load_reconstruction_state(root)
    if page_number < 1 or page_number > len(state["pages"]):
        raise ValueError("V6 page number is out of range")
    page = state["pages"][page_number - 1]
    if page["state"] != "accepted":
        raise ValueError("V6 page must have a selected Image2 body before reconstruction")
    page_selected = page.get("selected_candidate")
    if not isinstance(page_selected, Mapping):
        raise ValueError("V6 selected body is missing")
    accepted_receipt = root / "04_v6" / "images" / f"page_{page_number:03d}.json"
    if not accepted_receipt.is_file():
        raise ValueError("V6 accepted Image2 receipt is missing")
    receipt_bytes = secure_io.read_bytes(root, accepted_receipt.relative_to(root))
    receipt = json.loads(receipt_bytes.decode("utf-8"))
    if not isinstance(receipt, Mapping) or receipt.get("page_number") != page_number:
        raise ValueError("V6 accepted Image2 receipt identity is invalid")
    selected = receipt.get("candidate", receipt.get("selected"))
    if not isinstance(selected, Mapping):
        raise ValueError("V6 accepted Image2 receipt has no selected body")
    relative_value = selected.get("path")
    receipt_image_digest = selected.get("output_sha256", selected.get("sha256"))
    if not isinstance(relative_value, str) or not isinstance(receipt_image_digest, str):
        raise ValueError("V6 accepted Image2 receipt image authority is incomplete")
    relative = Path(relative_value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.suffix.casefold() != ".png"
        or tuple(relative.parts[:2]) != ("04_v6", "images")
    ):
        raise ValueError("V6 accepted body path is invalid")
    if any(
        page_selected.get(field) != selected.get(field)
        for field in ("attempt", "path")
    ) or (
        "operation" in page_selected
        and page_selected.get("operation") != selected.get("operation")
    ) or (
        "sha256" in page_selected
        and page_selected.get("sha256") != receipt_image_digest
    ):
        raise ValueError("V6 accepted receipt and selected body do not match")
    image_bytes = secure_io.read_bytes(root, relative)
    image_digest = hashlib.sha256(image_bytes).hexdigest()
    if image_digest != receipt_image_digest:
        raise ValueError("V6 accepted body digest does not match its receipt")
    try:
        with Image.open(BytesIO(image_bytes)) as decoded:
            image_format = decoded.format
            image_size = decoded.size
    except OSError as exc:
        raise ValueError("V6 accepted body is not a readable PNG") from exc
    if image_format != "PNG" or image_size != (1904, 896):
        raise ValueError("V6 accepted body must be a 1904x896 PNG")
    request = {
        "artifact_version": "reconstruction-request-v6",
        "workflow_contract_version": "awesome-word-ppt-workflow-v1",
        "operation": "reconstruct_editable_slide",
        "page_number": page_number,
        "page_title": page["title"],
        "accepted_receipt": {
            "path": accepted_receipt.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        },
        "source_body": {
            "path": relative.as_posix(),
            "sha256": image_digest,
            "pixels": {"width": 1904, "height": 896},
        },
        "sealed_image_edits": [],
        "geometry": geometry_contract(),
        "requirements": {
            "object_level_editable": True,
            "body_only": True,
            "fixed_layers_added_after_reconstruction": True,
            "post_reconstruction_visual_qa": False,
            "exact_reference_material_custody": False,
        },
    }
    path = root / "05_v6" / "reconstruction_requests" / f"page_{page_number:03d}.json"
    _write_json(path, request)
    return request


def finalize_reconstructed_page(
    project: Path, *, page_number: int, reconstructed_body: Path
) -> dict[str, Any]:
    secure_io.reject_reparse_chain(Path(project))
    root = Path(project).resolve()
    state = _load_reconstruction_state(root)
    reconstructed_body = Path(reconstructed_body).resolve()
    if not reconstructed_body.is_file() or reconstructed_body.suffix.lower() != ".pptx":
        raise ValueError("V6 reconstructed body must be an existing PPTX")
    opened = Presentation(reconstructed_body)
    if len(opened.slides) != 1:
        raise ValueError("V6 reconstructed body must contain exactly one slide")
    page_index = page_number - 1
    page = state["pages"][page_index]
    if page["state"] not in {"accepted", "reconstructing", "page_complete"}:
        raise ValueError("V6 page is not ready for reconstruction finalization")
    repairing_complete_page = page["state"] == "page_complete"
    if not repairing_complete_page and page["state"] != "reconstructing":
        page = transition_page(page, "reconstructing")
    output_dir = root / "06_v6" / "pages" / f"page_{page_number:03d}"
    output = output_dir / "page.pptx"
    style = state["style_confirmation"]["contract"]
    if not isinstance(style, Mapping):
        raise ValueError("V6 confirmed style contract is missing")
    style_execution = _fixed_frame_style(style)
    logo = root / state["logo_source"]["path"]
    if repairing_complete_page:
        receipt_path = output_dir / "page.json"
        if not output.is_file() or not receipt_path.is_file():
            raise ValueError("V6 existing finalized page authority is missing")
        receipt = _read_json(receipt_path)
        existing_fixed = inspect_fixed_frame(
            output,
            expected_title=page["title"],
            expected_page_number=page_number,
            style_execution=style_execution,
            logo_svg=logo,
        )
        if (
            receipt.get("artifact_version") != "final-page-v6"
            or receipt.get("page_number") != page_number
            or receipt.get("page_pptx") != output.relative_to(root).as_posix()
            or receipt.get("sha256") != _sha256(output)
            or not isinstance(receipt.get("fixed_frame"), Mapping)
            or receipt["fixed_frame"].get("passed") is not True
            or existing_fixed.get("passed") is not True
        ):
            raise ValueError("V6 existing finalized page authority is invalid")
    reconstructed_bytes = reconstructed_body.read_bytes()
    repair_target: Path | None = None
    if repairing_complete_page:
        repair_target = output_dir / f".page-repair-{uuid.uuid4().hex[:8]}.pptx"
        secure_io.atomic_write_bytes(root, repair_target.relative_to(root), reconstructed_bytes)
        finalization_output = repair_target
    elif output.is_file():
        existing_bytes = secure_io.read_bytes(root, output.relative_to(root))
        if existing_bytes != reconstructed_bytes:
            raise ValueError("V6 reconstructed page output already contains different bytes")
        finalization_output = output
    else:
        secure_io.atomic_write_bytes(root, output.relative_to(root), reconstructed_bytes)
        finalization_output = output
    try:
        apply_fixed_frame(
            finalization_output,
            page_title=page["title"],
            page_number=page_number,
            style_execution=style_execution,
            logo_svg=logo,
        )
        fixed = inspect_fixed_frame(
            finalization_output,
            expected_title=page["title"],
            expected_page_number=page_number,
            style_execution=style_execution,
            logo_svg=logo,
        )
        if fixed.get("passed") is not True:
            raise ValueError("V6 fixed-layer validation failed: " + "; ".join(fixed.get("issues", [])))
        if repairing_complete_page:
            secure_io.atomic_write_bytes(
                root,
                output.relative_to(root),
                secure_io.read_bytes(root, finalization_output.relative_to(root)),
                replace=True,
            )
    finally:
        if repair_target is not None and repair_target.is_file():
            repair_target.unlink()
    if not repairing_complete_page:
        page = transition_page(page, "page_complete")
        _update_reconstruction_page(root, page_number, page)
    report = {
        "artifact_version": "final-page-v6",
        "page_number": page_number,
        "page_pptx": output.relative_to(root).as_posix(),
        "sha256": _sha256(output),
        "fixed_frame": fixed,
        "post_reconstruction_visual_qa": False,
    }
    _write_json(output_dir / "page.json", report)
    return report


def assemble_v6_deck(project: Path) -> dict[str, Any]:
    secure_io.reject_reparse_chain(Path(project))
    root = Path(project).resolve()
    state = _load_reconstruction_state(root)
    if any(page["state"] != "page_complete" for page in state["pages"]):
        raise ValueError("every V6 page must be complete before assembly")
    pages = [
        root / "06_v6" / "pages" / f"page_{page['page_number']:03d}" / "page.pptx"
        for page in state["pages"]
    ]
    if any(not path.is_file() for path in pages):
        raise ValueError("a V6 finalized page package is missing")
    output_dir = root / "08_final"
    output = output_dir / "deck.pptx"
    temporary = output_dir / f".deck-v6-{uuid.uuid4().hex[:8]}.tmp"
    deck = Presentation(pages[0])
    layout = deck.slides[0].slide_layout
    for page_number, path in enumerate(pages[1:], start=2):
        _copy_page_slide(path, deck, layout, page_number)
    with secure_io.hold_parent(root, temporary.relative_to(root), create=True):
        deck.save(temporary)
        reopened = Presentation(temporary)
        if len(reopened.slides) != len(pages):
            raise ValueError("assembled V6 slide count is incorrect")
        if any(
            len([shape for shape in slide.shapes if shape.name.startswith("fixed-frame-")]) != 4
            for slide in reopened.slides
        ):
            raise ValueError("assembled V6 fixed-layer inventory is incorrect")
        if any(
            not any(shape.has_text_frame or shape.has_table for shape in slide.shapes)
            for slide in reopened.slides
        ):
            raise ValueError("assembled V6 slide has no editable text or table object")
        if not zipfile.is_zipfile(temporary):
            raise ValueError("assembled V6 output is not an OpenXML package")
        temporary_bytes = temporary.read_bytes()
    secure_io.atomic_write_bytes(root, output.relative_to(root), temporary_bytes, replace=output.exists())
    temporary.unlink(missing_ok=True)
    report = {
        "artifact_version": "final-assembly-v6",
        "workflow_contract_version": "awesome-word-ppt-workflow-v1",
        "status": "complete",
        "page_count": len(pages),
        "page_order": [page["page_number"] for page in state["pages"]],
        "output": output.relative_to(root).as_posix(),
        "sha256": _sha256(output),
        "mechanical_validation": {
            "openxml_package": True,
            "slide_count": True,
            "fixed_layers": True,
            "editable_objects": True,
        },
        "office_render_required": False,
        "post_reconstruction_visual_qa": False,
    }
    _write_json(output_dir / "assembly.json", report)
    return report

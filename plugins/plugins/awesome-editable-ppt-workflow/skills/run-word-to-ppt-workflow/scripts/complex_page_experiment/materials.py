"""Complete, lossless page-material custody for the page-1 experiment."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from jsonschema import Draft202012Validator

from awesome_page_materials import collect_page_materials, validate_page_materials
from awesome_attachment_render import SUPPORTED_DOCUMENTS, SUPPORTED_IMAGES
from workflow_v6_secure_io import atomic_write_bytes, read_bytes
from workflow_v6_state import load

from .workspace import ExperimentWorkspace


MaterialKind = Literal[
    "word_block",
    "word_comment",
    "word_image",
    "attachment_original",
    "attachment_render_page",
    "attachment_contact_sheet",
]

SCHEMA_VERSION = "awesome-complete-page-material-view-v1"
SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "schemas"
    / "complex_page_material_view_v1.schema.json"
)
PAGINATED_SOURCE = PurePosixPath("02_v6/paginated_word_source.json")
ASSET_MANIFEST = PurePosixPath("02_v6/source_assets.json")


@dataclass(frozen=True)
class CompletePageMaterialView:
    value: Mapping[str, object]
    multimodal_images: tuple[Path, ...]
    material_ids: tuple[str, ...]
    sha256: str


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object_digest(value: object) -> str:
    return _digest(_canonical(value))


def _load_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not canonical UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _project_path(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{label} path is invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or str(path) != value
        or ":" in path.parts[0]
    ):
        raise ValueError(f"{label} path is invalid")
    return path


def _read_receipted_file(
    project: Path,
    record: Mapping[str, object],
    label: str,
    *,
    max_bytes: int = 96 * 1024 * 1024,
) -> tuple[PurePosixPath, bytes]:
    relative = _project_path(record.get("path"), label)
    data = read_bytes(project, relative, max_bytes=max_bytes)
    if record.get("sha256") != _digest(data) or record.get("byte_size") != len(data):
        raise ValueError(f"{label} digest or byte size does not match owned bytes")
    return relative, data


def _file_receipt(project: Path, relative: PurePosixPath) -> dict[str, object]:
    data = read_bytes(project, relative)
    return {"path": str(relative), "sha256": _digest(data), "byte_size": len(data)}


def _published_materials(
    workspace: ExperimentWorkspace,
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    state = load(workspace.project_copy)
    page = state["pages"][workspace.page_number - 1]
    receipt = copy.deepcopy(page.get("material_receipt"))
    if not isinstance(receipt, dict) or receipt.get("page_number") != workspace.page_number:
        raise ValueError("selected page has no durable page-material receipt")
    relative = _project_path(receipt.get("path"), "page-material receipt")
    data = read_bytes(workspace.project_copy, relative)
    if receipt.get("digest") != _digest(data):
        raise ValueError("durable page-material receipt digest does not match owned bytes")
    published = _load_object(data, "published page materials")
    validate_page_materials(published)

    current = collect_page_materials(workspace.project_copy, workspace.page_number)
    published_attachments = published.get("attachment_inputs")
    current_attachments = current.get("attachment_inputs")
    if not isinstance(published_attachments, list) or not isinstance(current_attachments, list):
        raise ValueError("published attachment authorities are invalid")
    if len(published_attachments) != len(current_attachments):
        raise ValueError("durable receipt differs from current page authority")
    complete_current = copy.deepcopy(current)
    for index, (base, sealed) in enumerate(
        zip(current_attachments, published_attachments, strict=True)
    ):
        if not isinstance(base, dict) or not isinstance(sealed, dict):
            raise ValueError("published attachment authority is invalid")
        without_receipt = {key: value for key, value in sealed.items() if key != "render_receipt"}
        if without_receipt != base:
            raise ValueError(
                f"durable receipt differs from current authority for attachment {index + 1}"
            )
        render_receipt = sealed.get("render_receipt")
        if not isinstance(render_receipt, dict) and Path(str(base["path"])).suffix.lower() in (
            SUPPORTED_IMAGES | SUPPORTED_DOCUMENTS
        ):
            raise ValueError("durable receipt is missing a renderable attachment authority")
    complete_current["attachment_inputs"] = copy.deepcopy(published_attachments)
    if complete_current != published:
        raise ValueError("durable page-material receipt differs from current authority")
    return published, receipt, data


def _common(
    *,
    material_id: str,
    kind: MaterialKind,
    source_order: int,
    authority_path: str,
    sha256: str,
    media_type: str,
    viewable_image: bool,
) -> dict[str, object]:
    return {
        "material_id": material_id,
        "kind": kind,
        "source_order": source_order,
        "authority_path": authority_path,
        "sha256": sha256,
        "media_type": media_type,
        "viewable_image": viewable_image,
    }


def _material_records(
    project: Path, published: Mapping[str, Any]
) -> tuple[list[dict[str, object]], tuple[Path, ...], list[dict[str, str]]]:
    records: list[dict[str, object]] = []
    retained_images: list[Path] = []
    duplicates: list[dict[str, str]] = []
    retained_by_digest: dict[str, str] = {}

    def append(
        record: dict[str, object],
        image_bytes: bytes | None = None,
        *,
        derivative: bool = False,
    ) -> None:
        records.append(record)
        if image_bytes is None:
            return
        digest = _digest(image_bytes)
        if digest != record["sha256"]:
            raise ValueError(f"material {record['material_id']} digest does not match owned bytes")
        if not derivative:
            retained_images.append(
                project.joinpath(*PurePosixPath(str(record["authority_path"])).parts)
            )
            retained_by_digest.setdefault(digest, str(record["material_id"]))
            return
        prior = retained_by_digest.get(digest)
        if prior is None:
            retained_by_digest[digest] = str(record["material_id"])
            retained_images.append(project.joinpath(*PurePosixPath(str(record["authority_path"])).parts))
        else:
            duplicates.append(
                {
                    "material_id": str(record["material_id"]),
                    "duplicate_of": prior,
                    "sha256": digest,
                }
            )

    for block in published["complete_word_content"]:
        original = copy.deepcopy(block)
        block_id = str(original["source_block_id"])
        append(
            {
                **_common(
                    material_id=f"word-block:{block_id}",
                    kind="word_block",
                    source_order=int(original["source_order"]),
                    authority_path=str(PAGINATED_SOURCE),
                    sha256=_object_digest(original),
                    media_type="application/vnd.awesome.word-block+json",
                    viewable_image=False,
                ),
                "original": original,
            }
        )

    for comment in published["original_comments"]:
        original = copy.deepcopy(comment)
        comment_id = str(original["comment_id"])
        append(
            {
                **_common(
                    material_id=f"word-comment:{comment_id}",
                    kind="word_comment",
                    source_order=int(original["source_order"]),
                    authority_path=str(PAGINATED_SOURCE),
                    sha256=_object_digest(original),
                    media_type="application/vnd.awesome.word-comment+json",
                    viewable_image=False,
                ),
                "original": original,
            }
        )

    for source in published["word_images"]:
        relative, data = _read_receipted_file(project, source, "Word image")
        asset_id = str(source["asset_id"])
        original = copy.deepcopy(source)
        append(
            {
                **_common(
                    material_id=f"word-image:{asset_id}",
                    kind="word_image",
                    source_order=int(source["source_order"]),
                    authority_path=str(relative),
                    sha256=_digest(data),
                    media_type=str(source["media_type"]),
                    viewable_image=True,
                ),
                "asset_id": asset_id,
                "original_filename": str(source["original_filename"]),
                "byte_size": len(data),
                "original": original,
            },
            data,
        )

    for attachment in published["attachment_inputs"]:
        relative, data = _read_receipted_file(project, attachment, "attachment original")
        asset_id = str(attachment["asset_id"])
        attachment_id = f"attachment:{asset_id}"
        original = copy.deepcopy(attachment)
        append(
            {
                **_common(
                    material_id=attachment_id,
                    kind="attachment_original",
                    source_order=int(attachment["source_order"]),
                    authority_path=str(relative),
                    sha256=_digest(data),
                    media_type=str(attachment["media_type"]),
                    viewable_image=False,
                ),
                "asset_id": asset_id,
                "original_filename": str(attachment["original_filename"]),
                "byte_size": len(data),
                "original": original,
            }
        )
        render_receipt = attachment.get("render_receipt")
        if render_receipt is None:
            continue
        if (
            render_receipt["original_path"] != str(relative)
            or render_receipt["original_sha256"] != _digest(data)
            or render_receipt["original_byte_size"] != len(data)
        ):
            raise ValueError("attachment render receipt does not bind its original authority")
        derivatives = [
            ("attachment_render_page", item)
            for item in render_receipt["pages"]
        ] + [("attachment_contact_sheet", render_receipt["contact_sheet"])]
        for derivative_order, (kind, derivative_record) in enumerate(derivatives, start=1):
            derivative_relative, derivative_bytes = _read_receipted_file(
                project, derivative_record, "attachment render derivative"
            )
            page_number = int(derivative_record["page_number"])
            suffix = (
                f"page:{page_number:04d}"
                if kind == "attachment_render_page"
                else "contact-sheet"
            )
            append(
                {
                    **_common(
                        material_id=f"attachment-render:{asset_id}:{suffix}",
                        kind=kind,
                        source_order=derivative_order,
                        authority_path=str(derivative_relative),
                        sha256=_digest(derivative_bytes),
                        media_type="image/png",
                        viewable_image=True,
                    ),
                    "attachment_material_id": attachment_id,
                    "page_number": page_number,
                    "width": int(derivative_record["width"]),
                    "height": int(derivative_record["height"]),
                    "byte_size": len(derivative_bytes),
                    "renderer_identity": str(render_receipt["renderer_identity"]),
                    "original": copy.deepcopy(derivative_record),
                },
                derivative_bytes,
                derivative=True,
            )
    return records, tuple(retained_images), duplicates


def validate_complete_page_material_view(value: Mapping[str, object]) -> None:
    """Validate the closed schema and all self-contained custody relationships."""
    schema = _load_object(SCHEMA.read_bytes(), "complete material-view schema")
    errors = sorted(
        Draft202012Validator(schema).iter_errors(dict(value)),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        path = "/".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise ValueError(f"complete material view schema rejected {path}: {errors[0].message}")

    records = value["materials"]
    assert isinstance(records, list)
    ids = [str(record["material_id"]) for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("material IDs must be unique")
    block_records = [record for record in records if record["kind"] == "word_block"]
    comment_records = [record for record in records if record["kind"] == "word_comment"]
    word_image_records = [record for record in records if record["kind"] == "word_image"]
    attachment_records = [
        record for record in records if record["kind"] == "attachment_original"
    ]
    if [record["original"] for record in block_records] != value["complete_word_content"]:
        raise ValueError("Word content differs from its material authority records")
    if [record["original"] for record in comment_records] != value["original_comments"]:
        raise ValueError("original comments differ from their material authority records")
    for record in [*block_records, *comment_records]:
        if record["source_order"] != record["original"]["source_order"]:
            raise ValueError("Word material source order differs from its original authority")
        if record["sha256"] != _object_digest(record["original"]):
            raise ValueError(f"material {record['material_id']} digest is invalid")
        if record["authority_path"] != str(PAGINATED_SOURCE):
            raise ValueError("Word authority path is invalid")

    reconstructed_materials = {
        "page_number": value["page_number"],
        "fixed_page_title": value["fixed_page_title"],
        "complete_word_content": value["complete_word_content"],
        "original_comments": value["original_comments"],
        "word_images": [record["original"] for record in word_image_records],
        "attachment_inputs": [record["original"] for record in attachment_records],
        "visual_contract": value["visual_contract"],
        "body_frame": value["body_frame"],
    }
    try:
        validate_page_materials(reconstructed_materials)
    except Exception as exc:
        raise ValueError("complete view does not preserve valid page materials") from exc

    source_receipts = value["source_receipts"]
    if source_receipts["paginated_word_source"]["path"] != str(PAGINATED_SOURCE):
        raise ValueError("paginated Word source receipt path is invalid")
    if source_receipts["source_asset_manifest"]["path"] != str(ASSET_MANIFEST):
        raise ValueError("source asset manifest receipt path is invalid")

    for record in records:
        kind = record["kind"]
        if kind in {"word_block", "word_comment"}:
            continue
        original = record["original"]
        if record["authority_path"] != original["path"]:
            raise ValueError(f"material {record['material_id']} original path is inconsistent")
        if record["sha256"] != original["sha256"]:
            raise ValueError(f"material {record['material_id']} original digest is inconsistent")
        if record["byte_size"] != original["byte_size"]:
            raise ValueError(f"material {record['material_id']} original byte size is inconsistent")
        if kind == "word_image":
            if (
                record["asset_id"] != original["asset_id"]
                or record["original_filename"] != original["original_filename"]
                or record["media_type"] != original["media_type"]
                or record["source_order"] != original["source_order"]
            ):
                raise ValueError("Word image original authority is inconsistent")
        elif kind == "attachment_original":
            if (
                record["asset_id"] != original["asset_id"]
                or record["original_filename"] != original["original_filename"]
                or record["media_type"] != original["media_type"]
                or record["source_order"] != original["source_order"]
            ):
                raise ValueError("attachment original authority is inconsistent")
        else:
            if (
                record["page_number"] != original["page_number"]
                or record["width"] != original["width"]
                or record["height"] != original["height"]
            ):
                raise ValueError("attachment render original authority is inconsistent")

    derivative_records = [
        record
        for record in records
        if record["kind"] in {"attachment_render_page", "attachment_contact_sheet"}
    ]
    expected_derivatives: list[dict[str, object]] = []
    for attachment in attachment_records:
        attachment_id = str(attachment["material_id"])
        asset_id = str(attachment["asset_id"])
        receipt = attachment["original"].get("render_receipt")
        if not isinstance(receipt, Mapping):
            continue
        page_receipts = receipt.get("pages")
        contact_receipt = receipt.get("contact_sheet")
        if not isinstance(page_receipts, list) or not isinstance(contact_receipt, Mapping):
            raise ValueError("attachment render receipt derivatives are invalid")
        ordered = [
            ("attachment_render_page", page) for page in page_receipts
        ] + [("attachment_contact_sheet", contact_receipt)]
        for source_order, (kind, rendered) in enumerate(ordered, start=1):
            assert isinstance(rendered, Mapping)
            page_number = int(rendered["page_number"])
            suffix = (
                f"page:{page_number:04d}"
                if kind == "attachment_render_page"
                else "contact-sheet"
            )
            expected_derivatives.append(
                {
                    "material_id": f"attachment-render:{asset_id}:{suffix}",
                    "kind": kind,
                    "source_order": source_order,
                    "authority_path": rendered["path"],
                    "sha256": rendered["sha256"],
                    "media_type": "image/png",
                    "viewable_image": True,
                    "attachment_material_id": attachment_id,
                    "page_number": page_number,
                    "width": rendered["width"],
                    "height": rendered["height"],
                    "byte_size": rendered["byte_size"],
                    "renderer_identity": receipt["renderer_identity"],
                    "original": rendered,
                }
            )
    if derivative_records != expected_derivatives:
        raise ValueError(
            "attachment derivative records must exactly match render receipts in parent order"
        )

    by_id = {str(record["material_id"]): record for record in records}
    observed_retained_by_digest: dict[str, str] = {}
    expected_duplicates: list[dict[str, str]] = []
    for record in records:
        if not record["viewable_image"]:
            continue
        material_id = str(record["material_id"])
        digest = str(record["sha256"])
        if record["kind"] in {"word_image"}:
            observed_retained_by_digest.setdefault(digest, material_id)
            continue
        retained = observed_retained_by_digest.get(digest)
        if retained is None:
            observed_retained_by_digest[digest] = material_id
        else:
            expected_duplicates.append(
                {"material_id": material_id, "duplicate_of": retained, "sha256": digest}
            )
    if value["deduplicated_derivatives"] != expected_duplicates:
        raise ValueError("duplicate derivative records do not match exact digest order")

    omitted: set[str] = set()
    for duplicate in value["deduplicated_derivatives"]:
        material_id = str(duplicate["material_id"])
        retained_id = str(duplicate["duplicate_of"])
        if material_id in omitted or material_id not in by_id or retained_id not in by_id:
            raise ValueError("duplicate derivative relationship is invalid")
        duplicate_record = by_id[material_id]
        retained_record = by_id[retained_id]
        if (
            not duplicate_record["viewable_image"]
            or not retained_record["viewable_image"]
            or duplicate_record["sha256"] != retained_record["sha256"]
            or duplicate["sha256"] != duplicate_record["sha256"]
        ):
            raise ValueError("duplicate derivative digest relationship is invalid")
        omitted.add(material_id)


def _publish_identical_or_new(project: Path, relative: PurePosixPath, payload: bytes) -> Path:
    target = project.joinpath(*relative.parts)
    if os.path.lexists(target):
        existing = read_bytes(project, relative, max_bytes=len(payload) + 1)
        if existing != payload:
            raise ValueError("complete material view is already published with different bytes")
        return target
    try:
        return atomic_write_bytes(project, relative, payload, replace=False)
    except FileExistsError:
        existing = read_bytes(project, relative, max_bytes=len(payload) + 1)
        if existing != payload:
            raise ValueError("complete material view publication raced with different bytes")
        return target


def validate_published_complete_page_material_view(
    workspace: ExperimentWorkspace,
    material_view: CompletePageMaterialView,
) -> None:
    """Bind a material-view dataclass to Task 2's durable published authorities."""
    validate_complete_page_material_view(material_view.value)
    published_materials, page_receipt, _published_material_bytes = _published_materials(
        workspace
    )
    if material_view.value["source_receipts"]["page_materials"] != page_receipt:
        raise ValueError("complete material view page-material receipt is not authoritative")
    reconstructed = {
        "page_number": material_view.value["page_number"],
        "fixed_page_title": material_view.value["fixed_page_title"],
        "complete_word_content": material_view.value["complete_word_content"],
        "original_comments": material_view.value["original_comments"],
        "word_images": [
            record["original"]
            for record in material_view.value["materials"]
            if record["kind"] == "word_image"
        ],
        "attachment_inputs": [
            record["original"]
            for record in material_view.value["materials"]
            if record["kind"] == "attachment_original"
        ],
        "visual_contract": material_view.value["visual_contract"],
        "body_frame": material_view.value["body_frame"],
    }
    if reconstructed != published_materials:
        raise ValueError("complete material view differs from durable page-material authority")

    for record in material_view.value["materials"]:
        if record["kind"] in {"attachment_render_page", "attachment_contact_sheet"}:
            _read_receipted_file(
                workspace.project_copy,
                record["original"],
                "attachment render derivative authority",
            )

    experiment_id = workspace.experiment_id
    if (
        not experiment_id
        or "/" in experiment_id
        or "\\" in experiment_id
        or experiment_id in {".", ".."}
    ):
        raise ValueError("experiment ID is not a safe published-view component")
    relative = PurePosixPath(
        "02_v6", "experiments", experiment_id, "complete_page_material_view.json"
    )
    expected = _canonical(material_view.value)
    try:
        actual = read_bytes(workspace.project_copy, relative)
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError("published complete material view is missing or invalid") from exc
    if actual != expected:
        raise ValueError("published complete material view is not exact canonical authority")
    if _digest(actual) != material_view.sha256:
        raise ValueError("published complete material view digest is invalid")


def build_complete_page_material_view(
    workspace: ExperimentWorkspace,
) -> CompletePageMaterialView:
    """Load one selected page's original authorities and existing renders."""
    project = workspace.project_copy.resolve(strict=True)
    workflow = load(project)
    page_numbers = {
        page.get("page_number")
        for page in workflow.get("pages", [])
        if isinstance(page, Mapping)
    }
    if workspace.page_number not in page_numbers:
        raise ValueError("complete material view page is not present in the current project")
    if (
        not workspace.experiment_id
        or "/" in workspace.experiment_id
        or "\\" in workspace.experiment_id
        or workspace.experiment_id in {".", ".."}
    ):
        raise ValueError("experiment ID is not a safe output component")
    published, page_receipt, _published_bytes = _published_materials(workspace)
    records, multimodal_images, duplicates = _material_records(project, published)
    value: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": workspace.experiment_id,
        "page_number": workspace.page_number,
        "fixed_page_title": published["fixed_page_title"],
        "complete_word_content": copy.deepcopy(published["complete_word_content"]),
        "original_comments": copy.deepcopy(published["original_comments"]),
        "visual_contract": copy.deepcopy(published["visual_contract"]),
        "body_frame": copy.deepcopy(published["body_frame"]),
        "materials": records,
        "deduplicated_derivatives": duplicates,
        "source_receipts": {
            "page_materials": page_receipt,
            "paginated_word_source": _file_receipt(project, PAGINATED_SOURCE),
            "source_asset_manifest": _file_receipt(project, ASSET_MANIFEST),
        },
    }
    validate_complete_page_material_view(value)
    payload = _canonical(value)
    relative = PurePosixPath(
        "02_v6",
        "experiments",
        workspace.experiment_id,
        "complete_page_material_view.json",
    )
    _publish_identical_or_new(project, relative, payload)
    return CompletePageMaterialView(
        value=value,
        multimodal_images=multimodal_images,
        material_ids=tuple(str(record["material_id"]) for record in records),
        sha256=_digest(payload),
    )

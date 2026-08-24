"""Canonical contracts for the adaptive V6 Word-to-PPT workflow."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Any

from fixed_region_contract import BODY_BOX_CM, CONTRACT_VERSION, SLIDE_SIZE_CM
from director_taskbook import taskbook_digest, validate_taskbook
from director_templates import DIRECTOR_TEMPLATES


PLUGIN_ID = "awesome-editable-ppt-workflow"
PLUGIN_VERSION = "1.2.1"
WORKFLOW_VERSION = "awesome-word-ppt-workflow-v1"
PROJECT_ARTIFACT_VERSION = "awesome-word-ppt-project-v1"
PAGE_ARTIFACT_VERSION = "word-ppt-page-v6"
IMAGE_POLICY = "generate-without-refs-edit-with-confirmed-refs"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

PAGE_STATES = frozenset({
    "prepared",
    "generating",
    "qa_review",
    "accepted",
    "reconstructing",
    "page_complete",
    "technical_failed",
})
MATERIAL_STATES = frozenset({"available", "unavailable", "not_requested"})

_ALLOWED_TRANSITIONS = {
    "prepared": {"generating", "technical_failed"},
    "generating": {"qa_review", "technical_failed"},
    "qa_review": {
        "generating",
        "accepted",
        "technical_failed",
    },
    "accepted": {"reconstructing", "technical_failed"},
    "reconstructing": {"page_complete", "technical_failed"},
    "page_complete": set(),
    "technical_failed": {"generating", "reconstructing"},
}


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def request_identity(
    *,
    revision_digest: str,
    prompt_sha256: str,
    operation: str,
    quality: str,
    input_sha256s: Sequence[str],
    plugin_id: str = PLUGIN_ID,
    plugin_version: str = PLUGIN_VERSION,
    workflow_contract: str = WORKFLOW_VERSION,
    page_material_digest: str = "",
    prompt_output_sha256: str = "",
    selected_reference_ids: Sequence[str] = (),
    model: str = "gpt-image-2",
    size: str = "1904x896",
    source_identity: str = "",
) -> str:
    """Return the local, path-neutral identity of one adaptive Image2 request."""
    for name, value in (
        ("revision_digest", revision_digest),
        ("prompt_sha256", prompt_sha256),
    ):
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    if operation not in {"generate", "edit"}:
        raise ValueError("operation must be generate or edit")
    if quality not in {"medium", "high"}:
        raise ValueError("quality must be medium or high")
    if isinstance(input_sha256s, (str, bytes)) or not isinstance(input_sha256s, Sequence):
        raise ValueError("input_sha256s must be an ordered digest sequence")
    inputs = list(input_sha256s)
    if any(not isinstance(value, str) or _SHA256.fullmatch(value) is None for value in inputs):
        raise ValueError("input_sha256s contains an invalid digest")
    for name, value in (("plugin_id", plugin_id), ("plugin_version", plugin_version),
                        ("workflow_contract", workflow_contract), ("model", model), ("size", size)):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be nonempty")
    for name, value in (("page_material_digest", page_material_digest),
                        ("prompt_output_sha256", prompt_output_sha256),
                        ("source_identity", source_identity)):
        if value and _SHA256.fullmatch(value) is None:
            raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    refs = list(selected_reference_ids)
    if any(not isinstance(value, str) or not value for value in refs):
        raise ValueError("selected_reference_ids contains an invalid ID")
    return canonical_sha256({
        "input_sha256s": inputs,
        "model": model,
        "operation": operation,
        "page_material_digest": page_material_digest,
        "plugin_id": plugin_id,
        "plugin_version": plugin_version,
        "prompt_sha256": prompt_sha256,
        "prompt_output_sha256": prompt_output_sha256,
        "quality": quality,
        "revision_digest": revision_digest,
        "selected_reference_ids": refs,
        "size": size,
        "source_identity": source_identity,
        "workflow_contract": workflow_contract,
    })


def geometry_contract() -> dict[str, Any]:
    return {
        "version": CONTRACT_VERSION,
        "slide_cm": dict(SLIDE_SIZE_CM),
        "body_cm": dict(BODY_BOX_CM),
        "slide_aspect": "16:9",
        "body_aspect": "17:8",
        "body_pixels": {"width": 1904, "height": 896},
        "fixed_layers": ["title", "logo", "footer", "page_number"],
        "image2_exclusions": ["title", "fixed_logo", "footer", "page_number"],
    }


def new_page(page_number: int, *, title: str) -> dict[str, Any]:
    if type(page_number) is not int or page_number < 1:
        raise ValueError("page_number must be a positive integer")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("page title is required")
    return {
        "artifact_version": PAGE_ARTIFACT_VERSION,
        "page_number": page_number,
        "title": title.strip(),
        "state": "prepared",
        "material_state": "not_requested",
        "material_receipt": None,
        "first_candidate": None,
        "selected_candidate": None,
        "qa_attempts": 0,
        "degraded_reasons": [],
        "technical_failure": None,
    }


def new_project(
    *,
    word_source: Mapping[str, Any],
    logo_source: Mapping[str, Any],
    pages: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    project = {
        "artifact_version": PROJECT_ARTIFACT_VERSION,
        "plugin_id": PLUGIN_ID,
        "plugin_version": PLUGIN_VERSION,
        "workflow_contract": WORKFLOW_VERSION,
        "workflow_contract_version": WORKFLOW_VERSION,
        "image_policy": IMAGE_POLICY,
        "geometry": geometry_contract(),
        "word_source": copy.deepcopy(dict(word_source)),
        "logo_source": copy.deepcopy(dict(logo_source)),
        "style_confirmation": {"status": "pending", "contract": None},
        "director_confirmation": None,
        "confirmed_ui_revision": None,
        "confirmed_ui_digest": None,
        "page_materials_status": "pre_confirmation",
        "pages": [copy.deepcopy(dict(page)) for page in pages],
    }
    project["source_identity"] = canonical_sha256({
        "word_source": project["word_source"],
        "logo_source": project["logo_source"],
    })
    validate_project(project)
    return project


def validate_page(page: Mapping[str, Any]) -> None:
    required = {
        "artifact_version",
        "page_number",
        "title",
        "state",
        "material_state",
        "material_receipt",
        "first_candidate",
        "selected_candidate",
        "qa_attempts",
        "degraded_reasons",
        "technical_failure",
    }
    if set(page) != required:
        raise ValueError("V6 page fields are invalid")
    if page["artifact_version"] != PAGE_ARTIFACT_VERSION:
        raise ValueError("V6 page artifact version is invalid")
    if type(page["page_number"]) is not int or page["page_number"] < 1:
        raise ValueError("V6 page number is invalid")
    if not isinstance(page["title"], str) or not page["title"].strip():
        raise ValueError("V6 page title is invalid")
    if page["state"] not in PAGE_STATES:
        raise ValueError("V6 page state is invalid")
    if page["material_state"] not in MATERIAL_STATES:
        raise ValueError("V6 material state is invalid")
    receipt = page["material_receipt"]
    if receipt is not None:
        if not isinstance(receipt, Mapping) or set(receipt) != {"schema_version", "page_number", "path", "digest"}:
            raise ValueError("V6 material receipt is invalid")
        if receipt["schema_version"] != "awesome-page-materials-v1" or receipt["page_number"] != page["page_number"]:
            raise ValueError("V6 material receipt identity is invalid")
        expected_path = f"02_v6/awesome_page_materials/page_{page['page_number']:03d}.json"
        if receipt["path"] != expected_path or not isinstance(receipt["digest"], str) or not _SHA256.fullmatch(receipt["digest"]):
            raise ValueError("V6 material receipt is invalid")
    if page["material_state"] == "available" and receipt is None:
        raise ValueError("available V6 materials require a receipt")
    if type(page["qa_attempts"]) is not int or page["qa_attempts"] < 0:
        raise ValueError("V6 QA attempt count is invalid")
    if not isinstance(page["degraded_reasons"], list) or any(
        not isinstance(item, str) or not item for item in page["degraded_reasons"]
    ):
        raise ValueError("V6 degraded reasons are invalid")
    for field in ("first_candidate", "selected_candidate"):
        if page[field] is not None and not isinstance(page[field], Mapping):
            raise ValueError(f"V6 {field} is invalid")
    if page["technical_failure"] is not None and not isinstance(
        page["technical_failure"], Mapping
    ):
        raise ValueError("V6 technical failure is invalid")


def validate_project(project: Mapping[str, Any]) -> None:
    required = {
        "artifact_version",
        "plugin_id",
        "plugin_version",
        "workflow_contract",
        "workflow_contract_version",
        "image_policy",
        "geometry",
        "word_source",
        "logo_source",
        "source_identity",
        "style_confirmation",
        "director_confirmation",
        "confirmed_ui_revision",
        "confirmed_ui_digest",
        "page_materials_status",
        "pages",
    }
    if set(project) != required:
        identity_fields = {"plugin_id", "plugin_version", "workflow_contract"}
        if not identity_fields.issubset(project):
            raise ValueError("Project is from an older workflow. Create a new project from the original Word document, SVG logo, and attachments.")
        raise ValueError("V6 project fields are invalid")
    if project["artifact_version"] != PROJECT_ARTIFACT_VERSION:
        raise ValueError("Project is from an older workflow. Create a new project from the original Word document, SVG logo, and attachments.")
    if (
        project["plugin_id"] != PLUGIN_ID
        or project["plugin_version"] != PLUGIN_VERSION
        or project["workflow_contract"] != WORKFLOW_VERSION
    ):
        raise ValueError("Project is from an older workflow. Create a new project from the original Word document, SVG logo, and attachments.")
    if project["workflow_contract_version"] != WORKFLOW_VERSION:
        raise ValueError("V6 workflow contract version is invalid")
    if project["image_policy"] != IMAGE_POLICY:
        raise ValueError(
            "V6 image policy must generate without confirmed references and edit with them"
        )
    if project["geometry"] != geometry_contract():
        raise ValueError("V6 fixed geometry contract changed")
    if not isinstance(project["word_source"], Mapping) or not isinstance(
        project["logo_source"], Mapping
    ):
        raise ValueError("V6 source records are invalid")
    expected_identity = canonical_sha256({
        "word_source": project["word_source"],
        "logo_source": project["logo_source"],
    })
    if project["source_identity"] != expected_identity:
        raise ValueError("V6 source identity is invalid")
    style = project["style_confirmation"]
    if not isinstance(style, Mapping) or set(style) != {"status", "contract"}:
        raise ValueError("V6 style confirmation is invalid")
    if style["status"] not in {"pending", "confirmed"}:
        raise ValueError("V6 style status is invalid")
    if style["status"] == "confirmed" and not isinstance(style["contract"], Mapping):
        raise ValueError("confirmed V6 style requires a contract")
    director = project["director_confirmation"]
    if director is not None:
        if not isinstance(director, Mapping) or set(director) != {
            "template_id", "template_version", "taskbook", "taskbook_digest",
        }:
            raise ValueError("V6 director confirmation is invalid")
        if director["template_id"] not in {item["id"] for item in DIRECTOR_TEMPLATES}:
            raise ValueError("V6 director template is invalid")
        if director["template_version"] != "1.0":
            raise ValueError("V6 director template version is invalid")
        try:
            taskbook = validate_taskbook(director["taskbook"])
        except ValueError as exc:
            raise ValueError("V6 director taskbook is invalid") from exc
        if director["taskbook_digest"] != taskbook_digest(taskbook):
            raise ValueError("V6 director taskbook digest is invalid")
    revision = project["confirmed_ui_revision"]
    digest = project["confirmed_ui_digest"]
    materials_status = project["page_materials_status"]
    if materials_status not in {"pre_confirmation", "pending", "confirmed"}:
        raise ValueError("V6 page materials status is invalid")
    if materials_status == "pre_confirmation" and (revision is not None or digest is not None):
        raise ValueError("unconfirmed V6 materials cannot carry a confirmed UI revision")
    if materials_status == "pending":
        if style["status"] != "confirmed":
            raise ValueError("pending V6 page materials require a confirmed visual contract")
        if type(revision) is not int or revision < 1:
            raise ValueError("pending V6 page materials require a positive UI revision")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError("pending V6 page materials require a UI digest")
    if materials_status == "confirmed":
        if type(revision) is not int or revision < 1:
            raise ValueError("confirmed V6 materials require a positive UI revision")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError("confirmed V6 materials require a UI digest")
    pages = project["pages"]
    if not isinstance(pages, list) or not pages:
        raise ValueError("V6 project requires at least one page")
    numbers = []
    for page in pages:
        if not isinstance(page, Mapping):
            raise ValueError("V6 page record is invalid")
        validate_page(page)
        numbers.append(page["page_number"])
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError("V6 page order must be contiguous and start at one")


def validate_material_receipts(project_root: Path, project: Mapping[str, Any]) -> None:
    if project.get("page_materials_status") != "confirmed":
        return
    root = Path(project_root).resolve(strict=True)
    for page in project["pages"]:
        receipt = page["material_receipt"]
        if not isinstance(receipt, Mapping):
            raise ValueError("confirmed V6 materials require durable page receipts")
        path = root / receipt["path"]
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        from workflow_v6_media import _open_project_root_handle, _verify_handle_within
        root_handle = _open_project_root_handle(root)
        try:
            try:
                descriptor = os.open(path, flags)
            except FileNotFoundError as exc:
                raise ValueError("confirmed V6 material receipt file is missing") from exc
            with os.fdopen(descriptor, "rb") as handle:
                _verify_handle_within(root_handle, handle.fileno())
                before = os.fstat(handle.fileno())
                data = handle.read()
                try:
                    metadata = path.lstat()
                except FileNotFoundError as exc:
                    raise ValueError("confirmed V6 material receipt file changed during verification") from exc
                reparse = bool(
                    getattr(metadata, "st_file_attributes", 0)
                    & getattr(metadata, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                )
                if path.is_symlink() or reparse or not os.path.samestat(before, metadata):
                    raise ValueError("confirmed V6 material receipt file changed during verification")
        finally:
            os.close(root_handle)
        if hashlib.sha256(data).hexdigest() != receipt["digest"]:
            raise ValueError("confirmed V6 material receipt digest is invalid")
        value = json.loads(data.decode("utf-8"))
        if value.get("page_number") != page["page_number"]:
            raise ValueError("confirmed V6 material receipt page identity is invalid")


def transition_page(page: Mapping[str, Any], target: str) -> dict[str, Any]:
    validate_page(page)
    current = str(page["state"])
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"invalid V6 page transition: {current} -> {target}")
    updated = copy.deepcopy(dict(page))
    updated["state"] = target
    if target != "technical_failed":
        updated["technical_failure"] = None
    validate_page(updated)
    return updated

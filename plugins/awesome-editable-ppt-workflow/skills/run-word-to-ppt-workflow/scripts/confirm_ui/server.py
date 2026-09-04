#!/usr/bin/env python3
"""Serve the embedded visual-and-composition confirmation session."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
import urllib.error
import urllib.request
import webbrowser
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory
from jsonschema import Draft202012Validator


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
WORKFLOW_SCRIPT_DIR = SCRIPT_DIR.parent
if str(WORKFLOW_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(WORKFLOW_SCRIPT_DIR))

from fixed_region_contract import (  # noqa: E402
    BODY_BOX_CM,
    BODY_REMAINDER_CM,
    CONTRACT_VERSION,
    GEOMETRY_TOLERANCE_RATIO,
    SLIDE_SIZE_CM,
)
from workflow_v6_composition import freeze_composition, validate_composition  # noqa: E402
from workflow_v6_contract import validate_project as validate_v6_project  # noqa: E402
from workflow_v6_state import (  # noqa: E402
    load as load_v6_state,
    mutation_lock,
)
from director_taskbook import taskbook_digest, validate_taskbook  # noqa: E402
from director_templates import public_templates, taskbook_for_template  # noqa: E402


LOGGER = logging.getLogger("word_to_editable_ppt.confirm_ui")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5050
LOCK_NAME = ".confirm_ui.lock"
START_LOCK_NAME = ".confirm_ui.start.lock"
CONFIRM_DIR = "confirm_ui"
RECOMMENDATIONS = "recommendations.json"
RESULT = "result.json"
SESSION = "session.json"
NEW_PROJECT_REQUIRED = (
    "Project is incompatible with awesome-editable-ppt-workflow. "
    "Create a new project from the original Word document, SVG logo, and attachments."
)
_START_THREAD_LOCK = threading.Lock()
_LOCK_MUTATION_THREAD_LOCK = threading.RLock()
_TRANSACTION_DIR = Path("02_v6") / ".confirm_composition_transaction"
_TRANSACTION_PREPARING_DIR = Path("02_v6") / ".confirm_composition_transaction.preparing"
_PAGE_AUTHORITY_DIRECTORIES = (
    "page_sources",
    "effective_pages",
    "page_materials",
    "reference_materials",
)
_REPARSE_POINT = 0x400

REQUIRED_VISUAL_FIELDS = (
    "primary_color",
    "secondary_color",
    "background_color",
    "cjk_font",
    "latin_font",
    "title_size_pt",
    "body_size_pt",
    "caption_size_pt",
)
OPTIONAL_VISUAL_FIELDS = ("highlight_color",)
VISUAL_FIELDS = (*REQUIRED_VISUAL_FIELDS, *OPTIONAL_VISUAL_FIELDS)
CONFIRMATION_FIELDS = ("submission_id", "revision", *REQUIRED_VISUAL_FIELDS)
DIRECTOR_SUBMISSION_FIELDS = ("selected_director_template_id", "director_taskbook")
STYLE_SCHEMA_PATH = SCRIPT_DIR.parents[1] / "schemas" / "style_confirmation.schema.json"
TEMPLATE_DEFAULTS = tuple(
    {
        **template,
        "director_taskbook": taskbook_for_template(template["id"]),
    }
    for template in public_templates()
)

STAGE1_EDITABLE = (
    "audience",
    "core_message",
    "delivery_context",
    "content_divergence",
    "canvas",
)
STAGE1_FACTS = (
    "page_count",
    "pagination_mode",
    "one_page_to_one_slide",
)
STAGE2_FIELDS = (
    "direction",
    "delivery_purpose",
    "mode",
    "visual_style",
    "color",
    "icons",
    "typography",
    "image_rendering",
    "style_axes",
    "information_density",
    "additional_requirements",
)
STAGE3_FIELDS = (
    "formula_policy",
    "generation_mode",
    "refine_spec",
    "image_quality",
    "max_concurrency",
    "automatic_repair_budget",
    "editable_output",
    "start_generation",
)
ONE_SCREEN_FIELDS = (
    "direction",
    "template_selection",
    "canvas",
    "visual_style",
    "color",
    "icons",
    "typography",
    "image_rendering",
    "style_axes",
    "layout_preferences",
    "information_density",
    "regional_style",
    "background_system",
    "image_role",
    "evidence_strength",
    "composition_tendency",
    "brand_device",
    "production_profile",
    "additional_requirements",
)
PRODUCTION_PROFILES = {
    "quality": {"image_quality": "high", "max_concurrency": 2, "automatic_repair_budget": 2},
    "balanced": {"image_quality": "high", "max_concurrency": 2, "automatic_repair_budget": 1},
    "speed": {"image_quality": "medium", "max_concurrency": 3, "automatic_repair_budget": 1},
}
ONE_SCREEN_PRODUCTION_BASE = {
    "formula_policy": "mixed", "generation_mode": "continuous", "refine_spec": False,
    "editable_output": True, "start_generation": True,
}
FORMULA_POLICIES = {"mixed", "editable", "rendered"}
GENERATION_MODES = {"continuous", "split"}
IMAGE_QUALITIES = {"auto", "low", "medium", "high"}
INFORMATION_DENSITIES = {"low", "balanced", "high"}
IMAGE_USAGE_POLICIES = {"content-driven", "visual-preference", "source-only"}
LAYOUT_PREFERENCES = {
    "auto",
    "editorial",
    "conclusion-first",
    "split",
    "table",
    "matrix",
    "data-led",
    "timeline",
    "modular",
}
CANVAS_ID = "ppt169"
TEMPLATE_IDS = {"policy-project-brief", "brand-narrative-business", "evidence-investment-bp"}
BP_SUBSTYLE_IDS = {"dark-tech", "white-rd"}
BACKGROUND_SYSTEMS = {"light", "dark", "mixed", "light-with-dark-highlights"}
IMAGE_ROLES = {"text-structure", "evidence", "technical-evidence", "product-evidence", "narrative", "balanced"}
IMAGE_PROPORTIONS = {"low", "medium-low", "medium", "high"}
EVIDENCE_STRENGTHS = {"business", "data-case", "strict"}
COMPOSITION_TENDENCIES = {"auto", "formal-consulting", "brand-editorial", "technical-rd", "product-launch"}
BRAND_DEVICES = {"none", "light", "medium", "strong"}
PALETTE_ROLES = (
    "background",
    "secondary_bg",
    "primary",
    "accent",
    "secondary_accent",
    "body_text",
)
HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")
LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# These inputs belong to workflows that this Word-only plugin deliberately does
# not expose. They are removed at both recommendation and submission boundaries.
OMITTED_KEYS = {
    "communication_intent",
    "audience_outcome",
    "artifact_afterlife",
    "template_application",
    "template_reuse_scope",
    "template_adherence",
    "external_style_upload",
    "style_upload",
    "image_usage",
    "image_source",
    "image_sources",
    "image_ai_path",
    "provided_images",
    "web_images",
}

REMOVED_ONE_SCREEN_FIELDS = {
    "frame_geometry",
    "frame_preset",
    "body_bounds",
    "title_bounds",
    "logo_bounds",
    "footer_y",
    "preview_image",
    "preview_screenshot",
    "visual_reference_image",
    "approved_visual_reference",
}


def _fixed_region_view() -> dict[str, Any]:
    """Return the authoritative, read-only frame facts used by the browser."""
    return {
        "contract_version": CONTRACT_VERSION,
        "read_only": True,
        "canvas": CANVAS_ID,
        "slide_cm": dict(SLIDE_SIZE_CM),
        "body_cm": dict(BODY_BOX_CM),
        "remaining_cm": dict(BODY_REMAINDER_CM),
        "source_pixels": "dynamic",
        "tolerance_percent": GEOMETRY_TOLERANCE_RATIO * 100,
        "deterministic_layers": ["page_title", "svg_logo", "footer", "page_number"],
        "ui_preview_used_for_generation": False,
    }


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _json_bytes(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _is_reparse(path: Path) -> bool:
    status = path.lstat()
    return path.is_symlink() or bool(
        getattr(status, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _literal_project_directory(
    project: Path, relative: Path, *, required: bool
) -> Path | None:
    root = Path(project).resolve(strict=True)
    current = root
    for part in relative.parts:
        current = current / part
        if not os.path.lexists(current):
            if required:
                raise ValueError(f"required literal project directory is missing: {relative}")
            return None
        if _is_reparse(current) or not current.is_dir() or current.resolve(strict=True) != current.absolute():
            raise ValueError(f"page authority must use a literal project directory, not a reparse point: {relative}")
    return current


def _literal_existing_file(path: Path) -> Path:
    if not os.path.lexists(path) or _is_reparse(path) or not path.is_file():
        raise ValueError(f"transaction target must be a literal regular file: {path.name}")
    if path.resolve(strict=True) != path.absolute() or path.lstat().st_nlink != 1:
        raise ValueError(f"transaction target must be an unaliased project file: {path.name}")
    return path


def _transaction_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _transaction_replace(source: Path, target: Path) -> None:
    os.replace(source, target)


def _transaction_directory(project: Path, *, required: bool) -> Path | None:
    directory = _literal_project_directory(project, _TRANSACTION_DIR, required=required)
    if directory is not None and directory.resolve(strict=True) != directory.absolute():
        raise ValueError("confirmation transaction directory must be project-local")
    return directory


def _preparing_transaction_directory(project: Path, *, required: bool) -> Path | None:
    directory = _literal_project_directory(
        project, _TRANSACTION_PREPARING_DIR, required=required
    )
    if directory is not None and directory.resolve(strict=True) != directory.absolute():
        raise ValueError("confirmation preparing directory must be project-local")
    return directory


def _remove_transaction(project: Path) -> None:
    directory = _transaction_directory(project, required=False)
    if directory is not None:
        shutil.rmtree(directory)


def _remove_preparing_transaction(project: Path) -> None:
    directory = _preparing_transaction_directory(project, required=False)
    if directory is not None:
        shutil.rmtree(directory)


def _transaction_target(project: Path, relative: str) -> Path:
    allowed = {
        "workflow_v6.json",
        "02_v6/page_composition.json",
        "02_v6/paginated_word_source.json",
        "confirm_ui/result.json",
    }
    match = re.fullmatch(
        r"02_v6/(page_sources|effective_pages|page_materials|reference_materials)/page_([0-9]{3})\.json",
        relative,
    )
    if relative not in allowed and match is None:
        raise ValueError("confirmation recovery manifest contains an unauthorized target")
    if match is not None:
        _literal_project_directory(
            project, Path("02_v6") / match.group(1), required=True
        )
    elif relative == "02_v6/page_composition.json":
        _literal_project_directory(project, Path("02_v6"), required=True)
    elif relative == "confirm_ui/result.json":
        _literal_project_directory(project, Path("confirm_ui"), required=True)
    path = Path(project).resolve(strict=True) / Path(relative)
    if os.path.lexists(path):
        _literal_existing_file(path)
    return path


def _read_transaction_manifest(project: Path) -> dict[str, Any]:
    directory = _transaction_directory(project, required=True)
    assert directory is not None
    manifest = _read_json(_literal_existing_file(directory / "manifest.json"))
    if set(manifest) != {"version", "phase", "targets"} or manifest["version"] != 1:
        raise ValueError("confirmation recovery manifest is invalid")
    if manifest["phase"] not in {"preparing", "prepared", "committing", "committed"}:
        raise ValueError("confirmation recovery phase is invalid")
    if not isinstance(manifest["targets"], list):
        raise ValueError("confirmation recovery target list is invalid")
    for item in manifest["targets"]:
        if not isinstance(item, dict) or set(item) != {"relative", "original", "staged"}:
            raise ValueError("confirmation recovery target is invalid")
        _transaction_target(project, item["relative"])
        for field in ("original", "staged"):
            value = item[field]
            if value is not None and not re.fullmatch(rf"{field}/[0-9]{{4}}\.bin", value):
                raise ValueError("confirmation recovery payload path is invalid")
    return manifest


def _restore_transaction(project: Path, manifest: dict[str, Any]) -> None:
    directory = _transaction_directory(project, required=True)
    assert directory is not None
    for item in manifest["targets"]:
        target = _transaction_target(project, item["relative"])
        original = item["original"]
        if original is None:
            if os.path.lexists(target):
                _literal_existing_file(target).unlink()
            continue
        backup = _literal_existing_file(directory / Path(original))
        _transaction_write_bytes(target, backup.read_bytes())
    _remove_transaction(project)


def _recover_composition_transaction(project: Path) -> bool:
    directory = _transaction_directory(project, required=False)
    preparing = _preparing_transaction_directory(project, required=False)
    if directory is None:
        if preparing is not None:
            _remove_preparing_transaction(project)
        return False
    if preparing is not None:
        raise ValueError("ambiguous confirmation transaction directories")
    manifest = _read_transaction_manifest(project)
    if manifest["phase"] == "preparing":
        _remove_transaction(project)
        return False
    if manifest["phase"] == "committed":
        _remove_transaction(project)
        return True
    else:
        _restore_transaction(project, manifest)
        return False


def _clean(value: Any) -> Any:
    """Recursively remove capabilities outside this plugin's workflow."""
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items() if key not in OMITTED_KEYS}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def _visual_contract_validator() -> Draft202012Validator:
    return Draft202012Validator(_read_json(STYLE_SCHEMA_PATH))


def _contract_error(payload: dict[str, Any]) -> str | None:
    errors = sorted(
        _visual_contract_validator().iter_errors(payload),
        key=lambda error: (list(error.path), error.message),
    )
    return errors[0].message if errors else None


def _composition_view(project: Path, composition: dict[str, Any]) -> dict[str, Any]:
    validate_composition(composition)
    source_directory = _literal_project_directory(
        project, Path("02_v6") / "page_sources", required=True
    )
    assert source_directory is not None
    pages = []
    for page in composition["pages"]:
        number = page["output_page_number"]
        source = _read_json(_literal_existing_file(
            source_directory / f"page_{number:03d}.json"
        ))
        if source.get("page_number") != number or not isinstance(source.get("word_original"), str):
            raise ValueError("composition source preview is not backed by a valid page source")
        pages.append({**copy.deepcopy(page), "source_preview": source["word_original"]})
    return {
        "page_count": composition["page_count"],
        "warnings": copy.deepcopy(composition["warnings"]),
        "pages": pages,
    }


def _template_recommendations(project: Path) -> dict[str, Any]:
    recommendation_path = project / CONFIRM_DIR / RECOMMENDATIONS
    source = _read_json(recommendation_path) if recommendation_path.is_file() else {}
    requested = source.get("recommended_template_id")
    if requested is None and isinstance(source.get("recommend"), dict):
        direction = source["recommend"].get("direction")
        if type(direction) is int and 0 <= direction < len(TEMPLATE_DEFAULTS):
            requested = TEMPLATE_DEFAULTS[direction]["id"]
    known = {template["id"] for template in TEMPLATE_DEFAULTS}
    recommended = requested if requested in known else TEMPLATE_DEFAULTS[0]["id"]
    result_path = project / CONFIRM_DIR / RESULT
    revision = 0
    if result_path.is_file():
        existing = _read_json(result_path)
        if type(existing.get("revision")) is int:
            revision = existing["revision"]
    result = {
        "step_count": 3,
        "recommended_template_id": recommended,
        "revision": revision,
        "templates": _clean(list(TEMPLATE_DEFAULTS)),
        "recommendation_reason": _clean(source.get(
            "recommendation_reason", f"默认推荐“{TEMPLATE_DEFAULTS[0]['name']}”。"
        )),
        "recommendation_confidence": _clean(source.get("recommendation_confidence", "low")),
        "director_taskbook": _clean(source.get(
            "director_taskbook", taskbook_for_template(recommended)
        )),
    }
    return result


def _director_confirmation(project: Path, payload: dict[str, Any]) -> dict[str, Any]:
    source_path = project / CONFIRM_DIR / RECOMMENDATIONS
    source = _read_json(source_path) if source_path.is_file() else {}
    template_id = payload.get("selected_director_template_id", source.get("recommended_template_id"))
    known = {template["id"] for template in TEMPLATE_DEFAULTS}
    if template_id not in known:
        template_id = TEMPLATE_DEFAULTS[0]["id"]
    taskbook = payload.get("director_taskbook", source.get("director_taskbook"))
    if taskbook is None:
        taskbook = taskbook_for_template(template_id)
    taskbook = validate_taskbook(taskbook)
    return {
        "template_id": template_id,
        "template_version": "1.0",
        "taskbook": taskbook,
        "taskbook_digest": taskbook_digest(taskbook),
    }


def _save_visual_contract(project: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if (project / "workflow_v6.json").is_file():
        legacy = {*CONFIRMATION_FIELDS, "confirmed_pages"}
        current = {*CONFIRMATION_FIELDS, *DIRECTOR_SUBMISSION_FIELDS}
        payload_fields = set(payload)
        base_fields = payload_fields - set(OPTIONAL_VISUAL_FIELDS)
        if base_fields not in (legacy, current):
            raise ValueError("confirmation must contain visual fields and approved director fields only")
        if base_fields == legacy:
            proposed = _read_json(project / "02_v6" / "page_composition.json")
            if payload["confirmed_pages"] != proposed["pages"]:
                raise ValueError("page composition is automatic and cannot be changed")
            payload = {
                field: payload[field]
                for field in (*CONFIRMATION_FIELDS, *OPTIONAL_VISUAL_FIELDS)
                if field in payload
            }
        visual_payload = {
            field: payload[field]
            for field in (*CONFIRMATION_FIELDS, *OPTIONAL_VISUAL_FIELDS)
            if field in payload
        }
        if type(visual_payload["revision"]) is not int or visual_payload["revision"] < 0:
            raise ValueError("revision must be a non-negative integer")
        error = _contract_error({**visual_payload, "revision": visual_payload["revision"] + 1})
        if error:
            raise ValueError(error)
        return _v6_final_submission(
            project,
            {field: _clean(payload[field]) for field in VISUAL_FIELDS if field in payload},
            payload,
        )
    error = _contract_error(payload)
    if error:
        raise ValueError(error)
    with _v6_confirmation_lock(project):
        state_path = project / "workflow_v6.json"
        if state_path.is_file():
            state = load_v6_state(project)
            if (
                state.get("confirmed_ui_revision") is not None
                or state.get("confirmed_ui_digest") is not None
                or state.get("style_confirmation", {}).get("status") == "confirmed"
            ):
                raise RuntimeError("visual contract is sealed and cannot be replaced")
        result_path = project / CONFIRM_DIR / RESULT
        if result_path.is_file():
            raise RuntimeError("final visual contract is already submitted and cannot be replaced")
        current_revision = 0
        if payload["revision"] != current_revision + 1:
            raise RuntimeError("stale confirmation revision")
        exact = {
            field: _clean(payload[field])
            for field in (*CONFIRMATION_FIELDS, *OPTIONAL_VISUAL_FIELDS)
            if field in payload
        }
        _write_json(result_path, exact)
        return exact


def _stage_number(value: Any) -> int:
    normalized = str(value or "").strip().lower()
    return {
        "1": 1,
        "stage1": 1,
        "2": 2,
        "stage2": 2,
        "3": 3,
        "stage3": 3,
        "final": 4,
    }.get(normalized, 0)


def _confirmed_stage(result_path: Path) -> int:
    if not result_path.is_file():
        return 0
    try:
        result = _read_json(result_path)
        if not _contract_error(result):
            return 4
        if result.get("status") == "confirmed" and type(result.get("revision")) is int:
            return 4
        return _stage_number(result.get("stage"))
    except (OSError, ValueError, json.JSONDecodeError):
        return 0


def _expected_recommendation_stage(result_path: Path) -> int:
    confirmed = _confirmed_stage(result_path)
    if confirmed == 0:
        return 1
    if confirmed == 1:
        return 2
    if confirmed == 2:
        return 3
    return 0


def _project_facts(project: Path) -> dict[str, Any]:
    """Read immutable pagination facts from a validated Awesome project only."""
    v6_path = project / "workflow_v6.json"
    if not v6_path.is_file():
        raise ValueError(NEW_PROJECT_REQUIRED)
    try:
        workflow = load_v6_state(project)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(NEW_PROJECT_REQUIRED) from exc
    pages = workflow.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError(NEW_PROJECT_REQUIRED)
    return {
        "page_count": len(pages),
        "pagination_mode": "explicit-markers-or-physical",
        "one_page_to_one_slide": True,
    }


def _v6_project_pages(project: Path) -> list[dict[str, Any]]:
    state = _read_json(project / "workflow_v6.json")
    frozen_result = _read_json(project / CONFIRM_DIR / RESULT) if (project / CONFIRM_DIR / RESULT).is_file() else {}
    frozen_pages = {
        item.get("page_number"): item for item in frozen_result.get("confirmed_pages", [])
        if isinstance(item, dict) and type(item.get("page_number")) is int
    }
    pages = []
    for page in state.get("pages", []):
        number = page.get("page_number")
        source = _read_json(project / "02_v6" / "page_sources" / f"page_{number:03d}.json")
        materials = _read_json(project / "02_v6" / "page_materials" / f"page_{number:03d}.json")
        frozen = frozen_pages.get(number)
        if frozen:
            materials = {**materials, **{field: frozen[field] for field in (
                "effective_body", "attachment_extracts", "chart_facts", "image_requirements",
                "degradations", "reference_images",
            ) if field in frozen}}
        references = materials.get("reference_images", [])
        public_references = []
        for item in references:
            if not isinstance(item, dict):
                continue
            public_references.append({
                "reference_id": item.get("reference_id"),
                "purpose": item.get("purpose", ""),
                "allow_crop": bool(item.get("allow_crop")),
                "allow_restyle": bool(item.get("allow_restyle")),
                "status": item.get("status", "available"),
                "review_decision": "",
                "thumbnail_url": "/api/media/thumbnail?path=" + str(item.get("thumbnail_path", "")),
                "original_url": "/api/media/original?path=" + str(item.get("original_path", "")),
                "model_input_url": "/api/media/model-input?path=" + str(item.get("model_input_path", "")),
            })
        receipt_path = project / "02_v6" / "reference_materials" / f"page_{number:03d}.json"
        receipt = _read_json(receipt_path) if receipt_path.is_file() else {}
        found_candidates = []
        for acquisition in receipt.get("reference_acquisitions", []) if not frozen else []:
            if not isinstance(acquisition, dict):
                continue
            candidate = acquisition.get("candidate")
            reference = candidate.get("reference") if isinstance(candidate, dict) else None
            if acquisition.get("status") != "found" or not isinstance(reference, dict):
                continue
            found_candidates.append({
                "request_id": acquisition.get("request_id"),
                "purpose": reference.get("purpose", acquisition.get("purpose", "")),
                "thumbnail_url": "/api/media/thumbnail?path=" + str(reference.get("thumbnail_path", "")),
                "original_url": "/api/media/original?path=" + str(reference.get("original_path", "")),
                "model_input_url": "/api/media/model-input?path=" + str(reference.get("model_input_path", "")),
            })
        pages.append({
            "page_number": number,
            "title": page.get("title"),
            "fixed_page_title": materials.get("fixed_page_title", page.get("title", "")),
            "word_original": materials.get("word_original", source.get("word_original", "")),
            "effective_body": materials.get("effective_body", ""),
            "attachment_extracts": materials.get("attachment_extracts", []),
            "chart_facts": materials.get("chart_facts", []),
            "image_requirements": materials.get("image_requirements", []),
            "degradations": materials.get("degradations", []),
            "reference_images": public_references,
            "reference_count": len(public_references),
            "reference_warning": (
                "reject" if len(public_references) > 16 else
                "strong" if len(public_references) >= 11 else
                "warning" if len(public_references) >= 7 else None
            ),
            "found_reference_candidates": found_candidates,
            "reference_decisions": frozen.get("reference_decisions", []) if frozen else [],
        })
    return pages


@contextmanager
def _v6_confirmation_lock(project: Path, timeout: float = 15.0):
    """Serialize the one authoritative UI commit without touching V6 source state."""
    with mutation_lock(project, timeout=timeout):
        yield


def _composition_trace(page: dict[str, Any]) -> tuple[Any, ...]:
    page_id = page.get("composition_page_id")
    if isinstance(page_id, str) and page_id:
        return ("composition_page_id", page_id)
    block_ids = page.get("material_source_block_ids")
    return (
        page.get("source_page_id"),
        page.get("source_page_number"),
        tuple(block_ids) if isinstance(block_ids, list) else (),
    )


_IMMUTABLE_COMPOSITION_FIELDS = (
    "source_page_id",
    "source_page_number",
    "material_source_block_ids",
    "composition_page_id",
)
_EDITABLE_COMPOSITION_FIELDS = (
    "output_page_number",
    "page_role",
    "chapter_title",
    "fixed_page_title",
    "visible_page_number",
)


def _freeze_confirmed_composition(
    proposed: dict[str, Any], submitted_pages: list[dict[str, Any]],
) -> dict[str, Any]:
    submitted = freeze_composition(proposed, submitted_pages)
    proposed_by_id = {
        page["composition_page_id"]: page
        for page in proposed["pages"] if "composition_page_id" in page
    }
    proposed_by_trace = {
        _composition_trace(page): page
        for page in proposed["pages"] if "composition_page_id" not in page
    }
    if len(proposed_by_id) + len(proposed_by_trace) != len(proposed["pages"]):
        raise ValueError("proposed composition pages require unique traceability")
    trusted_pages = []
    seen = set()
    for page in submitted["pages"]:
        page_id = page.get("composition_page_id")
        if page_id is not None:
            original = proposed_by_id.get(page_id)
            identity = ("composition_page_id", page_id)
        else:
            identity = _composition_trace(page)
            original = proposed_by_trace.get(identity)
        if original is None or identity in seen:
            raise ValueError("confirmed composition page traceability is invalid")
        seen.add(identity)
        if any(page.get(field) != original.get(field) for field in _IMMUTABLE_COMPOSITION_FIELDS):
            raise ValueError("confirmed composition page immutable trace was modified")
        role_changed = page["page_role"] != original["page_role"]
        accepted_role_sources = (
            {"explicit"} if role_changed else {original["role_source"], "explicit"}
        )
        if page["role_source"] not in accepted_role_sources:
            raise ValueError("confirmed composition role provenance is invalid")
        trusted = copy.deepcopy(original)
        for field in _EDITABLE_COMPOSITION_FIELDS:
            trusted[field] = copy.deepcopy(page[field])
        trusted["role_source"] = page["role_source"]
        trusted_pages.append(trusted)
    return freeze_composition(proposed, trusted_pages)


def _validated_paginated_word_source(
    project: Path, state: dict[str, Any], proposed_pages: list[dict[str, Any]],
) -> dict[str, Any]:
    source_path = _literal_existing_file(
        project / "02_v6" / "paginated_word_source.json"
    )
    source = _read_json(source_path)
    if not isinstance(source, dict):
        raise ValueError("pre-confirmation paginated Word source is invalid")
    pages = source.get("pages")
    expected_count = len(proposed_pages)
    if (
        type(source.get("page_count")) is not int
        or source["page_count"] != expected_count
        or not isinstance(pages, list)
        or len(pages) != expected_count
        or len(state.get("pages", [])) != expected_count
    ):
        raise ValueError("pre-confirmation paginated Word source page count is incorrect")
    if any(
        not isinstance(page, dict)
        or type(page.get("page_number")) is not int
        or page["page_number"] != number
        for number, page in enumerate(pages, start=1)
    ):
        raise ValueError("pre-confirmation paginated Word source pages must be continuous")
    for source_page, proposed_page in zip(pages, proposed_pages):
        for source_field, proposed_field in (
            ("source_page_id", "source_page_id"),
            ("source_asset_page_number", "source_page_number"),
        ):
            source_value = source_page.get(source_field)
            proposed_value = proposed_page.get(proposed_field)
            if type(source_value) is not type(proposed_value) or source_value != proposed_value:
                raise ValueError("pre-confirmation paginated Word source identity is incorrect")
    return source


def _composition_transaction_targets(
    project: Path,
    state: dict[str, Any],
    proposed_pages: list[dict[str, Any]],
    frozen: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, bytes | None]:
    if state.get("page_materials_status") != "pre_confirmation":
        raise ValueError("page composition can only be frozen before page materials are prepared")
    original_numbers = {
        _composition_trace(page): page["output_page_number"] for page in proposed_pages
    }
    frozen_pages = frozen["pages"]
    selected_numbers = [original_numbers[_composition_trace(page)] for page in frozen_pages]

    targets: dict[str, bytes | None] = {}
    source_manifest = _validated_paginated_word_source(project, state, proposed_pages)
    source_pages = source_manifest["pages"]
    migrated_source_pages = []
    for new_number, old_number in enumerate(selected_numbers, start=1):
        source_page = copy.deepcopy(source_pages[old_number - 1])
        source_page["page_number"] = new_number
        migrated_source_pages.append(source_page)
    migrated_source = copy.deepcopy(source_manifest)
    migrated_source["page_count"] = len(migrated_source_pages)
    migrated_source["pages"] = migrated_source_pages
    targets["02_v6/paginated_word_source.json"] = _json_bytes(migrated_source)
    for directory_name in _PAGE_AUTHORITY_DIRECTORIES:
        directory = _literal_project_directory(
            project, Path("02_v6") / directory_name, required=False
        )
        if directory is None:
            continue
        original_values: dict[int, dict[str, Any]] = {}
        for old_number in range(1, len(proposed_pages) + 1):
            source = directory / f"page_{old_number:03d}.json"
            if not os.path.lexists(source):
                if directory_name != "reference_materials":
                    raise ValueError(f"pre-confirmation {directory_name} page is missing")
                continue
            original_values[old_number] = _read_json(_literal_existing_file(source))
        rewritten: dict[int, bytes] = {}
        for new_number, old_number in enumerate(selected_numbers, start=1):
            if old_number not in original_values:
                if directory_name == "reference_materials":
                    continue
                raise ValueError(f"pre-confirmation {directory_name} page is missing")
            value = copy.deepcopy(original_values[old_number])
            value["page_number"] = new_number
            if "fixed_page_title" in value:
                value["fixed_page_title"] = frozen_pages[new_number - 1]["fixed_page_title"]
            rewritten[new_number] = _json_bytes(value)
        for number in range(1, len(proposed_pages) + 1):
            relative = f"02_v6/{directory_name}/page_{number:03d}.json"
            targets[relative] = rewritten.get(number)

    next_state_pages = []
    for new_number, (old_number, composition_page) in enumerate(
        zip(selected_numbers, frozen_pages), start=1
    ):
        page = dict(state["pages"][old_number - 1])
        page["page_number"] = new_number
        page["title"] = composition_page["fixed_page_title"]
        next_state_pages.append(page)
    next_state = copy.deepcopy(state)
    next_state["pages"] = next_state_pages
    next_state["director_confirmation"] = copy.deepcopy(result["director_confirmation"])
    validate_v6_project(next_state)
    targets.update({
        "workflow_v6.json": _json_bytes(next_state),
        "02_v6/page_composition.json": _json_bytes(frozen),
        "confirm_ui/result.json": _json_bytes(result),
    })
    return targets


def _prepare_composition_transaction(
    project: Path, targets: dict[str, bytes | None]
) -> dict[str, Any]:
    _literal_project_directory(project, Path("02_v6"), required=True)
    if _transaction_directory(project, required=False) is not None:
        raise ValueError("unrecovered confirmation transaction already exists")
    if _preparing_transaction_directory(project, required=False) is not None:
        raise ValueError("unrecovered confirmation preparation already exists")
    project_root = Path(project).resolve(strict=True)
    preparing = project_root / _TRANSACTION_PREPARING_DIR
    directory = project_root / _TRANSACTION_DIR
    preparing.mkdir()
    manifest = {"version": 1, "phase": "preparing", "targets": []}
    records = []
    published = False
    try:
        _transaction_write_bytes(preparing / "manifest.json", _json_bytes(manifest))
        validated_preparing = _preparing_transaction_directory(project, required=True)
        assert validated_preparing is not None
        if _read_json(
            _literal_existing_file(validated_preparing / "manifest.json")
        ) != manifest:
            raise ValueError("confirmation preparing manifest validation failed")
        os.replace(validated_preparing, directory)
        directory = _transaction_directory(project, required=True)
        assert directory is not None
        published = True
        ordered_targets = sorted(
            targets, key=lambda relative: (relative == "confirm_ui/result.json", relative)
        )
        for index, relative in enumerate(ordered_targets, start=1):
            target = _transaction_target(project, relative)
            original_name = None
            if os.path.lexists(target):
                original_name = f"original/{index:04d}.bin"
                _transaction_write_bytes(
                    directory / original_name,
                    _literal_existing_file(target).read_bytes(),
                )
            staged_name = None
            if targets[relative] is not None:
                staged_name = f"staged/{index:04d}.bin"
                _transaction_write_bytes(directory / staged_name, targets[relative])
            records.append({
                "relative": relative,
                "original": original_name,
                "staged": staged_name,
            })
        manifest = {"version": 1, "phase": "prepared", "targets": records}
        _transaction_write_bytes(directory / "manifest.json", _json_bytes(manifest))
        return manifest
    except Exception:
        if published:
            _remove_transaction(project)
        else:
            _remove_preparing_transaction(project)
        raise


def _commit_composition_transaction(
    project: Path, manifest: dict[str, Any]
) -> None:
    directory = _transaction_directory(project, required=True)
    assert directory is not None
    manifest["phase"] = "committing"
    _transaction_write_bytes(directory / "manifest.json", _json_bytes(manifest))
    try:
        for item in manifest["targets"]:
            target = _transaction_target(project, item["relative"])
            if item["staged"] is None:
                if os.path.lexists(target):
                    _literal_existing_file(target).unlink()
                continue
            staged = _literal_existing_file(directory / Path(item["staged"]))
            _transaction_replace(staged, target)
        manifest["phase"] = "committed"
        _transaction_write_bytes(directory / "manifest.json", _json_bytes(manifest))
    except Exception:
        try:
            _restore_transaction(project, manifest)
        except Exception as recovery_error:
            raise RuntimeError(
                "confirmation commit failed and durable recovery remains pending"
            ) from recovery_error
        raise
    _remove_transaction(project)


def _v6_final_submission(
    project: Path, global_contract: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Freeze the visual contract and complete page composition in one revision."""
    with _v6_confirmation_lock(project):
        recovered_commit = _recover_composition_transaction(project)
        result_path = project / CONFIRM_DIR / RESULT
        if result_path.is_file():
            existing = _read_json(_literal_existing_file(result_path))
            if recovered_commit and (
                existing.get("submission_id") == payload.get("submission_id")
                and existing.get("global_visual_contract") == global_contract
                and existing.get("confirmed_pages") == payload.get("confirmed_pages")
            ):
                return existing
            raise RuntimeError("V6 final confirmation is sealed and may be submitted exactly once")
        state = load_v6_state(project)
        if (
            state.get("confirmed_ui_revision") is not None
            or state.get("confirmed_ui_digest") is not None
            or state.get("style_confirmation", {}).get("status") == "confirmed"
        ):
            raise RuntimeError("visual contract and composition are sealed and cannot be replaced")
        if payload.get("revision") != 0:
            raise RuntimeError("stale confirmation revision")

        proposed = _read_json(project / "02_v6" / "page_composition.json")
        validate_composition(proposed)
        pages = payload.get("confirmed_pages")
        if pages is None:
            frozen = freeze_composition(proposed, proposed["pages"])
        else:
            if not isinstance(pages, list) or not pages:
                raise ValueError("confirmed_pages must contain at least one page")
            if sum(page.get("page_role") == "closing" for page in pages if isinstance(page, dict)) > 1:
                raise ValueError("composition cannot contain two closing pages")
            frozen = _freeze_confirmed_composition(proposed, pages)
        frozen_pages = frozen["pages"]
        director_confirmation = _director_confirmation(project, payload)
        result = {
            "status": "confirmed",
            "revision": 1,
            "submission_id": payload["submission_id"],
            "confirmed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "global_visual_contract": _clean(global_contract),
            "director_confirmation": director_confirmation,
            "confirmed_pages": frozen_pages,
        }
        targets = _composition_transaction_targets(
            project, state, proposed["pages"], frozen, result
        )
        manifest = _prepare_composition_transaction(project, targets)
        _commit_composition_transaction(project, manifest)
        return result


def _field_value(value: Any, default: Any = "") -> Any:
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return default if value is None else value


def _localized_present(value: Any, stem: str) -> bool:
    if not isinstance(value, dict):
        return False
    for key in (stem, f"{stem}_zh", f"{stem}_en", f"{stem}_ja"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return True
    return False


def _palette_error(color: Any, label: str) -> str | None:
    if not isinstance(color, dict):
        return f"{label}.color must be an object"
    palette = color.get("palette")
    if not isinstance(palette, dict):
        return f"{label}.color.palette must be an object"
    for role in PALETTE_ROLES:
        if not isinstance(palette.get(role), str) or not HEX_COLOR.fullmatch(palette[role]):
            return f"{label}.color.palette.{role} must be a six-digit HEX color"
    return None


def _typography_error(typography: Any, label: str) -> str | None:
    if not isinstance(typography, dict):
        return f"{label}.typography must be an object"
    if set(typography) != {"name_zh", "heading", "body", "body_size", "type_scale_pt"}:
        return f"{label}.typography must define exactly name_zh, heading, body, body_size, and type_scale_pt"
    if not isinstance(typography["name_zh"], str):
        return f"{label}.typography.name_zh must be a string"
    for role in ("heading", "body"):
        stack = typography.get(role)
        if not isinstance(stack, dict):
            return f"{label}.typography.{role} must be an object"
        if set(stack) != {"cjk", "latin", "css"}:
            return f"{label}.typography.{role} must define exactly cjk, latin, and css"
        for field in ("cjk", "latin", "css"):
            if not isinstance(stack.get(field), str) or not stack[field].strip():
                return f"{label}.typography.{role}.{field} must be non-empty"
    body_size = typography.get("body_size")
    if not isinstance(body_size, (int, float)) or isinstance(body_size, bool) or body_size <= 0:
        return f"{label}.typography.body_size must be positive"
    scale = typography.get("type_scale_pt")
    if not isinstance(scale, dict) or set(scale) != {"page_title", "section_title", "body", "caption"}:
        return f"{label}.typography.type_scale_pt must define the complete four-role scale"
    bounds = {
        "page_title": (12, 72),
        "section_title": (10, 48),
        "body": (8, 32),
        "caption": (8, 24),
    }
    for role, (minimum, maximum) in bounds.items():
        value = scale.get(role)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not minimum <= value <= maximum:
            return f"{label}.typography.type_scale_pt.{role} is outside the supported range"
    return None


def _style_axes_error(value: Any, label: str) -> str | None:
    if not isinstance(value, dict) or set(value) != {"formal", "modern", "minimal"}:
        return f"{label}.style_axes must define formal, modern, and minimal"
    for axis in ("formal", "modern", "minimal"):
        score = value.get(axis)
        if type(score) is not int or not 0 <= score <= 100:
            return f"{label}.style_axes.{axis} must be an integer from 0 through 100"
    return None


def _direction_error(direction: Any, index: int) -> str | None:
    label = f"design_directions.candidates[{index}]"
    if not isinstance(direction, dict):
        return f"{label} must be an object"
    for field in ("visual_style", "icons"):
        if not isinstance(direction.get(field), str) or not direction[field].strip():
            return f"{label}.{field} must be non-empty"
    error = _palette_error(direction.get("color"), label)
    if error:
        return error
    error = _typography_error(direction.get("typography"), label)
    if error:
        return error
    error = _style_axes_error(direction.get("style_axes"), label)
    if error:
        return error
    if direction.get("information_density") not in INFORMATION_DENSITIES:
        return f"{label}.information_density must be low, balanced, or high"
    rendering = direction.get("image_rendering")
    if not isinstance(rendering, dict):
        return f"{label}.image_rendering must be an object"
    if not isinstance(rendering.get("rendering"), str) or not rendering["rendering"].strip():
        return f"{label}.image_rendering.rendering must be non-empty"
    if not _localized_present(rendering, "visual") or not _localized_present(rendering, "mood"):
        return f"{label}.image_rendering must describe visual expression and mood"
    return None


def _stage2_error(recommendations: dict[str, Any]) -> str | None:
    directions = recommendations.get("design_directions")
    candidates = directions.get("candidates") if isinstance(directions, dict) else None
    if not isinstance(candidates, list) or len(candidates) < 3:
        return "Stage 2 requires at least three coordinated design directions"
    for index, direction in enumerate(candidates):
        error = _direction_error(direction, index)
        if error:
            return error
    selected = directions.get("selected")
    if selected is None:
        recommend = recommendations.get("recommend")
        selected = recommend.get("direction", 0) if isinstance(recommend, dict) else 0
    if type(selected) is not int or not 0 <= selected < len(candidates):
        return "design direction selection must be an in-range candidate index"
    return None


def _stage2_submission_error(payload: dict[str, Any], candidate_count: int) -> str | None:
    """Validate the complete user-confirmed visual system before advancing."""
    direction = payload.get("direction")
    if type(direction) is not int or not 0 <= direction < candidate_count:
        return "direction must be an in-range candidate index"
    for field in ("delivery_purpose", "mode", "visual_style", "icons"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            return f"{field} must be non-empty"
    error = _palette_error(payload.get("color"), "submission")
    if error:
        return error
    error = _typography_error(payload.get("typography"), "submission")
    if error:
        return error
    error = _style_axes_error(payload.get("style_axes"), "submission")
    if error:
        return error
    if payload.get("information_density") not in INFORMATION_DENSITIES:
        return "information_density must be low, balanced, or high"
    requirements = payload.get("additional_requirements")
    if not isinstance(requirements, str) or len(requirements) > 2000:
        return "additional_requirements must be text no longer than 2000 characters"
    rendering = payload.get("image_rendering")
    if not isinstance(rendering, dict):
        return "submission.image_rendering must be an object"
    if not isinstance(rendering.get("rendering"), str) or not rendering["rendering"].strip():
        return "submission.image_rendering.rendering must be non-empty"
    if not _localized_present(rendering, "visual") or not _localized_present(rendering, "mood"):
        return "submission.image_rendering must describe visual expression and mood"
    return None


def _stage3_submission_error(payload: dict[str, Any]) -> str | None:
    """Validate all production choices before writing a final confirmation."""
    formula_policy = payload.get("formula_policy")
    if formula_policy not in FORMULA_POLICIES:
        return "formula_policy must be a supported production value"
    generation_mode = payload.get("generation_mode")
    if generation_mode not in GENERATION_MODES:
        return "generation_mode must be a supported production value"
    if type(payload.get("refine_spec")) is not bool:
        return "refine_spec must be a boolean"
    if payload.get("image_quality") not in IMAGE_QUALITIES:
        return "image_quality must be auto, low, medium, or high"
    concurrency = payload.get("max_concurrency")
    if type(concurrency) is not int or not 1 <= concurrency <= 8:
        return "max_concurrency must be an integer from 1 through 8"
    repair_budget = payload.get("automatic_repair_budget")
    if type(repair_budget) is not int or not 0 <= repair_budget <= 3:
        return "automatic_repair_budget must be an integer from 0 through 3"
    if type(payload.get("editable_output")) is not bool:
        return "editable_output must be a boolean"
    if payload.get("start_generation") is not True:
        return "start_generation must be confirmed"
    return None


def _one_screen_submission_error(payload: dict[str, Any], candidate_count: int) -> str | None:
    removed = sorted(REMOVED_ONE_SCREEN_FIELDS.intersection(payload))
    if removed:
        return f"removed fixed-frame/reference fields are not accepted: {', '.join(removed)}"
    direction = payload.get("direction")
    if type(direction) is not int or not 0 <= direction < candidate_count:
        return "direction must be an in-range candidate index"
    if payload.get("canvas") != CANVAS_ID:
        return "canvas must be ppt169"
    template = payload.get("template_selection")
    if not isinstance(template, dict):
        return "template_selection must be an object"
    required_template_fields = {"id", "label", "version", "substyle_id", "override_fields"}
    if set(template) != required_template_fields:
        return "template_selection must define id, label, version, substyle_id, and override_fields"
    if template.get("id") not in TEMPLATE_IDS:
        return "template_selection.id must be a supported template"
    if not isinstance(template.get("label"), str) or not template["label"].strip() or template.get("version") != "1.0":
        return "template_selection label/version is invalid"
    substyle = template.get("substyle_id")
    if template["id"] == "evidence-investment-bp":
        if substyle not in BP_SUBSTYLE_IDS:
            return "investment BP requires dark-tech or white-rd substyle"
    elif substyle is not None:
        return "only investment BP may define a substyle"
    overrides = template.get("override_fields")
    if not isinstance(overrides, list) or len(overrides) != len(set(overrides)) or any(not isinstance(item, str) or not item for item in overrides):
        return "template_selection.override_fields must be a unique string list"
    for field in ("visual_style", "icons"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            return f"{field} must be non-empty"
    for validator, value in (
        (_palette_error, payload.get("color")),
        (_typography_error, payload.get("typography")),
        (_style_axes_error, payload.get("style_axes")),
    ):
        error = validator(value, "submission")
        if error:
            return error
    if payload.get("information_density") not in INFORMATION_DENSITIES:
        return "information_density must be low, balanced, or high"
    if payload.get("image_usage_policy", "content-driven") not in IMAGE_USAGE_POLICIES:
        return "image_usage_policy must be content-driven, visual-preference, or source-only"
    layouts = payload.get("layout_preferences")
    if (
        not isinstance(layouts, list)
        or not layouts
        or len(layouts) != len(set(layouts))
        or any(layout not in LAYOUT_PREFERENCES for layout in layouts)
    ):
        return "layout_preferences must be a non-empty unique list of supported layout ids"
    rendering = payload.get("image_rendering")
    if not isinstance(rendering, dict):
        return "submission.image_rendering must be an object"
    if not isinstance(rendering.get("rendering"), str) or not rendering["rendering"].strip():
        return "submission.image_rendering.rendering must be non-empty"
    if not _localized_present(rendering, "visual") or not _localized_present(rendering, "mood"):
        return "submission.image_rendering must describe visual expression and mood"
    regional = payload.get("regional_style")
    if not isinstance(regional, dict) or type(regional.get("enabled")) is not bool:
        return "regional_style must be an object with a boolean enabled field"
    if payload.get("background_system") not in BACKGROUND_SYSTEMS:
        return "background_system must be supported"
    image_role = payload.get("image_role")
    if not isinstance(image_role, dict) or set(image_role) != {"role", "proportion"}:
        return "image_role must define role and proportion"
    if image_role.get("role") not in IMAGE_ROLES or image_role.get("proportion") not in IMAGE_PROPORTIONS:
        return "image_role contains an unsupported role or proportion"
    if payload.get("evidence_strength") not in EVIDENCE_STRENGTHS:
        return "evidence_strength must be supported"
    if payload.get("composition_tendency") not in COMPOSITION_TENDENCIES:
        return "composition_tendency must be supported"
    if payload.get("brand_device") not in BRAND_DEVICES:
        return "brand_device must be supported"
    if payload.get("production_profile") not in PRODUCTION_PROFILES:
        return "production_profile must be quality, balanced, or speed"
    requirements = payload.get("additional_requirements")
    if not isinstance(requirements, str) or len(requirements) > 2000:
        return "additional_requirements must be text no longer than 2000 characters"
    return None


def _stage1_submission_error(payload: dict[str, Any]) -> str | None:
    for field in ("audience", "core_message", "delivery_context", "content_divergence"):
        if not isinstance(payload.get(field), str):
            return f"{field} must be text"
    if payload.get("canvas") != CANVAS_ID:
        return "canvas must be ppt169"
    return None


def _normalize_stage2(recommendations: dict[str, Any]) -> dict[str, Any]:
    """Adapt the authoritative bundled-direction shape to the reduced UI."""
    normalized = _clean(recommendations)
    directions = normalized.get("design_directions")
    candidates = directions.get("candidates") if isinstance(directions, dict) else None
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            strategy = candidate.pop("image_strategy", None)
            if "image_rendering" not in candidate and isinstance(strategy, dict):
                candidate["image_rendering"] = strategy
    return normalized


def _recommendation_view(project: Path, recommendations: dict[str, Any]) -> dict[str, Any]:
    stage = _stage_number(recommendations.get("stage"))
    if stage == 1:
        view: dict[str, Any] = {
            "stage": "stage1",
            "lang": recommendations.get("lang", "zh"),
            "editable_fields": list(STAGE1_EDITABLE),
            "read_only_fields": list(STAGE1_FACTS),
        }
        recommended = recommendations.get("recommend")
        recommended = recommended if isinstance(recommended, dict) else {}
        for field in STAGE1_EDITABLE:
            value = recommendations.get(field)
            if value is None and field in recommended:
                value = recommended[field]
            view[field] = {"value": _field_value(value)}
        for field, value in _project_facts(project).items():
            view[field] = {"value": value, "read_only": True}
        return view
    if stage == 2:
        return _normalize_stage2(recommendations)
    if stage == 3:
        allowed = {"stage", "lang", "recommend", "refine_spec"}
        return _clean({key: value for key, value in recommendations.items() if key in allowed})
    if stage == 4:
        view = _normalize_stage2(recommendations)
        view.update(_project_facts(project))
        view["stage"] = "final"
        view["fixed_region"] = _fixed_region_view()
        if (project / "workflow_v6.json").is_file():
            composition = _read_json(project / "02_v6" / "page_composition.json")
            view.update({
                "page_requirement_summary": [],
                "comments_are_page_authority": True,
                "composition": _composition_view(project, composition),
            })
        else:
            summary_path = project / PAGE_REQUIREMENT_SUMMARY_PATH
            if not summary_path.is_file():
                raise ValueError("sealed page requirement summary is missing")
            view.update(public_requirement_summary(project, _read_json(summary_path)))
        return view
    raise ValueError("recommendations.json must declare stage1, stage2, stage3, or final")


def _session_state(project: Path) -> dict[str, Any]:
    confirm_dir = project / CONFIRM_DIR
    recommendation_stage = 0
    recommendation_path = confirm_dir / RECOMMENDATIONS
    if recommendation_path.is_file():
        try:
            recommendation_stage = _stage_number(_read_json(recommendation_path).get("stage"))
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    confirmed = _confirmed_stage(confirm_dir / RESULT)
    state = {
        "recommendation_stage": recommendation_stage,
        "confirmed_stage": confirmed,
        "expected_stage": _expected_recommendation_stage(confirm_dir / RESULT),
        "complete": confirmed == 4,
    }
    if (project / "workflow_v6.json").is_file():
        try:
            result_path = confirm_dir / RESULT
            result = _read_json(result_path) if result_path.is_file() else {}
            state["revision"] = result.get("revision") or 0
        except (OSError, ValueError, json.JSONDecodeError):
            state["revision"] = 0
    return state


def _write_session(project: Path, event: str) -> dict[str, Any]:
    state = _session_state(project)
    state["event"] = event
    state["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    _write_json(project / CONFIRM_DIR / SESSION, state)
    return state


def _stage_submission(project: Path, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    confirm_dir = project / CONFIRM_DIR
    recommendations_path = confirm_dir / RECOMMENDATIONS
    result_path = confirm_dir / RESULT
    if not recommendations_path.is_file():
        return None, "recommendations.json not found"
    recommendations = _read_json(recommendations_path)
    rec_stage = _stage_number(recommendations.get("stage"))
    submitted_stage = _stage_number(payload.get("stage"))
    expected_stage = _expected_recommendation_stage(result_path)
    if expected_stage == 1 and rec_stage == 4 and not result_path.exists():
        expected_stage = 4
    v6_resubmission = (
        (project / "workflow_v6.json").is_file()
        and rec_stage == 4 and submitted_stage == 4
    )
    if (rec_stage != expected_stage and not v6_resubmission) or submitted_stage != rec_stage:
        return None, (
            f"strict stage order requires stage{expected_stage or ' complete'}; "
            f"recommendation is stage{rec_stage or ' invalid'} and submission is "
            f"stage{submitted_stage or ' invalid'}"
        )

    existing: dict[str, Any] = {}
    if result_path.is_file():
        existing = _read_json(result_path)
        existing.pop("confirmed_at", None)
        existing.pop("status", None)

    if rec_stage == 1:
        submission_error = _stage1_submission_error(payload)
        if submission_error:
            raise ValueError(submission_error)
        result = {field: payload.get(field, "") for field in STAGE1_EDITABLE}
        result.update(_project_facts(project))
        result["stage"] = "stage1"
        result["status"] = "stage1-confirmed"
    elif rec_stage == 2:
        normalized_recommendations = _normalize_stage2(recommendations)
        recommendation_error = _stage2_error(normalized_recommendations)
        if recommendation_error:
            return None, recommendation_error
        candidates = normalized_recommendations["design_directions"]["candidates"]
        submission_error = _stage2_submission_error(payload, len(candidates))
        if submission_error:
            raise ValueError(submission_error)
        result = existing
        for field in STAGE2_FIELDS:
            if field in payload:
                result[field] = _clean(payload[field])
        result["stage"] = "stage2"
        result["status"] = "stage2-confirmed"
    elif rec_stage == 3:
        submission_error = _stage3_submission_error(payload)
        if submission_error:
            raise ValueError(submission_error)
        result = existing
        for field in STAGE3_FIELDS:
            if field in payload:
                result[field] = payload[field]
        result["stage"] = "final"
        result["status"] = "confirmed"
    else:
        normalized_recommendations = _normalize_stage2(recommendations)
        recommendation_error = _stage2_error(normalized_recommendations)
        if recommendation_error:
            return None, recommendation_error
        candidates = normalized_recommendations["design_directions"]["candidates"]
        submission_error = _one_screen_submission_error(payload, len(candidates))
        if submission_error:
            raise ValueError(submission_error)
        result = {field: _clean(payload[field]) for field in ONE_SCREEN_FIELDS}
        result["image_usage_policy"] = _clean(payload.get("image_usage_policy", "content-driven"))
        result.update(_project_facts(project))
        result.update(ONE_SCREEN_PRODUCTION_BASE)
        result.update(PRODUCTION_PROFILES[result["production_profile"]])
        result["stage"] = "final"
        result["status"] = "confirmed"
    result = _clean(result)
    result["confirmed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    if rec_stage == 4 and (project / "workflow_v6.json").is_file():
        global_contract = {
            key: value for key, value in result.items()
            if key not in {"stage", "status", "confirmed_at"}
        }
        global_contract.update({
            "stage": "final",
            "status": "confirmed",
            "confirmed_at": result["confirmed_at"],
        })
        return _v6_final_submission(project, global_contract, payload), None
    return result, None


def create_app(
    project_dir: str,
    idle_timeout: int = 900,
    *,
    lock_file: Path | None = None,
    server_port: int | None = None,
    lock_owner: dict[str, Any] | None = None,
) -> Flask:
    """Create the Flask app without any dependency on an installed PPT skill."""
    project = Path(project_dir).resolve()
    static_dir = Path(__file__).resolve().parent / "static"
    app = Flask(__name__, static_folder=str(static_dir), static_url_path="/static")
    app.config.update(
        PROJECT=project,
        LOCK_FILE=lock_file,
        SERVER_PORT=server_port,
        LOCK_OWNER=dict(lock_owner) if lock_owner else None,
        LAST_REQUEST=time.monotonic(),
    )
    @app.before_request
    def _activity() -> None:
        app.config["LAST_REQUEST"] = time.monotonic()

    @app.get("/")
    def index():
        return send_from_directory(static_dir, "index.html")

    @app.get("/api/health")
    def health():
        try:
            facts = _project_facts(project)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            response = jsonify({"status": "incompatible", "error": str(exc)})
            response.headers["Cache-Control"] = "no-store"
            return response, 409
        response = jsonify(
            {
                "status": "ok",
                "project": str(project),
                "pid": lock_owner.get("pid") if lock_owner else None,
                "nonce": lock_owner.get("nonce") if lock_owner else None,
                "pagination_locked": True,
                "session": _session_state(project),
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/session")
    def session():
        response = jsonify(_write_session(project, "poll"))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/recommendations")
    def recommendations():
        try:
            response = jsonify(_template_recommendations(project))
            response.headers["Cache-Control"] = "no-store"
            return response
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/confirm")
    def confirm():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "confirmation payload must be an object"}), 400
        try:
            result = _save_visual_contract(project, payload)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return jsonify({"error": str(exc)}), 400
        _write_session(project, "final-submitted")
        if (
            app.config.get("LOCK_FILE") is not None
        ):
            threading.Thread(
                target=_delayed_exit,
                args=(app.config["LOCK_FILE"], app.config.get("LOCK_OWNER")),
                daemon=True,
            ).start()
        return jsonify({"status": "ok", "stage": "final", "revision": result["revision"]})

    @app.post("/api/shutdown")
    def shutdown():
        owner = app.config.get("LOCK_OWNER")
        if owner is not None and request.headers.get("X-Confirm-Nonce") != owner.get("nonce"):
            return jsonify({"error": "confirmation UI shutdown ownership mismatch"}), 409
        if app.config.get("LOCK_FILE") is not None:
            threading.Thread(
                target=_delayed_exit,
                args=(app.config["LOCK_FILE"], app.config.get("LOCK_OWNER")),
                daemon=True,
            ).start()
        return jsonify({"status": "ok"})

    if idle_timeout > 0 and lock_file is not None:
        def idle_watchdog() -> None:
            while True:
                time.sleep(min(10, idle_timeout))
                if time.monotonic() - app.config["LAST_REQUEST"] > idle_timeout:
                    _remove_lock(lock_file, expected=app.config.get("LOCK_OWNER"))
                    os._exit(0)

        threading.Thread(target=idle_watchdog, daemon=True).start()

    return app


def _read_lock(path: Path) -> dict[str, Any] | None:
    try:
        return _read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _process_alive(pid: Any) -> bool:
    try:
        pid_number = int(pid)
        if pid_number <= 0:
            return False
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL

            process = kernel32.OpenProcess(0x1000, False, pid_number)
            if not process:
                return False
            try:
                exit_code = wintypes.DWORD()
                return bool(
                    kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code))
                    and exit_code.value == 259
                )
            finally:
                kernel32.CloseHandle(process)
        os.kill(pid_number, 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _same_lock(left: Any, right: Any) -> bool:
    return isinstance(left, dict) and isinstance(right, dict) and left == right


@contextmanager
def _lock_mutation_guard(path: Path):
    """Serialize compare/delete and create operations across threads and processes."""
    guard_path = path.with_name(f".{path.name}.mutation-lock")
    guard_path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK_MUTATION_THREAD_LOCK:
        descriptor = os.open(guard_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _remove_lock(path: Path, *, expected: dict[str, Any] | None = None) -> bool:
    with _lock_mutation_guard(path):
        try:
            if expected is not None and not _same_lock(_read_lock(path), expected):
                return False
            path.unlink(missing_ok=True)
            return True
        except OSError:
            return False


def _claim_lock(path: Path, owner: dict[str, Any]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(owner, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    with _lock_mutation_guard(path):
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False
        try:
            os.write(descriptor, payload)
        finally:
            os.close(descriptor)
        return True


def _lock_for(project: Path, *, pid: int, port: int, nonce: str) -> dict[str, Any]:
    return {"pid": pid, "port": port, "project": str(project.resolve()), "nonce": nonce}


def _valid_owner(lock: Any, project: Path) -> bool:
    if not isinstance(lock, dict) or set(lock) != {"pid", "port", "project", "nonce"}:
        return False
    return (
        type(lock["pid"]) is int and lock["pid"] > 0
        and type(lock["port"]) is int and 0 < lock["port"] < 65536
        and lock["project"] == str(project.resolve())
        and isinstance(lock["nonce"], str) and len(lock["nonce"]) >= 16
    )


def _delayed_exit(lock_file: Path, owner: dict[str, Any] | None) -> None:
    time.sleep(0.25)
    _remove_lock(lock_file, expected=owner)
    os._exit(0)


def _server_url(port: int, suffix: str = "") -> str:
    return f"http://{DEFAULT_HOST}:{port}{suffix}"


def _health_matches(
    payload: Any, *, project: Path | None = None, pid: int | None = None, nonce: str | None = None,
) -> bool:
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return False
    if project is None and pid is None and nonce is None:
        return True
    return (
        payload.get("project") == str(project.resolve()) if project is not None else True
    ) and (payload.get("pid") == pid if pid is not None else True) and (
        payload.get("nonce") == nonce if nonce is not None else True
    )


def _probe_health(
    port: int, *, project: Path | None = None, pid: int | None = None, nonce: str | None = None,
) -> bool:
    try:
        with LOOPBACK_OPENER.open(_server_url(port, "/api/health"), timeout=0.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status == 200 and _health_matches(
                payload, project=project, pid=pid, nonce=nonce,
            )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, urllib.error.URLError):
        return False


def _owner_healthy(lock: Any, project: Path) -> bool:
    return bool(
        _valid_owner(lock, project)
        and _process_alive(lock["pid"])
        and _probe_health(
            lock["port"], project=project, pid=lock["pid"], nonce=lock["nonce"],
        )
    )


def _wait_health(
    port: int,
    process: subprocess.Popen[Any],
    timeout: float = 10,
    *,
    expected_project: Path | None = None,
    expected_pid: int | None = None,
    expected_nonce: str | None = None,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if _probe_health(
            port, project=expected_project, pid=expected_pid, nonce=expected_nonce,
        ):
            return True
        time.sleep(0.1)
    return False


def _acquire_start_lock(project: Path, timeout: float = 12) -> tuple[Path, dict[str, Any]] | None:
    path = project / START_LOCK_NAME
    record = {"pid": os.getpid(), "token": secrets.token_hex(16)}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _claim_lock(path, record):
            return path, record
        current = _read_lock(path)
        if isinstance(current, dict) and not _process_alive(current.get("pid")):
            _remove_lock(path, expected=current)
            continue
        time.sleep(0.05)
    return None


def _start(project: Path, port: int, no_browser: bool, idle_timeout: int) -> int:
    if not (project / CONFIRM_DIR / RECOMMENDATIONS).is_file():
        LOGGER.error("%s is missing", project / CONFIRM_DIR / RECOMMENDATIONS)
        return 1
    try:
        _project_facts(project)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.error("%s", exc)
        return 1
    with _START_THREAD_LOCK:
        acquired = _acquire_start_lock(project)
        if acquired is None:
            LOGGER.error("timed out acquiring confirmation UI lifecycle ownership")
            return 1
        start_path, start_record = acquired
        try:
            lock_file = project / LOCK_NAME
            lock = _read_lock(lock_file)
            if _owner_healthy(lock, project):
                url = _server_url(int(lock["port"]))
                print(json.dumps({
                    "status": "already_running", "url": url, "pid": lock["pid"],
                }, ensure_ascii=False))
                return 0
            if lock is not None:
                if _process_alive(lock.get("pid")):
                    LOGGER.error("confirmation UI lock is owned by an unverified live process")
                    return 1
                _remove_lock(lock_file, expected=lock)

            nonce = secrets.token_hex(24)
            log_path = project / CONFIRM_DIR / "server.log"
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "serve",
                "--project",
                str(project),
                "--port",
                str(port),
                "--idle-timeout",
                str(idle_timeout),
                "--nonce",
                nonce,
            ]
            creationflags = 0
            kwargs: dict[str, Any] = {}
            if os.name == "nt":
                creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            with log_path.open("a", encoding="utf-8") as log:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                    **kwargs,
                )
            spawned_owner = _lock_for(project, pid=process.pid, port=port, nonce=nonce)
            if not _claim_lock(lock_file, spawned_owner):
                existing_owner = _read_lock(lock_file)
                if not _same_lock(existing_owner, spawned_owner):
                    try:
                        if process.poll() is None:
                            process.terminate()
                            process.wait(timeout=2)
                    except (OSError, subprocess.SubprocessError, AttributeError):
                        pass
                    LOGGER.error("confirmation UI ownership was claimed by another process")
                    return 1
            if not _wait_health(
                port,
                process,
                expected_project=project,
                expected_pid=process.pid,
                expected_nonce=nonce,
            ):
                try:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=2)
                except (OSError, subprocess.SubprocessError, AttributeError):
                    pass
                if process.poll() is None:
                    LOGGER.error(
                        "confirmation UI startup failed and its process could not be stopped; "
                        "preserving the owner lock"
                    )
                else:
                    _remove_lock(lock_file, expected=spawned_owner)
                LOGGER.error("confirmation UI did not become healthy with expected ownership; see %s", log_path)
                return 1
            url = _server_url(port)
            if not no_browser:
                webbrowser.open(url)
            print(json.dumps({"status": "started", "url": url, "pid": process.pid}, ensure_ascii=False))
            return 0
        finally:
            _remove_lock(start_path, expected=start_record)


def _wait(project: Path, stage: str, timeout: int) -> int:
    target = {"stage1": 1, "stage2": 2, "final": 4}[stage]
    result_path = project / CONFIRM_DIR / RESULT
    deadline = None if timeout <= 0 else time.monotonic() + timeout
    unhealthy_since: float | None = None
    while True:
        if _confirmed_stage(result_path) >= target:
            if target == 4:
                # A final UI acknowledgement is not yet an executable style
                # contract.  Freeze it at the wait boundary so both the
                # documented confirm-ui flow and the one-command runner enter
                # production with the same immutable identity.
                if (project / "workflow_v6.json").is_file():
                    from workflow_v6_state import load as load_v6, save as save_v6
                    with mutation_lock(project):
                        live = _read_json(result_path)
                        revision = live.get("revision")
                        digest = hashlib.sha256(json.dumps(
                            live, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                        ).encode("utf-8")).hexdigest()
                        if type(revision) is not int or revision < 1:
                            raise ValueError("V6 wait requires a valid confirmed UI revision")
                        state = load_v6(project)
                        existing_revision = state.get("confirmed_ui_revision")
                        existing_digest = state.get("confirmed_ui_digest")
                        if existing_revision is not None and (
                            existing_revision != revision or existing_digest != digest
                        ):
                            raise ValueError("V6 live confirmation revision changed before sealing")
                        if existing_revision is None:
                            if state.get("page_materials_status") != "pre_confirmation":
                                raise ValueError(
                                    "V6 initial visual seal requires pre_confirmation materials state"
                                )
                            visual_contract = live.get("global_visual_contract", live)
                            state["style_confirmation"] = {
                                "status": "confirmed",
                                "contract": {
                                    field: _clean(visual_contract[field])
                                    for field in VISUAL_FIELDS if field in visual_contract
                                },
                            }
                            state["confirmed_ui_revision"] = revision
                            state["confirmed_ui_digest"] = digest
                            state["page_materials_status"] = "pending"
                            save_v6(project, state)
                else:
                    from style_contract import freeze_style_contract

                    freeze_style_contract(project)
            print(result_path)
            return 0
        lock = _read_lock(project / LOCK_NAME)
        if _owner_healthy(lock, project):
            unhealthy_since = None
        elif _valid_owner(lock, project) and _process_alive(lock["pid"]):
            unhealthy_since = unhealthy_since or time.monotonic()
            if time.monotonic() - unhealthy_since >= 2:
                LOGGER.error("confirmation UI owner stopped responding")
                return 1
        else:
            LOGGER.error("confirmation UI is not running")
            return 1
        if deadline is not None and time.monotonic() >= deadline:
            LOGGER.error("timed out waiting for %s confirmation", stage)
            return 124
        time.sleep(0.25)


def _shutdown(project: Path) -> int:
    lock_file = project / LOCK_NAME
    lock = _read_lock(lock_file)
    if not lock:
        print(json.dumps({"status": "stopped"}))
        return 0
    if not _process_alive(lock.get("pid")):
        _remove_lock(lock_file, expected=lock)
        print(json.dumps({"status": "stopped"}))
        return 0
    if not _owner_healthy(lock, project):
        LOGGER.error("refusing to stop an unauthenticated confirmation UI owner")
        return 1
    port = int(lock.get("port", DEFAULT_PORT))
    try:
        request_data = urllib.request.Request(
            _server_url(port, "/api/shutdown"),
            data=b"{}",
            headers={"Content-Type": "application/json", "X-Confirm-Nonce": str(lock["nonce"])},
            method="POST",
        )
        LOOPBACK_OPENER.open(request_data, timeout=2).close()
    except (OSError, urllib.error.URLError):
        try:
            os.kill(int(lock["pid"]), signal.SIGTERM)
        except (OSError, TypeError, ValueError):
            pass
    deadline = time.monotonic() + 3
    while _process_alive(lock.get("pid")) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _process_alive(lock.get("pid")):
        LOGGER.error("confirmation UI did not stop; preserving its owner lock")
        return 1
    _remove_lock(lock_file, expected=lock)
    print(json.dumps({"status": "stopped"}))
    return 0


def _serve(project: Path, port: int, idle_timeout: int, *, nonce: str) -> int:
    lock_file = project / LOCK_NAME
    owner = _lock_for(project, pid=os.getpid(), port=port, nonce=nonce)
    if not _claim_lock(lock_file, owner):
        existing = _read_lock(lock_file)
        if _same_lock(existing, owner):
            pass
        elif (
            os.name == "nt"
            and _valid_owner(existing, project)
            and os.getppid() == existing["pid"]
            and existing["port"] == port
            and existing["nonce"] == nonce
            and _process_alive(existing["pid"])
        ):
            # On Windows a virtual-environment python.exe can remain as the
            # launcher while a second interpreter PID runs this server.  The
            # parent reserves ownership with the launcher PID and a fresh
            # nonce before spawning; adopt that exact launch identity so the
            # supervisor remains the lifecycle owner reported by health.
            owner = existing
        elif existing is not None and not _process_alive(existing.get("pid")):
            _remove_lock(lock_file, expected=existing)
            if not _claim_lock(lock_file, owner):
                LOGGER.error("confirmation UI ownership was claimed concurrently")
                return 1
        else:
            LOGGER.error("confirmation UI ownership is already held")
            return 1
    try:
        create_app(
            str(project),
            idle_timeout,
            lock_file=lock_file,
            server_port=port,
            lock_owner=owner,
        ).run(host=DEFAULT_HOST, port=port, debug=False, use_reloader=False)
    finally:
        _remove_lock(lock_file, expected=owner)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="start the browser confirmation session")
    start.add_argument("--project", type=Path, required=True)
    start.add_argument("--port", type=int, default=DEFAULT_PORT)
    start.add_argument("--no-browser", action="store_true")
    start.add_argument("--idle-timeout", type=int, default=900)

    wait = subparsers.add_parser("wait", help="wait for a stage confirmation")
    wait.add_argument("--project", type=Path, required=True)
    wait.add_argument("--stage", choices=("stage1", "stage2", "final"), default="final")
    wait.add_argument("--timeout", type=int, default=590)

    shutdown = subparsers.add_parser("shutdown", help="stop the browser confirmation session")
    shutdown.add_argument("--project", type=Path, required=True)

    serve = subparsers.add_parser("serve", help=argparse.SUPPRESS)
    serve.add_argument("--project", type=Path, required=True)
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--idle-timeout", type=int, default=900)
    serve.add_argument("--nonce", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    project = args.project.resolve()
    if not project.is_dir():
        LOGGER.error("project directory does not exist: %s", project)
        return 1
    if args.command == "start":
        return _start(project, args.port, args.no_browser, args.idle_timeout)
    if args.command == "wait":
        return _wait(project, args.stage, args.timeout)
    if args.command == "shutdown":
        return _shutdown(project)
    return _serve(project, args.port, args.idle_timeout, nonce=args.nonce)


if __name__ == "__main__":
    raise SystemExit(main())

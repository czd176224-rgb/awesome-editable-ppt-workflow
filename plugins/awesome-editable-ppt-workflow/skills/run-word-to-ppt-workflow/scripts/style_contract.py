"""Freeze the three-step visual confirmation into compact executable artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, NamedTuple

from jsonschema import Draft202012Validator

from approved_visual import render_ui_preview_audit
from fixed_region_contract import SLIDE_SIZE_IN, fixed_frame_execution
from body_image_profile import body_image_profile
from current_ui_adapter import adapt_current_ui_payload
from page_complexity import classify_page
from adaptive_scheduler import PROFILE_CONCURRENCY


EDITPPT_CLI = Path(__file__).resolve().parents[2] / "reconstruct-editable-slide" / "cli"
if str(EDITPPT_CLI) not in sys.path:
    sys.path.append(str(EDITPPT_CLI))

from editppt.runtime import workflow_state_store  # noqa: E402
from editppt.runtime.project_paths import project_output_path, require_project_file  # noqa: E402


STYLE_DIR = "02_style"
CONFIRMATION_FILE = "style_confirmation.json"
EXECUTION_FILE = "style_execution.json"
EXECUTION_HASH_FILE = "style_execution.sha256"
UI_PREVIEW_AUDIT_FILE = "ui_preview_audit.png"
UI_PREVIEW_AUDIT_HASH_FILE = "ui_preview_audit.sha256"

CANVAS_PROFILES = {
    "ppt169": {
        "aspect_ratio": "16:9",
        "slide_width_inches": SLIDE_SIZE_IN["w"],
        "slide_height_inches": SLIDE_SIZE_IN["h"],
        "fit": "reconstruct_to_body",
        "coordinate_space": "dynamic_source_normalized",
        "allow_crop": False,
    },
}


def _activate_confirmed_page_jobs(project: Path, state: dict[str, Any]) -> None:
    """Commit the confirmation boundary without deferring it to status/resume.

    A confirmed style and pages that still say ``pending_style_confirmation``
    describe mutually inconsistent project state.  Resolve that transition in
    the same atomic state replacement that records the confirmed style.  The
    status endpoint can then remain read-only and never re-read every page
    contract merely to discover launch capacity.
    """
    jobs = state.get("jobs")
    if not isinstance(jobs, list) or not jobs or any(not isinstance(job, dict) for job in jobs):
        raise ValueError("style confirmation requires locked page jobs")
    for job in jobs:
        if job.get("status") != "pending_style_confirmation":
            raise ValueError("pending style confirmation contains a page in an invalid state")
        relative = job.get("contract_file")
        if not isinstance(relative, str):
            raise ValueError("page contract identity is missing at style confirmation")
        contract_path = require_project_file(project, relative)
        contract = _read_object(contract_path)
        if contract.get("page_number") != job.get("page_number"):
            raise ValueError("page contract does not match its style-confirmation job")
        job["complexity_weight"] = classify_page(contract).weight
        job["status"] = "queued"


def resolve_canvas_profile(canvas: str) -> dict[str, Any]:
    try:
        return dict(CANVAS_PROFILES[canvas])
    except (KeyError, TypeError) as exc:
        raise ValueError("fixed-canvas-cm-v2 only supports ppt169") from exc


def canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _schema(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "schemas" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(instance: dict[str, Any], schema_name: str) -> None:
    errors = sorted(
        Draft202012Validator(_schema(schema_name)).iter_errors(instance),
        key=lambda error: list(error.path),
    )
    if errors:
        raise ValueError(f"{schema_name} validation failed: {errors[0].message}")


def canonical_confirmation(confirmed: dict[str, Any]) -> dict[str, Any]:
    snapshot = adapt_current_ui_payload(confirmed)
    _validate(snapshot, "style_confirmation.schema.json")
    return snapshot


def compile_style_execution(confirmed: dict[str, Any]) -> dict[str, Any]:
    snapshot = canonical_confirmation(confirmed)
    palette = snapshot["color"].get("palette") if isinstance(snapshot["color"], dict) else None
    title_color = palette.get("primary") if isinstance(palette, dict) else None
    if not isinstance(title_color, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", title_color):
        raise ValueError("confirmed palette primary/title color must be a six-digit HEX color")
    execution = {
        "schema_version": "2.0",
        "canvas": snapshot["canvas"],
        "canvas_profile": resolve_canvas_profile(snapshot["canvas"]),
        "body_image_profile": body_image_profile(snapshot["production_profile"]),
        "hard_constraints": {
            "content_fidelity": "preserve_information_and_logic",
            "one_page_to_one_slide": True,
            "title_color": title_color.upper(),
            "palette": snapshot["color"]["palette"],
            "typography": snapshot["typography"],
        },
        "fixed_frame": {"title_color": title_color.upper(), **fixed_frame_execution()},
        "soft_preferences": {
            field: snapshot[field]
            for field in (
                "direction",
                "template_selection",
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
                "additional_requirements",
            )
        },
        "creative_freedom": {
            "layout": True,
            "composition": True,
            "visual_hierarchy": True,
            "content_visualization": True,
            "page_specific_emphasis": True,
        },
    }
    _validate(execution, "style_execution.schema.json")
    return execution


def _atomic_write(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(contents)
    os.replace(temporary, path)


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read JSON object: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _assert_immutable(path: Path, expected: bytes) -> None:
    if path.exists() and path.read_bytes() != expected:
        raise ValueError(f"immutable style artifact already differs: {path}")


def _is_link_or_reparse(path: Path) -> bool:
    info = path.lstat()
    return bool(
        path.is_symlink()
        or getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _require_regular_unlinked(path: Path) -> Path:
    info = path.lstat()
    if _is_link_or_reparse(path) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError(f"style revision artifact must be a regular unlinked file: {path}")
    return path


class _CreateOnceResult(NamedTuple):
    newly_created: bool
    identity: tuple[int, int] | None


def _create_once(path: Path, contents: bytes) -> _CreateOnceResult:
    """Publish immutable bytes without ever replacing an existing pathname."""
    if os.path.lexists(path):
        _require_regular_unlinked(path)
        if path.read_bytes() != contents:
            raise ValueError(f"immutable style revision already differs: {path}")
        return _CreateOnceResult(False, None)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{os.urandom(8).hex()}.tmp"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= (
        getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(temporary, flags, 0o600)
        try:
            offset = 0
            while offset < len(contents):
                written = os.write(descriptor, contents[offset:])
                if written <= 0:
                    raise OSError("immutable style revision temporary write made no progress")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(temporary, path)
        except FileExistsError:
            _require_regular_unlinked(path)
            if path.read_bytes() != contents:
                raise ValueError(f"immutable style revision already differs: {path}")
            newly_created = False
        else:
            newly_created = True
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    _require_regular_unlinked(path)
    if not newly_created:
        return _CreateOnceResult(False, None)
    info = path.lstat()
    return _CreateOnceResult(True, (int(info.st_dev), int(info.st_ino)))


def _rollback_new_style_artifact(
    path: Path, contents: bytes, identity: tuple[int, int],
) -> None:
    """Remove only the unchanged regular file created by this revision attempt."""
    try:
        info = path.lstat()
        if (
            _is_link_or_reparse(path)
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (int(info.st_dev), int(info.st_ino)) != identity
            or path.read_bytes() != contents
        ):
            return
        latest = path.lstat()
        if (
            _is_link_or_reparse(path)
            or not stat.S_ISREG(latest.st_mode)
            or latest.st_nlink != 1
            or (int(latest.st_dev), int(latest.st_ino)) != identity
        ):
            return
        path.unlink()
    except FileNotFoundError:
        return


def _versions_dir(project: Path) -> Path:
    versions = project_output_path(project, f"{STYLE_DIR}/versions")
    if os.path.lexists(versions):
        info = versions.lstat()
        if _is_link_or_reparse(versions) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("style versions path must be a project-local plain directory")
    else:
        versions.mkdir(parents=True)
    return project_output_path(project, versions)


def _gate_file(project: Path, gate: dict[str, Any], field: str) -> Path:
    value = gate.get(field)
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError(f"confirmed style gate {field} is invalid")
    return require_project_file(project, project_output_path(project, value))


def _verify_confirmed_gate(project: Path, gate: Any) -> None:
    base_fields = {
        "status", "confirmed_at", "confirmation_file", "execution_file", "execution_sha256",
        "ui_preview_audit_file", "ui_preview_audit_sha256",
    }
    if not isinstance(gate, dict) or gate.get("status") != "confirmed":
        raise ValueError("style revision requires an existing confirmed style gate")
    if frozenset(gate) not in {frozenset(base_fields), frozenset(base_fields | {"confirmation_sha256"})}:
        raise ValueError("confirmed style gate fields are invalid")
    execution_sha = gate.get("execution_sha256")
    preview_sha = gate.get("ui_preview_audit_sha256")
    if not isinstance(execution_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", execution_sha):
        raise ValueError("confirmed style execution SHA-256 is invalid")
    if not isinstance(preview_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", preview_sha):
        raise ValueError("confirmed UI preview SHA-256 is invalid")

    confirmation_path = _gate_file(project, gate, "confirmation_file")
    execution_path = _gate_file(project, gate, "execution_file")
    preview_path = _gate_file(project, gate, "ui_preview_audit_file")
    confirmation_bytes = confirmation_path.read_bytes()
    confirmed = canonical_confirmation(_read_object(confirmation_path))
    if confirmation_bytes != canonical_json_bytes(confirmed):
        raise ValueError("confirmed style confirmation is not canonical")
    confirmation_sha = hashlib.sha256(confirmation_bytes).hexdigest()
    if "confirmation_sha256" in gate and gate["confirmation_sha256"] != confirmation_sha:
        raise ValueError("confirmed style confirmation SHA-256 mismatch")
    if gate.get("confirmed_at") != confirmed.get("confirmed_at"):
        raise ValueError("confirmed style timestamp does not match its confirmation artifact")

    execution_bytes = execution_path.read_bytes()
    if hashlib.sha256(execution_bytes).hexdigest() != execution_sha:
        raise ValueError("confirmed style execution SHA-256 mismatch")
    if execution_bytes != canonical_json_bytes(compile_style_execution(confirmed)):
        raise ValueError("confirmed style execution does not match its confirmation")
    preview_bytes = preview_path.read_bytes()
    if hashlib.sha256(preview_bytes).hexdigest() != preview_sha:
        raise ValueError("confirmed UI preview SHA-256 mismatch")
    title, body = _first_page_source(project)
    if preview_bytes != render_ui_preview_audit(confirmed, title, body):
        raise ValueError("confirmed UI preview does not match its confirmation")


def _first_page_source(project: Path) -> tuple[str, str]:
    contract = _read_object(project / "01_page_contracts" / "page_001.json")
    source = contract.get("source_text")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("first locked page source text is missing")
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    if lines and re.fullmatch(r"第\s*1\s*页", lines[0]):
        lines = lines[1:]
    return (lines[0] if lines else "演示文稿视觉系统", source)


def revise_style_contract(project: Path, confirmed_result: dict[str, Any]) -> dict[str, Any]:
    """Create an immutable style revision and atomically repoint the live gate."""
    project = Path(project).resolve()
    with workflow_state_store.project_state_lock(project):
        state = workflow_state_store.load_state(project)
        _verify_confirmed_gate(project, state.get("style_confirmation"))

        confirmed = canonical_confirmation(confirmed_result)
        scheduler = state.get("scheduler", {})
        runtime = state.get("runtime", {})
        live_values = {
            "max_concurrency": scheduler.get("configured_max"),
            "generation_mode": runtime.get("generation_mode"),
            "image_quality": runtime.get("image_quality"),
            "automatic_repair_budget": runtime.get("automatic_repair_budget"),
        }
        if any(confirmed[field] != value for field, value in live_values.items()):
            raise ValueError(
                "style-only revision cannot change live scheduler or runtime settings"
            )
        execution = compile_style_execution(confirmed)
        confirmation_bytes = canonical_json_bytes(confirmed)
        execution_bytes = canonical_json_bytes(execution)
        confirmation_sha = hashlib.sha256(confirmation_bytes).hexdigest()
        execution_sha = hashlib.sha256(execution_bytes).hexdigest()
        title, body = _first_page_source(project)
        preview_bytes = render_ui_preview_audit(confirmed, title, body)
        preview_sha = hashlib.sha256(preview_bytes).hexdigest()

        versions = _versions_dir(project)
        confirmation_path = project_output_path(
            project, versions / f"style_confirmation_{confirmation_sha}.json",
        )
        execution_path = project_output_path(
            project, versions / f"style_execution_{execution_sha}.json",
        )
        preview_path = project_output_path(
            project, versions / f"ui_preview_audit_{preview_sha}.png",
        )
        artifacts = (
            (confirmation_path, confirmation_bytes),
            (execution_path, execution_bytes),
            (preview_path, preview_bytes),
        )
        newly_created: list[tuple[Path, bytes, tuple[int, int]]] = []
        try:
            for path, contents in artifacts:
                created = _create_once(path, contents)
                if created.newly_created:
                    assert created.identity is not None
                    newly_created.append((path, contents, created.identity))

            gate = {
                "status": "confirmed",
                "confirmed_at": confirmed["confirmed_at"],
                "confirmation_file": confirmation_path.relative_to(project).as_posix(),
                "confirmation_sha256": confirmation_sha,
                "execution_file": execution_path.relative_to(project).as_posix(),
                "execution_sha256": execution_sha,
                "ui_preview_audit_file": preview_path.relative_to(project).as_posix(),
                "ui_preview_audit_sha256": preview_sha,
            }
            result = {
                "confirmation": confirmed,
                "execution": execution,
                "sha256": execution_sha,
                "gate": gate,
                "style_confirmation_path": str(confirmation_path),
                "style_execution_path": str(execution_path),
                "ui_preview_audit_path": str(preview_path),
            }
            if state["style_confirmation"] == gate:
                return result
            state["style_confirmation"] = gate
            workflow_state_store.replace_state(project, state)
            return result
        except BaseException:
            for path, contents, identity in reversed(newly_created):
                _rollback_new_style_artifact(path, contents, identity)
            raise


def freeze_style_contract(project: Path) -> dict[str, Any]:
    project = Path(project).resolve()
    with workflow_state_store.project_state_lock(project):
        result_path = project / "confirm_ui" / "result.json"
        if not result_path.is_file():
            raise FileNotFoundError(f"final confirmation is missing: {result_path}")

        confirmed = canonical_confirmation(_read_object(result_path))
        title, body = _first_page_source(project)
        ui_preview_audit_bytes = render_ui_preview_audit(confirmed, title, body)
        ui_preview_audit_sha256 = hashlib.sha256(ui_preview_audit_bytes).hexdigest()
        execution = compile_style_execution(confirmed)
        confirmation_bytes = canonical_json_bytes(confirmed)
        execution_bytes = canonical_json_bytes(execution)
        digest = hashlib.sha256(execution_bytes).hexdigest()
        style_dir = project / STYLE_DIR
        artifacts = (
            (style_dir / CONFIRMATION_FILE, confirmation_bytes),
            (style_dir / EXECUTION_FILE, execution_bytes),
            (style_dir / EXECUTION_HASH_FILE, (digest + "\n").encode("ascii")),
            (style_dir / UI_PREVIEW_AUDIT_FILE, ui_preview_audit_bytes),
            (style_dir / UI_PREVIEW_AUDIT_HASH_FILE, (ui_preview_audit_sha256 + "\n").encode("ascii")),
        )
        frozen_result = {
            "confirmation": confirmed,
            "execution": execution,
            "sha256": digest,
            "style_confirmation_path": str(style_dir / CONFIRMATION_FILE),
            "style_execution_path": str(style_dir / EXECUTION_FILE),
            "ui_preview_audit_path": str(style_dir / UI_PREVIEW_AUDIT_FILE),
        }
        state = _read_object(project / "workflow_run.json")
        gate = state.get("style_confirmation")
        if not isinstance(gate, dict) or gate.get("status") not in {"pending", "confirmed"}:
            raise ValueError("style confirmation state is invalid")
        expected_gate = {
            "status": "confirmed",
            "confirmed_at": confirmed["confirmed_at"],
            "confirmation_file": f"{STYLE_DIR}/{CONFIRMATION_FILE}",
            "execution_file": f"{STYLE_DIR}/{EXECUTION_FILE}",
            "execution_sha256": digest,
            "ui_preview_audit_file": f"{STYLE_DIR}/{UI_PREVIEW_AUDIT_FILE}",
            "ui_preview_audit_sha256": ui_preview_audit_sha256,
        }
        if gate.get("status") == "confirmed":
            if gate != expected_gate:
                raise ValueError("confirmed style gate does not match the immutable confirmation result")
            for path, contents in artifacts:
                if not path.is_file():
                    raise ValueError(f"confirmed immutable style artifact is missing: {path}")
                _assert_immutable(path, contents)
            return frozen_result

        for path, contents in artifacts:
            _assert_immutable(path, contents)
            _atomic_write(path, contents)
        state["style_confirmation"] = expected_gate
        state["scheduler"] = {
            "concurrency": PROFILE_CONCURRENCY[confirmed["production_profile"]],
            "configured_max": PROFILE_CONCURRENCY[confirmed["production_profile"]],
            "last_trigger": "style_confirmation",
        }
        state["runtime"] = {
            "generation_mode": confirmed["generation_mode"],
            "image_quality": confirmed["image_quality"],
            "automatic_repair_budget": confirmed["automatic_repair_budget"],
        }
        _activate_confirmed_page_jobs(project, state)
        workflow_state_store.replace_state(project, state)
        return frozen_result

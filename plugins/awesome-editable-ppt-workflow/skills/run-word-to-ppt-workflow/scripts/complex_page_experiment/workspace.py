"""Read-only baseline capture and isolated project copying."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from awesome_page_materials import validate_page_materials
from awesome_attachment_render import SUPPORTED_DOCUMENTS, SUPPORTED_IMAGES
from workflow_v6_contract import validate_material_receipts, validate_project
from workflow_v6_secure_io import reject_reparse_chain
from workflow_v6_state import load


SNAPSHOT_FILE = "source_snapshot.json"
PROJECT_DIRECTORY = "project"


@dataclass(frozen=True)
class ExperimentWorkspace:
    experiment_id: str
    source_project: Path
    experiment_root: Path
    project_copy: Path
    page_number: int
    source_snapshot_sha256: str


def _is_reparse(path: Path, metadata: os.stat_result | None = None) -> bool:
    metadata = metadata or path.lstat()
    return path.is_symlink() or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _file_record(path: Path, relative: str) -> dict[str, object]:
    before = path.lstat()
    if _is_reparse(path, before) or not stat.S_ISREG(before.st_mode):
        raise ValueError(f"project contains a reparse or non-regular file: {relative}")
    data = path.read_bytes()
    after = path.lstat()
    if _is_reparse(path, after) or not os.path.samestat(before, after):
        raise ValueError(f"project file changed during fingerprinting: {relative}")
    return {
        "path": relative,
        "sha256": hashlib.sha256(data).hexdigest(),
        "byte_size": len(data),
    }


def fingerprint_project(project_root: Path) -> dict[str, object]:
    """Return sorted regular-file records and a canonical tree digest."""
    literal_root = Path(os.path.abspath(project_root))
    reject_reparse_chain(literal_root)
    root = literal_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("project root must be a directory")

    files: list[dict[str, object]] = []

    def visit(directory: Path) -> None:
        for entry in sorted(os.scandir(directory), key=lambda item: item.name):
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            metadata = entry.stat(follow_symlinks=False)
            if entry.is_symlink() or _is_reparse(path, metadata):
                raise ValueError(f"project contains a reparse point: {relative}")
            if stat.S_ISDIR(metadata.st_mode):
                visit(path)
            elif stat.S_ISREG(metadata.st_mode):
                files.append(_file_record(path, relative))
            else:
                raise ValueError(f"project contains a non-regular file: {relative}")

    visit(root)
    files.sort(key=lambda item: str(item["path"]))
    canonical = json.dumps(
        files, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {"files": files, "tree_sha256": hashlib.sha256(canonical).hexdigest()}


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _paths_overlap(left: Path, right: Path) -> bool:
    left_text = os.path.normcase(str(_absolute(left)))
    right_text = os.path.normcase(str(_absolute(right)))
    try:
        common = os.path.commonpath((left_text, right_text))
    except ValueError:
        return False
    return common in {left_text, right_text}


def _resolved_for_overlap(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError as exc:
        raise ValueError("source project and experiment target overlap cannot be resolved") from exc


def _load_valid_baseline(source: Path) -> dict[str, Any]:
    state_path = source / "workflow_v6.json"
    if not state_path.is_file():
        raise ValueError("source is missing the current Awesome workflow state")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("source has invalid current Awesome workflow state") from exc
    if not isinstance(state, dict):
        raise ValueError("source has invalid current Awesome workflow state")
    validate_project(state)
    validate_material_receipts(source, state)
    _verify_source_record(source, state["word_source"], "Word source")
    _verify_source_record(source, state["logo_source"], "logo source")
    if len(state["pages"]) != 4:
        raise ValueError("complex-page experiment requires the full four-page Awesome baseline")
    if state["page_materials_status"] != "confirmed" or any(
        page["material_state"] != "available" or page["material_receipt"] is None
        for page in state["pages"]
    ):
        raise ValueError("complex-page experiment requires confirmed durable page materials")
    for page in state["pages"]:
        material_path = source / page["material_receipt"]["path"]
        try:
            material = json.loads(material_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("source has invalid Awesome page materials") from exc
        if not isinstance(material, dict):
            raise ValueError("source has invalid Awesome page materials")
        validate_page_materials(material)
        if material["page_number"] != page["page_number"]:
            raise ValueError("Awesome page material identity does not match workflow state")
        for record in material["word_images"]:
            _verify_source_record(source, record, "Word image")
        for record in material["attachment_inputs"]:
            _verify_source_record(source, record, "attachment source")
            receipt = record.get("render_receipt")
            if receipt is None:
                if Path(str(record["path"])).suffix.lower() in (
                    SUPPORTED_IMAGES | SUPPORTED_DOCUMENTS
                ):
                    raise ValueError("renderable attachment receipt is missing")
                continue
            if (
                receipt["original_path"] != record["path"]
                or receipt["original_sha256"] != record["sha256"]
                or receipt["original_byte_size"] != record["byte_size"]
            ):
                raise ValueError("attachment source digest does not match its render receipt")
            for rendered in [*receipt["pages"], receipt["contact_sheet"]]:
                _verify_source_record(source, rendered, "attachment render cache")
        if page["first_candidate"] is not None or page["selected_candidate"] is not None:
            raise ValueError("source baseline already contains candidate or acceptance output")
        if page["state"] in {
            "generating",
            "qa_review",
            "accepted",
            "reconstructing",
            "page_complete",
        }:
            raise ValueError("source baseline already contains candidate, acceptance, or reconstruction state")
    return state


def _verify_source_record(source: Path, record: dict[str, Any], label: str) -> None:
    relative = record.get("path")
    if not isinstance(relative, str) or "\\" in relative:
        raise ValueError(f"{label} path is invalid")
    posix = PurePosixPath(relative)
    if posix.is_absolute() or not posix.parts or any(
        part in {"", ".", ".."} for part in posix.parts
    ) or str(posix) != relative or ":" in posix.parts[0]:
        raise ValueError(f"{label} path is invalid")
    literal_path = source.joinpath(*posix.parts)
    try:
        reject_reparse_chain(literal_path)
        path = literal_path.resolve(strict=True)
        path.relative_to(source)
        observed = _file_record(path, relative)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} file is invalid") from exc
    if record.get("sha256") != observed["sha256"] or (
        "byte_size" in record and record.get("byte_size") != observed["byte_size"]
    ):
        raise ValueError(f"{label} digest or byte size does not match its file")


def _reject_existing_outputs(source: Path) -> None:
    output_locations = (
        ("prompt", source / "02_v6" / "page_image_prompts"),
        ("prompt", source / "03_v6" / "page_image_prompts"),
        ("candidate or acceptance", source / "04_v6"),
        ("reconstruction", source / "05_v6"),
        ("reconstruction", source / "06_v6"),
        ("reconstruction", source / "08_final"),
    )
    for kind, location in output_locations:
        if location.exists() and any(path.is_file() or path.is_symlink() for path in location.rglob("*")):
            raise ValueError(f"source baseline already contains {kind} output")


def _write_snapshot(
    experiment_root: Path,
    *,
    experiment_id: str,
    page_number: int,
    source_project: Path,
    fingerprint: dict[str, object],
) -> Path:
    snapshot = {
        "experiment_id": experiment_id,
        "experiment_scope": {
            f"page_{page_number:03d}": {"page_number": page_number}
        },
        "page_number": page_number,
        "source_project": str(source_project),
        "source_snapshot_sha256": fingerprint["tree_sha256"],
        "files": fingerprint["files"],
    }
    path = experiment_root / SNAPSHOT_FILE
    path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def create_experiment_copy(
    source_project: Path,
    experiment_root: Path,
    *,
    experiment_id: str,
    page_number: int = 1,
) -> ExperimentWorkspace:
    """Copy the full four-page Awesome baseline for one selected source page."""
    if not isinstance(experiment_id, str) or not experiment_id.strip():
        raise ValueError("experiment_id is required")
    if type(page_number) is not int or page_number < 1 or page_number > 4:
        raise ValueError("this experiment milestone authorizes pages 1 through 4")

    literal_source = _absolute(source_project)
    literal_target = _absolute(experiment_root)
    if _paths_overlap(literal_source, literal_target) or _paths_overlap(
        _resolved_for_overlap(literal_source), _resolved_for_overlap(literal_target)
    ):
        raise ValueError("source project and experiment target overlap")
    if os.path.lexists(literal_target):
        raise FileExistsError(f"experiment target already exists: {literal_target}")

    reject_reparse_chain(literal_source)
    source = literal_source.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("source project must be an existing directory")
    _load_valid_baseline(source)
    _reject_existing_outputs(source)
    source_fingerprint = fingerprint_project(source)

    literal_target.mkdir(parents=True, exist_ok=False)
    _write_snapshot(
        literal_target,
        experiment_id=experiment_id.strip(),
        page_number=page_number,
        source_project=source,
        fingerprint=source_fingerprint,
    )
    project_copy = literal_target / PROJECT_DIRECTORY
    # Retain, then reject, any link inserted after the source fingerprint rather
    # than allowing copytree to dereference content outside the baseline.
    shutil.copytree(source, project_copy, symlinks=True)

    copied_state = _load_valid_baseline(project_copy)
    validate_project(copied_state)
    copied_fingerprint = fingerprint_project(project_copy)
    if copied_fingerprint != source_fingerprint:
        raise RuntimeError("experiment copy does not match the source snapshot")

    workspace = ExperimentWorkspace(
        experiment_id=experiment_id.strip(),
        source_project=source,
        experiment_root=literal_target.resolve(strict=True),
        project_copy=project_copy.resolve(strict=True),
        page_number=page_number,
        source_snapshot_sha256=str(source_fingerprint["tree_sha256"]),
    )
    verify_source_unchanged(workspace)
    return workspace


def open_live_page_workspace(project: Path, page_number: int) -> ExperimentWorkspace:
    """Open one current workflow page in place without copying the project."""
    literal_project = _absolute(project)
    reject_reparse_chain(literal_project)
    project_root = literal_project.resolve(strict=True)
    if not project_root.is_dir():
        raise ValueError("live project must be an existing directory")
    state = load(project_root)
    pages = state["pages"]
    if type(page_number) is not int or page_number < 1 or page_number > len(pages):
        raise ValueError("live page number is out of range for the current project")

    experiment_id = f"live-page-{page_number:03d}"
    experiment_root = project_root / "04_v6" / "experiments" / experiment_id
    experiment_root.mkdir(parents=True, exist_ok=True)
    return ExperimentWorkspace(
        experiment_id=experiment_id,
        source_project=project_root,
        experiment_root=experiment_root.resolve(strict=True),
        project_copy=project_root,
        page_number=page_number,
        source_snapshot_sha256=str(state["source_identity"]),
    )


def verify_source_unchanged(workspace: ExperimentWorkspace) -> None:
    """Raise if the source no longer matches the captured source snapshot."""
    if workspace.source_project == workspace.project_copy:
        try:
            state = load(workspace.project_copy)
        except (OSError, ValueError) as exc:
            raise RuntimeError("live project source identity is missing or invalid") from exc
        if state["source_identity"] != workspace.source_snapshot_sha256:
            raise RuntimeError("live project source identity changed during page processing")
        return
    snapshot_path = workspace.experiment_root / SNAPSHOT_FILE
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("source snapshot is missing or invalid") from exc
    expected = {
        "files": snapshot.get("files"),
        "tree_sha256": snapshot.get("source_snapshot_sha256"),
    }
    if expected["tree_sha256"] != workspace.source_snapshot_sha256:
        raise RuntimeError("source snapshot identity does not match the workspace")
    try:
        current = fingerprint_project(workspace.source_project)
    except (OSError, ValueError) as exc:
        raise RuntimeError("source project changed after experiment creation") from exc
    if current != expected:
        raise RuntimeError("source project changed after experiment creation")

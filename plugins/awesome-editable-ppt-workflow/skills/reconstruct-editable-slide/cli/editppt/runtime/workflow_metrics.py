"""Revisioned, project-local metrics derived only from saved workflow state."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from pathlib import Path
from typing import Any, Mapping

try:
    from .project_paths import (
        ProjectPathError,
        project_output_path,
        require_plain_project,
        require_project_file,
    )
except ImportError:  # direct runtime script entrypoint
    from project_paths import (
        ProjectPathError,
        project_output_path,
        require_plain_project,
        require_project_file,
    )


PIPELINE_METRICS_FILE = Path("09_reports") / "pipeline_metrics.json"
SNAPSHOT_ROOT = Path("09_reports") / "pipeline_metrics_snapshots"


def _count(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def page_metrics(
    job: Mapping[str, Any],
    selected_evidence: Mapping[str, Any] | None = None,
    *,
    selected_evidence_consistent: bool = True,
    selected_evidence_error: str | None = None,
) -> dict[str, Any]:
    selected_evidence = selected_evidence or {}
    qa = job.get("qa_result") if isinstance(job.get("qa_result"), Mapping) else {}
    coverage = job.get("coverage_result") if isinstance(job.get("coverage_result"), Mapping) else {}
    mapping = job.get("body_image_mapping") if isinstance(job.get("body_image_mapping"), Mapping) else {}
    failure = job.get("page_failure") if isinstance(job.get("page_failure"), Mapping) else {}
    generation_calls = _count(job.get("generation_calls"))
    return {
        "page_number": job.get("page_number"),
        "status": job.get("status", "unknown"),
        "route": job.get("route", "unresolved"),
        "input_evidence_chars": selected_evidence.get("available_chars", 0),
        "selected_evidence_chars": selected_evidence.get("selected_chars", 0),
        "selected_evidence_consistent": selected_evidence_consistent,
        "selected_evidence_error": selected_evidence_error,
        "generation_calls": generation_calls,
        "reconstruction_calls": _count(job.get("reconstruction_calls")),
        "semantic_calls": 0,
        "image_calls": generation_calls,
        "qa_status": qa.get("status"),
        "qa_path": job.get("qa_path", "deterministic_only"),
        "image_repairs": _count(job.get("automatic_repairs_used")),
        "cache_hit": bool(job.get("cache_hit", False)),
        "coverage_passed": coverage.get("passed"),
        "coverage_missing_count": (
            len(coverage.get("missing", []))
            if isinstance(coverage.get("missing", []), list)
            else None
        ),
        "body_image_mapping": mapping.get("mode"),
        "content_overflow": (
            bool(job.get("content_overflow")) or failure.get("category") == "content_overflow"
        ),
        "failure_phase": failure.get("phase"),
        "failure_category": failure.get("category"),
        "failure_retryable": failure.get("retryable"),
        "warnings": (
            list(qa.get("issues", [])) if qa.get("status") == "pass_with_advisory" else []
        ),
    }


def build_pipeline_metrics(
    run: Mapping[str, Any],
    evidence_by_page: Mapping[int, Mapping[str, Any]] | None = None,
    evidence_status_by_page: Mapping[int, tuple[bool, str | None]] | None = None,
) -> dict[str, Any]:
    evidence_by_page = evidence_by_page or {}
    evidence_status_by_page = evidence_status_by_page or {}
    pages = []
    for job in run.get("jobs", []):
        page_number = int(job.get("page_number", 0))
        consistent, error = evidence_status_by_page.get(page_number, (True, None))
        pages.append(page_metrics(
            job,
            evidence_by_page.get(page_number),
            selected_evidence_consistent=consistent,
            selected_evidence_error=error,
        ))
    return {
        "schema_version": "2.0",
        "workflow_contract_version": run.get("workflow_contract_version"),
        "style_confirmation_status": (
            run.get("style_confirmation", {}).get("status")
            if isinstance(run.get("style_confirmation"), Mapping)
            else None
        ),
        "final_pptx": run.get("final_pptx"),
        "pages": pages,
        "totals": {
            "generation_calls": sum(item["generation_calls"] for item in pages),
            "reconstruction_calls": sum(item["reconstruction_calls"] for item in pages),
            "semantic_calls": 0,
            "image_calls": sum(item["image_calls"] for item in pages),
            "selected_evidence_chars": sum(item["selected_evidence_chars"] for item in pages),
            "image_repairs": sum(item["image_repairs"] for item in pages),
        },
    }


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _selected_evidence(
    project: Path, job: Mapping[str, Any]
) -> tuple[Mapping[str, Any], bool, str | None]:
    relative = job.get("selected_evidence_file")
    expected = job.get("selected_evidence_sha256")
    if not isinstance(relative, str):
        return {}, False, "path_missing"
    if not _valid_sha256(expected):
        return {}, False, "sha256_missing"
    try:
        path = require_project_file(project, relative)
        payload = path.read_bytes()
    except (OSError, ProjectPathError):
        return {}, False, "path_invalid"
    if hashlib.sha256(payload).hexdigest() != expected:
        return {}, False, "sha256_mismatch"
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}, False, "json_invalid"
    if not isinstance(value, Mapping):
        return {}, False, "json_invalid"
    return value, True, None


def _atomic_json(project: Path, path: Path, value: Mapping[str, Any]) -> None:
    project = require_plain_project(project)
    path = project_output_path(project, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    require_plain_project(path.parent)
    if os.path.lexists(path):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ProjectPathError("metrics output must be a regular unlinked file")
    temporary = project_output_path(
        project, path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    )
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _published_index(project: Path) -> dict[str, Any] | None:
    path = project_output_path(project, project / PIPELINE_METRICS_FILE)
    if not os.path.lexists(path):
        return None
    path = require_project_file(project, path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectPathError("published metrics index is invalid") from exc
    if not isinstance(value, dict):
        raise ProjectPathError("published metrics index must be an object")
    return value


def _published_snapshot_complete(
    project: Path, index: Mapping[str, Any], revision: str
) -> bool:
    revision_directory = revision[:16]
    expected_snapshot = (SNAPSHOT_ROOT / revision_directory).as_posix()
    if index.get("snapshot") != expected_snapshot:
        return False
    page_files = index.get("page_metric_files")
    pages = index.get("pages")
    if not isinstance(page_files, Mapping) or not isinstance(pages, list):
        return False
    expected_pages = {
        str(page.get("page_number")): (
            SNAPSHOT_ROOT / revision_directory / "pages" / f"page_{int(page.get('page_number', 0)):03d}.json"
        ).as_posix()
        for page in pages
        if isinstance(page, Mapping)
    }
    if dict(page_files) != expected_pages:
        return False
    files = [SNAPSHOT_ROOT / revision_directory / "pipeline_metrics.json"] + [
        Path(relative) for relative in expected_pages.values()
    ]
    try:
        for relative in files:
            value = json.loads(require_project_file(project, relative).read_text(encoding="utf-8"))
            if not isinstance(value, Mapping) or value.get("state_revision") != revision:
                return False
    except (OSError, json.JSONDecodeError, ProjectPathError):
        return False
    return True


def _remove_snapshot_tree(project: Path, path: Path) -> None:
    path = project_output_path(project, path)
    if not os.path.lexists(path):
        return
    info = path.lstat()
    reparse = bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )
    if stat.S_ISLNK(info.st_mode) or reparse:
        raise ProjectPathError("metrics snapshot cleanup refuses links or reparse points")
    if stat.S_ISDIR(info.st_mode):
        for child in path.iterdir():
            _remove_snapshot_tree(project, child)
        path.rmdir()
    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
        path.unlink()
    else:
        raise ProjectPathError("metrics snapshot cleanup refuses special files")


def publish_pipeline_metrics(project: Path) -> dict[str, Any]:
    """Build an immutable state-revision snapshot, then atomically publish its index."""
    project = require_plain_project(project)
    state_path = require_project_file(project, "workflow_run.json")
    state_bytes = state_path.read_bytes()
    revision = hashlib.sha256(state_bytes).hexdigest()
    try:
        run = json.loads(state_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectPathError("workflow state is invalid for metrics") from exc
    if not isinstance(run, Mapping):
        raise ProjectPathError("workflow state must be an object")
    prior = _published_index(project)
    if (
        prior is not None
        and prior.get("state_revision") == revision
        and _published_snapshot_complete(project, prior, revision)
    ):
        return prior

    jobs = run.get("jobs", [])
    if not isinstance(jobs, list) or any(not isinstance(job, Mapping) for job in jobs):
        raise ProjectPathError("workflow jobs are invalid for metrics")
    evidence: dict[int, Mapping[str, Any]] = {}
    evidence_status: dict[int, tuple[bool, str | None]] = {}
    for job in jobs:
        page_number = int(job.get("page_number", 0))
        selected, consistent, error = _selected_evidence(project, job)
        evidence[page_number] = selected
        evidence_status[page_number] = (consistent, error)

    report = build_pipeline_metrics(run, evidence, evidence_status)
    snapshot_relative = SNAPSHOT_ROOT / revision[:16]
    snapshot = project_output_path(project, project / snapshot_relative)
    snapshot.mkdir(parents=True, exist_ok=True)
    require_plain_project(snapshot)
    try:
        page_files: dict[str, str] = {}
        pages_with_revision = []
        for page in report["pages"]:
            page_number = int(page["page_number"])
            page_value = {**page, "state_revision": revision}
            relative = snapshot_relative / "pages" / f"page_{page_number:03d}.json"
            _atomic_json(project, project / relative, page_value)
            page_files[str(page_number)] = relative.as_posix()
            pages_with_revision.append(page_value)

        published = {
            **report,
            "state_revision": revision,
            "snapshot": snapshot_relative.as_posix(),
            "page_metric_files": page_files,
            "pages": pages_with_revision,
        }
        _atomic_json(project, project / snapshot_relative / "pipeline_metrics.json", published)
        _atomic_json(project, project / PIPELINE_METRICS_FILE, published)
    except BaseException:
        try:
            _remove_snapshot_tree(project, snapshot)
        except (OSError, ProjectPathError):
            pass
        raise
    return published

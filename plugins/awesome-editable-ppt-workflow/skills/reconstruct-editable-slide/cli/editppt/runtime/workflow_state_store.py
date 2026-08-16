"""Shared atomic workflow-state persistence and derived-metrics publication boundary."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ContextManager, Mapping

try:
    from .project_paths import (
        ProjectPathError,
        project_output_path,
        require_plain_project,
        require_project_file,
    )
    from . import workflow_metrics
except ImportError:  # direct runtime script entrypoint
    from project_paths import (
        ProjectPathError,
        project_output_path,
        require_plain_project,
        require_project_file,
    )
    import workflow_metrics


STATE_FILE = "workflow_run.json"


def _is_link_or_reparse(path: Path) -> bool:
    info = path.lstat()
    return bool(
        path.is_symlink()
        or getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


@contextmanager
def _regular_file_lock(
    lock_path: Path, *, timeout_seconds: float, unsafe_message: str, timeout_message: str,
) -> ContextManager[None]:
    if os.path.lexists(lock_path):
        info = lock_path.lstat()
        if _is_link_or_reparse(lock_path) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ProjectPathError(unsafe_message)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    handle = os.fdopen(descriptor, "r+b", closefd=True)
    try:
        after_open = lock_path.lstat()
        if _is_link_or_reparse(lock_path) or not stat.S_ISREG(after_open.st_mode) or after_open.st_nlink != 1:
            raise ProjectPathError(unsafe_message)
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(timeout_message)
                time.sleep(0.01)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def state_path(project: Path) -> Path:
    return require_project_file(require_plain_project(project), STATE_FILE)


def load_state(project: Path) -> dict[str, Any]:
    try:
        value = json.loads(state_path(project).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectPathError("workflow project state is invalid") from exc
    if not isinstance(value, dict):
        raise ProjectPathError("workflow project state must be an object")
    return value


def _replace_file(project: Path, path: Path, state: Mapping[str, Any]) -> None:
    path = project_output_path(project, path)
    if os.path.lexists(path):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ProjectPathError("workflow state must be a regular unlinked file")
    temporary = project_output_path(
        project, path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    )
    try:
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _emit_metrics_error(receipt: Mapping[str, Any]) -> None:
    try:
        sys.stderr.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
        sys.stderr.flush()
    except BaseException:
        pass


def _save(project: Path, state: Mapping[str, Any], *, initialize: bool) -> dict[str, Any]:
    project = require_plain_project(project)
    path = project_output_path(project, project / STATE_FILE)
    exists = os.path.lexists(path)
    if initialize and exists:
        raise ProjectPathError("workflow project state already exists")
    if not initialize:
        state_path(project)
    _replace_file(project, path, state)
    state_revision = hashlib.sha256(state_path(project).read_bytes()).hexdigest()
    try:
        metrics = workflow_metrics.publish_pipeline_metrics(project)
        return {
            "state_saved": True,
            "state_revision": metrics["state_revision"],
            "metrics": {"status": "published", "state_revision": metrics["state_revision"]},
        }
    except Exception as exc:
        receipt = {
            "event": "pipeline_metrics_refresh_failed",
            "state_saved": True,
            "state_revision": state_revision,
            "metrics": {
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        }
        _emit_metrics_error(receipt)
        return receipt


def initialize_state(project: Path, state: Mapping[str, Any]) -> dict[str, Any]:
    return _save(project, state, initialize=True)


def replace_state(project: Path, state: Mapping[str, Any]) -> dict[str, Any]:
    return _save(project, state, initialize=False)


def _ensure_plain_directory(path: Path) -> Path:
    missing: list[str] = []
    current = path
    while not os.path.lexists(current):
        if current == current.parent:
            raise ProjectPathError("workflow project parent does not exist")
        missing.append(current.name)
        current = current.parent
    current = require_plain_project(current)
    for name in reversed(missing):
        current = current / name
        try:
            current.mkdir()
        except FileExistsError:
            # A competing bootstrap may have created this exact component.
            # The strict lstat/reparse/directory validation below decides
            # whether the raced path is safe; no other mkdir error is hidden.
            pass
        current = require_plain_project(current)
    return current


@contextmanager
def project_bootstrap_lock(
    destination: Path, timeout_seconds: float = 30.0,
) -> ContextManager[Path]:
    """Serialize creation of a project before its workflow state exists."""
    destination = Path(os.path.abspath(os.fspath(destination)))
    if destination == Path(destination.anchor) or not destination.name:
        raise ProjectPathError("workflow project destination must be a named child directory")
    parent = _ensure_plain_directory(destination.parent)
    destination = parent / destination.name
    if os.path.lexists(destination):
        info = destination.lstat()
        if (
            _is_link_or_reparse(destination)
            or not stat.S_ISDIR(info.st_mode)
            or destination.resolve(strict=True) != destination
        ):
            raise ProjectPathError("workflow project destination cannot be redirected")
    lock_path = parent / f".{destination.name}.workflow-bootstrap.lock"
    with _regular_file_lock(
        lock_path,
        timeout_seconds=timeout_seconds,
        unsafe_message="workflow bootstrap lock must be a parent-local regular file",
        timeout_message="timed out waiting for workflow bootstrap lock",
    ):
        parent = require_plain_project(parent)
        destination = parent / destination.name
        if os.path.lexists(destination):
            info = destination.lstat()
            if (
                _is_link_or_reparse(destination)
                or not stat.S_ISDIR(info.st_mode)
                or destination.resolve(strict=True) != destination
            ):
                raise ProjectPathError("workflow project destination cannot be redirected")
        yield destination


@contextmanager
def project_state_lock(project: Path, timeout_seconds: float = 30.0) -> ContextManager[None]:
    """Serialize a full read/validate/transition/replace transaction."""
    project = require_plain_project(project)
    state_path(project)
    lock_path = project_output_path(project, project / ".workflow_state.lock")
    if os.path.lexists(lock_path):
        info = lock_path.lstat()
        reparse = bool(
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
        if lock_path.is_symlink() or reparse or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ProjectPathError("workflow state lock must be a project-local regular file")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    handle = os.fdopen(descriptor, "r+b", closefd=True)
    try:
        after_open = lock_path.lstat()
        if lock_path.is_symlink() or not stat.S_ISREG(after_open.st_mode) or after_open.st_nlink != 1:
            raise ProjectPathError("workflow state lock must be a project-local regular file")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out waiting for workflow state lock")
                time.sleep(0.01)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()

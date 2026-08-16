"""Low-level project-local path validation without workflow business imports."""

from __future__ import annotations

import os
import stat
from pathlib import Path


class ProjectPathError(ValueError):
    """A workflow project path is unsafe or invalid."""


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    reparse = bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )
    return stat.S_ISLNK(info.st_mode) or reparse


def _literal_absolute(value: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(value)))


def require_plain_project(value: str | Path) -> Path:
    project = _literal_absolute(value)
    current = Path(project.anchor)
    for part in project.parts[1:]:
        current /= part
        if os.path.lexists(current) and _is_link_or_reparse(current):
            raise ProjectPathError(
                f"workflow project ancestor cannot be a link or reparse point: {current}"
            )
    try:
        info = project.lstat()
    except OSError as exc:
        raise ProjectPathError("workflow project does not exist") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise ProjectPathError("workflow project must be a directory")
    resolved = project.resolve(strict=True)
    if resolved != project:
        raise ProjectPathError("workflow project path is redirected")
    return resolved


def project_output_path(project: Path, value: str | Path) -> Path:
    project = require_plain_project(project)
    raw = Path(value)
    path = _literal_absolute(raw if raw.is_absolute() else project / raw)
    try:
        relative = path.relative_to(project)
    except ValueError as exc:
        raise ProjectPathError("project artifact must be project-local") from exc
    current = project
    for part in relative.parts:
        current /= part
        if os.path.lexists(current) and _is_link_or_reparse(current):
            raise ProjectPathError(
                f"project path cannot contain a link or reparse point: {current}"
            )
    if path == project:
        raise ProjectPathError("project artifact must be a project-local child")
    return path


def require_project_file(project: Path, value: str | Path) -> Path:
    path = project_output_path(project, value)
    if not path.is_file():
        raise ProjectPathError(f"project artifact is missing: {path}")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ProjectPathError("project artifact must be a regular unlinked file")
    if path.resolve(strict=True) != path:
        raise ProjectPathError("project artifact path is redirected")
    return path

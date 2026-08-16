"""Project-local artifact paths that reject links, junctions, and reparse points."""

from __future__ import annotations

import sys
from pathlib import Path


EDITPPT_CLI = Path(__file__).resolve().parents[2] / "reconstruct-editable-slide" / "cli"
if str(EDITPPT_CLI) not in sys.path:
    sys.path.append(str(EDITPPT_CLI))

from editppt.runtime.project_paths import (  # noqa: E402
    ProjectPathError,
    project_output_path,
    require_plain_project,
)


def project_artifact_path(
    project: Path, relative: Path, *, create_parent: bool = False,
) -> Path:
    """Return a literal in-project path after checking every existing component."""
    project = require_plain_project(project)
    path = project_output_path(project, relative)
    parent = project_output_path(project, path.parent)
    if create_parent:
        parent.mkdir(parents=True, exist_ok=True)
        parent = project_output_path(project, parent)
    elif not parent.exists():
        return path
    if not parent.is_dir() or parent.resolve(strict=True) != parent:
        raise ProjectPathError("project artifact parent is redirected or missing")
    return project_output_path(project, path)

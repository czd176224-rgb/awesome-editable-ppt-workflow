"""Read-only production preflight for frozen V6 pages and the page worker."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import workflow_v6_secure_io as secure_io
from workflow_v6_composition import load_composition_authority
from workflow_v6_contract import (
    PLUGIN_ID,
    PLUGIN_VERSION,
    WORKFLOW_VERSION,
    validate_material_receipts,
    validate_project,
)
from workflow_v6_special_pages import (
    SPECIAL_ROLES,
    SpecialPageOverflowError,
    check_special_page,
)


SCRIPTS = Path(__file__).resolve().parent
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
EDITPPT_CLI = PLUGIN_ROOT / "skills" / "reconstruct-editable-slide" / "cli"
_BUNDLED_MODULES = {
    "workflow_v6_contract": SCRIPTS / "workflow_v6_contract.py",
    "workflow_v6_reconstruction_worker": SCRIPTS / "workflow_v6_reconstruction_worker.py",
    "editppt": EDITPPT_CLI / "editppt" / "__init__.py",
    "editppt.runtime.validate_pptx": EDITPPT_CLI / "editppt" / "runtime" / "validate_pptx.py",
    "editppt.runtime.build_pptx_from_manifest": EDITPPT_CLI / "editppt" / "runtime" / "build_pptx_from_manifest.py",
}
_RUNTIME_MODULES = (*_BUNDLED_MODULES, "pptx", "PIL", "numpy", "requests", "jsonschema", "yaml", "pypdf", "pypdfium2")


def _issue(code: str, message: str, page_number: int | None = None) -> dict[str, Any]:
    issue: dict[str, Any] = {"code": code, "message": message}
    if page_number is not None:
        issue["page_number"] = page_number
    return issue


def _read_json(root: Path, relative: Path) -> dict[str, Any]:
    value = json.loads(secure_io.read_bytes(root, relative).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {relative.as_posix()}")
    return value


def _runtime_probe() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    script = (
        "import importlib,json,sys\n"
        "names=" + repr(_RUNTIME_MODULES) + "\n"
        "modules={name:importlib.import_module(name) for name in names}\n"
        "contract=modules['workflow_v6_contract']\n"
        "print(json.dumps({'python_executable':sys.executable,'python_version':sys.version.split()[0],"
        "'plugin_id':contract.PLUGIN_ID,'plugin_version':contract.PLUGIN_VERSION,"
        "'modules':{name:str(getattr(module,'__file__',None)) for name,module in modules.items()}}))\n"
    )
    environment = os.environ.copy()
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONPATH"] = os.pathsep.join((str(EDITPPT_CLI), str(SCRIPTS)))
    evidence: dict[str, Any] = {
        "checked": True,
        "python_executable": str(Path(sys.executable).resolve()),
        "user_site_enabled": False,
        "pythonpath": environment["PYTHONPATH"].split(os.pathsep),
        "modules": {},
    }
    try:
        completed = subprocess.run(
            [sys.executable, "-s", "-c", script],
            cwd=str(SCRIPTS),
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return evidence, [_issue(
            "runtime_probe_failed",
            f"Worker runtime probe could not run with {sys.executable}: {exc}",
        )]
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        evidence["error"] = detail
        return evidence, [_issue(
            "runtime_probe_failed",
            f"Worker runtime imports failed under {sys.executable}: {detail}",
        )]
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        evidence["error"] = completed.stdout.strip()
        return evidence, [_issue(
            "runtime_probe_failed",
            f"Worker runtime probe returned invalid JSON: {exc}",
        )]
    if not isinstance(payload, Mapping) or not isinstance(payload.get("modules"), Mapping):
        return evidence, [_issue(
            "runtime_probe_failed",
            "Worker runtime probe returned no module-path evidence.",
        )]
    evidence.update(dict(payload))
    issues: list[dict[str, Any]] = []
    if Path(str(payload.get("python_executable", ""))).resolve() != Path(sys.executable).resolve():
        issues.append(_issue(
            "runtime_interpreter_mismatch",
            f"Worker probe used {payload.get('python_executable')}; rerun with {sys.executable}.",
        ))
    if payload.get("plugin_id") != PLUGIN_ID or payload.get("plugin_version") != PLUGIN_VERSION:
        issues.append(_issue(
            "runtime_version_mismatch",
            f"Worker loaded {payload.get('plugin_id')} {payload.get('plugin_version')}; expected {PLUGIN_ID} {PLUGIN_VERSION}.",
        ))
    modules = payload["modules"]
    for name, expected in _BUNDLED_MODULES.items():
        actual = modules.get(name)
        try:
            matches = Path(str(actual)).resolve() == expected.resolve(strict=True)
        except (OSError, ValueError):
            matches = False
        if not matches:
            issues.append(_issue(
                "runtime_module_mismatch",
                f"Worker resolves {name} to {actual}; expected this checkout at {expected.resolve()}.",
            ))
    return evidence, issues


def preflight_project(
    project: Path,
    page_numbers: Sequence[int],
    *,
    check_runtime: bool = True,
) -> dict[str, Any]:
    """Return all deterministic blockers before any requested page can start."""
    issues: list[dict[str, Any]] = []
    runtime: dict[str, Any] = {
        "checked": False,
        "python_executable": str(Path(sys.executable).resolve()),
    }
    expected = {
        "plugin_id": PLUGIN_ID,
        "plugin_version": PLUGIN_VERSION,
        "workflow_contract_version": WORKFLOW_VERSION,
    }
    try:
        secure_io.reject_reparse_chain(Path(project))
        root = Path(project).resolve(strict=True)
        state = _read_json(root, Path("workflow_v6.json"))
    except Exception as exc:
        issues.append(_issue("project_state_invalid", f"Project state cannot be read: {type(exc).__name__}: {exc}"))
        if check_runtime:
            runtime, runtime_issues = _runtime_probe()
            issues.extend(runtime_issues)
        return {"passed": False, "issues": issues, "expected": expected, "project": {}, "runtime": runtime}

    project_evidence = {
        "root": str(root),
        "plugin_id": state.get("plugin_id"),
        "plugin_version": state.get("plugin_version"),
        "workflow_contract_version": state.get("workflow_contract_version"),
    }
    if state.get("plugin_id") != PLUGIN_ID or state.get("plugin_version") != PLUGIN_VERSION:
        issues.append(_issue(
            "project_version_mismatch",
            f"Project is {state.get('plugin_id')} {state.get('plugin_version')}; expected {PLUGIN_ID} {PLUGIN_VERSION}.",
        ))
    if state.get("workflow_contract_version") != WORKFLOW_VERSION:
        issues.append(_issue(
            "project_contract_mismatch",
            f"Project workflow contract is {state.get('workflow_contract_version')}; expected {WORKFLOW_VERSION}.",
        ))
    try:
        validate_project(state)
    except Exception as exc:
        issues.append(_issue("project_state_invalid", f"Project state is invalid: {type(exc).__name__}: {exc}"))

    if state.get("page_materials_status") != "confirmed":
        issues.append(_issue(
            "page_materials_not_confirmed",
            "Project page materials must be confirmed before generation, including selected-page runs.",
        ))

    pages: list[int] = []
    seen: set[int] = set()
    page_count = len(state.get("pages", [])) if isinstance(state.get("pages"), list) else 0
    requested_pages = page_numbers or tuple(range(1, page_count + 1))
    for page_number in requested_pages:
        if type(page_number) is not int or not 1 <= page_number <= page_count:
            issues.append(_issue(
                "page_out_of_range",
                f"Requested page {page_number!r} is outside 1..{page_count}.",
                page_number if type(page_number) is int else None,
            ))
        elif page_number not in seen:
            pages.append(page_number)
            seen.add(page_number)

    composition: dict[str, Any] | None = None
    try:
        composition = load_composition_authority(root)
    except Exception as exc:
        issues.append(_issue(
            "composition_invalid",
            f"Frozen page composition cannot be used: {type(exc).__name__}: {exc}",
        ))

    for page_number in pages:
        try:
            validate_material_receipts(
                root,
                {"page_materials_status": "confirmed", "pages": [state["pages"][page_number - 1]]},
            )
        except Exception as exc:
            issues.append(_issue(
                "material_receipt_invalid",
                f"Page {page_number} material receipt is missing or invalid: {exc}",
                page_number,
            ))
        if (
            composition is not None
            and composition["pages"][page_number - 1]["page_role"] in SPECIAL_ROLES
        ):
            try:
                check_special_page(root, page_number)
            except SpecialPageOverflowError as exc:
                issues.append(_issue("special_page_overflow", str(exc), page_number))
            except Exception as exc:
                issues.append(_issue("special_page_invalid", str(exc), page_number))

    if check_runtime:
        runtime, runtime_issues = _runtime_probe()
        issues.extend(runtime_issues)
    return {
        "passed": not issues,
        "issues": issues,
        "expected": expected,
        "project": project_evidence,
        "runtime": runtime,
    }


__all__ = ["preflight_project"]

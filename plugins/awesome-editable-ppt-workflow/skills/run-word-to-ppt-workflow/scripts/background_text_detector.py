"""Zero-token background-text detector with one explicit editable-runtime boundary."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
from importlib.util import find_spec as find_module_spec
from pathlib import Path
from subprocess import run as run_process
from typing import Any


SOURCE_EDITPPT_CLI_ROOT = (
    Path(__file__).resolve().parents[2]
    / "reconstruct-editable-slide"
    / "cli"
)
WORKER_TIMEOUT_SECONDS = 30


def _runtime_dir() -> Path:
    try:
        runtime_package = importlib.import_module("editppt.runtime")
    except ModuleNotFoundError:
        if not SOURCE_EDITPPT_CLI_ROOT.is_dir():
            raise
        sys.path.insert(0, str(SOURCE_EDITPPT_CLI_ROOT))
        runtime_package = importlib.import_module("editppt.runtime")
    package_file = getattr(runtime_package, "__file__", None)
    if not package_file:
        raise RuntimeError("editppt.runtime package has no filesystem location")
    return Path(package_file).resolve().parent


def _runtime() -> tuple[Any, Any, Any, Any]:
    from editppt.runtime.page_text_metrics import load_gray
    from editppt.runtime.text_hints import binarize_page, measure_leaves, xy_cut

    return load_gray, binarize_page, measure_leaves, xy_cut


def _local_capability_status() -> dict[str, Any]:
    try:
        _runtime()
    except Exception as exc:
        return {
            "available": False,
            "backend": f"bundled_classical_ink_detector: {type(exc).__name__}",
            "failure_behavior": "content_blocked",
        }
    return {
        "available": True,
        "backend": "bundled_classical_ink_detector",
        "failure_behavior": "content_blocked",
    }


def _local_detect(image: Path) -> dict[str, Any]:
    load_gray, binarize_page, measure_leaves, xy_cut = _runtime()
    gray = load_gray(Path(image))
    height, width = gray.shape
    mask = binarize_page(gray)
    boxes: list[tuple[int, int, int, int]] = []
    xy_cut(
        mask,
        0,
        0,
        max(6, round(height * 0.008)),
        max(14, round(width * 0.011)),
        boxes,
    )
    regions = measure_leaves(gray, mask, boxes, 6)
    return {
        "background_text_detected": bool(regions),
        "background_text_regions": regions,
    }


def _python_for_editable_runtime() -> Path:
    configured = os.getenv("EDITPPT_PYTHON")
    executable = os.getenv("EDITPPT_EXE")
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser().resolve())
    if executable:
        candidates.append(Path(executable).expanduser().resolve().parent / "python.exe")
    if find_module_spec("editppt") is not None:
        candidates.append(Path(sys.executable).resolve())
    candidates.append(Path.home() / ".codex/plugin-runtimes/awesome-editable-ppt-workflow-fixed-canvas-cm-v2/editable-ppt/Scripts/python.exe")
    command = shutil.which("editppt")
    if command:
        candidates.append(Path(command).resolve().parent / "python.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    if SOURCE_EDITPPT_CLI_ROOT.is_dir():
        return Path(sys.executable).resolve()
    raise FileNotFoundError("editable-PPT Python runtime was not found")


def _worker_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return environment


def _invoke_worker(*arguments: str) -> dict[str, Any]:
    completed = run_process(
        [str(_python_for_editable_runtime()), "-m", "background_text_detector", *arguments],
        capture_output=True,
        text=True,
        timeout=WORKER_TIMEOUT_SECONDS,
        check=False,
        env=_worker_environment(),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[:500]
        raise RuntimeError(f"background text detector worker returned {completed.returncode}: {detail}")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("background text detector worker returned no result")
    result = json.loads(lines[-1])
    if not isinstance(result, dict):
        raise RuntimeError("background text detector worker returned a non-object result")
    return result


def capability_status() -> dict[str, Any]:
    """Probe the mandatory detector inside its owning editable runtime."""
    try:
        return _invoke_worker("--worker-capability")
    except Exception as exc:
        return {
            "available": False,
            "backend": f"bundled_classical_ink_detector: {type(exc).__name__}",
            "failure_behavior": "content_blocked",
        }


def detect_background_text(image: Path) -> dict[str, Any]:
    """Scan once in the owning editable runtime and return text-like regions."""
    return _invoke_worker("--worker-detect", str(Path(image).resolve()))


def _main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--worker-capability":
        print(json.dumps(_local_capability_status(), ensure_ascii=False))
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == "--worker-detect":
        print(json.dumps(_local_detect(Path(sys.argv[2])), ensure_ascii=False))
        return 0
    raise SystemExit("background_text_detector.py is an internal runtime worker")


if __name__ == "__main__":
    raise SystemExit(_main())

#!/usr/bin/env python3
"""Validate and record one manifest-authoritative editable page."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from deck_run_state import (
    find_page,
    load_jobs,
    now_iso,
    page_dir_for,
    rel_to_run,
    resolve_inside,
    run_dir_from_target,
    save_jobs,
    set_run_status,
    sha256_file,
    update_jobs_run_status,
)


RUNTIME_DIR = Path(__file__).resolve().parent
WORKFLOW_SCRIPTS = RUNTIME_DIR.parents[3] / "run-word-to-ppt-workflow" / "scripts"
REQUIRED_RESULT_PATHS = {
    "page_manifest": "manifest.json",
    "imagegen_jobs": "imagegen-jobs.json",
    "page_pptx": "page.pptx",
    "preview": "preview.png",
    "contact_sheet": "split_assets_contact.png",
    "validation": "validation.json",
    "page_result": "page_result.json",
}


def _read_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unavailable or invalid: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value


def _result_artifacts(page_dir: Path) -> tuple[dict, dict[str, Path]]:
    result_path = page_dir / "page_result.json"
    result = _read_object(result_path, "page_result.json")
    artifacts: dict[str, Path] = {}
    for field, default_name in REQUIRED_RESULT_PATHS.items():
        value = result.get(field, default_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"page_result.json.{field} must be a non-empty path")
        path = resolve_inside(page_dir, value)
        if not path.is_file():
            raise ValueError(f"missing required page artifact: {path}")
        artifacts[field] = path
    if artifacts["page_result"] != result_path.resolve():
        raise ValueError("page_result.json.page_result must refer to page_result.json")
    return result, artifacts


def record_manifest_page(run: Path, *, page_id: str, agent_id: str) -> dict:
    run_dir = run_dir_from_target(run)
    jobs = load_jobs(run_dir)
    page = find_page(jobs, page_id)
    if page.get("status") != "dispatched":
        raise ValueError(f"{page['page_id']} must be dispatched before record")
    dispatch = page.get("dispatch") or {}
    if dispatch.get("agent_id") != agent_id:
        raise ValueError(
            f"agent id mismatch for {page['page_id']}: "
            f"dispatch={dispatch.get('agent_id')} record={agent_id}"
        )
    request_path = (run_dir / page["page_request"]).resolve()
    if not request_path.is_file() or sha256_file(request_path) != dispatch.get("page_request_sha256"):
        raise ValueError("page_request.json changed after dispatch")

    page_dir = page_dir_for(run_dir, page)
    _result, artifacts = _result_artifacts(page_dir)
    validation = _read_object(artifacts["validation"], "validation.json")
    if validation.get("passed") is not True:
        raise ValueError("validation.json must contain top-level passed: true")

    verified_report = page_dir / ".record-validation.json"
    command = [
        sys.executable,
        str(RUNTIME_DIR / "validate_pptx.py"),
        str(artifacts["page_pptx"]),
        "--manifest",
        str(artifacts["page_manifest"]),
        "--report",
        str(verified_report),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(WORKFLOW_SCRIPTS), env.get("PYTHONPATH")) if value
    )
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        command, text=True, encoding="utf-8", errors="replace", capture_output=True, env=env,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "page validation failed"
        raise ValueError(detail)
    record_report = _read_object(verified_report, "record validation report")
    if record_report.get("passed") is not True:
        raise ValueError("deterministic page validation did not pass")

    hashes = {
        field: sha256_file(path)
        for field, path in artifacts.items()
        if field != "page_result"
    }
    page["result"] = {
        "agent_id": agent_id,
        "recorded_at": now_iso(),
        "artifacts": {field: rel_to_run(run_dir, path) for field, path in artifacts.items()},
        "sha256": hashes,
        "record_validation": rel_to_run(run_dir, verified_report),
    }
    page["status"] = "recorded"
    update_jobs_run_status(jobs)
    save_jobs(run_dir, jobs)
    if jobs.get("run_status") == "pages_recorded":
        set_run_status(run_dir, "pages_recorded", "all manifest pages recorded")
    return {"page_id": page["page_id"], "status": "recorded", "result": page["result"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--page", required=True)
    parser.add_argument("--agent-id", required=True)
    args = parser.parse_args(argv)
    try:
        result = record_manifest_page(args.run, page_id=args.page, agent_id=args.agent_id)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

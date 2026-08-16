#!/usr/bin/env python3
"""Rebuild and atomically publish a deck from recorded page manifests."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from build_pptx_from_manifest import (
    output_path_from_deck_manifest,
    page_entries_from_deck_manifest,
    write_deck,
)
from deck_run_state import (
    load_deck,
    load_jobs,
    now_iso,
    run_dir_from_target,
    save_deck,
    save_jobs,
    set_run_status,
    sha256_file,
    update_jobs_run_status,
    write_json,
)


RUNTIME_DIR = Path(__file__).resolve().parent
WORKFLOW_SCRIPTS = RUNTIME_DIR.parents[3] / "run-word-to-ppt-workflow" / "scripts"


def _manifest_hashes(run_dir: Path, jobs: dict) -> dict[str, str]:
    hashes = {}
    for page in jobs.get("pages", []):
        manifest = (run_dir / page["manifest"]).resolve()
        if not manifest.is_file():
            raise ValueError(f"missing recorded page manifest: {manifest}")
        hashes[page["page_id"]] = sha256_file(manifest)
    return hashes


def finalize_manifest_run(run: Path) -> dict:
    run_dir = run_dir_from_target(run)
    deck_path = run_dir / "deck_manifest.json"
    deck = load_deck(run_dir)
    jobs = load_jobs(run_dir)
    pages = jobs.get("pages", [])
    statuses = {page.get("status") for page in pages}
    if (
        not pages
        or not statuses.issubset({"recorded", "accepted"})
        or "recorded" not in statuses
    ):
        raise ValueError(
            "finalization requires every page to be recorded or previously accepted, "
            "with at least one newly recorded page"
        )
    before = _manifest_hashes(run_dir, jobs)
    output = output_path_from_deck_manifest(deck_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.parent / f".{output.name}.{uuid.uuid4().hex}.tmp"
    report_path = output.parent / "final_validation.json"
    temporary_report = output.parent / f".{report_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        deck_payload, entries, notes = page_entries_from_deck_manifest(deck_path)
        write_deck(deck_payload, entries, temporary, notes)
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            value for value in (str(WORKFLOW_SCRIPTS), env.get("PYTHONPATH")) if value
        )
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [
                sys.executable,
                str(RUNTIME_DIR / "validate_pptx.py"),
                str(temporary),
                "--deck-manifest",
                str(deck_path),
                "--report",
                str(temporary_report),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            env=env,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "deck validation failed"
            raise ValueError(detail)
        report = json.loads(temporary_report.read_text(encoding="utf-8"))
        if report.get("passed") is not True:
            raise ValueError("deterministic final deck validation did not pass")
        if before != _manifest_hashes(run_dir, jobs):
            raise ValueError("recorded page manifests changed during finalization")
        repair_finalization = bool(output.exists() and deck.get("completed_at"))
        if output.exists() and not repair_finalization:
            raise ValueError(f"final output already exists: {output}")
        os.replace(temporary, output)
        os.replace(temporary_report, report_path)
        output_hash = sha256_file(output)

        for page in pages:
            page["status"] = "accepted"
            page["accepted"] = True
            page["accepted_at"] = now_iso()
        update_jobs_run_status(jobs)
        save_jobs(run_dir, jobs)
        for page in deck.get("pages", []):
            page["status"] = "accepted"
            page["accepted"] = True
        deck["completed_at"] = now_iso()
        deck["final_output_sha256"] = output_hash
        save_deck(run_dir, deck)
        set_run_status(run_dir, "complete", "manifest-authoritative deck finalized")
        summary = {
            "workflow_contract_version": deck.get("workflow_contract_version"),
            "status": "complete",
            "page_count": len(pages),
            "output": str(output),
            "output_sha256": output_hash,
            "validation": str(report_path),
            "assembly_authority": "recorded-page-manifests",
        }
        write_json(output.parent / "run_summary.json", summary)
        return summary
    finally:
        for path in (temporary, temporary_report):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args(argv)
    try:
        result = finalize_manifest_run(args.run)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

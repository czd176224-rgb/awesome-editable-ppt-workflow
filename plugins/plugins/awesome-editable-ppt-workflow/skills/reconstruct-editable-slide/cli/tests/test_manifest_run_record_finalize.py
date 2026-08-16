from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image


RUNTIME = Path(__file__).resolve().parents[1] / "editppt" / "runtime"
sys.path.insert(0, str(RUNTIME))

from build_pptx_from_manifest import render_preview, write_pptx  # noqa: E402
from deck_run_state import sha256_file  # noqa: E402
from finalize_manifest_deck_run import finalize_manifest_run  # noqa: E402
from fixed_region_runtime import CONTENT_BOX, SLIDE  # noqa: E402
from record_manifest_page_result import record_manifest_page  # noqa: E402


QUALITY = {
    "font_size_calibrated": True,
    "visual_inventory_matched": True,
    "background_strategy_checked": True,
    "shape_corner_geometry_checked": True,
}


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _prepared_run(tmp_path: Path) -> tuple[Path, Path]:
    run = tmp_path / "run"
    page_dir = run / "pages" / "page_001"
    page_dir.mkdir(parents=True)
    Image.new("RGB", (1904, 896), "white").save(page_dir / "source.png")
    manifest = {
        "workflow_contract_version": "fixed-canvas-cm-v2",
        "reconstruction_contract_version": "editable-image-v3",
        "slide": dict(SLIDE),
        "content_box": dict(CONTENT_BOX),
        "source": {"width_px": 1904, "height_px": 896},
        "text_inventory": ["真实内容"],
        "visual_inventory": [],
        "background_strategy": "native white body background",
        "quality_checks": dict(QUALITY),
        "text_boxes": [
            {
                "object_id": "t1",
                "name": "body-text",
                "text": "真实内容",
                "box_px": [120, 120, 900, 120],
                "font_size": 26,
            }
        ],
        "shapes": [],
        "images": [],
        "asset_provenance": [],
    }
    manifest_path = page_dir / "manifest.json"
    _write(manifest_path, manifest)
    write_pptx(manifest, page_dir / "page.pptx", manifest_path)
    render_preview(manifest, manifest_path, page_dir / "preview.png")
    Image.new("RGB", (400, 200), "white").save(page_dir / "split_assets_contact.png")
    _write(page_dir / "imagegen-jobs.json", {"jobs": []})
    _write(page_dir / "validation.json", {"passed": True})
    _write(
        page_dir / "page_result.json",
        {
            "page_manifest": "manifest.json",
            "imagegen_jobs": "imagegen-jobs.json",
            "page_pptx": "page.pptx",
            "preview": "preview.png",
            "contact_sheet": "split_assets_contact.png",
            "validation": "validation.json",
            "page_result": "page_result.json",
        },
    )
    request = page_dir / "page_request.json"
    _write(request, {"page_id": "page_001"})
    deck = {
        "workflow_contract_version": "fixed-canvas-cm-v2",
        "run_id": "run",
        "job_dir": str(run),
        "page_count": 1,
        "slide": dict(SLIDE),
        "output": "final/deck.pptx",
        "pages": [
            {
                "page_id": "page_001",
                "page_index": 1,
                "page_dir": "pages/page_001",
                "source_image": "pages/page_001/source.png",
                "manifest": "pages/page_001/manifest.json",
                "validation": "pages/page_001/validation.json",
                "status": "dispatched",
                "accepted": False,
            }
        ],
    }
    _write(run / "deck_manifest.json", deck)
    _write(
        run / "page_jobs.json",
        {
            "workflow_contract_version": "fixed-canvas-cm-v2",
            "run_status": "pages_dispatched",
            "max_concurrent_pages": 1,
            "pages": [
                {
                    "page_id": "page_001",
                    "page_index": 1,
                    "page_dir": "pages/page_001",
                    "page_request": "pages/page_001/page_request.json",
                    "manifest": "pages/page_001/manifest.json",
                    "validation": "pages/page_001/validation.json",
                    "status": "dispatched",
                    "accepted": False,
                    "dispatch": {
                        "agent_id": "worker-1",
                        "page_request_sha256": sha256_file(request),
                    },
                    "result": None,
                }
            ],
        },
    )
    _write(run / "run_state.json", {"status": "pages_dispatched", "history": []})
    return run, page_dir


def test_record_and_finalize_use_page_manifest_as_single_authority(tmp_path: Path) -> None:
    run, page_dir = _prepared_run(tmp_path)
    recorded = record_manifest_page(run, page_id="page_001", agent_id="worker-1")
    assert recorded["status"] == "recorded"
    assert recorded["result"]["sha256"]["page_manifest"] == sha256_file(page_dir / "manifest.json")

    summary = finalize_manifest_run(run)
    assert summary["status"] == "complete"
    assert summary["assembly_authority"] == "recorded-page-manifests"
    assert Path(summary["output"]).is_file()
    assert json.loads((run / "page_jobs.json").read_text(encoding="utf-8"))["pages"][0]["status"] == "accepted"


def test_accepted_page_requires_explicit_final_qa_repair_reset(tmp_path: Path) -> None:
    run, _page_dir = _prepared_run(tmp_path)
    record_manifest_page(run, page_id="page_001", agent_id="worker-1")
    finalize_manifest_run(run)
    command = [sys.executable, str(RUNTIME / "reset_page_job.py"), str(run), "--page", "page_001"]

    denied = subprocess.run(command, capture_output=True, text=True, check=False)
    reopened = subprocess.run(command + ["--for-repair"], capture_output=True, text=True, check=False)
    page = json.loads((run / "page_jobs.json").read_text(encoding="utf-8"))["pages"][0]

    assert denied.returncode != 0
    assert "--for-repair" in denied.stderr
    assert reopened.returncode == 0
    assert page["status"] == "pending"
    assert page["accepted"] is False
    assert page["dispatch"] is None
    assert page["result"] is None

    jobs_path = run / "page_jobs.json"
    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
    jobs["pages"][0]["status"] = "dispatched"
    jobs["pages"][0]["dispatch"] = {
        "agent_id": "worker-2",
        "page_request_sha256": sha256_file(run / "pages/page_001/page_request.json"),
    }
    _write(jobs_path, jobs)
    record_manifest_page(run, page_id="page_001", agent_id="worker-2")
    repaired = finalize_manifest_run(run)

    assert repaired["status"] == "complete"
    assert json.loads(jobs_path.read_text(encoding="utf-8"))["pages"][0]["status"] == "accepted"


def test_record_rejects_changed_request_and_failed_worker_validation(tmp_path: Path) -> None:
    run, page_dir = _prepared_run(tmp_path)
    (page_dir / "page_request.json").write_text("{}", encoding="utf-8")
    try:
        record_manifest_page(run, page_id="page_001", agent_id="worker-1")
    except ValueError as exc:
        assert "changed after dispatch" in str(exc)
    else:
        raise AssertionError("changed request must fail closed")

    run, page_dir = _prepared_run(tmp_path / "second")
    _write(page_dir / "validation.json", {"passed": False})
    try:
        record_manifest_page(run, page_id="page_001", agent_id="worker-1")
    except ValueError as exc:
        assert "passed: true" in str(exc)
    else:
        raise AssertionError("failed validation must not be recorded")

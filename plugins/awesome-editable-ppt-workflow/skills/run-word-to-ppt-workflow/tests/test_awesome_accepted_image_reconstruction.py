from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from test_workflow_v6_reconstruction import (  # noqa: E402
    _body,
    _project,
    _write_signed_receipt,
    finalize_reconstructed_page,
)
from workflow_v6_reconstruction import (  # noqa: E402
    assemble_v6_deck,
    build_reconstruction_request,
)
import workflow_v6_reconstruction as reconstruction_module  # noqa: E402
import workflow_v6_reconstruction_worker as worker_module  # noqa: E402
from complex_page_experiment import (  # noqa: E402
    open_accepted_page_workspace,
    open_live_page_workspace,
    verify_signed_acceptance_receipt,
)
from provider_keyring import signing_key  # noqa: E402
from workflow_v6_state import load, save  # noqa: E402


def _remove_transient_pre_acceptance_inputs(project: Path) -> None:
    for name in ("00_source", "01_ui", "03_v6"):
        shutil.rmtree(project / name, ignore_errors=True)


def _rewrite_receipt_as_recovery(
    project: Path, *, candidate_origin: str,
) -> dict:
    receipt_path = project / "04_v6/images/page_001.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["experiment_id"] = "live-page-001-recovery-001"
    receipt["evidence_checkpoint"]["experiment_id"] = receipt["experiment_id"]
    receipt["evidence_checkpoint"]["candidate_origin"] = candidate_origin
    receipt["page_plan"]["primary_relationship"]["nodes"] = []
    receipt["page_plan"]["primary_relationship"]["edges"] = []
    receipt.pop("key_id", None)
    receipt.pop("hmac_sha256", None)
    key_id, key = signing_key()
    receipt["key_id"] = key_id
    receipt["hmac_sha256"] = hmac.new(
        key,
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    experiment = project / "04_v6/experiments/live-page-001-recovery-001"
    experiment.mkdir(parents=True, exist_ok=True)
    (experiment / "accepted_image.json").write_bytes(receipt_path.read_bytes())
    return receipt


def test_preserved_legacy_v1_acceptance_receipt_remains_readable_without_rewrite(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, 1)
    receipt_path = project / "04_v6/images/page_001.json"
    before = receipt_path.read_bytes()
    assert "candidate_origin" not in json.loads(before)["evidence_checkpoint"]

    verified = verify_signed_acceptance_receipt(
        open_live_page_workspace(project, 1), before,
    )

    assert verified["experiment_id"] == "live-page-001"
    assert receipt_path.read_bytes() == before


def test_recovery_v1_acceptance_receipt_requires_candidate_origin(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, 1)
    receipt = _rewrite_receipt_as_recovery(project, candidate_origin="image2")
    receipt["evidence_checkpoint"].pop("candidate_origin")
    receipt.pop("hmac_sha256")
    key_id, key = signing_key()
    receipt["key_id"] = key_id
    receipt["hmac_sha256"] = hmac.new(
        key,
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    payload = (
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    (project / "04_v6/images/page_001.json").write_bytes(payload)
    (project / "04_v6/experiments/live-page-001-recovery-001/accepted_image.json").write_bytes(payload)

    with pytest.raises(ValueError, match="candidate origin"):
        verify_signed_acceptance_receipt(
            open_accepted_page_workspace(project, 1), payload,
        )


@pytest.mark.parametrize("candidate_origin", ["adopted_candidate", "image2"])
def test_recovery_acceptance_enters_reconstruction_request_and_final_authority_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, candidate_origin: str,
) -> None:
    project = _project(tmp_path, 1)
    receipt = _rewrite_receipt_as_recovery(project, candidate_origin=candidate_origin)

    request = build_reconstruction_request(project, page_number=1)
    run_dir, page_dir, _prompt = worker_module._prepare_run(project, request, 1)
    body = page_dir / "page.pptx"
    _body(body, "Editable recovery body")
    (page_dir / "manifest.json").write_text("{}\n", encoding="utf-8")
    jobs_path = run_dir / "page_jobs.json"
    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
    page_request = page_dir / "page_request.json"
    jobs["pages"][0]["dispatch"] = {
        "page_request_sha256": hashlib.sha256(page_request.read_bytes()).hexdigest(),
    }
    jobs_path.write_text(json.dumps(jobs, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        reconstruction_module, "_require_recorded_worker_output", lambda *_args: None,
    )

    authority = reconstruction_module._require_final_authority(
        project, 1, body, reconstruction_module.Presentation(str(body)),
        "sealed_reconstruction",
    )

    assert request["accepted_receipt"]["sha256"] == hashlib.sha256(
        (project / "04_v6/images/page_001.json").read_bytes()
    ).hexdigest()
    assert authority["accepted_receipt"] == request["accepted_receipt"]
    assert open_accepted_page_workspace(project, 1).experiment_id == receipt["experiment_id"]


def test_reconstruction_request_builds_and_recovers_after_transient_inputs_are_removed(
    tmp_path: Path,
):
    project = _project(tmp_path, 1)
    receipt_path = project / "04_v6" / "images" / "page_001.json"
    receipt_digest = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    accepted_image = project / receipt["candidate"]["path"]
    accepted_digest = hashlib.sha256(accepted_image.read_bytes()).hexdigest()
    _remove_transient_pre_acceptance_inputs(project)

    first = build_reconstruction_request(project, page_number=1)
    recovered = build_reconstruction_request(project, page_number=1)

    assert recovered == first
    assert first["accepted_receipt"] == {
        "path": "04_v6/images/page_001.json",
        "sha256": receipt_digest,
    }
    with Image.open(accepted_image) as image:
        normalized_pixels = hashlib.sha256(
            f"RGBA8\0{image.width}x{image.height}\0".encode("ascii")
            + image.convert("RGBA").tobytes()
        ).hexdigest()
    assert first["source_body"] == {
        "path": receipt["candidate"]["path"],
        "sha256": accepted_digest,
        "pixels": {"width": 1904, "height": 896},
        "normalized_pixel_format": "RGBA8",
        "normalized_pixel_sha256": normalized_pixels,
    }
    assert first["sealed_image_edits"] == []
    assert "effective_page" not in first
    serialized = json.dumps(first, ensure_ascii=False)
    assert "00_source" not in serialized
    assert "02_v6" not in serialized
    assert "03_v6" not in serialized


def test_reconstruction_request_rejects_accepted_image_with_wrong_dimensions(tmp_path: Path):
    project = _project(tmp_path, 1)
    receipt_path = project / "04_v6" / "images" / "page_001.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    accepted_image = project / receipt["candidate"]["path"]
    Image.new("RGB", (1536, 1024), "white").save(accepted_image)
    receipt["candidate"]["sha256"] = hashlib.sha256(accepted_image.read_bytes()).hexdigest()
    _write_signed_receipt(project, 1, receipt)

    with pytest.raises(ValueError, match="1904x896"):
        build_reconstruction_request(project, page_number=1)


def test_reconstruction_request_accepts_current_candidate_acceptance_receipt(tmp_path: Path):
    project = _project(tmp_path, 1)
    receipt_path = project / "04_v6" / "images" / "page_001.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    candidate = receipt["candidate"]

    assert "selected" not in receipt

    request = build_reconstruction_request(project, page_number=1)

    assert request["source_body"]["path"] == candidate["path"]
    assert request["source_body"]["sha256"] == candidate["sha256"]
    assert request["sealed_image_edits"] == []


def test_finalize_and_assemble_continue_after_transient_pre_acceptance_authorities_exit(tmp_path: Path):
    project = _project(tmp_path, 1)
    for name in ("01_ui", "03_v6"):
        shutil.rmtree(project / name, ignore_errors=True)

    request = build_reconstruction_request(project, page_number=1)
    reconstructed_body = tmp_path / "body.pptx"
    _body(reconstructed_body, "Editable accepted body")
    page = finalize_reconstructed_page(
        project, page_number=1, reconstructed_body=reconstructed_body,
    )
    deck = assemble_v6_deck(project)

    assert request["sealed_image_edits"] == []
    assert (project / page["page_pptx"]).is_file()
    if deck["status"] == "complete":
        assert (project / deck["output"]).is_file()
        if not deck["release_ready"]:
            assert deck["release_status"] == "not_release_ready"
            assert deck["structure_validation"]["reason"] == "presentation_structure_not_confirmed"
    else:
        assert deck["status"] == "validation_incomplete"
        assert deck["release_ready"] is False
        assert deck["release_status"] == "not_release_ready"
        assert deck["final_output"] is None
        assert "output" not in deck
        assert (project / deck["candidate_output"]["relative_path"]).is_file()
    persisted = json.loads((project / "workflow_v6.json").read_text(encoding="utf-8"))
    assert persisted["pages"][0]["state"] == "page_complete"


def test_finalize_maps_current_confirmed_ui_contract_to_fixed_frame(tmp_path: Path):
    project = _project(tmp_path, 1)
    state = load(project)
    state["style_confirmation"]["contract"] = {
        "primary_color": "#17365D",
        "secondary_color": "#C7352B",
        "background_color": "#FFFFFF",
        "cjk_font": "Microsoft YaHei",
        "latin_font": "Arial",
        "title_size_pt": 28,
        "body_size_pt": 12,
        "caption_size_pt": 9,
        "regional_characteristics": "",
        "visual_description": "Formal editorial presentation.",
    }
    save(project, state)
    reconstructed_body = tmp_path / "current-ui-body.pptx"
    _body(reconstructed_body, "Editable accepted body")

    report = finalize_reconstructed_page(
        project, page_number=1, reconstructed_body=reconstructed_body,
    )

    assert report["fixed_frame"]["passed"] is True


def test_finalize_recovers_same_body_left_by_pre_frame_failure(tmp_path: Path):
    project = _project(tmp_path, 1)
    state = load(project)
    state["style_confirmation"]["contract"] = {}
    save(project, state)
    reconstructed_body = tmp_path / "recoverable-body.pptx"
    _body(reconstructed_body, "Editable accepted body")

    with pytest.raises(ValueError, match="fixed frame"):
        finalize_reconstructed_page(
            project, page_number=1, reconstructed_body=reconstructed_body,
        )

    state = load(project)
    state["style_confirmation"]["contract"] = {
        "primary_color": "#17365D",
        "secondary_color": "#C7352B",
        "background_color": "#FFFFFF",
        "cjk_font": "Microsoft YaHei",
        "latin_font": "Arial",
        "title_size_pt": 28,
        "body_size_pt": 12,
        "caption_size_pt": 9,
        "regional_characteristics": "",
        "visual_description": "Formal editorial presentation.",
    }
    save(project, state)

    report = finalize_reconstructed_page(
        project, page_number=1, reconstructed_body=reconstructed_body,
    )

    assert report["fixed_frame"]["passed"] is True

from __future__ import annotations

import hashlib
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
from workflow_v6_state import load, save  # noqa: E402


def _remove_transient_pre_acceptance_inputs(project: Path) -> None:
    for name in ("00_source", "01_ui", "03_v6"):
        shutil.rmtree(project / name, ignore_errors=True)


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
    assert (project / deck["output"]).is_file()
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

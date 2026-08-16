from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from complex_page_experiment import (
    EvidenceRecorder,
    build_complete_page_material_view,
    open_live_page_workspace,
)
from complex_page_experiment.loop import AcceptedImageSeal, run_candidate_loop
from complex_page_experiment.provider import CandidateArtifact
from workflow_v6_contract import canonical_sha256, new_page, new_project
from workflow_v6_state import create, load, save


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _source_record(project: Path, relative: str) -> dict[str, object]:
    payload = (project / relative).read_bytes()
    return {
        "path": relative,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "byte_size": len(payload),
    }


def _live_project(tmp_path: Path, *, page_count: int = 5) -> Path:
    project = tmp_path / "live-project"
    (project / "00_source").mkdir(parents=True)
    (project / "00_source" / "source.docx").write_bytes(b"live-word-source")
    (project / "00_source" / "logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="4"/></svg>',
        encoding="utf-8",
    )

    visual = {
        "primary_color": "#17365D",
        "secondary_color": "#C7352B",
        "background_color": "#FFFFFF",
        "cjk_font": "Microsoft YaHei",
        "latin_font": "Arial",
        "title_size_pt": 28,
        "body_size_pt": 12,
        "caption_size_pt": 9,
        "regional_characteristics": "",
        "visual_description": "Confirmed live project",
    }
    pages = [new_page(number, title=f"Live page {number}") for number in range(1, page_count + 1)]
    state = new_project(
        word_source=_source_record(project, "00_source/source.docx"),
        logo_source=_source_record(project, "00_source/logo.svg"),
        pages=pages,
    )
    state["style_confirmation"] = {"status": "confirmed", "contract": visual}
    state["confirmed_ui_revision"] = 1
    state["confirmed_ui_digest"] = canonical_sha256(visual)
    state["page_materials_status"] = "confirmed"

    paginated_pages: list[dict[str, object]] = []
    for page in state["pages"]:
        number = page["page_number"]
        block = {
            "type": "paragraph",
            "text": f"Authoritative body {number}",
            "source_block_id": f"body-{number}",
            "source_block_index": number,
            "source_order": 1,
            "relationship_ids": [],
            "comment_ids": [],
        }
        material = {
            "page_number": number,
            "fixed_page_title": f"Live page {number}",
            "complete_word_content": [block],
            "original_comments": [],
            "word_images": [],
            "attachment_inputs": [],
            "visual_contract": visual,
            "body_frame": {
                "geometry_version": "fixed-canvas-cm-v2",
                "body_bounds_cm": {"x": 0.81, "y": 2.3, "w": 23.78, "h": 11.18},
                "body_pixels": {"width": 1904, "height": 896},
                "fixed_layers": ["title", "logo", "footer", "page_number"],
            },
        }
        material_path = project / "02_v6" / "awesome_page_materials" / f"page_{number:03d}.json"
        material_path.parent.mkdir(parents=True, exist_ok=True)
        material_bytes = _canonical(material)
        material_path.write_bytes(material_bytes)
        page["material_state"] = "available"
        page["material_receipt"] = {
            "schema_version": "awesome-page-materials-v1",
            "page_number": number,
            "path": material_path.relative_to(project).as_posix(),
            "digest": hashlib.sha256(material_bytes).hexdigest(),
        }
        paginated_pages.append(
            {
                "page_number": number,
                "fixed_page_title": f"Live page {number}",
                "fixed_page_title_source_block_id": f"title-{number}",
                "blocks": [block],
                "page_comments": [],
            }
        )

    (project / "02_v6" / "paginated_word_source.json").write_bytes(
        _canonical(
            {
                "schema_version": "1.0",
                "source_file": "source.docx",
                "page_count": page_count,
                "pages": paginated_pages,
            }
        )
    )
    (project / "02_v6" / "source_assets.json").write_bytes(
        _canonical({"schema_version": "1.0", "assets": []})
    )
    create(project, state)
    return project


def test_live_workspace_uses_project_in_place_and_accepts_actual_last_page(
    tmp_path: Path,
) -> None:
    project = _live_project(tmp_path, page_count=5)

    workspace = open_live_page_workspace(project, 5)

    assert workspace.source_project == project.resolve()
    assert workspace.project_copy == project.resolve()
    assert workspace.page_number == 5
    assert workspace.source_snapshot_sha256 == json.loads(
        (project / "workflow_v6.json").read_text(encoding="utf-8")
    )["source_identity"]
    assert workspace.experiment_root == (
        project / "04_v6" / "experiments" / "live-page-005"
    ).resolve()
    assert not (project.parent / "project").exists()


def test_complete_material_view_accepts_actual_project_last_page(tmp_path: Path) -> None:
    project = _live_project(tmp_path, page_count=5)
    workspace = open_live_page_workspace(project, 5)

    material_view = build_complete_page_material_view(workspace)

    assert material_view.value["page_number"] == 5
    assert material_view.value["fixed_page_title"] == "Live page 5"


@pytest.mark.parametrize("page_number", [0, 6, True])
def test_live_workspace_rejects_pages_outside_current_project(
    tmp_path: Path, page_number: int
) -> None:
    project = _live_project(tmp_path, page_count=5)

    with pytest.raises(ValueError, match="page number|out of range"):
        open_live_page_workspace(project, page_number)


def test_live_evidence_uses_explicit_validated_workflow_source_identity(
    tmp_path: Path,
) -> None:
    project = _live_project(tmp_path)
    workspace = open_live_page_workspace(project, 3)

    recorder = EvidenceRecorder(
        workspace.experiment_root,
        project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id,
        page_number=workspace.page_number,
        source_identity=workspace.source_snapshot_sha256,
    )

    assert recorder.page_number == 3
    assert recorder.source_snapshot_sha256 == workspace.source_snapshot_sha256


@pytest.mark.parametrize(
    ("experiment_id", "page_number", "source_identity"),
    [
        ("live-page-003", 2, None),
        ("live-page-002", 2, "f" * 64),
        ("live-page-006", 6, None),
    ],
)
def test_live_evidence_rejects_identity_outside_current_workflow(
    tmp_path: Path,
    experiment_id: str,
    page_number: int,
    source_identity: str | None,
) -> None:
    project = _live_project(tmp_path)
    current_identity = load(project)["source_identity"]
    root = project / "04_v6" / "experiments" / experiment_id
    root.mkdir(parents=True)

    with pytest.raises(ValueError, match="live evidence|page|source_identity"):
        EvidenceRecorder(
            root,
            project_copy=project,
            experiment_id=experiment_id,
            page_number=page_number,
            source_identity=source_identity or current_identity,
        )


def test_live_loop_rejects_changed_source_before_materials_or_external_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _live_project(tmp_path)
    workspace = open_live_page_workspace(project, 2)
    material_view = build_complete_page_material_view(workspace)
    recorder = EvidenceRecorder(
        workspace.experiment_root,
        project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id,
        page_number=workspace.page_number,
        source_identity=workspace.source_snapshot_sha256,
    )
    state = load(project)
    (project / "00_source" / "source.docx").write_bytes(b"changed-live-word-source")
    state["word_source"] = _source_record(project, "00_source/source.docx")
    state["source_identity"] = canonical_sha256(
        {"word_source": state["word_source"], "logo_source": state["logo_source"]}
    )
    save(project, state)
    calls = {"materials": 0, "director": 0, "provider": 0, "reviewer": 0}

    def materials(_workspace):
        calls["materials"] += 1
        return material_view

    def director(*_args, **_kwargs):
        calls["director"] += 1
        raise AssertionError("director called before live source rejection")

    def provider(*_args, **_kwargs):
        calls["provider"] += 1
        raise AssertionError("provider called before live source rejection")

    def reviewer(*_args, **_kwargs):
        calls["reviewer"] += 1
        raise AssertionError("reviewer called before live source rejection")

    monkeypatch.setattr(recorder, "refresh_from_disk", lambda: None)

    with pytest.raises(RuntimeError, match="source identity changed"):
        run_candidate_loop(
            workspace,
            timeout=1,
            recorder=recorder,
            material_view_factory=materials,
            director_invoke=director,
            provider_runner=provider,
            reviewer_invoke=reviewer,
        )

    assert calls == {"materials": 0, "director": 0, "provider": 0, "reviewer": 0}


def test_live_loop_rechecks_source_after_materials_before_director(
    tmp_path: Path,
) -> None:
    project = _live_project(tmp_path)
    workspace = open_live_page_workspace(project, 2)
    material_view = build_complete_page_material_view(workspace)
    recorder = EvidenceRecorder(
        workspace.experiment_root,
        project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id,
        page_number=workspace.page_number,
        source_identity=workspace.source_snapshot_sha256,
    )
    calls = {"director": 0, "provider": 0, "reviewer": 0}

    def materials(_workspace):
        state = load(project)
        (project / "00_source" / "source.docx").write_bytes(
            b"changed-during-material-view"
        )
        state["word_source"] = _source_record(project, "00_source/source.docx")
        state["source_identity"] = canonical_sha256(
            {
                "word_source": state["word_source"],
                "logo_source": state["logo_source"],
            }
        )
        save(project, state)
        return material_view

    def director(*_args, **_kwargs):
        calls["director"] += 1
        raise AssertionError("director called after material-stage source mutation")

    def provider(*_args, **_kwargs):
        calls["provider"] += 1
        raise AssertionError("provider called after material-stage source mutation")

    def reviewer(*_args, **_kwargs):
        calls["reviewer"] += 1
        raise AssertionError("reviewer called after material-stage source mutation")

    with pytest.raises(RuntimeError, match="source identity changed"):
        run_candidate_loop(
            workspace,
            timeout=1,
            recorder=recorder,
            material_view_factory=materials,
            director_invoke=director,
            provider_runner=provider,
            reviewer_invoke=reviewer,
        )

    assert calls == {"director": 0, "provider": 0, "reviewer": 0}


def test_live_accepted_resume_makes_zero_injected_external_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _live_project(tmp_path)
    workspace = open_live_page_workspace(project, 2)
    recorder = EvidenceRecorder(
        workspace.experiment_root,
        project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id,
        page_number=workspace.page_number,
        source_identity=workspace.source_snapshot_sha256,
    )
    candidate = CandidateArtifact(
        attempt=1,
        path=project / "04_v6" / "images" / "page_002.candidate_1.png",
        trace_path=project / "04_v6" / "traces" / "page_002.candidate_1.json",
        prompt_path=workspace.experiment_root / "prompt.txt",
        operation="generate",
        quality="high",
        selected_reference_ids=(),
        input_sha256s=(),
        prompt_sha256="1" * 64,
        request_identity="2" * 64,
        duration_seconds=1.0,
    )
    accepted = AcceptedImageSeal(
        receipt_path=project / "04_v6" / "images" / "page_002.json",
        candidate=candidate,
        receipt_sha256="3" * 64,
        recovered=True,
    )
    import complex_page_experiment.loop as loop

    monkeypatch.setattr(loop, "load_accepted_image_seal", lambda _workspace: accepted)

    outcome = run_candidate_loop(
        workspace,
        timeout=1,
        recorder=recorder,
        material_view_factory=lambda _workspace: pytest.fail("materials were read"),
        director_invoke=lambda *_args, **_kwargs: pytest.fail("director was called"),
        reviewer_invoke=lambda *_args, **_kwargs: pytest.fail("reviewer was called"),
        provider_runner=lambda *_args, **_kwargs: pytest.fail("provider was called"),
    )

    assert outcome.accepted is accepted
    summary = recorder.finalize()
    assert summary["call_totals"] == {
        "page_director": 0,
        "correction_decision": 0,
        "image2": 0,
        "visual_review": 0,
        "reconstruct_edit": 0,
    }
    assert summary["recovery"]["skipped_calls"] == [
        "page_director",
        "correction_decision",
        "image2",
        "visual_review",
        "reconstruct_edit",
    ]

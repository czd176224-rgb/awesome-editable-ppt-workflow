from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_subscription_runtime import CodexStructuredResult
from awesome_page_materials import collect_page_materials
from complex_page_experiment.evidence import EvidenceRecorder
from complex_page_experiment.loop import load_accepted_image_seal, run_candidate_loop
from complex_page_experiment.materials import build_complete_page_material_view
from complex_page_experiment.workspace import create_experiment_copy
from test_director import _director_value, _result
from test_loop import _review_result
from test_materials import _prepare_complete_page_one
from test_provider import _real_worker_runner


def _page_director_invoke(view, page_number: int):
    value = _director_value(view)
    value["page_number"] = page_number

    def invoke(_project: Path, **kwargs: object) -> CodexStructuredResult:
        assert kwargs["role"] == "awesome-page-director"
        return _result(value)

    return invoke


@pytest.mark.parametrize("page_number", [2, 4])
def test_workspace_snapshot_authorizes_exact_selected_page(
    awesome_four_page_project: Path, tmp_path: Path, page_number: int
) -> None:
    root = tmp_path / f"page-{page_number}"
    workspace = create_experiment_copy(
        awesome_four_page_project,
        root,
        experiment_id=f"round-one-page-{page_number}",
        page_number=page_number,
    )
    snapshot = json.loads((root / "source_snapshot.json").read_text(encoding="utf-8"))
    assert workspace.page_number == page_number
    assert snapshot["page_number"] == page_number
    assert snapshot["experiment_scope"] == {
        f"page_{page_number:03d}": {"page_number": page_number}
    }


def test_page_two_runs_the_existing_vertical_loop_and_recovers_without_provider(
    awesome_four_page_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_complete_page_one(awesome_four_page_project)
    asset_manifest = awesome_four_page_project / "02_v6" / "source_assets.json"
    assets = json.loads(asset_manifest.read_text(encoding="utf-8"))
    for asset in assets["assets"]:
        if asset["asset_id"] == "appendix-pdf":
            asset["page_numbers"] = [1, 2, 3, 4]
    asset_manifest.write_text(
        json.dumps(assets, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    page_material_path = (
        awesome_four_page_project / "02_v6" / "awesome_page_materials" / "page_002.json"
    )
    sealed_page_two = json.loads(page_material_path.read_text(encoding="utf-8"))
    current_page_two = collect_page_materials(awesome_four_page_project, 2)
    current_page_two["attachment_inputs"][0]["render_receipt"] = (
        sealed_page_two["attachment_inputs"][0]["render_receipt"]
    )
    page_material_bytes = (
        json.dumps(
            current_page_two,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    page_material_path.write_bytes(page_material_bytes)
    state_path = awesome_four_page_project / "workflow_v6.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    import hashlib

    state["pages"][1]["material_receipt"]["digest"] = hashlib.sha256(
        page_material_bytes
    ).hexdigest()
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    workspace = create_experiment_copy(
        awesome_four_page_project,
        tmp_path / "page-two-experiment",
        experiment_id="round-one-page-2",
        page_number=2,
    )
    view = build_complete_page_material_view(workspace)
    assert view.value["page_number"] == 2
    assert view.value["fixed_page_title"] == "Awesome page 2"
    assert view.value["complete_word_content"][0]["text"] == "Authoritative body 2"

    evidence_root = (
        workspace.project_copy / "04_v6" / "experiments" / workspace.experiment_id
    )
    evidence_root.mkdir(parents=True, exist_ok=True)
    recorder = EvidenceRecorder(
        evidence_root,
        project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id,
    )
    provider_calls: list[list[str]] = []
    outcome = run_candidate_loop(
        workspace,
        timeout=17,
        recorder=recorder,
        material_view_factory=lambda _workspace: view,
        director_invoke=_page_director_invoke(view, 2),
        reviewer_invoke=lambda *_args, **_kwargs: _review_result("accept"),
        provider_runner=_real_worker_runner(monkeypatch, provider_calls),
    )
    assert outcome.status == "accepted"
    assert outcome.accepted is not None
    assert outcome.accepted.candidate.path.name.startswith("page_002.")
    assert (
        workspace.project_copy / "04_v6" / "images" / "page_002.json"
    ).is_file()
    assert len(provider_calls) == 1
    recorder.finalize()

    events = [
        json.loads(line)
        for line in (evidence_root / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events
    assert {event["page_number"] for event in events} == {2}

    recovered_recorder = EvidenceRecorder(
        evidence_root,
        project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id,
    )

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("accepted page recovery must not repeat any model or Provider call")

    recovered = run_candidate_loop(
        workspace,
        timeout=17,
        recorder=recovered_recorder,
        material_view_factory=forbidden,
        director_invoke=forbidden,
        reviewer_invoke=forbidden,
        provider_runner=forbidden,
    )
    assert recovered.status == "accepted"
    assert recovered.accepted is not None and recovered.accepted.recovered is True
    assert len(provider_calls) == 1
    assert load_accepted_image_seal(workspace) is not None

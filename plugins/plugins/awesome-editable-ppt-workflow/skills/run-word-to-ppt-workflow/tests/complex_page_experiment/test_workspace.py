from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from complex_page_experiment import (
    create_experiment_copy,
    fingerprint_project,
    verify_source_unchanged,
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _state(project: Path) -> dict:
    return json.loads((project / "workflow_v6.json").read_text(encoding="utf-8"))


def test_fingerprint_is_sorted_and_changes_with_file_bytes(
    awesome_four_page_project: Path,
):
    before = fingerprint_project(awesome_four_page_project)

    assert set(before) == {"files", "tree_sha256"}
    assert [item["path"] for item in before["files"]] == sorted(
        item["path"] for item in before["files"]
    )
    assert all(set(item) == {"path", "sha256", "byte_size"} for item in before["files"])
    assert len(before["tree_sha256"]) == 64

    source = awesome_four_page_project / "00_source" / "source.docx"
    source.write_bytes(source.read_bytes() + b"-changed")
    after = fingerprint_project(awesome_four_page_project)
    assert after["tree_sha256"] != before["tree_sha256"]


def test_copy_accepts_production_source_receipts_without_optional_byte_size(
    awesome_four_page_project: Path, tmp_path: Path
):
    state_path = awesome_four_page_project / "workflow_v6.json"
    state = _state(awesome_four_page_project)
    state["word_source"].pop("byte_size", None)
    state["logo_source"].pop("byte_size", None)
    state["source_identity"] = hashlib.sha256(
        json.dumps(
            {"word_source": state["word_source"], "logo_source": state["logo_source"]},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    workspace = create_experiment_copy(
        awesome_four_page_project,
        tmp_path / "production-shape-copy",
        experiment_id="production-shape",
    )

    assert workspace.project_copy.joinpath("00_source/source.docx").is_file()


def test_copy_preserves_full_four_page_baseline_and_records_page_one_scope(
    awesome_four_page_project: Path,
    tmp_path: Path,
):
    source_state = _state(awesome_four_page_project)
    source_state_bytes = [
        json.dumps(page, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        for page in source_state["pages"]
    ]
    protected_paths = [
        "00_source/source.docx",
        "00_source/logo.svg",
        "01_source_assets/appendix.pdf",
        "02_v6/attachment_renders/appendix/page_0001.png",
        *[
            f"02_v6/awesome_page_materials/page_{number:03d}.json"
            for number in range(1, 5)
        ],
    ]
    protected_hashes = {
        relative: _digest(awesome_four_page_project / relative) for relative in protected_paths
    }
    experiment_root = tmp_path / "experiments" / "complex-page-001"

    workspace = create_experiment_copy(
        awesome_four_page_project,
        experiment_root,
        experiment_id="complex-page-001",
    )

    copied_state = _state(workspace.project_copy)
    assert workspace.experiment_id == "complex-page-001"
    assert workspace.source_project == awesome_four_page_project.resolve()
    assert workspace.experiment_root == experiment_root.resolve()
    assert workspace.project_copy == experiment_root.resolve() / "project"
    assert workspace.page_number == 1
    assert len(workspace.source_snapshot_sha256) == 64
    assert (workspace.project_copy / "workflow_v6.json").read_bytes() == (
        awesome_four_page_project / "workflow_v6.json"
    ).read_bytes()
    assert [page["page_number"] for page in copied_state["pages"]] == [1, 2, 3, 4]
    assert [page["title"] for page in copied_state["pages"]] == [
        "Awesome page 1",
        "Awesome page 2",
        "Awesome page 3",
        "Awesome page 4",
    ]
    assert copied_state["source_identity"] == source_state["source_identity"]
    assert copied_state["confirmed_ui_digest"] == source_state["confirmed_ui_digest"]
    assert [page["material_receipt"] for page in copied_state["pages"]] == [
        page["material_receipt"] for page in source_state["pages"]
    ]
    copied_state_bytes = [
        json.dumps(page, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        for page in copied_state["pages"]
    ]
    assert copied_state_bytes[1:] == source_state_bytes[1:]
    assert {
        relative: _digest(workspace.project_copy / relative) for relative in protected_paths
    } == protected_hashes

    snapshot = json.loads(
        (workspace.experiment_root / "source_snapshot.json").read_text(encoding="utf-8")
    )
    assert snapshot["experiment_id"] == "complex-page-001"
    assert snapshot["page_number"] == 1
    assert snapshot["source_snapshot_sha256"] == workspace.source_snapshot_sha256
    assert set(snapshot["experiment_scope"]) == {"page_001"}


def test_copy_requires_absent_nonoverlapping_target(
    awesome_four_page_project: Path,
    tmp_path: Path,
):
    existing = tmp_path / "already-exists"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        create_experiment_copy(
            awesome_four_page_project, existing, experiment_id="existing"
        )

    with pytest.raises(ValueError, match="overlap"):
        create_experiment_copy(
            awesome_four_page_project,
            awesome_four_page_project / "experiment",
            experiment_id="nested-target",
        )
    with pytest.raises(ValueError, match="overlap"):
        create_experiment_copy(
            awesome_four_page_project,
            awesome_four_page_project.parent,
            experiment_id="parent-target",
        )


def test_only_pages_in_the_four_page_baseline_are_authorized(
    awesome_four_page_project: Path, tmp_path: Path
):
    with pytest.raises(ValueError, match="pages 1 through 4"):
        create_experiment_copy(
            awesome_four_page_project,
            tmp_path / "wrong-page",
            experiment_id="wrong-page",
            page_number=5,
        )


def test_fingerprint_rejects_a_source_symlink(
    awesome_four_page_project: Path,
    tmp_path: Path,
):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"must-never-be-read-as-project-content")
    link = awesome_four_page_project / "00_source" / "outside-link.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"test account cannot create symlinks: {exc}")

    with pytest.raises(ValueError, match="reparse"):
        fingerprint_project(awesome_four_page_project)


@pytest.mark.parametrize(
    ("kind", "relative"),
    [
        ("prompt", "02_v6/page_image_prompts/page_001.output.json"),
        ("candidate", "04_v6/images/page_001/attempt_001.png"),
        ("acceptance", "04_v6/images/page_001.json"),
        ("reconstruction", "05_v6/reconstruction_requests/page_001.json"),
    ],
)
def test_copy_rejects_baseline_outputs(
    awesome_four_page_project: Path,
    tmp_path: Path,
    kind: str,
    relative: str,
):
    output = awesome_four_page_project / relative
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"pre-existing experiment output")

    with pytest.raises(ValueError, match=kind):
        create_experiment_copy(
            awesome_four_page_project,
            tmp_path / f"dirty-{kind}",
            experiment_id=f"dirty-{kind}",
        )


def test_copy_rejects_candidate_or_accepted_state(
    awesome_four_page_project: Path,
    tmp_path: Path,
):
    state_path = awesome_four_page_project / "workflow_v6.json"
    state = _state(awesome_four_page_project)
    state["pages"][0]["state"] = "accepted"
    state["pages"][0]["first_candidate"] = {"path": "candidate.png", "attempt": 1}
    state["pages"][0]["selected_candidate"] = {"path": "candidate.png", "attempt": 1}
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="candidate|acceptance"):
        create_experiment_copy(
            awesome_four_page_project,
            tmp_path / "dirty-state",
            experiment_id="dirty-state",
        )


def test_copy_rejects_material_with_self_consistent_but_false_attachment_digest(
    awesome_four_page_project: Path,
    tmp_path: Path,
):
    material_path = (
        awesome_four_page_project
        / "02_v6"
        / "awesome_page_materials"
        / "page_001.json"
    )
    material = json.loads(material_path.read_text(encoding="utf-8"))
    material["attachment_inputs"][0]["sha256"] = "0" * 64
    material["attachment_inputs"][0]["render_receipt"]["original_sha256"] = "0" * 64
    payload = (
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    material_path.write_bytes(payload)
    state_path = awesome_four_page_project / "workflow_v6.json"
    state = _state(awesome_four_page_project)
    state["pages"][0]["material_receipt"]["digest"] = hashlib.sha256(payload).hexdigest()
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="attachment.*digest|source.*digest"):
        create_experiment_copy(
            awesome_four_page_project,
            tmp_path / "false-inner-receipt",
            experiment_id="false-inner-receipt",
        )


def test_copy_rejects_windows_backslash_escape_in_material_source_path(
    awesome_four_page_project: Path,
    tmp_path: Path,
):
    outside = awesome_four_page_project.parent / "outside.bin"
    outside.write_bytes(b"external-content-must-not-enter-the-baseline")
    material_path = (
        awesome_four_page_project
        / "02_v6"
        / "awesome_page_materials"
        / "page_001.json"
    )
    material = json.loads(material_path.read_text(encoding="utf-8"))
    attachment = material["attachment_inputs"][0]
    attachment["path"] = "..\\outside.bin"
    attachment["sha256"] = _digest(outside)
    attachment["byte_size"] = outside.stat().st_size
    attachment["render_receipt"]["original_path"] = attachment["path"]
    attachment["render_receipt"]["original_sha256"] = attachment["sha256"]
    attachment["render_receipt"]["original_byte_size"] = attachment["byte_size"]
    payload = (
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    material_path.write_bytes(payload)
    state_path = awesome_four_page_project / "workflow_v6.json"
    state = _state(awesome_four_page_project)
    state["pages"][0]["material_receipt"]["digest"] = hashlib.sha256(payload).hexdigest()
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="path is invalid|outside"):
        create_experiment_copy(
            awesome_four_page_project,
            tmp_path / "escaped-source",
            experiment_id="escaped-source",
        )


def test_source_mutation_after_copy_is_detected(
    awesome_four_page_project: Path,
    tmp_path: Path,
):
    workspace = create_experiment_copy(
        awesome_four_page_project,
        tmp_path / "source-mutation",
        experiment_id="source-mutation",
    )
    verify_source_unchanged(workspace)

    source = awesome_four_page_project / "00_source" / "source.docx"
    source.write_bytes(source.read_bytes() + b"-mutated-after-copy")
    with pytest.raises(RuntimeError, match="source.*changed"):
        verify_source_unchanged(workspace)

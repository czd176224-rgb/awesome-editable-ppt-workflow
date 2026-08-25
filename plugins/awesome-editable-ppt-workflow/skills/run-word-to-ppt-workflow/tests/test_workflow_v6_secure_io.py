from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import workflow_v6_secure_io as secure_io  # noqa: E402
import workflow_v6_image  # noqa: E402
IMAGE_SCRIPTS = Path(__file__).resolve().parents[2] / "generate-slide-body-image" / "scripts"
if str(IMAGE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(IMAGE_SCRIPTS))
import provider_keyring  # noqa: E402
import codex_gpt_image  # noqa: E402


pytestmark = pytest.mark.skipif(os.name != "nt", reason="real Windows junction race test")


def _junction(link: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)


@pytest.mark.parametrize("swap_component", ["04_v6", "images"])
def test_atomic_write_holds_every_ancestor_against_junction_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, swap_component: str,
) -> None:
    project = tmp_path / "project"
    target_parent = project / "04_v6" / "images"
    target_parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    attempted: list[str] = []

    def attack(_root: Path, _relative: Path, stage: str) -> None:
        if stage != "before_publish" or attempted:
            return
        attempted.append(stage)
        victim = project / "04_v6" if swap_component == "04_v6" else target_parent
        moved = victim.with_name(victim.name + ".moved")
        move = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Move-Item -LiteralPath $args[0] -Destination $args[1]", str(victim), str(moved)],
            capture_output=True,
            text=True,
            check=False,
        )
        if move.returncode == 0:
            _junction(victim, outside)

    monkeypatch.setattr(secure_io, "_secure_io_boundary", attack)
    try:
        secure_io.atomic_write_bytes(project, Path("04_v6/images/result.bin"), b"correct")
    except (OSError, ValueError):
        pass

    assert attempted == ["before_publish"]
    assert list(outside.iterdir()) == []
    candidates = list(project.rglob("result.bin"))
    assert not candidates or candidates == [project / "04_v6" / "images" / "result.bin"]
    if candidates:
        assert candidates[0].read_bytes() == b"correct"


def test_atomic_replace_and_stable_read_use_canonical_relative_path(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "04_v6" / "images").mkdir(parents=True)
    relative = Path("04_v6/images/result.bin")
    secure_io.atomic_write_bytes(project, relative, b"one")
    secure_io.atomic_write_bytes(project, relative, b"two", replace=True)
    assert secure_io.read_bytes(project, relative) == b"two"
    with pytest.raises(ValueError):
        secure_io.atomic_write_bytes(project, Path("../escape.bin"), b"bad")
    assert not (tmp_path / "escape.bin").exists()


def test_capability_publish_cannot_escape_after_parent_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    (project / "04_v6" / "image_request_capabilities").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    attempted: list[bool] = []

    def attack(_root: Path, _relative: Path, stage: str) -> None:
        if stage != "before_publish" or attempted:
            return
        attempted.append(True)
        victim = project / "04_v6" / "image_request_capabilities"
        moved = victim.with_name("image_request_capabilities.moved")
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Move-Item -LiteralPath $args[0] -Destination $args[1]", str(victim), str(moved)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            _junction(victim, outside)

    monkeypatch.setattr(secure_io, "_secure_io_boundary", attack)
    monkeypatch.setattr(workflow_v6_image, "load", lambda _root: {
        "plugin_id": "awesome-editable-ppt-workflow", "plugin_version": "1.2.1",
        "workflow_contract": "awesome-word-ppt-workflow-v1", "source_identity": "s",
        "word_source": {}, "logo_source": {}, "pages": [{}],
    })
    monkeypatch.setattr(workflow_v6_image, "_material_authority", lambda *_args: ({}, b"{}", "m"))
    monkeypatch.setattr(workflow_v6_image, "_canonical_prompt_paths", lambda *_args: (
        project / "prompt.json", project / "prompt.receipt.json"
    ))
    monkeypatch.setattr(workflow_v6_image.v6_media, "_read_file_limited", lambda *_args: b"{}")
    monkeypatch.setattr(workflow_v6_image, "signing_key", lambda: ("test", b"k" * 32))
    request = workflow_v6_image.ImageRequest(
        operation="generate", quality="medium", prompt="p", input_images=(), image_roles=(),
        source_identity="s", page_material_digest="m", prompt_output_sha256="p",
        project_root=project, page_number=1,
    )
    try:
        workflow_v6_image._issue_capability(request, attempt=1)
    except (OSError, ValueError):
        pass
    assert attempted == [True]
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("kind", ["project_root", "project_ancestor"])
def test_project_root_and_ancestor_junctions_are_rejected_without_outside_write(
    tmp_path: Path, kind: str,
) -> None:
    outside = tmp_path / "outside"
    (outside / "04_v6" / "images").mkdir(parents=True)
    if kind == "project_root":
        project = tmp_path / "project"
        _junction(project, outside)
    else:
        real_parent = tmp_path / "real-parent"
        project = real_parent / "project"
        project.mkdir(parents=True)
        linked_parent = tmp_path / "linked-parent"
        _junction(linked_parent, real_parent)
        project = linked_parent / "project"
    with pytest.raises((OSError, ValueError)):
        secure_io.atomic_write_bytes(project, Path("04_v6/images/escape.bin"), b"bad")
    assert not (outside / "04_v6" / "images" / "escape.bin").exists()


@pytest.mark.parametrize("kind", ["project_root", "project_ancestor"])
def test_production_image_entry_rejects_literal_project_junction_before_capability(
    tmp_path: Path, kind: str,
) -> None:
    real_parent = tmp_path / "real-parent"
    real_project = real_parent / "project"
    real_project.mkdir(parents=True)
    if kind == "project_root":
        linked = tmp_path / "linked-project"
        _junction(linked, real_project)
    else:
        linked_parent = tmp_path / "linked-parent"
        _junction(linked_parent, real_parent)
        linked = linked_parent / "project"
    with pytest.raises((OSError, ValueError), match="reparse"):
        workflow_v6_image.load_validated_image_request(linked, 1)
    assert not (real_project / "04_v6" / "image_request_capabilities").exists()


@pytest.mark.parametrize("kind", ["project_root", "project_ancestor"])
def test_production_reconstruction_entry_rejects_literal_project_junction_before_artifact(
    tmp_path: Path, kind: str,
) -> None:
    real_parent = tmp_path / "real-parent"
    real_project = real_parent / "project"
    real_project.mkdir(parents=True)
    if kind == "project_root":
        linked = tmp_path / "linked-project"
        _junction(linked, real_project)
    else:
        linked_parent = tmp_path / "linked-parent"
        _junction(linked_parent, real_parent)
        linked = linked_parent / "project"
    from workflow_v6_reconstruction import build_reconstruction_request
    with pytest.raises((OSError, ValueError), match="reparse"):
        build_reconstruction_request(linked, page_number=1)
    assert not (real_project / "05_v6" / "reconstruction_requests").exists()


def test_pipeline_dispatch_rejects_composition_directory_junction(tmp_path: Path) -> None:
    from workflow_v6_pipeline import PipelineConfiguration, PipelineDependencies, run_pages

    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside-composition"
    outside.mkdir()
    (outside / "page_composition.json").write_text("{}", encoding="utf-8")
    _junction(project / "02_v6", outside)
    calls: list[int] = []

    with pytest.raises((OSError, ValueError)):
        run_pages(
            project, [1],
            dependencies=PipelineDependencies(
                open_workspace=lambda root, page: calls.append(page),
                evidence_recorder=lambda workspace: object(), candidate_loop=lambda workspace, **kwargs: {},
            ),
            configuration=PipelineConfiguration(page_workers=1, initial_page_concurrency=1, maximum_page_concurrency=1),
        )

    assert calls == []


def test_keyring_rejects_plugin_secrets_junction_without_outside_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    outside = tmp_path / "outside-secrets"
    outside.mkdir()
    _junction(codex_home / "plugin-secrets", outside)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    with pytest.raises((OSError, ValueError)):
        provider_keyring.signing_key()
    assert list(outside.iterdir()) == []


def test_journal_signing_key_id_survives_one_rotation_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    project = tmp_path / "project"
    project.mkdir()
    journal = project / "04_v6/image_request_capabilities/journal/n.json"
    capability = {"nonce": "n", "attempt": 1, "operation": "generate", "input_sha256s": []}
    authority = codex_gpt_image.AuthorityContext(
        capability=capability, journal=journal, project=project,
        output=project / "04_v6/images/out.png", trace=project / "04_v6/images/out.trace.json",
    )
    digest = __import__("hashlib").sha256(__import__("json").dumps(
        capability, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    codex_gpt_image._atomic_journal(journal, {
        "schema_version": "awesome-image-submission-v2", "nonce": "n", "attempt": 1,
        "operation": "generate", "input_sha256s": [], "capability_sha256": digest,
        "generation": 1, "owner": "test", "state": "response_received", "network_started": True,
    })
    old_key = __import__("json").loads(journal.read_text())["key_id"]
    new_key = provider_keyring.rotate()
    assert new_key != old_key
    assert codex_gpt_image._verified_journal(authority)["key_id"] == old_key

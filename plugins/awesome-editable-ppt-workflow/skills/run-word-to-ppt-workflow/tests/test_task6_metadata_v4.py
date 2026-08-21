from __future__ import annotations

import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[5]
PLUGIN = REPO / "plugins/awesome-editable-ppt-workflow"


def test_release_metadata_is_consistently_v6_version() -> None:
    package = json.loads((REPO / "package-info.json").read_text(encoding="utf-8"))
    manifest = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    marketplace = json.loads((REPO / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
    assert package["pluginVersion"] == manifest["version"] == "1.0.1"
    assert package["releaseTag"] == "v1.0.1"
    assert package["workflowContractVersion"] == "awesome-word-ppt-workflow-v1"
    assert package["promptContractVersion"] == "page-prompt-v6-adaptive-confirmed-materials"
    assert package["qaPolicyVersion"] == "sole-independent-visual-review-v1"
    assert package["apiKeyRequired"] is False
    assert package["marketplacePreviewIdentity"] == marketplace["name"] == "editable-ppt-public"
    assert marketplace["interface"]["displayName"].endswith("1.0.1")
    assert package["bodyImageAspectPolicy"] == "dynamic-centered-17:8-crop-then-uniform-1904x896-no-stretch"
    assert package["everyPageCallsImage2"] is True
    assert package["initialImageEndpoint"] == "adaptive-images/generate-or-edit"
    assert package["localRepairEndpoint"] == "same-provider-at-most-two-review-directed-corrections"
    assert package["pageImagePolicy"] == "generate-without-refs-edit-with-confirmed-refs"
    assert package["qaPolicy"] == "sole-independent-visual-review-over-final-adapted-candidate"


def test_active_v6_docs_do_not_advertise_removed_production_semantics() -> None:
    paths = [
        REPO / "README.md",
        PLUGIN / "README.md",
        PLUGIN / "skills/run-word-to-ppt-workflow/SKILL.md",
    ]
    banned = (
        "word-ppt-workflow-v3",
        "word-ppt-workflow-v5",
        "reference images cause an Images edit request",
        "mandatory Office validation",
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "awesome-word-ppt-workflow-v1" in text, path
        assert "17:8" in text, path
        assert all(token not in text for token in banned), path


def test_subscription_runtime_has_no_api_key_or_openai_sdk_prerequisite() -> None:
    doctor = (PLUGIN / "skills/run-word-to-ppt-workflow/scripts/doctor.py").read_text(encoding="utf-8")
    requirements = (PLUGIN / "skills/run-word-to-ppt-workflow/requirements.txt").read_text(encoding="utf-8")
    pyproject = (PLUGIN / "skills/reconstruct-editable-slide/cli/pyproject.toml").read_text(encoding="utf-8")
    assert '"api_key_required": False' in doctor
    assert "codex-app-server" in doctor
    assert not any(line.strip().lower().startswith("openai") for line in requirements.splitlines())
    assert '"openai' not in pyproject.lower() and "'openai" not in pyproject.lower()

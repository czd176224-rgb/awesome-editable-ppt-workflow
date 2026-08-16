from __future__ import annotations

import sys
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def test_v4_workflow_version_is_independent_from_unchanged_v2_geometry() -> None:
    from workflow_contract import GEOMETRY_VERSION, WORKFLOW_VERSION, version_vector

    assert WORKFLOW_VERSION == "word-ppt-workflow-v4"
    assert GEOMETRY_VERSION == "fixed-canvas-cm-v2"
    assert version_vector() == {
        "workflow_contract_version": "word-ppt-workflow-v4",
        "geometry_contract_version": "fixed-canvas-cm-v2",
        "prompt_contract_version": "page-prompt-v8",
        "qa_policy_version": "risk-qa-v5",
        "reconstruction_version": "editable-image-v3",
        "fixed_layer_version": "native-layer-v3",
    }


def test_only_v4_workflow_projects_are_accepted() -> None:
    from workflow_contract import require_v4

    require_v4({"workflow_contract_version": "word-ppt-workflow-v4"})
    with pytest.raises(ValueError, match="word-ppt-workflow-v4"):
        require_v4({"workflow_contract_version": "fixed-canvas-cm-v2"})


def test_cache_key_accepts_v4_workflow_with_v2_geometry() -> None:
    from cache_key import CacheKeyInputs

    value = CacheKeyInputs(
        workflow_contract_version="word-ppt-workflow-v4",
        full_source_sha256="1" * 64,
        style_execution_sha256="2" * 64,
        page_asset_inputs=[],
        generation_parameters={"model": "gpt-image-2"},
        repair_feedback={"repair_scope": "none", "issues": []},
        reconstruction_version="editable-image-v3",
        geometry_version="fixed-canvas-cm-v2",
        fixed_layer_version="native-layer-v3",
        title_sha256="3" * 64,
        logo_sha256="4" * 64,
        page_number=1,
    )

    assert value.payload["workflow_contract_version"] == "word-ppt-workflow-v4"
    assert value.payload["geometry_version"] == "fixed-canvas-cm-v2"


def test_project_template_is_ready_for_fresh_v4_preparation() -> None:
    skill_root = Path(__file__).resolve().parents[1]
    template = json.loads((skill_root / "template" / "project.json").read_text(encoding="utf-8"))
    schema = json.loads((skill_root / "schemas" / "project_config.schema.json").read_text(encoding="utf-8"))

    assert template["workflow_contract_version"] == "word-ppt-workflow-v4"
    template["created_date"] = "2026-07-31"
    assert not list(Draft202012Validator(schema).iter_errors(template))


@pytest.mark.parametrize("schema_name", ["source_lock.schema.json", "page_contract.schema.json"])
def test_persisted_contract_schemas_require_v4_version(schema_name: str) -> None:
    skill_root = Path(__file__).resolve().parents[1]
    schema = json.loads((skill_root / "schemas" / schema_name).read_text(encoding="utf-8"))

    assert "workflow_contract_version" in schema["required"]

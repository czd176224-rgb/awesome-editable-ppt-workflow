"""Release-facing metadata checks for the Awesome plugin identity."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "awesome-editable-ppt-workflow"


def test_public_metadata_exposes_only_the_awesome_v1_product():
    package = json.loads((ROOT / "package-info.json").read_text(encoding="utf-8"))
    manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))

    assert manifest == {
        **manifest,
        "name": "awesome-editable-ppt-workflow",
        "version": "1.1.0",
    }
    assert package["plugin"] == "awesome-editable-ppt-workflow"
    assert package["pluginVersion"] == manifest["version"] == "1.1.0"
    assert package["releaseTag"] == "v1.1.0"
    assert package["workflowContractVersion"] == "awesome-word-ppt-workflow-v1"
    assert package["promptContractVersion"] == "consulting-page-director-v2-six-part-image2"
    assert package["qaPolicyVersion"] == "sole-independent-consulting-visual-review-v2"
    assert marketplace["plugins"] == [{
        **marketplace["plugins"][0],
        "name": "awesome-editable-ppt-workflow",
        "source": {"source": "local", "path": "./plugins/awesome-editable-ppt-workflow"},
    }]


def test_packaged_runtime_has_no_legacy_director_schema_or_compiler() -> None:
    runtime = PLUGIN / "skills" / "run-word-to-ppt-workflow"
    files = [path for path in runtime.rglob("*") if path.is_file()]
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in files
        if path.suffix.lower() in {".py", ".json", ".md"}
        and "tests" not in path.parts
    )

    assert not (runtime / "schemas" / "complex_page_director_v1.schema.json").exists()
    assert "def compile_six_part_prompt" not in text
    assert '"awesome-complex-page-director-v1"' not in text
    assert "scene_and_composition" not in text

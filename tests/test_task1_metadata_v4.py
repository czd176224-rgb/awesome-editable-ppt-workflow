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
        "version": "1.0.0",
    }
    assert package["plugin"] == "awesome-editable-ppt-workflow"
    assert package["pluginVersion"] == manifest["version"] == "1.0.0"
    assert package["releaseTag"] == "v1.0.0"
    assert package["workflowContractVersion"] == "awesome-word-ppt-workflow-v1"
    assert marketplace["plugins"] == [{
        **marketplace["plugins"][0],
        "name": "awesome-editable-ppt-workflow",
        "source": {"source": "local", "path": "./plugins/awesome-editable-ppt-workflow"},
    }]

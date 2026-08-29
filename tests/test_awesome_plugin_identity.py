"""Product-boundary tests for the independent Awesome plugin."""

from __future__ import annotations

import json
import ast
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "awesome-editable-ppt-workflow"
PLUGIN_VERSION = "1.2.2"
RELEASE_TAG = "v1.2.2"
WORKFLOW_CONTRACT = "awesome-word-ppt-workflow-v1"
PLUGIN_ROOT = REPO_ROOT / "plugins" / PLUGIN_ID


def test_awesome_plugin_has_its_own_public_identity():
    """Changing an installed identity must not leave the new product unavailable."""
    manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    package = json.loads((REPO_ROOT / "package-info.json").read_text(encoding="utf-8"))

    assert manifest["name"] == PLUGIN_ID
    assert manifest["version"] == PLUGIN_VERSION
    assert package["plugin"] == PLUGIN_ID
    assert package["pluginVersion"] == PLUGIN_VERSION
    assert package["releaseTag"] == RELEASE_TAG
    assert package["workflowContractVersion"] == WORKFLOW_CONTRACT


def test_installation_and_documentation_use_the_awesome_identity():
    """Installing the public product must target only its own cache and docs."""
    install = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
    uninstall = (REPO_ROOT / "uninstall.ps1").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "plugins\\awesome-editable-ppt-workflow" in install
    assert "plugins\\awesome-editable-ppt-workflow" in uninstall
    runtime_installer = (PLUGIN_ROOT / "scripts" / "install_runtime.ps1").read_text(encoding="utf-8")
    assert "awesome-editable-ppt-workflow-fixed-canvas-cm-v2" in runtime_installer
    assert "Awesome Editable PPT Workflow 1.2.2" in readme


def test_runtime_diagnostic_accepts_the_new_product_without_old_installed_plugin():
    """An independently copied runtime must pass its diagnostic from its new root."""
    scanner = PLUGIN_ROOT / "scripts" / "check_current_runtime.py"
    completed = subprocess.run(
        [sys.executable, str(scanner), "--repo-root", str(REPO_ROOT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_all_production_runtime_sources_are_free_of_old_plugin_discovery():
    """No shipped runtime may locate, import, or execute an installed old plugin."""
    forbidden = "plugins/cache/editable-ppt-public/editable-ppt-workflow"
    runtime_files = [
        path for path in PLUGIN_ROOT.rglob("*")
        if path.is_file() and (path.suffix in {".py", ".ps1"} or path.name == "SKILL.md")
    ]
    assert runtime_files
    for path in runtime_files:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if path.name != "check_current_runtime.py":
            assert forbidden not in text, path
        if path.name != "check_current_runtime.py":
            assert "editable-ppt-public/editable-ppt-workflow" not in text, path
        if path.suffix == ".py" and "tests" not in path.parts:
            tree = ast.parse(text, filename=str(path))
            imports = [
                node.module for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            ]
            imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
            assert not any("editable_ppt_workflow" in name.replace("-", "_") for name in imports), path


def test_release_pipeline_targets_the_same_plugin_identity():
    """CI, packaging, export, and public validation must ship the new product root."""
    release_files = [
        REPO_ROOT / ".github" / "workflows" / "ci.yml",
        REPO_ROOT / ".github" / "workflows" / "release.yml",
        REPO_ROOT / "scripts" / "release_gate.ps1",
        REPO_ROOT / "scripts" / "check_public_release.py",
        REPO_ROOT / "scripts" / "export_public_release.ps1",
        REPO_ROOT / "scripts" / "package_release.ps1",
    ]
    for path in release_files:
        text = path.read_text(encoding="utf-8-sig")
        assert "plugins/editable-ppt-workflow" not in text, path
        assert "awesome-editable-ppt-workflow" in text, path


def test_new_project_records_the_awesome_identity_and_rejects_legacy_state(tmp_path: Path):
    """Resume must refuse state that was not created by this product contract."""
    scripts = PLUGIN_ROOT / "skills" / "run-word-to-ppt-workflow" / "scripts"
    probe = "\n".join([
        "import json, sys",
        f"sys.path.insert(0, {str(scripts)!r})",
        "from workflow_v6_contract import new_page, new_project, validate_project",
        "project = new_project(word_source={'path':'word.docx'}, logo_source={'path':'logo.svg'}, pages=[new_page(1, title='One')])",
        "assert project['plugin_id'] == 'awesome-editable-ppt-workflow'",
        "assert project['plugin_version'] == '1.2.2'",
        "assert project['workflow_contract'] == 'awesome-word-ppt-workflow-v1'",
        "legacy = dict(project); legacy.pop('plugin_id')",
        "try: validate_project(legacy)\nexcept ValueError as error: print(error)\nelse: raise AssertionError('legacy project accepted')",
    ])
    completed = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=False)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Create a new project from the original Word document, SVG logo, and attachments." in completed.stdout

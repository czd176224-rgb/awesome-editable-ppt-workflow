"""Runtime boundary checks for the Awesome production entry."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    REPO_ROOT
    / "plugins"
    / "awesome-editable-ppt-workflow"
    / "skills"
    / "run-word-to-ppt-workflow"
    / "scripts"
)
ENTRY = SCRIPTS / "word_to_editable_ppt.py"
LEGACY_MODULES = tuple(SCRIPTS.glob("workflow_v4_*.py")) + tuple(SCRIPTS.glob("workflow_v5_*.py"))
PLUGIN_ROOT = REPO_ROOT / "plugins" / "awesome-editable-ppt-workflow"
EDITPPT_RUNTIME = PLUGIN_ROOT / "skills" / "reconstruct-editable-slide" / "cli" / "editppt" / "runtime"


def _guarded_entry(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the public entry with legacy imports made unavailable."""
    probe = "\n".join([
        "import importlib.abc, runpy, sys",
        "class LegacyImportBlocker(importlib.abc.MetaPathFinder):",
        "    def find_spec(self, fullname, path=None, target=None):",
        "        if any(token in fullname for token in ('workflow_v4', 'workflow_v5', 'editable_ppt_workflow', 'workflow_state', 'page_pipeline')):",
        "            raise ImportError('legacy runtime import blocked: ' + fullname)",
        "        return None",
        "sys.meta_path.insert(0, LegacyImportBlocker())",
        f"sys.argv = [{str(ENTRY)!r}, *{list(arguments)!r}]",
        f"runpy.run_path({str(ENTRY)!r}, run_name='__main__')",
    ])
    return subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _write_sources(tmp_path: Path) -> tuple[Path, Path]:
    word = tmp_path / "source.docx"
    with zipfile.ZipFile(word, "w") as archive:
        archive.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""")
        archive.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>""")
        archive.writestr("word/_rels/document.xml.rels", """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>""")
        archive.writestr("word/document.xml", """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
  <w:p><w:r><w:t>第1页</w:t></w:r></w:p>
  <w:p><w:r><w:t>Runtime boundary</w:t></w:r></w:p>
  <w:p><w:r><w:t>The production entry initializes an Awesome project.</w:t></w:r></w:p>
  <w:sectPr/>
</w:body></w:document>""")
    logo = tmp_path / "logo.svg"
    logo.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 20"/>', encoding="utf-8")
    return word, logo


def test_public_entry_initializes_and_rejects_old_projects_without_legacy_imports(tmp_path: Path):
    """A legacy import or legacy-state mutation must break the public runtime boundary."""
    word, logo = _write_sources(tmp_path)
    project = tmp_path / "new-project"

    initialized = _guarded_entry(
        "v6", "init", "--word", str(word), "--logo", str(logo), "--project", str(project),
    )

    assert initialized.returncode == 0, initialized.stdout + initialized.stderr
    state_path = project / "workflow_v6.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["workflow_contract"] == "awesome-word-ppt-workflow-v1"

    legacy_project = tmp_path / "legacy-project"
    legacy_project.mkdir()
    legacy_state = dict(state)
    legacy_state["plugin_id"] = "editable-ppt-workflow"
    (legacy_project / "workflow_v6.json").write_text(json.dumps(legacy_state), encoding="utf-8")
    original = (legacy_project / "workflow_v6.json").read_bytes()

    rejected = _guarded_entry("v6", "status", "--project", str(legacy_project))

    assert rejected.returncode != 0
    assert "Create a new project from the original Word document, SVG logo, and attachments." in rejected.stderr
    assert (legacy_project / "workflow_v6.json").read_bytes() == original


def test_no_v4_or_v5_production_modules_remain_in_the_public_runtime():
    """Retaining a V4/V5 module would leave an unsupported production route shippable."""
    assert not LEGACY_MODULES, [path.name for path in LEGACY_MODULES]


def test_legacy_project_marker_rejects_initialization_before_any_destination_mutation(tmp_path: Path):
    """Treating a legacy destination as empty would overwrite a user project on init."""
    word, logo = _write_sources(tmp_path)
    project = tmp_path / "legacy-project"
    project.mkdir()
    marker = project / "workflow_run.json"
    marker.write_bytes(b'{"legacy":"state"}\n')
    before = {path.relative_to(project): path.read_bytes() for path in project.rglob("*") if path.is_file()}

    rejected = _guarded_entry(
        "v6", "init", "--word", str(word), "--logo", str(logo), "--project", str(project),
    )

    after = {path.relative_to(project): path.read_bytes() for path in project.rglob("*") if path.is_file()}
    assert rejected.returncode != 0
    assert "Create a new project from the original Word document, SVG logo, and attachments." in rejected.stderr
    assert after == before


def test_wrong_or_missing_identity_mutations_create_no_v6_lock_or_files(tmp_path: Path):
    """Locking before identity validation leaves a mutation artifact on rejected projects."""
    ui = tmp_path / "result.json"
    ui.write_text("{}", encoding="utf-8")
    commands = (
        ("confirm-style", "--ui-result", str(ui)),
        ("run-pages", "--pages", "1"),
    )
    for label, payload in (("wrong", '{"plugin_id":"editable-ppt-workflow"}'), ("missing", "{}")):
        for command in commands:
            project = tmp_path / f"{label}-{command[0]}"
            project.mkdir()
            (project / "workflow_v6.json").write_text(payload, encoding="utf-8")
            before = {path.relative_to(project): path.read_bytes() for path in project.rglob("*") if path.is_file()}

            rejected = _guarded_entry("v6", command[0], "--project", str(project), *command[1:])

            after = {path.relative_to(project): path.read_bytes() for path in project.rglob("*") if path.is_file()}
            assert rejected.returncode != 0
            assert after == before
            assert not (project / ".workflow_v6.lock").exists()


def test_installer_and_editppt_runtime_have_no_deleted_helper_import_routes():
    """A fresh install must not retain a hidden dispatch into deleted V4 helpers."""
    installer = (PLUGIN_ROOT / "scripts" / "install_runtime.ps1").read_text(encoding="utf-8")
    main = (EDITPPT_RUNTIME / "main.py").read_text(encoding="utf-8")
    cache = (EDITPPT_RUNTIME / "editable_page_cache.py").read_text(encoding="utf-8")

    assert "import workflow_state, final_mechanical_qa" not in installer
    assert "workflow_v6_contract, workflow_v6_source, workflow_v6_image, workflow_v6_reconstruction" in installer
    assert '"record_page_result.py"' not in main
    assert '"finalize_deck_run.py"' not in main
    assert "from page_pipeline import" not in cache


def test_installer_v6_import_smoke_uses_shipped_runtime_modules_only():
    """The installer import command must resolve in a clean V6-only module path."""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(SCRIPTS), str(EDITPPT_RUNTIME.parents[1])))
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import editppt, workflow_v6_contract, workflow_v6_source, workflow_v6_image, workflow_v6_reconstruction; print('awesome-v6-workflow-boundary=ok')",
        ],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "awesome-v6-workflow-boundary=ok"

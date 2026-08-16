"""Regression tests for removal of legacy template surfaces."""

from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[5]
SCANNER_PATH = REPO_ROOT / "plugins/awesome-editable-ppt-workflow/scripts/check_current_runtime.py"
SPEC = importlib.util.spec_from_file_location("check_current_runtime", SCANNER_PATH)
assert SPEC and SPEC.loader
scanner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanner)


def test_scanner_reads_template_gitignore(tmp_path: Path):
    skill_root = tmp_path / "skill"
    template = skill_root / "template"
    template.mkdir(parents=True)
    gitignore = template / ".gitignore"
    gitignore.write_text("04_assets/master_visuals/*\n", encoding="utf-8")

    files = scanner._runtime_text_files(skill_root)
    findings = scanner._scan_tokens(skill_root, tmp_path)

    assert gitignore in files
    assert any("legacy generated page category" in finding for finding in findings)


def test_scanner_rejects_both_removed_legacy_template_directories(tmp_path: Path):
    skill_root = tmp_path / "skill"
    (skill_root / "template/04_assets/master_visuals").mkdir(parents=True)
    (skill_root / "template/07_editable/master").mkdir(parents=True)

    findings = scanner._scan_removed_paths(skill_root, tmp_path)

    assert any("template/04_assets/master_visuals" in finding for finding in findings)
    assert any("template/07_editable/master" in finding for finding in findings)


def test_scanner_rejects_obsolete_image_and_delivery_directories(tmp_path: Path):
    skill_root = tmp_path / "skill"
    for relative in ("template/06_images/approved", "template/06_images/draft", "template/09_deliverables"):
        (skill_root / relative).mkdir(parents=True)

    findings = scanner._scan_removed_paths(skill_root, tmp_path)

    assert any("template/06_images/approved" in finding for finding in findings)
    assert any("template/06_images/draft" in finding for finding in findings)
    assert any("template/09_deliverables" in finding for finding in findings)


def test_scanner_rejects_retired_regional_skill_name(tmp_path: Path):
    skill_root = tmp_path / "skill"
    scripts = skill_root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "entry.py").write_text('name = "zhejiang-ppt-v2"\n', encoding="utf-8")

    findings = scanner._scan_tokens(skill_root, tmp_path)

    assert any("retired regional skill name" in finding for finding in findings)


def test_skill_documents_v6_adaptive_reconstruction_without_manual_state_bypass():
    skill = (REPO_ROOT / "plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/SKILL.md").read_text(encoding="utf-8")
    assert "word_to_editable_ppt.py v6" in skill
    assert "Zero compiler-selected page references selects `generate`" in skill
    assert "one to sixteen compiler-selected page references selects `edit`" in skill
    assert "workflow_v6.json" in skill
    assert "editppt run record" not in skill


def test_current_command_registry_matches_runtime_policy():
    findings = scanner._scan_commands(
        REPO_ROOT / "plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow",
        REPO_ROOT,
    )

    assert findings == []


def test_scanner_rejects_legacy_import_in_any_active_v6_ui_module(tmp_path: Path):
    skill_root = tmp_path / "plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow"
    ui = skill_root / "scripts/confirm_ui"
    ui.mkdir(parents=True)
    (ui / "preview.py").write_text("from workflow_v5_ui import Legacy\n", encoding="utf-8")

    findings = scanner._scan_v6_runtime_contract(skill_root, tmp_path)

    assert any("legacy workflow import" in finding and "preview.py" in finding for finding in findings)


def test_scanner_rejects_obsolete_generate_only_policy_in_contract(tmp_path: Path):
    skill_root = tmp_path / "plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow"
    scripts = skill_root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "workflow_v6_contract.py").write_text(
        'IMAGE_POLICY = "gpt-image-2-generate-only"\n', encoding="utf-8",
    )

    findings = scanner._scan_v6_runtime_contract(skill_root, tmp_path)

    assert any("obsolete generate-only policy" in finding for finding in findings)


def test_scanner_rejects_wrong_adaptive_policy_literal(tmp_path: Path):
    skill_root = tmp_path / "plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow"
    scripts = skill_root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "workflow_v6_contract.py").write_text(
        'IMAGE_POLICY = "adaptive-ish"\n', encoding="utf-8",
    )

    findings = scanner._scan_v6_runtime_contract(skill_root, tmp_path)

    assert any("adaptive image policy" in finding for finding in findings)


def test_scanner_rejects_deleted_generic_helper_import_in_active_runtime(tmp_path: Path):
    skill_root = tmp_path / "plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow"
    scripts = skill_root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "workflow_v6_cli.py").write_text("import workflow_state\n", encoding="utf-8")

    findings = scanner._scan_v6_runtime_contract(skill_root, tmp_path)

    assert any("deleted legacy helper import" in finding for finding in findings)


def test_scanner_rejects_deleted_helper_import_in_shipped_editppt_runtime(tmp_path: Path):
    plugin_root = tmp_path / "plugins/awesome-editable-ppt-workflow/skills"
    (plugin_root / "generate-slide-body-image/scripts").mkdir(parents=True)
    runtime = plugin_root / "reconstruct-editable-slide/cli/editppt/runtime"
    runtime.mkdir(parents=True)
    (runtime / "stale_route.py").write_text("from page_pipeline import cache_record\n", encoding="utf-8")

    findings = scanner._scan_other_plugin_runtime(tmp_path)

    assert any("deleted legacy helper import" in finding and "stale_route.py" in finding for finding in findings)

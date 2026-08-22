from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT.parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v6_cli import _parser  # noqa: E402


def _commands() -> set[str]:
    parser = _parser()
    subparsers = next(
        action for action in parser._actions if getattr(action, "choices", None)
    )
    return set(subparsers.choices)


def test_final_plugin_has_one_word_to_ppt_entry_and_no_legacy_prompt_skill() -> None:
    assert not (PLUGIN / "skills" / "compile-page-image-prompt" / "SKILL.md").exists()
    workflow_skills = []
    for skill_file in (PLUGIN / "skills").glob("*/SKILL.md"):
        frontmatter = skill_file.read_text(encoding="utf-8").split("---", 2)[1]
        if "Word" in frontmatter and "PowerPoint" in frontmatter:
            workflow_skills.append(skill_file.parent.name)
    assert workflow_skills == ["run-word-to-ppt-workflow"]


def test_production_cli_exposes_run_pages_without_replaced_single_page_entries() -> None:
    commands = _commands()
    assert "run-pages" in commands
    assert "generate-page" not in commands
    assert "seal-page-image-prompt" not in commands


def test_private_experiment_cli_is_not_shipped_as_an_independent_entry() -> None:
    assert not (SCRIPTS / "complex_page_experiment" / "cli.py").exists()


def test_main_skill_documents_only_run_pages_for_body_generation() -> None:
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "run-pages" in skill
    assert "generate-page" not in skill
    assert "seal-page-image-prompt" not in skill
    assert "compile-page-image-prompt" not in skill


def test_prompt_validator_is_internal_to_the_main_workflow_skill() -> None:
    import validate_page_image_prompt as validator

    assert validator.ROOT == ROOT
    assert validator.SCHEMA == ROOT / "schemas" / "page_image_prompt_v1.schema.json"
    assert validator.DESIGN_PLAN_SCHEMA_PATH == ROOT / "schemas" / "design_plan_v1.schema.json"
    assert validator.MATERIAL_SCHEMA == ROOT / "schemas" / "awesome_page_materials_v1.schema.json"


def test_v6_source_does_not_import_the_retired_page_contract_builder() -> None:
    source = (SCRIPTS / "workflow_v6_source.py").read_text(encoding="utf-8")
    assert "build_page_contracts" not in source


def test_confirm_ui_ships_only_assets_loaded_by_the_current_document() -> None:
    static = SCRIPTS / "confirm_ui" / "static"
    assert not (static / "style.css").exists()
    assert not (static / "visual_system.js").exists()
    assert not (static / "color.js").exists()
    assert not (SCRIPTS / "confirm_ui" / "preview.py").exists()

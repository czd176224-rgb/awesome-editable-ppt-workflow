from pathlib import Path
import importlib.util
import json
import re
import shutil
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import doctor


REPO_ROOT = Path(__file__).resolve().parents[5]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "awesome-editable-ppt-workflow"
WORKFLOW = PLUGIN_ROOT / "skills" / "run-word-to-ppt-workflow"
GENERATOR = PLUGIN_ROOT / "skills" / "generate-slide-body-image"


def _load_runtime_checker():
    path = PLUGIN_ROOT / "scripts" / "check_current_runtime.py"
    spec = importlib.util.spec_from_file_location("check_current_runtime", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_auth_prefers_explicit_file(monkeypatch, tmp_path):
    selected = tmp_path / "explicit.json"
    monkeypatch.setenv("CODEX_AUTH_FILE", str(selected))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    assert doctor.resolve_codex_auth_file() == selected


def test_auth_uses_codex_home_before_user_profile(monkeypatch, tmp_path):
    root = tmp_path / "codex-home"
    root.mkdir()
    selected = root / "auth.json"
    selected.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("CODEX_AUTH_FILE", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(root))
    assert doctor.resolve_codex_auth_file() == selected


def test_fake_ip_dns_is_reported_as_unavailable(monkeypatch):
    monkeypatch.setattr(doctor.socket, "getaddrinfo", lambda *_: [(2, 1, 6, "", ("198.18.0.1", 443))])
    result = doctor.codex_dns_status()
    assert result["fake_ip"] is True
    assert result["available"] is False


@pytest.mark.skip(reason="legacy external-reference documentation contract removed")
def test_active_workflow_documents_the_sealed_adaptive_v6_contract():
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            WORKFLOW / "SKILL.md",
            WORKFLOW / "README.md",
            PLUGIN_ROOT / "README.md",
        )
    )
    required = (
        "new V6 project",
        "effective body",
        "attachment extraction",
        "failed_no_retry",
        "one Confirm UI",
        "thumbnail",
        "original",
        "model-input",
        "sealed result is the only prompt and QA authority",
        "zero valid confirmed references",
        "one to sixteen valid confirmed references",
        "never candidate 1",
        "medium",
        "high",
        "at most two candidates",
        "bounded concurrency",
        "original SVG Logo",
        "no post-generation exact overlay",
        "no post-reconstruction visual repair or comparison",
        "no V4/V5 runtime fallback",
    )
    for phrase in required:
        assert phrase in text


def test_active_skills_reject_obsolete_or_impossible_contracts():
    active = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            WORKFLOW / "SKILL.md",
            WORKFLOW / "README.md",
            GENERATOR / "SKILL.md",
            PLUGIN_ROOT / "README.md",
        )
    ).casefold()
    for obsolete in (
        "generate-only",
        "generate only",
        "reference-description-only",
        "pixel-perfect",
        "pixel perfect",
        "exact pixel fidelity",
        "post-generation exact overlay is required",
        "v4/v5 fallback",
    ):
        assert obsolete not in active


def test_runtime_checker_accepts_adaptive_cli_and_required_v6_modules():
    checker = _load_runtime_checker()
    assert checker.check(REPO_ROOT) == []
    assert checker.REQUIRED_V6_MODULES >= {
        "workflow_v6_materials.py",
        "workflow_v6_media.py",
        "workflow_v6_prompt_contract.py",
        "workflow_v6_image.py",
        "workflow_v6_qa.py",
    }
    assert checker.REQUIRED_V6_SCHEMAS >= {
        "page_materials_v6.schema.json",
        "reference_image_v6.schema.json",
    }


def test_runtime_checker_verifies_both_image_operations_and_input_guards():
    checker = _load_runtime_checker()
    findings = checker._scan_image_cli(PLUGIN_ROOT, REPO_ROOT)
    assert findings == []


@pytest.mark.skip(reason="external reference commands removed from awesome production")
def test_documented_v6_reference_commands_match_parser_flags():
    import workflow_v6_cli

    text = (WORKFLOW / "SKILL.md").read_text(encoding="utf-8")
    documented = {}
    for line in text.splitlines():
        match = re.search(r"\bv6 (import-reference|confirm-reference|fail-reference)\b(.*)", line)
        if match:
            documented[match.group(1)] = set(re.findall(r"--[a-z-]+", match.group(2)))
    parser = workflow_v6_cli._parser()
    subparsers = next(action for action in parser._actions if isinstance(getattr(action, "choices", None), dict))
    for command, expected_flags in documented.items():
        actual = {option for action in subparsers.choices[command]._actions for option in action.option_strings}
        assert expected_flags <= actual
    assert "--request-id" in documented["confirm-reference"]
    assert "--reference-id" not in documented["confirm-reference"]


def _mutated_plugin(tmp_path, *, relative, old, new):
    target = tmp_path / "plugins" / "awesome-editable-ppt-workflow"
    shutil.copytree(PLUGIN_ROOT, target)
    path = target / relative
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return target


def test_runtime_checker_rejects_renamed_edit_parser(tmp_path):
    checker = _load_runtime_checker()
    plugin = _mutated_plugin(
        tmp_path,
        relative="skills/generate-slide-body-image/scripts/codex_gpt_image.py",
        old='sub.add_parser("edit",',
        new='sub.add_parser("revise",',
    )
    assert any("edit" in item for item in checker._scan_image_cli(plugin, tmp_path))


def test_runtime_checker_rejects_removed_edit_argument_wiring(tmp_path):
    checker = _load_runtime_checker()
    plugin = _mutated_plugin(
        tmp_path,
        relative="skills/generate-slide-body-image/scripts/codex_gpt_image.py",
        old='add_image_arguments(edit, "edit")',
        new='pass  # removed edit argument wiring',
    )
    assert any("edit" in item and "wiring" in item for item in checker._scan_image_cli(plugin, tmp_path))


def test_runtime_checker_rejects_edit_wired_as_generate(tmp_path):
    checker = _load_runtime_checker()
    plugin = _mutated_plugin(
        tmp_path,
        relative="skills/generate-slide-body-image/scripts/codex_gpt_image.py",
        old='add_image_arguments(edit, "edit")',
        new='add_image_arguments(edit, "generate")',
    )
    assert any("edit" in item and "declared operation" in item for item in checker._scan_image_cli(plugin, tmp_path))


def test_runtime_checker_rejects_hardcoded_generate_selection(tmp_path):
    checker = _load_runtime_checker()
    plugin = _mutated_plugin(
        tmp_path,
        relative="skills/run-word-to-ppt-workflow/scripts/workflow_v6_image.py",
        old='operation="edit" if images else "generate"',
        new='operation="generate"',
    )
    skill = plugin / "skills/run-word-to-ppt-workflow"
    assert any("adaptive" in item for item in checker._scan_initial_generation(skill, tmp_path))


def test_runtime_checker_rejects_invalid_required_schema(tmp_path):
    checker = _load_runtime_checker()
    plugin = _mutated_plugin(
        tmp_path,
        relative="skills/run-word-to-ppt-workflow/schemas/page_materials_v6.schema.json",
        old='  "$schema":',
        new='  "broken":,\n  "$schema":',
    )
    skill = plugin / "skills/run-word-to-ppt-workflow"
    assert any("invalid JSON schema" in item for item in checker._scan_required_v6_files(skill, tmp_path))


def test_legacy_visual_qa_variants_are_detected_but_current_contract_is_allowed(tmp_path):
    checker = _load_runtime_checker()
    root = tmp_path / "skill"
    (root / "scripts").mkdir(parents=True)
    path = root / "scripts" / "sample.py"
    for allowed in (
        "global_visual_contract",
        "filter_global_visual_contract",
        "frozen_global_visual_contract",
        "global_visual_contract_digest",
    ):
        path.write_text(f"value = {allowed!r}\n", encoding="utf-8")
        assert checker._scan_tokens(root, tmp_path) == [], allowed
    for variant in (
        "global_visual_qa", "global-visual-qa", "global visual QA",
        "global_visual_review", "global-visual-review", "global visual review",
        "global_visual_check", "global-visual-check", "global visual check",
        "cross_page_similarity",
    ):
        path.write_text(f"value = {variant!r}\n", encoding="utf-8")
        findings = checker._scan_tokens(root, tmp_path)
        assert any("legacy deck-wide visual QA" in item for item in findings), variant

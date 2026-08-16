from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "plugins" / "awesome-editable-ppt-workflow" / "skills"

EXPECTED = {
    "run-word-to-ppt-workflow": "Word 转可编辑 PPT 总流程",
    "generate-slide-body-image": "生成 PPT 页面主体图",
    "reconstruct-editable-slide": "重建可编辑 PPT 页面",
    "validate-ppt-output": "校验与修复 PPT 成品",
}

RETIRED = {
    "word-to-editable-ppt",
    "codex-gpt-image",
    "image-to-editable-ppt",
    "officecli",
}


def test_plugin_exposes_only_role_named_skills() -> None:
    discovered = {
        path.name
        for path in SKILLS.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    }
    assert discovered == set(EXPECTED)
    assert not any((SKILLS / name).exists() for name in RETIRED)


def test_skill_frontmatter_and_ui_metadata_use_folder_name() -> None:
    for name, display_name in EXPECTED.items():
        skill_text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
        assert f"name: {name}\n" in skill_text

        metadata = yaml.safe_load(
            (SKILLS / name / "agents" / "openai.yaml").read_text(encoding="utf-8")
        )["interface"]
        assert metadata["display_name"] == display_name
        assert f"${name}" in metadata["default_prompt"]


def test_slide_body_generator_cannot_bypass_v6_authority() -> None:
    skill = (SKILLS / "generate-slide-body-image" / "SKILL.md").read_text(encoding="utf-8")
    assert "prepared by `run-word-to-ppt-workflow`" in skill
    assert "zero compiler-selected page-owned references" in skill
    assert "one to sixteen compiler-selected page-owned references" in skill
    assert "workflow calls `generate`" in skill
    assert "workflow calls `edit`" in skill
    assert "aligned image bytes, roles, digests, and capability identity" in skill
    assert "never candidate 1" in skill
    assert "high-fidelity best effort" in skill
    assert "Do not draw the fixed page title, SVG Logo, footer or page number" in skill
    assert "1904x896, 17:8" in skill
    assert "Avoid adding logos, watermarks, or extra text unless requested" not in skill


def test_adaptive_role_metadata_has_no_generate_only_claims() -> None:
    for name in ("run-word-to-ppt-workflow", "generate-slide-body-image"):
        metadata_text = (SKILLS / name / "agents" / "openai.yaml").read_text(encoding="utf-8")
        folded = metadata_text.casefold()
        assert "adaptive" in folded
        assert "generate-only" not in folded
        assert "generate only" not in folded


def test_reconstruction_user_messages_use_role_name_but_keep_cli_compatibility() -> None:
    runtime = SKILLS / "reconstruct-editable-slide" / "cli" / "editppt" / "runtime"
    user_facing = "\n".join(
        (runtime / name).read_text(encoding="utf-8")
        for name in (
            "image_gen.py",
            "main.py",
            "prepare_deck_run.py",
            "remove_chroma_key.py",
            "runtime_env.py",
        )
    )
    assert "Install image-to-editable-ppt" not in user_facing
    assert "User-requested image-to-editable-ppt conversion" not in user_facing
    assert "reconstruct-editable-slide" in user_facing

    package = (SKILLS / "reconstruct-editable-slide" / "cli" / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "image-to-editable-ppt-cli"' in package

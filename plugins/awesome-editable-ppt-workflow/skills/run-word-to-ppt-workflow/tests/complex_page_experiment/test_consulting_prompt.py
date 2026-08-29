from __future__ import annotations

import json
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from jsonschema import Draft202012Validator


SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "schemas"
    / "consulting_page_director_v2.schema.json"
)
MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "complex_page_experiment"
    / "consulting_prompt.py"
)
VISUAL_DIRECTOR_REFERENCE = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "complex_page_experiment"
    / "references"
    / "visual_director.md"
)

EXPECTED_CREATIVE_FIELDS = {
    "business_proposition",
    "explanatory_lead",
    "analytical_backbone",
    "evidence_interpretation_conclusion",
    "content_hierarchy",
    "reading_path_and_density",
    "takeaway_statement",
    "supporting_visual_policy",
    "anti_ai_visual_policy",
}

EXPECTED_PROMPT_FIELDS = {
    "task_and_canvas",
    "core_proposition_and_content",
    "consulting_information_architecture",
    "visual_style_and_color",
    "text_and_typography",
    "strict_prohibitions",
}


def test_v2_schema_defines_only_the_consulting_director_contract() -> None:
    assert SCHEMA_PATH.is_file(), "consulting director v2 schema is missing"
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["schema_version"] == {
        "type": "string",
        "const": "awesome-consulting-page-director-v2",
    }
    creative = schema["properties"]["creative_direction"]
    assert set(creative["required"]) == EXPECTED_CREATIVE_FIELDS
    assert set(creative["properties"]) == EXPECTED_CREATIVE_FIELDS
    prompt_sections = schema["$defs"]["promptSections"]
    assert set(prompt_sections["required"]) == EXPECTED_PROMPT_FIELDS
    assert set(prompt_sections["properties"]) == EXPECTED_PROMPT_FIELDS

    serialized = json.dumps(schema, ensure_ascii=False)
    for legacy_name in (
        "scene_or_background",
        "subject_and_core_expression",
        "key_details",
        "composition_viewpoint_hierarchy_and_medium",
        "reference_roles_and_combination",
        "preservation_and_fixed_exclusions",
    ):
        assert legacy_name not in serialized


def _load_compiler_module():
    assert MODULE_PATH.is_file(), "consulting prompt compiler is missing"
    spec = importlib.util.spec_from_file_location("consulting_prompt_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _director_value() -> dict[str, object]:
    return {
        "schema_version": "awesome-consulting-page-director-v2",
        "page_number": 4,
        "quality": "high",
        "machine_record": {
            "facts_and_sources": ["The proposition is supported by the Word body."],
            "must_preserve_entities": ["Preserve every source-exact industry name."],
            "core_content_and_comment_direction": ["Use a formal reporting expression."],
            "material_use": [],
            "selected_references": [],
            "fixed_layer_exclusions": ["title", "logo", "footer", "page_number"],
        },
        "creative_direction": {
            "business_proposition": "Three industry tasks require differentiated capital.",
            "explanatory_lead": "Explain why one fund cannot cover every stage equally.",
            "analytical_backbone": "Use one continuous three-lane portfolio map.",
            "evidence_interpretation_conclusion": "Connect industries to capital needs and conclusion.",
            "content_hierarchy": "Lead, analytical backbone, annotations, takeaway.",
            "reading_path_and_density": "Read left to right with a dense but disciplined grid.",
            "takeaway_statement": "Match capital tools to industry stages.",
            "supporting_visual_policy": "Use restrained line icons only as navigation aids.",
            "anti_ai_visual_policy": "Avoid cinematic scenes and decorative 3D objects.",
        },
        "prompt_sections": {
            "task_and_canvas": "Create one coherent body-region consulting report image.",
            "core_proposition_and_content": "Show the proposition, explanatory lead, evidence, and takeaway.",
            "consulting_information_architecture": "Build one analytical backbone with a clear reading path.",
            "visual_style_and_color": "Use a formal, restrained, grid-aligned consulting style.",
            "text_and_typography": "Render accurate Simplified Chinese with presentation-scale hierarchy.",
            "strict_prohibitions": "Avoid decorative hero scenes, miniature factories, neon, and 3D machinery.",
        },
    }


def _material_view(*, font_accent_allowed: bool = True):
    return SimpleNamespace(
        value={
            "visual_contract": {
                "background_color": "#F7F7F7",
                "primary_color": "#161616",
                "secondary_color": "#CD202A",
            }
        }
    )


def test_compiler_emits_the_new_six_sections_and_seals_owned_constraints() -> None:
    module = _load_compiler_module()

    prompt = module.compile_consulting_six_part_prompt(
        _director_value(), _material_view(), font_accent_allowed=True
    )

    assert [line[3:] for line in prompt.splitlines() if line.startswith("## ")] == [
        "Task and Canvas",
        "Core Proposition and Content",
        "Consulting Information Architecture",
        "Visual Style and Color",
        "Text and Typography",
        "Strict Prohibitions",
    ]
    for marker in ("1904x896", "17:8", "#F7F7F7", "#161616", "#CD202A"):
        assert prompt.count(marker) == 1
    assert prompt.count("title, logo, footer, or page number") == 1
    assert "one coherent body-region consulting report image" in prompt
    assert "evidence, interpretation, and conclusion" in prompt
    assert "explanatory copy" in prompt
    assert "planning instructions, not visible slide copy" in prompt
    assert "exact contiguous spans from complete_word_content" in prompt
    assert "visual semantic expansion" in prompt
    assert "process, hierarchy, parallelism, membership, comparison, and causality" in prompt
    assert "same hue" in prompt
    assert "derived shades of secondary color #CD202A" in prompt
    assert "strong #A41A22" in prompt
    assert "support #E1797F" in prompt
    assert "soft #F0BCBF" in prompt
    assert "wash #F9E4E5" in prompt
    assert "build the page skeleton before placing text" in prompt
    assert "at least two visibly distinct tones" in prompt
    assert "must not create a relationship that complete_word_content does not state" in prompt
    assert "This is a user-confirmed emphasis page" in prompt
    for obsolete_quota in ("70%-85%", "15%-25%", "3%-7%", "never above 10%"):
        assert obsolete_quota not in prompt
    assert "3D machinery" in prompt
    for legacy_name in (
        "Scene or Background",
        "Subject and Core Expression",
        "Key Details",
        "Composition Viewpoint Hierarchy and Medium",
        "Reference Roles and Combination",
        "Preservation and Fixed Exclusions",
    ):
        assert legacy_name not in prompt


@pytest.mark.parametrize(
    ("relationship", "quantitative_form", "qualitative_substitute"),
    [
        ("increase_decrease_drivers", "cumulative bridge", "driver bridge"),
        ("change_over_time", "line or point chart", "timeline/roadmap"),
        ("two_variable_relationship", "scatter plot", "source-labelled qualitative quadrant or table"),
        ("third_variable_size", "bubble chart", "uniform nodes"),
        ("market_size_share", "variable-width hierarchy", "equal-width hierarchy"),
        ("project_stage_time", "time-interval chart", "roadmap/milestones"),
        ("option_comparison", "bar or column chart", "comparison table"),
        ("target_actual_variance", "target-versus-actual chart", "goal-current-gap"),
    ],
)
def test_compiler_emits_exact_dual_mode_relationship_mapping(
    relationship: str, quantitative_form: str, qualitative_substitute: str,
) -> None:
    module = _load_compiler_module()

    prompt = module.compile_consulting_six_part_prompt(
        _director_value(), _material_view(), font_accent_allowed=True
    )

    row = next(line for line in prompt.splitlines() if line.startswith(f"{relationship}:"))
    assert quantitative_form in row
    assert qualitative_substitute in row
    assert "only when source evidence is complete" in row
    assert "otherwise" in row


def test_compiler_keeps_quantitative_and_qualitative_discipline_inside_six_sections() -> None:
    module = _load_compiler_module()

    prompt = module.compile_consulting_six_part_prompt(
        _director_value(), _material_view(), font_accent_allowed=True
    )

    assert len([line for line in prompt.splitlines() if line.startswith("## ")]) == 6
    assert "numeric_authority" not in prompt
    for label_dimension in ("subject", "unit", "period", "basis"):
        assert label_dimension in prompt
    for forbidden_geometry in (
        "numeric axes",
        "proportional geometry",
        "bubble-size ranking",
        "target-line magnitude",
        "difference magnitude",
    ):
        assert forbidden_geometry in prompt
    assert "do not calculate new metrics" in prompt


def test_compiler_bans_accent_family_text_only_on_non_emphasis_pages() -> None:
    module = _load_compiler_module()

    prompt = module.compile_consulting_six_part_prompt(
        _director_value(), _material_view(), font_accent_allowed=False
    )

    assert "This is not a user-confirmed emphasis page" in prompt
    assert "Do not use any secondary-color-family shade for any text" in prompt
    assert "text-box fills, shapes, borders, nodes, and connectors" in prompt


def test_compiler_legacy_mode_omits_only_the_new_page_gate() -> None:
    module = _load_compiler_module()
    prompt = module.compile_consulting_six_part_prompt(
        _director_value(), _material_view(), font_accent_allowed=None
    )
    assert "user-confirmed emphasis page" not in prompt
    assert "derived shades of secondary color #CD202A" in prompt


def test_compiler_rejects_the_legacy_six_part_shape() -> None:
    module = _load_compiler_module()
    value = _director_value()
    value["prompt_sections"] = {
        "scene_or_background": "A luminous scene.",
        "subject_and_core_expression": "A hero object.",
        "key_details": "Some labels.",
        "composition_viewpoint_hierarchy_and_medium": "A cinematic view.",
        "reference_roles_and_combination": "Use references.",
        "preservation_and_fixed_exclusions": "Preserve facts.",
    }

    with pytest.raises(ValueError, match="consulting six-part shape"):
        module.compile_consulting_six_part_prompt(value, _material_view())


def test_compiler_accepts_the_v2_correction_authority() -> None:
    module = _load_compiler_module()
    value = _director_value()
    value["schema_version"] = "awesome-page-correction-v2"

    prompt = module.compile_consulting_six_part_prompt(value, _material_view())

    assert "## Consulting Information Architecture" in prompt


def test_visual_director_reference_teaches_the_consulting_report_recipe() -> None:
    text = VISUAL_DIRECTOR_REFERENCE.read_text(encoding="utf-8").casefold()

    for required in (
        "one business proposition",
        "one analytical backbone",
        "explanatory lead",
        "evidence, interpretation, and conclusion",
        "explicit takeaway",
        "supporting imagery",
        "miniature",
        "3d",
        "neon",
    ):
        assert required in text


def test_runtime_exports_only_the_v2_compiler_and_schema() -> None:
    from complex_page_experiment import director

    assert director.SCHEMA.name == "consulting_page_director_v2.schema.json"
    assert hasattr(director, "compile_consulting_six_part_prompt")
    assert not hasattr(director, "compile_six_part_prompt")
    assert not (
        Path(__file__).resolve().parents[2]
        / "schemas"
        / "complex_page_director_v1.schema.json"
    ).exists()


def test_initial_director_and_correction_schema_use_the_v2_shape(tmp_path: Path) -> None:
    from complex_page_experiment.director import _correction_schema, direct_page
    from test_director import _material_view as runtime_material_view
    from test_director import _result, _workspace

    workspace = _workspace(tmp_path)
    view = runtime_material_view(workspace)
    value = _director_value()
    value["page_number"] = workspace.page_number

    artifact = direct_page(
        workspace,
        view,
        timeout=60,
        invoke=lambda *args, **kwargs: _result(value),
    )

    assert artifact.value["schema_version"] == "awesome-consulting-page-director-v2"
    assert "## Task and Canvas" in artifact.actual_prompt
    assert "## Strict Prohibitions" in artifact.actual_prompt
    correction_sections = _correction_schema()["properties"]["prompt_sections"]
    assert set(correction_sections["required"]) == EXPECTED_PROMPT_FIELDS
    assert set(correction_sections["properties"]) == EXPECTED_PROMPT_FIELDS

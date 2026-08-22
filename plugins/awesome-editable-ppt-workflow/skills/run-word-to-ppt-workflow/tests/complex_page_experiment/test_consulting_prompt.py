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


def _material_view():
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

    prompt = module.compile_consulting_six_part_prompt(_director_value(), _material_view())

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

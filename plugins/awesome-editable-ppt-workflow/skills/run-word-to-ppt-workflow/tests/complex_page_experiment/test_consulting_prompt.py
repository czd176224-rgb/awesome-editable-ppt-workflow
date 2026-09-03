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
    / "consulting_page_director_v3.schema.json"
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

EXPECTED_PROMPT_FIELDS = {
    "task_and_canvas",
    "core_proposition_and_content",
    "consulting_information_architecture",
    "visual_style_and_color",
    "text_and_typography",
    "strict_prohibitions",
}


def test_v3_schema_defines_only_the_compact_consulting_director_contract() -> None:
    assert SCHEMA_PATH.is_file(), "consulting director v3 schema is missing"
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["schema_version"] == {
        "type": "string",
        "const": "awesome-consulting-page-director-v3",
    }
    assert set(schema["required"]) == {
        "schema_version",
        "page_number",
        "quality",
        "page_plan",
        "selected_references",
    }
    assert set(schema["properties"]) == set(schema["required"])
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


def _word_facts() -> list[dict[str, object]]:
    return [
        {
            "type": "paragraph",
            "text": text,
            "source_block_id": f"body-{index}",
            "source_order": index,
        }
        for index, text in enumerate(
            (
                "Regional resources are available.",
                "Resources enter the fund platform.",
                "Operations support the entry path.",
                "The source does not quantify flow volume.",
            ),
            start=1,
        )
    ]


def _material_view(*, font_accent_allowed: bool = True):
    return SimpleNamespace(
        value={
            "complete_word_content": _word_facts(),
            "visual_contract": {
                "background_color": "#F7F7F7",
                "primary_color": "#161616",
                "secondary_color": "#CD202A",
            }
        }
    )


def _director_value() -> dict[str, object]:
    return {
        "schema_version": "awesome-consulting-page-director-v3",
        "page_number": 5,
        "quality": "high",
        "page_plan": {
            "page_purpose": "Explain how regional resources enter the fund system.",
            "primary_relationship": {
                "grammar": "geography",
                "description": "Regional resource entrances feed the fund platform.",
                "fact_ids": ["body-1", "body-2"],
                "visual_instruction": "Use a map-led resource-entry diagram with explicit connectors.",
                "nodes": [
                    {
                        "node_id": "regional-resources",
                        "label": "Regional resources",
                        "fact_ids": ["body-1"],
                    },
                    {
                        "node_id": "fund-platform",
                        "label": "Fund platform",
                        "fact_ids": ["body-2"],
                    },
                ],
                "edges": [
                    {
                        "from_node": "regional-resources",
                        "to_node": "fund-platform",
                        "label": "enter",
                        "fact_ids": ["body-2"],
                    }
                ],
            },
            "core_exhibit": {
                "grammar": "geography",
                "description": "A regional map with labeled resource flows.",
                "fact_ids": ["body-1", "body-2"],
            },
            "support_groups": [
                {"role": "support", "label": "Operating support", "fact_ids": ["body-3"]},
                {"role": "note", "label": "Source limitation", "fact_ids": ["body-4"]},
            ],
            "reading_path": "Read the map first, then the operating support and limitation.",
            "local_visuals": [
                {
                    "grammar": "flow",
                    "instruction": "Use one small arrow sequence for the source-supported entry path.",
                    "fact_ids": ["body-2"],
                }
            ],
        },
        "selected_references": [],
    }


def _correction_value() -> dict[str, object]:
    return {
        "schema_version": "awesome-page-correction-v2",
        "page_number": 5,
        "strategy": "edit_previous",
        "problem_addressed": ["Repair the stated defect."],
        "preserve": ["Preserve correct source facts."],
        "selected_reference_ids": [],
        "prompt_sections": {
            "task_and_canvas": "Keep the existing body canvas.",
            "core_proposition_and_content": "Preserve the source-backed proposition.",
            "consulting_information_architecture": "Repair the stated relationship.",
            "visual_style_and_color": "Retain the confirmed visual roles.",
            "text_and_typography": "Keep all labels legible.",
            "strict_prohibitions": "Do not introduce new facts.",
        },
    }


def _compact_material_view():
    return SimpleNamespace(
        value={
            "complete_word_content": _word_facts(),
            "visual_contract": {
                "background_color": "#F7F7F7",
                "primary_color": "#161616",
                "secondary_color": "#CD202A",
            },
            "body_frame": {"aspect_ratio": "17:8"},
            "material_audit": "MATERIAL_AUDIT_SENTINEL",
            "template_name": "TEMPLATE_NAME_SENTINEL",
        }
    )


def _compiled_sections(prompt: str) -> dict[str, str]:
    headings = [line[3:] for line in prompt.splitlines() if line.startswith("## ")]
    return {
        heading: prompt.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]
        for heading in headings
    }


def test_compiler_builds_the_six_sections_from_the_compact_page_plan(
    record_property,
) -> None:
    module = _load_compiler_module()
    value = _director_value()
    material_view = _compact_material_view()
    fact_ids = [
        "FACT-REGIONAL-ORIGIN-SENTINEL",
        "FACT-FUND-DESTINATION-SENTINEL",
        "FACT-OPERATING-SUPPORT-SENTINEL",
        "FACT-SOURCE-LIMIT-SENTINEL",
    ]
    for block, fact_id in zip(
        material_view.value["complete_word_content"], fact_ids, strict=True
    ):
        block["source_block_id"] = fact_id
    plan = value["page_plan"]
    plan["page_purpose"] = "PAGE-PURPOSE-SENTINEL"
    relationship = plan["primary_relationship"]
    relationship.update(
        {
            "grammar": "causality",
            "description": "PRIMARY-RELATIONSHIP-DESCRIPTION-SENTINEL",
            "fact_ids": fact_ids[:2],
            "visual_instruction": "PRIMARY-VISUAL-INSTRUCTION-SENTINEL",
            "nodes": [
                {
                    "node_id": "NODE-ORIGIN-ID-SENTINEL",
                    "label": "NODE-ORIGIN-LABEL-SENTINEL",
                    "fact_ids": [fact_ids[0]],
                },
                {
                    "node_id": "NODE-DESTINATION-ID-SENTINEL",
                    "label": "NODE-DESTINATION-LABEL-SENTINEL",
                    "fact_ids": [fact_ids[1]],
                },
            ],
            "edges": [
                {
                    "from_node": "NODE-ORIGIN-ID-SENTINEL",
                    "to_node": "NODE-DESTINATION-ID-SENTINEL",
                    "label": "EDGE-LABEL-SENTINEL",
                    "fact_ids": [fact_ids[1]],
                }
            ],
        }
    )
    core = plan["core_exhibit"]
    core.update(
        {
            "grammar": "geography",
            "description": "CORE-EXHIBIT-DESCRIPTION-SENTINEL",
            "fact_ids": fact_ids[:2],
        }
    )
    plan["support_groups"] = [
        {
            "role": "support",
            "label": "SUPPORT-GROUP-LABEL-SENTINEL",
            "fact_ids": [fact_ids[2]],
        },
        {
            "role": "note",
            "label": "NOTE-GROUP-LABEL-SENTINEL",
            "fact_ids": [fact_ids[3]],
        },
    ]
    plan["reading_path"] = "READING-PATH-SENTINEL"
    plan["local_visuals"] = [
        {
            "grammar": "flow",
            "instruction": "LOCAL-VISUAL-INSTRUCTION-SENTINEL",
            "fact_ids": [fact_ids[1]],
        }
    ]

    prompt = module.compile_consulting_six_part_prompt(
        value, material_view, font_accent_allowed=True
    )
    record_property("compiled_prompt_length", len(prompt))

    headings = [line[3:] for line in prompt.splitlines() if line.startswith("## ")]
    assert headings == [
        "Task and Canvas",
        "Core Proposition and Content",
        "Consulting Information Architecture",
        "Visual Style and Color",
        "Text and Typography",
        "Strict Prohibitions",
    ]
    facts = [block["text"] for block in material_view.value["complete_word_content"]]
    for fact in facts:
        assert prompt.count(fact) == 1

    sections = _compiled_sections(prompt)
    architecture = sections["Consulting Information Architecture"]
    assert plan["page_purpose"] in architecture
    assert json.dumps(relationship, ensure_ascii=False, sort_keys=True) in architecture
    assert json.dumps(core, ensure_ascii=False, sort_keys=True) in architecture
    for group in plan["support_groups"]:
        assert json.dumps(group, ensure_ascii=False, sort_keys=True) in architecture
    assert plan["reading_path"] in architecture
    for local_visual in plan["local_visuals"]:
        assert json.dumps(local_visual, ensure_ascii=False, sort_keys=True) in architecture

    positive, prohibited = module._color_constraints(
        material_view, font_accent_allowed=True
    )
    assert prompt.count(positive) == 1
    assert prompt.count(prohibited) == 1
    assert prompt.count("central largest 17:8 content region") == 1
    assert prompt.count("Word is the semantic authority") == 1

    for removed in (
        "creative_direction",
        "prompt_sections",
        "facts_and_sources",
        "material_use",
        "fixed_layer_exclusions",
        "investment-committee",
        "template_id",
        "template_version",
        "MATERIAL_AUDIT_SENTINEL",
        "TEMPLATE_NAME_SENTINEL",
    ):
        assert removed not in prompt


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
    ("relationship", "required_semantics"),
    [
        (
            "increase_decrease_drivers",
            (
                "scaled cumulative bridge/waterfall",
                "verified start, changes, and end",
                "equal-weight positive/negative driver bridge",
                "no cumulative baseline or computed end value",
            ),
        ),
        (
            "change_over_time",
            (
                "line or column chart",
                "explicit periods and values",
                "timeline or stage-evolution roadmap",
                "no implied slope or magnitude",
            ),
        ),
        (
            "two_variable_relationship",
            (
                "scatter plot",
                "numeric x/y values",
                "source supplies the two qualitative axes and item classifications",
                "otherwise use a comparison table",
            ),
        ),
        (
            "third_variable_size",
            (
                "bubble size",
                "real non-negative third numeric variable",
                "uniform-size nodes",
                "no size ranking",
            ),
        ),
        (
            "market_size_share",
            (
                "Mekko/variable rectangle",
                "complete width and share values",
                "equal-width hierarchy or portfolio matrix",
                "no area-based claim",
            ),
        ),
        (
            "project_stage_time",
            (
                "Gantt",
                "explicit start/end or start/duration",
                "ordered roadmap or milestone sequence",
                "dates/durations are absent",
            ),
        ),
        (
            "option_comparison",
            (
                "bar/dot plot",
                "comparable values or source ratings",
                "native comparison table",
                "source-backed criteria and wording",
            ),
        ),
        (
            "target_actual_variance",
            (
                "bar/dot plus target line or difference arrow",
                "both values share a unit/basis",
                "goal-current-gap narrative structure",
                "no target line, arrow magnitude, or calculated variance",
            ),
        ),
    ],
)
def test_compiler_emits_exact_dual_mode_relationship_mapping(
    relationship: str, required_semantics: tuple[str, ...],
) -> None:
    module = _load_compiler_module()

    prompt = module.compile_consulting_six_part_prompt(
        _director_value(), _material_view(), font_accent_allowed=True
    )

    row = next(line for line in prompt.splitlines() if line.startswith(f"{relationship}:"))
    for semantic in required_semantics:
        assert semantic in row


def test_compiler_keeps_quantitative_and_qualitative_discipline_inside_six_sections() -> None:
    module = _load_compiler_module()

    prompt = module.compile_consulting_six_part_prompt(
        _director_value(), _material_view(), font_accent_allowed=True
    )

    assert len([line for line in prompt.splitlines() if line.startswith("## ")]) == 6
    assert "numeric_authority" not in prompt
    for label_dimension in (
        "subject",
        "unit",
        "period",
        "basis",
        "actual/forecast status",
        "source-stated assumptions",
        "total-to-component relationships",
    ):
        assert label_dimension in prompt
    for analysis_layer in (
        "facts",
        "assumptions",
        "calculated results",
        "analytical judgments",
        "recommendations",
    ):
        assert analysis_layer in prompt
    for forbidden_geometry in (
        "numeric axes",
        "proportional geometry",
        "bubble-size ranking",
        "target-line magnitude",
        "difference magnitude",
    ):
        assert forbidden_geometry in prompt
    assert "do not calculate new metrics" in prompt


def test_non_relationship_page_contract_forbids_unsourced_chart_or_substitute() -> None:
    module = _load_compiler_module()
    prompt = module.compile_consulting_six_part_prompt(
        _director_value(), _material_view(), font_accent_allowed=True
    )

    assert "only when complete_word_content explicitly contains that relationship" in prompt
    assert "If none of the eight relationships is source-explicit, do not introduce a chart or any named qualitative substitute" in prompt


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


def test_compiler_accepts_the_v2_correction_authority() -> None:
    module = _load_compiler_module()
    value = _correction_value()

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


def test_runtime_exports_the_v3_director_schema_and_existing_compiler() -> None:
    from complex_page_experiment import director

    assert director.SCHEMA.name == "consulting_page_director_v3.schema.json"
    assert hasattr(director, "compile_consulting_six_part_prompt")
    assert not hasattr(director, "compile_six_part_prompt")
    assert not (
        Path(__file__).resolve().parents[2]
        / "schemas"
        / "complex_page_director_v1.schema.json"
    ).exists()


def test_initial_director_uses_v3_while_correction_retains_v2_sections(
    tmp_path: Path,
) -> None:
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

    assert artifact.value["schema_version"] == "awesome-consulting-page-director-v3"
    assert artifact.page_plan == value["page_plan"]
    assert "## Task and Canvas" in artifact.actual_prompt
    assert "## Strict Prohibitions" in artifact.actual_prompt
    correction_sections = _correction_schema()["properties"]["prompt_sections"]
    assert set(correction_sections["required"]) == EXPECTED_PROMPT_FIELDS
    assert set(correction_sections["properties"]) == EXPECTED_PROMPT_FIELDS

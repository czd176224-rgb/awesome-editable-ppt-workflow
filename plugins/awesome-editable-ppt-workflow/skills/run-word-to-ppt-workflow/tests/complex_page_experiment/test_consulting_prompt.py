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
    assert "promptSections" not in schema["$defs"]

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


def _material_view(*, font_accent_allowed: bool = True, highlight_color: str | None = None):
    visual_contract = {
        "background_color": "#F7F7F7",
        "primary_color": "#161616",
        "secondary_color": "#CD202A",
    }
    if highlight_color is not None:
        visual_contract["highlight_color"] = highlight_color
    return SimpleNamespace(
        value={
            "complete_word_content": _word_facts(),
            "visual_contract": visual_contract,
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


def test_complete_fact_renderer_preserves_paragraph_list_and_table_order() -> None:
    module = _load_compiler_module()
    material_view = _material_view()
    material_view.value["complete_word_content"] = [
        {"type": "table", "rows": [["Header A", "Header B"], ["Cell A", "Cell B"]], "source_block_id": "table-1", "source_order": 3},
        {"type": "list", "text": "Numbered fact", "list_kind": "number", "level": 1, "source_block_id": "list-1", "source_order": 2},
        {"type": "paragraph", "text": "Paragraph fact", "source_block_id": "body-1", "source_order": 1},
    ]

    rendered = module._complete_fact_content(material_view)

    assert rendered == (
        "[source fact body-1 in section 2]\nParagraph fact\n"
        "[source fact list-1 in section 2]\n  1. Numbered fact\n"
        "[source fact table-1 in section 2]\nHeader A | Header B\n"
        "Cell A | Cell B"
    )


def test_complete_table_renderer_never_uses_lossy_text_fallback() -> None:
    module = _load_compiler_module()
    block = {
        "type": "table",
        "text": "LOSSY-TABLE-FALLBACK",
        "rows": [["ROW-1-CELL-1", "ROW-1-CELL-2"], ["ROW-2-CELL-1", "ROW-2-CELL-2"]],
    }

    rendered = module._render_complete_source_block(block)

    assert rendered == "ROW-1-CELL-1 | ROW-1-CELL-2\nROW-2-CELL-1 | ROW-2-CELL-2"
    assert "LOSSY-TABLE-FALLBACK" not in rendered


def test_complete_fact_renderer_requires_a_stable_source_id() -> None:
    module = _load_compiler_module()
    material_view = _material_view()
    material_view.value["complete_word_content"] = [
        {"type": "paragraph", "text": "Unlabeled fact", "source_order": 1}
    ]

    with pytest.raises(ValueError, match="source_block_id is missing"):
        module._complete_fact_content(material_view)


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
    content = sections["Core Proposition and Content"]
    for fact, fact_id in zip(facts, fact_ids, strict=True):
        assert fact in content
        assert content.count(f"[source fact {fact_id} in section 2]") == 1
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
    assert "Give the source-supported main relationship the strongest visual priority" in prompt
    assert "Communicate one source-supported main message" in prompt
    assert "complete explanations and scoped qualifiers" in prompt
    assert "instruction metadata, not visible copy" in prompt
    assert "Lossless rewording, regrouping, and text-to-diagram conversion are allowed" in prompt
    assert "do not calculate new metrics, infer missing values" in prompt
    assert "Let relationships shape space" in prompt
    assert "parallel peers keep the same tone" in prompt
    assert "secondary color #CD202A" in prompt
    assert "strong #A41A22" in prompt
    assert "support #E1797F" in prompt
    assert "soft #F0BCBF" in prompt
    assert "wash #F9E4E5" in prompt
    assert "Labels, legends, numbering, and spatial structure remain primary" in prompt
    assert "at least two visibly distinct tones" in prompt
    assert "Do not use color as the sole carrier of a fact or relationship" in prompt
    assert "This is a user-confirmed emphasis page" in prompt
    for obsolete_quota in ("70%-85%", "15%-25%", "3%-7%", "never above 10%"):
        assert obsolete_quota not in prompt
    for legacy_name in (
        "Scene or Background",
        "Subject and Core Expression",
        "Key Details",
        "Composition Viewpoint Hierarchy and Medium",
        "Reference Roles and Combination",
        "Preservation and Fixed Exclusions",
    ):
        assert legacy_name not in prompt


def test_optional_highlight_color_adds_consulting_color_roles_only_to_visual_section() -> None:
    module = _load_compiler_module()

    prompt = module.compile_consulting_six_part_prompt(
        _director_value(),
        _material_view(highlight_color="#D3A62C"),
        font_accent_allowed=False,
    )
    sections = _compiled_sections(prompt)

    assert [line[3:] for line in prompt.splitlines() if line.startswith("## ")] == [
        "Task and Canvas",
        "Core Proposition and Content",
        "Consulting Information Architecture",
        "Visual Style and Color",
        "Text and Typography",
        "Strict Prohibitions",
    ]
    visual = sections["Visual Style and Color"]
    assert prompt.count("#D3A62C") == 1
    assert "primary color #161616 for long body text, ordinary labels, deep headings, and neutral structure" in visual
    assert "secondary color #CD202A for the main path, main option, and main data series" in visual
    assert "highlight color #D3A62C only for source-supported targets, differences, key numbers, and final nodes" in visual
    assert "Use light or neutral treatments for supporting evidence" in visual
    assert "Risk red or positive green is allowed only when the source explicitly assigns that business meaning" in visual
    assert "text objects may not use secondary-family or highlight-family colors" in visual
    for heading, section in sections.items():
        if heading != "Visual Style and Color":
            assert "#D3A62C" not in section


@pytest.mark.parametrize("font_accent_allowed", [False, True])
def test_highlight_color_keeps_shared_relationship_semantics(
    font_accent_allowed: bool,
) -> None:
    module = _load_compiler_module()
    prompt = module.compile_consulting_six_part_prompt(
        _director_value(),
        _material_view(highlight_color="#D3A62C"),
        font_accent_allowed=font_accent_allowed,
    )
    visual = _compiled_sections(prompt)["Visual Style and Color"]

    assert "parallel peers and same-category items use the same tone" in visual.casefold()
    assert "Ordered light-to-dark shades may communicate only a source-explicit process, hierarchy, stage, or visual focus" in visual
    for invented_meaning in (
        "order",
        "magnitude",
        "classification",
        "risk",
        "status",
        "rating",
        "positive/negative meaning",
    ):
        assert invented_meaning in visual


def test_highlight_color_respects_emphasis_page_text_gate() -> None:
    module = _load_compiler_module()
    material_view = _material_view(highlight_color="#D3A62C")

    emphasis = module.compile_consulting_six_part_prompt(
        _director_value(), material_view, font_accent_allowed=True
    )
    ordinary = module.compile_consulting_six_part_prompt(
        _director_value(), material_view, font_accent_allowed=False
    )

    assert "important short text may selectively use secondary or highlight" in emphasis
    assert "long body text remains primary or neutral" in emphasis.casefold()
    assert "text objects may not use secondary-family or highlight-family colors" in ordinary
    assert "non-text structural marks" in ordinary


def test_all_color_rules_compile_only_inside_visual_style_and_color() -> None:
    module = _load_compiler_module()
    prompt = module.compile_consulting_six_part_prompt(
        _director_value(), _material_view(), font_accent_allowed=False
    )
    sections = _compiled_sections(prompt)
    color_rule = "assign ordered color depth to merely parallel categories"

    assert color_rule in sections["Visual Style and Color"]
    for heading, section in sections.items():
        if heading != "Visual Style and Color":
            assert color_rule not in section


@pytest.mark.parametrize("invalid", ["", "   ", 123])
def test_optional_highlight_color_must_be_nonempty_text(invalid: object) -> None:
    module = _load_compiler_module()
    material_view = _material_view()
    material_view.value["visual_contract"]["highlight_color"] = invalid

    with pytest.raises(ValueError, match="highlight color is missing"):
        module.compile_consulting_six_part_prompt(_director_value(), material_view)


def test_specific_page_relations_survive_in_six_part_prompt() -> None:
    module = _load_compiler_module()
    value = _director_value()
    relationship = (
        "三类需求并列，共同通过拟转化为连接资产项目。"
        "可测算、可投资、可运营是共同属性。"
        "投前退出设计是限定安排，不是投资后的最后一步。"
    )
    value["page_plan"]["primary_relationship"]["description"] = relationship

    prompt = module.compile_consulting_six_part_prompt(value, _material_view())

    assert "".join(relationship.split()) in "".join(prompt.split())
    assert len([line for line in prompt.splitlines() if line.startswith("## ")]) == 6
    assert "creative_direction" not in prompt


def test_page_purpose_repeating_a_complete_fact_uses_only_its_source_id() -> None:
    module = _load_compiler_module()
    value = _director_value()
    fact = _word_facts()[0]["text"]
    value["page_plan"]["page_purpose"] = fact

    prompt = module.compile_consulting_six_part_prompt(value, _material_view())
    sections = _compiled_sections(prompt)

    assert prompt.count(fact) == 1
    assert fact in sections["Core Proposition and Content"]
    assert fact not in sections["Consulting Information Architecture"]
    assert "[source fact body-1 in section 2]" in sections["Core Proposition and Content"]
    assert "[source fact body-1 in section 2]" in sections["Consulting Information Architecture"]


def test_short_table_fact_does_not_corrupt_ids_or_unrelated_language() -> None:
    module = _load_compiler_module()
    material_view = _material_view()
    material_view.value["complete_word_content"] = [{
        "type": "table", "rows": [["1"]], "source_block_id": "table-1", "source_order": 1,
    }]
    value = _director_value()
    value["page_plan"]["page_purpose"] = "1"
    relationship = value["page_plan"]["primary_relationship"]
    relationship["description"] = "Phase 2021 overview"
    relationship["nodes"][0]["node_id"] = "1"
    relationship["nodes"][0]["fact_ids"] = ["1"]
    relationship["edges"][0]["from_node"] = "1"

    sections = _compiled_sections(
        module.compile_consulting_six_part_prompt(value, material_view)
    )
    architecture = sections["Consulting Information Architecture"]

    assert sections["Core Proposition and Content"].count("\n1") == 1
    assert "[source fact table-1 in section 2]\n1" in sections["Core Proposition and Content"]
    assert "Page purpose: [source fact table-1 in section 2]" in architecture
    assert '"node_id": "1"' in architecture
    assert '"fact_ids": ["1"]' in architecture
    assert '"from_node": "1"' in architecture
    assert "Phase 2021 overview" in architecture


def test_common_chinese_fact_does_not_corrupt_identifiers_or_longer_phrases() -> None:
    module = _load_compiler_module()
    material_view = _material_view()
    fact = "项目"
    source_id = "项目-id"
    material_view.value["complete_word_content"] = [{
        "type": "paragraph", "text": fact, "source_block_id": source_id, "source_order": 1,
    }]
    value = _director_value()
    value["page_plan"]["page_purpose"] = "项目"
    relationship = value["page_plan"]["primary_relationship"]
    relationship["description"] = "项目负责人统筹推进"
    relationship["nodes"][0]["node_id"] = "项目"
    relationship["nodes"][0]["fact_ids"] = ["项目"]
    relationship["edges"][0]["from_node"] = "项目"
    relationship["nodes"][0]["label"] = "项目组"

    sections = _compiled_sections(
        module.compile_consulting_six_part_prompt(value, material_view)
    )
    architecture = sections["Consulting Information Architecture"]
    content_lines = sections["Core Proposition and Content"].splitlines()

    assert content_lines.count(fact) == 1
    assert content_lines.count(f"[source fact {source_id} in section 2]") == 1
    assert "Page purpose: [source fact 项目-id in section 2]" in architecture
    assert '"node_id": "项目"' in architecture
    assert '"fact_ids": ["项目"]' in architecture
    assert '"from_node": "项目"' in architecture
    assert '"label": "项目组"' in architecture
    assert "项目负责人统筹推进" in architecture


def test_quoted_fact_is_normalized_before_json_serialization() -> None:
    module = _load_compiler_module()
    material_view = _material_view()
    fact = 'He said "go".'
    material_view.value["complete_word_content"] = [{
        "type": "paragraph", "text": fact, "source_block_id": "quote-1", "source_order": 1,
    }]
    value = _director_value()
    value["page_plan"]["primary_relationship"]["description"] = fact

    prompt = module.compile_consulting_six_part_prompt(value, material_view)
    sections = _compiled_sections(prompt)

    assert prompt.count(fact) == 1
    assert fact in sections["Core Proposition and Content"]
    assert "[source fact quote-1 in section 2]" in sections["Core Proposition and Content"]
    assert 'He said \\"go\\".' not in sections["Consulting Information Architecture"]
    assert "[source fact quote-1 in section 2]" in sections["Consulting Information Architecture"]


def test_embedded_complete_fact_is_normalized_at_text_boundaries() -> None:
    module = _load_compiler_module()
    material_view = _material_view()
    fact = "Regional resources enter the verified fund platform."
    material_view.value["complete_word_content"] = [{
        "type": "paragraph", "text": fact, "source_block_id": "embedded-1", "source_order": 1,
    }]
    value = _director_value()
    value["page_plan"]["page_purpose"] = f"Purpose: {fact}"
    value["page_plan"]["primary_relationship"]["visual_instruction"] = (
        f"Crop around evidence: {fact}"
    )

    prompt = module.compile_consulting_six_part_prompt(value, material_view)
    architecture = _compiled_sections(prompt)["Consulting Information Architecture"]

    assert prompt.count(fact) == 1
    assert "Purpose: [source fact embedded-1 in section 2]" in architecture
    assert "Crop around evidence: [source fact embedded-1 in section 2]" in architecture


def test_embedded_complete_fact_in_reference_instruction_is_rejected() -> None:
    module = _load_compiler_module()
    material_view = _material_view()
    fact = "Regional resources enter the verified fund platform."
    material_view.value["complete_word_content"] = [{
        "type": "paragraph", "text": fact, "source_block_id": "embedded-1", "source_order": 1,
    }]
    value = _director_value()
    value["selected_references"] = [{
        "material_id": "word-image:source-photo",
        "use": f"Crop around evidence: {fact}",
        "preserve": "Keep its identity.",
    }]

    with pytest.raises(ValueError, match="must not repeat complete Word facts"):
        module.compile_consulting_six_part_prompt(value, material_view)


def test_selected_reference_check_ignores_fixed_use_label_collision() -> None:
    module = _load_compiler_module()
    material_view = _material_view()
    material_view.value["complete_word_content"] = [{
        "type": "paragraph", "text": "use:", "source_block_id": "body-use", "source_order": 1,
    }]
    value = _director_value()
    value["selected_references"] = [{
        "material_id": "word-image:source-photo",
        "use": "Anchor the source photo.",
        "preserve": "Keep its identity.",
    }]

    prompt = module.compile_consulting_six_part_prompt(value, material_view)

    assert prompt.count("Anchor the source photo.") == 1
    assert prompt.count("Keep its identity.") == 1


@pytest.mark.parametrize("content,relationships", [
    (
        "研究组负责需求分析；工程组负责试制；两组同属项目部。研究组向工程组移交规格，质量组独立复核试制结果。",
        "嵌套呈现项目部与两组的归属；移交路径连接研究组与工程组；复核关系连接质量组与试制结果，不暗示质量组归属项目部。",
    ),
    (
        "甲项目须取得主管部门批复且完成安全评估后方可开工；乙项目仅须完成备案。安全评估有效期一年，仅适用于甲项目。",
        "两项目共享审批事项标签，各自条件依附所属项目；甲项目的批复和安全评估并列且同时满足，评估有效期贴近甲项目，不扩大到乙项目。",
    ),
    (
        "同一统计口径，2023年收入80万元，2024年收入100万元，均为已报告结果，不含税。2025年拟新增服务网点，能否落地取决于场地审批。",
        "收入局部比较保留2023年、2024年、万元和不含税口径；并置完整网点计划说明及审批条件，不把计划混入收入系列，不推算增长率。",
    ),
    (
        "甲部门截至2024年底有12名正式员工；乙部门2025年项目预算200万元。两项数字口径不同，原文未提供对应系列。",
        "12名与200万元作为各自对象的精确标签；日期、单位和口径随标签保留，不用长度、面积或共同刻度比较人数和预算。",
    ),
    (
        "资料中心设有档案室和阅览室，档案室保管纸质档案，阅览室提供预约查阅。原文仅描述组成和职责，未作绩效判断。",
        "资料中心与两室以归属组织，职责贴近各室；描述性信息不补写效率提升、优劣排名或投资建议。",
    ),
    (
        "北京团队承担研发，武汉团队负责试制，双方共同服务客户；原文没有提供合作流量或路线。",
        "以非地理位置示意呈现两地团队和共同客户，明确标注非地理位置示意；研发、试制贴近各自团队，不虚构准确位置、运输路线或联系强度。",
    ),
    (
        "目标是稳定交付，由研发与运营共同支撑，数据基础同时服务两项工作。原文没有给出两项工作的权重。",
        "采用神庙式支撑结构：屋顶承载稳定交付，两根支柱分别承载研发与运营，共享地基承载数据基础；完整说明依附各自部件，支柱尺寸不表示权重，图形不是实际建筑。",
    ),
], ids=["multiple-relationships", "scoped-conditions", "comparable-series-and-prose",
        "different-bases", "descriptive-purpose", "regional-schematic", "support-metaphor"])
def test_analytical_content_and_relationships_survive_six_part_compilation(
    content: str, relationships: str,
) -> None:
    from test_director import _compiled_prompt_sections

    module = _load_compiler_module()
    value = _director_value()
    value["page_plan"]["page_purpose"] = content
    value["page_plan"]["primary_relationship"]["description"] = relationships

    prompt = module.compile_consulting_six_part_prompt(value, _material_view())
    sections = _compiled_prompt_sections(prompt)

    # This is lossless transmission of supplied decisions, not an analysis-quality test.
    assert "".join(content.split()) in "".join(sections["Consulting Information Architecture"].split())
    assert "".join(relationships.split()) in "".join(sections["Consulting Information Architecture"].split())
    assert len([line for line in prompt.splitlines() if line.startswith("## ")]) == 6
    assert "creative_direction" not in prompt


@pytest.mark.parametrize("font_accent_allowed", [False, True, None])
def test_lean_prompt_allows_spatial_and_color_structure_without_measured_claims(
    font_accent_allowed,
) -> None:
    module = _load_compiler_module()
    value = _director_value()
    design = (
        "三方共同支撑项目，暂定2026年启动，须经评审；不是上下级或因果循环。"
        "用大色面、宽路径和不等空间呈现共同支撑，不表示数量或占比。"
    )
    value["page_plan"]["primary_relationship"]["visual_instruction"] = design

    prompt = module.compile_consulting_six_part_prompt(
        value, _material_view(), font_accent_allowed=font_accent_allowed,
    )

    assert design in "".join(prompt.split())
    assert "ordinary layout size, position, or hierarchy" in prompt
    assert "masquerade as measured scale" in prompt
    assert "source-explicit process, hierarchy, stage, or visual focus" in prompt
    assert "at least two visibly distinct tones" in prompt
    assert "proportional geometry" in prompt
    assert len([line for line in prompt.splitlines() if line.startswith("## ")]) == 6


@pytest.mark.parametrize("local_design", [
    "三家机构通过共同接口协作，箭头表示协作而非上下级。",
    "Use a local waterfall for the source-stated start 100, increase 20, and end 120.",
])
def test_compiler_keeps_page_design_without_injecting_unrelated_chart_catalogue(
    local_design: str,
) -> None:
    module = _load_compiler_module()
    value = _director_value()
    value["page_plan"]["primary_relationship"]["visual_instruction"] = local_design

    prompt = module.compile_consulting_six_part_prompt(value, _material_view())

    assert local_design in prompt
    assert "increase_decrease_drivers:" not in prompt
    assert "market_size_share:" not in prompt
    assert "eight relationship examples" not in prompt
    assert "without duplicating full prose" in prompt
    assert "attach complete explanations and scoped qualifiers to their subjects" in prompt
    assert "do not calculate new metrics" in prompt
    assert prompt.count("Word is the semantic authority") == 1


def test_compiler_restores_whole_page_backbone_without_freezing_source_appearance(
) -> None:
    module = _load_compiler_module()
    value = _director_value()
    value["page_plan"]["page_purpose"] = (
        "尚融牵头，三家机构协作；2026年目标为10个项目，须通过评审。"
    )
    prompt = module.compile_consulting_six_part_prompt(value, _material_view())

    assert "尚融牵头，三家机构协作；2026年目标为10个项目，须通过评审。" in "".join(prompt.split())
    assert "one source-supported main message in a coherent reading path" in prompt
    assert "Let relationships shape space" in prompt
    assert "parallel rows and paragraphs may be reordered" in prompt
    assert "Preserve meaningful sequence, membership and ownership" in prompt
    assert "Visual focus is not authority rank or measured magnitude" in prompt
    assert "no invented takeaway" in prompt
    assert "Preserve every distinct fact" in prompt


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
                "a comparison table is another option",
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
def test_compiler_preserves_director_selected_local_relationship_instructions(
    relationship: str, required_semantics: tuple[str, ...],
) -> None:
    module = _load_compiler_module()

    value = _director_value()
    value["page_plan"]["primary_relationship"]["visual_instruction"] = (
        f"{relationship}: " + "; ".join(required_semantics) + "."
    )
    prompt = module.compile_consulting_six_part_prompt(value, _material_view())

    row = next(line for line in prompt.splitlines() if relationship in line)
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
        "difference magnitude",
    ):
        assert forbidden_geometry in prompt
    assert "do not calculate new metrics" in prompt


def test_compiled_prompt_keeps_charts_optional_and_local() -> None:
    module = _load_compiler_module()
    value = _director_value()
    prompt = module.compile_consulting_six_part_prompt(
        value, _material_view(), font_accent_allowed=True
    )

    assert "Compose the whole slide first" in prompt
    assert "source evidence is complete and unambiguous" in prompt
    assert "Use charts only when they help" in prompt
    assert "KPIs, dates and counts may stay text" in prompt
    assert "complete data does not require a chart" in prompt
    assert "ordinary layout size, position, or hierarchy" in prompt
    assert "Use a quantitative form only" in prompt
    assert "use the named qualitative substitute" in prompt
    assert "otherwise use" not in prompt


def test_compiled_request_preserves_meaning_without_forcing_literal_copy_or_conclusion(
) -> None:
    module = _load_compiler_module()
    value = _director_value()
    value["page_plan"]["page_purpose"] = (
        "Group the source responsibilities by institution, retaining every condition."
    )
    prompt = module.compile_consulting_six_part_prompt(value, _material_view())

    assert "Preserve every distinct fact" in prompt
    assert "conditions, exceptions, degree of certainty" in prompt
    assert "do not split source body pages for layout density" in prompt
    assert "TOC continuations and source-backed section/closing additions" in prompt
    assert "Follow the frozen page composition" in prompt
    assert "exactly one PPT page" not in prompt
    assert "Lossless rewording, regrouping, and text-to-diagram conversion are allowed" in prompt
    assert "attach complete explanations and scoped qualifiers to their subjects" in prompt
    assert "source-supported conclusion where present, no invented takeaway" in prompt
    assert "Group the source responsibilities by institution" in prompt
    assert "exact contiguous spans from complete_word_content" not in prompt
    assert "Do not paraphrase, summarize" not in prompt
    assert "ending with an explicit takeaway" not in prompt


def test_compiler_distinguishes_instruction_introducers_from_quoted_source_copy(
) -> None:
    module = _load_compiler_module()
    value = _director_value()
    instruction = 'Lead instruction: render “项目按阶段推进”.'
    value["page_plan"]["page_purpose"] = instruction

    prompt = module.compile_consulting_six_part_prompt(value, _material_view())

    boundary = "Role labels, prompt section headings, and quote introducers are instruction metadata"
    assert boundary in prompt
    assert prompt.index(boundary) < prompt.index(instruction)
    assert "render only the source wording, never its instruction introducer" in prompt
    assert "Source-authored headings and labels remain allowed" in prompt
    assert "项目按阶段推进" in prompt


def test_compiler_bans_accent_family_text_only_on_non_emphasis_pages() -> None:
    module = _load_compiler_module()

    prompt = module.compile_consulting_six_part_prompt(
        _director_value(), _material_view(), font_accent_allowed=False
    )

    assert "This is not a user-confirmed emphasis page" in prompt
    assert "text objects may not use secondary-family or highlight-family colors" in prompt
    assert "text-box fills, shapes, borders, nodes, and connectors" in prompt


def test_compiler_legacy_mode_omits_only_the_new_page_gate() -> None:
    module = _load_compiler_module()
    prompt = module.compile_consulting_six_part_prompt(
        _director_value(), _material_view(), font_accent_allowed=None
    )
    assert "user-confirmed emphasis page" not in prompt
    assert "secondary color #CD202A" in prompt


def test_compiler_rejects_deleted_correction_v2_authority() -> None:
    module = _load_compiler_module()
    value = _director_value()
    value["schema_version"] = "awesome-page-correction-v2"

    with pytest.raises(ValueError, match="v3 director authority"):
        module.compile_consulting_six_part_prompt(value, _material_view())


def test_selected_reference_use_and_preserve_compile_exactly_once() -> None:
    module = _load_compiler_module()
    value = _director_value()
    value["selected_references"] = [{
        "material_id": "word-image:source-photo",
        "use": "REFERENCE-USE-SENTINEL",
        "preserve": "REFERENCE-PRESERVE-SENTINEL",
    }]

    prompt = module.compile_consulting_six_part_prompt(value, _material_view())
    architecture = _compiled_sections(prompt)["Consulting Information Architecture"]

    assert prompt.count("REFERENCE-USE-SENTINEL") == 1
    assert prompt.count("REFERENCE-PRESERVE-SENTINEL") == 1
    assert "REFERENCE-USE-SENTINEL" in architecture
    assert "REFERENCE-PRESERVE-SENTINEL" in architecture


def test_visual_director_reference_teaches_the_consulting_report_recipe() -> None:
    text = VISUAL_DIRECTOR_REFERENCE.read_text(encoding="utf-8").casefold()

    for required in (
        "page purpose",
        "primary relationship",
        "core exhibit",
        "allocate every word fact once",
        "analytical_table",
        "quantitative_chart",
        "source-bound nodes",
        "explicit directed edges",
        "pixel-relevant use",
        "identity features to preserve",
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


def test_initial_director_uses_v3_compact_page_plan(
    tmp_path: Path,
) -> None:
    from complex_page_experiment.director import direct_page
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

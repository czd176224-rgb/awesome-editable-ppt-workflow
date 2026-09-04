from __future__ import annotations

import json
from pathlib import Path

import pytest

from complex_page_experiment.consulting_prompt import (
    SECTION_SPECS,
    compile_consulting_six_part_prompt,
)
from complex_page_experiment.director import _validate_director_value
from complex_page_experiment.materials import CompletePageMaterialView


FIXTURE = Path(__file__).parent / "fixtures" / "consulting_director_cases.json"
VISUAL_QA = Path(__file__).resolve().parents[5] / "docs" / "CONSULTING_DIRECTOR_VISUAL_QA.md"
EXPECTED_CASES = (
    "three-lane-portfolio",
    "five-stage-capital-loop",
    "four-capability-transformation-chain",
    "four-row-investment-matrix",
)
LEGACY_TERMS = (
    "scene_and_composition",
    "subjects_and_relationships",
    "style_and_palette",
    "layout_and_text",
    "reference_usage",
    "constraints_and_avoidances",
    "compile_six_part_prompt",
    "awesome-complex-page-director-v1",
)


def _cases() -> list[dict[str, object]]:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert value["schema_version"] == "awesome-consulting-director-regressions-v2"
    return value["cases"]


def _director_value(case: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "awesome-consulting-page-director-v3",
        "page_number": 1,
        "quality": "high",
        "page_plan": case["page_plan"],
        "selected_references": [],
    }


def _material_view(case: dict[str, object]) -> CompletePageMaterialView:
    facts = [case["proposition"], case["explanatory_lead"], case["takeaway"]]
    return CompletePageMaterialView(
        value={
            "page_number": 1,
            "complete_word_content": [
                {
                    "type": "paragraph",
                    "text": text,
                    "source_block_id": f"body-{index}",
                    "source_order": index,
                }
                for index, text in enumerate(facts, start=1)
            ],
            "visual_contract": {
                "background_color": case["colors"]["background"],
                "primary_color": case["colors"]["primary"],
                "secondary_color": case["colors"]["secondary"],
            },
        },
        multimodal_images=(),
        material_ids=(),
        sha256="public-director-fixture",
    )


def test_public_regression_fixture_covers_the_four_consulting_body_patterns() -> None:
    cases = _cases()
    by_id = {case["id"]: case for case in cases}

    assert tuple(case["id"] for case in cases) == EXPECTED_CASES
    assert len({case["page_plan"]["primary_relationship"]["description"] for case in cases}) == 4
    assert all(case["explanatory_lead"] and case["takeaway"] for case in cases)
    loop = by_id["five-stage-capital-loop"]["page_plan"]["primary_relationship"]
    assert [node["node_id"] for node in loop["nodes"]] == [
        "sourcing", "screening", "investment", "value-creation", "realization",
    ]
    assert [(edge["from_node"], edge["to_node"]) for edge in loop["edges"]] == [
        ("sourcing", "screening"),
        ("screening", "investment"),
        ("investment", "value-creation"),
        ("value-creation", "realization"),
        ("realization", "sourcing"),
    ]
    chain = by_id["four-capability-transformation-chain"]["page_plan"][
        "primary_relationship"
    ]
    assert [node["node_id"] for node in chain["nodes"]] == [
        "data-foundation", "decision-intelligence", "operating-adoption", "measurable-outcome",
    ]
    assert [(edge["from_node"], edge["to_node"]) for edge in chain["edges"]] == [
        ("data-foundation", "decision-intelligence"),
        ("decision-intelligence", "operating-adoption"),
        ("operating-adoption", "measurable-outcome"),
    ]


@pytest.mark.parametrize("case", _cases(), ids=lambda case: str(case["id"]))
def test_each_public_case_is_a_v3_director_fixture(case) -> None:
    assert _validate_director_value(_director_value(case), _material_view(case)) == ()


@pytest.mark.parametrize("case", _cases(), ids=lambda case: str(case["id"]))
def test_each_public_case_compiles_to_the_sealed_consulting_prompt(case) -> None:
    value = _director_value(case)
    material_view = _material_view(case)

    prompt = compile_consulting_six_part_prompt(value, material_view)
    architecture = prompt.split("## Consulting Information Architecture\n", 1)[1].split(
        "\n\n## Visual Style and Color", 1
    )[0]
    visual = prompt.split("## Visual Style and Color\n", 1)[1].split(
        "\n\n## Text and Typography", 1
    )[0]

    headings = tuple(f"## {heading}" for heading, _key in SECTION_SPECS)
    assert tuple(prompt.index(heading) for heading in headings) == tuple(
        sorted(prompt.index(heading) for heading in headings)
    )
    assert case["proposition"] in prompt
    assert case["page_plan"]["primary_relationship"]["description"] in prompt
    assert case["page_plan"]["primary_relationship"]["visual_instruction"] in prompt
    assert case["explanatory_lead"] in prompt
    assert case["takeaway"] in prompt
    assert case["colors"]["background"] in prompt
    assert case["colors"]["primary"] in prompt
    assert case["colors"]["secondary"] in prompt
    # Prompt size remains diagnostic only; correctness is content and boundary preservation.
    assert len(prompt) > sum(
        len(block["text"])
        for block in material_view.value["complete_word_content"]
    )
    assert "Communicate one source-supported main message" in prompt
    assert "one source-supported main message in a coherent reading path" in prompt
    assert "no invented takeaway" in prompt
    assert "Do not generate title, logo, footer, or page number" in prompt
    assert "sole executable color contract is the compiler-owned contract" in architecture
    assert "This is not a user-confirmed emphasis page" in visual
    assert "text objects may not use secondary-family or highlight-family colors" in visual
    assert "Those colors remain allowed for non-text structural marks" in visual
    assert "text objects may not use secondary-family or highlight-family colors" not in architecture
    assert not any(term in prompt for term in LEGACY_TERMS)


def test_public_fixture_contains_no_private_project_or_page_identifiers() -> None:
    raw = FIXTURE.read_text(encoding="utf-8")

    for private_term in ("黄石", "Huangshi", "page 4", "page 19", "page 33", "page 36"):
        assert private_term not in raw


def test_visual_qa_keeps_private_pages_local_and_records_only_nonsensitive_results() -> None:
    text = VISUAL_QA.read_text(encoding="utf-8")

    assert "pages 4, 19, 33, and 36" in text
    assert "Store the private note outside the repository" in text
    assert "source project is read-only" in text
    assert "C:/" not in text and "C:\\" not in text


def test_director_template_context_uses_one_taskbook_helper_without_reviewing_director_output() -> None:
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    director = (scripts / "complex_page_experiment/director.py").read_text(encoding="utf-8")
    review = (scripts / "complex_page_experiment/review.py").read_text(encoding="utf-8")

    assert director.count("confirmed_taskbook_prompt(") == 1
    assert review.count("confirmed_taskbook_prompt(") == 1
    assert "CONFIRMED PRESENTATION TASKBOOK" in director
    assert "CONFIRMED PRESENTATION TASKBOOK" in review
    assert "_canonical_text(director.value)" not in review

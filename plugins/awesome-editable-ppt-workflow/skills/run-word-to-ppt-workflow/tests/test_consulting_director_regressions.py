from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def _cases() -> list[dict[str, object]]:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert value["schema_version"] == "awesome-consulting-director-regressions-v2"
    return value["cases"]


def _director_value(case: dict[str, object]) -> dict[str, object]:
    # Adapt the public synthetic source, not the retired compiler's output.
    facts = _source_facts(case)
    return {
        "schema_version": "awesome-page-design-v1",
        "page_number": 1,
        "quality": "high",
        "page_plan": {
            "image_prompt": "\n".join([
                *facts,
                case["page_plan"]["primary_relationship"]["visual_instruction"],
                case["page_plan"]["reading_path"],
                "Use colors: " + ", ".join(case["colors"].values()),
            ]),
            "content_inventory": [
                {"source_block_id": f"body-{index}", "source_quote": text,
                 "display_copy": text, "target": "body"}
                for index, text in enumerate(facts, start=1)
            ],
            "context_bridges": [],
            "quantitative_exhibits": None,
            "numeric_authorities": None,
        },
        "selected_references": [],
    }

def _source_facts(case):
    relationship = case["page_plan"]["primary_relationship"]
    labels = {node["node_id"]: node["label"] for node in relationship["nodes"]}
    return [
        case["proposition"], case["explanatory_lead"], case["takeaway"],
        relationship["description"],
        *labels.values(),
        *[
            f"{labels[edge['from_node']]} -> {labels[edge['to_node']]}"
            + (f": {edge['label']}" if edge.get("label") else "")
            for edge in relationship["edges"]
        ],
    ]


def _material_view(case: dict[str, object]) -> CompletePageMaterialView:
    facts = _source_facts(case)
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
def test_each_public_case_satisfies_the_current_director_contract(case) -> None:
    assert _validate_director_value(_director_value(case), _material_view(case)) == ()


@pytest.mark.parametrize("case", _cases(), ids=lambda case: str(case["id"]))
def test_each_public_case_retains_one_exact_prompt_and_complete_relationship(case) -> None:
    value = _director_value(case)
    material_view = _material_view(case)

    prompt = value["page_plan"]["image_prompt"]
    before = json.dumps(value, ensure_ascii=False)
    _validate_director_value(value, material_view)
    assert json.dumps(value, ensure_ascii=False) == before
    assert set(value["page_plan"]) == {
        "image_prompt", "content_inventory", "context_bridges",
        "quantitative_exhibits", "numeric_authorities",
    }
    assert all(fact in prompt for fact in _source_facts(case))
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


@pytest.mark.parametrize("case", _cases(), ids=lambda case: str(case["id"]))
def test_current_contract_rejects_each_omitted_fact_or_relationship(case) -> None:
    for index in range(len(_source_facts(case))):
        value = _director_value(case)
        del value["page_plan"]["content_inventory"][index]
        with pytest.raises(ValueError, match="content inventory omits source text"):
            _validate_director_value(value, _material_view(case))


@pytest.mark.parametrize("case", _cases(), ids=lambda case: str(case["id"]))
def test_current_contract_rejects_inventory_copy_missing_from_the_prompt(case) -> None:
    for fact in _source_facts(case):
        value = _director_value(case)
        value["page_plan"]["image_prompt"] = "\n".join(
            line for line in value["page_plan"]["image_prompt"].splitlines()
            if fact not in line
        )
        with pytest.raises(ValueError, match="visible copy is absent from image_prompt"):
            _validate_director_value(value, _material_view(case))


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
    assert "已确认任务书" in director
    assert "CONFIRMED PRESENTATION TASKBOOK" in review
    assert "_canonical_text(director.value)" not in review

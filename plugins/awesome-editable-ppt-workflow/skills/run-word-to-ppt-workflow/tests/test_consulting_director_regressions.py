from __future__ import annotations

import json
from pathlib import Path

import pytest

from complex_page_experiment.consulting_prompt import (
    SECTION_SPECS,
    compile_consulting_six_part_prompt,
)


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
    assert value["schema_version"] == "awesome-consulting-director-regressions-v1"
    return value["cases"]


def test_public_regression_fixture_covers_the_four_consulting_body_patterns() -> None:
    cases = _cases()

    assert tuple(case["id"] for case in cases) == EXPECTED_CASES
    assert len({case["analytical_backbone"] for case in cases}) == 4
    assert all(case["explanatory_lead"] and case["takeaway"] for case in cases)


@pytest.mark.parametrize("case", _cases(), ids=lambda case: str(case["id"]))
def test_each_public_case_compiles_to_the_sealed_consulting_prompt(case) -> None:
    value = {
        "schema_version": "awesome-consulting-page-director-v2",
        "prompt_sections": case["prompt_sections"],
    }
    material_view = {
        "visual_contract": {
            "background_color": case["colors"]["background"],
            "primary_color": case["colors"]["primary"],
            "secondary_color": case["colors"]["secondary"],
        }
    }

    prompt = compile_consulting_six_part_prompt(value, material_view)

    headings = tuple(f"## {heading}" for heading, _key in SECTION_SPECS)
    assert tuple(prompt.index(heading) for heading in headings) == tuple(
        sorted(prompt.index(heading) for heading in headings)
    )
    assert case["proposition"] in prompt
    assert case["analytical_backbone"] in prompt
    assert case["explanatory_lead"] in prompt
    assert case["takeaway"] in prompt
    assert case["colors"]["background"] in prompt
    assert case["colors"]["primary"] in prompt
    assert case["colors"]["secondary"] in prompt
    assert "evidence, interpretation, and conclusion" in prompt
    assert "Do not generate title, logo, footer, or page number" in prompt
    assert "secondary color" in prompt and "strictly as an accent" in prompt
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

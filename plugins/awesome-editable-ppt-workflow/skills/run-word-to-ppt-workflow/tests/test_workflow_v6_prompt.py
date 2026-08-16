from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v6_prompt_contract import compile_confirmed_page_prompt  # noqa: E402


EXPECTED_INITIAL_SECTIONS = [
    "system_generation_constraints",
    "frozen_global_visual_contract",
    "geometry_and_fixed_layer_exclusions",
    "confirmed_effective_body",
    "confirmed_attachment_extracts",
    "confirmed_chart_facts",
    "confirmed_image_requirements",
    "confirmed_usable_references",
    "confirmed_degradation_expressions",
]


def _confirmed_page() -> dict:
    return {
        "page_number": 7,
        "fixed_page_title": "FIXED_TITLE_MUST_NOT_RENDER",
        "word_original": "RAW_WORD_DUPLICATE_MUST_NOT_RETURN",
        "effective_body": "BODY_SENTINEL unchanged\nsecond line",
        "attachment_extracts": [{"kind": "text", "content": "ATTACHMENT_SENTINEL"}],
        "chart_facts": [{"series": "CHART_SENTINEL", "value": "17.25"}],
        "image_requirements": [{"requirement": "IMAGE_REQUIREMENT_SENTINEL"}],
        "degradations": [{"expression": "DEGRADATION_SENTINEL"}],
        "reference_images": [
            {
                "reference_id": "usable-reference",
                "status": "available",
                "source": "attachment",
                "purpose": "REFERENCE_ROLE_SENTINEL",
                "preservation": "required_presence",
                "allow_crop": False,
                "allow_restyle": False,
                "model_input_path": "02_v6/private/model-input.png",
                "integrity": {"model_input_sha256": "a" * 64},
            },
            {
                "reference_id": "unavailable-reference",
                "status": "unavailable",
                "purpose": "UNAVAILABLE_SENTINEL",
            },
        ],
        "raw_comments": ["RAW_COMMENT_SENTINEL"],
        "search_tasks": [{"query": "SEARCH_PROCESS_SENTINEL"}],
        "acquisition_receipt": {"result": "ACQUISITION_PROCESS_SENTINEL"},
        "backend_conclusions": ["BACKEND_CONCLUSION_SENTINEL"],
        "candidate_path": "04_v6/images/CANDIDATE_PATH_SENTINEL.png",
        "confirmed_revision": 9,
        "confirmed_revision_digest": "b" * 64,
    }


def test_prompt_serializes_exact_authoritative_sections_in_order_once_and_verbatim():
    contract = {"visual_style": "STYLE_SENTINEL", "color": {"z": 1, "a": 2}}

    prompt = compile_confirmed_page_prompt(contract, _confirmed_page())
    payload = json.loads(prompt, object_pairs_hook=dict)

    assert list(payload) == EXPECTED_INITIAL_SECTIONS
    assert payload["frozen_global_visual_contract"] == contract
    assert payload["confirmed_effective_body"] == "BODY_SENTINEL unchanged\nsecond line"
    assert payload["confirmed_attachment_extracts"][0]["content"] == "ATTACHMENT_SENTINEL"
    assert payload["confirmed_chart_facts"][0]["series"] == "CHART_SENTINEL"
    assert payload["confirmed_image_requirements"][0]["requirement"] == "IMAGE_REQUIREMENT_SENTINEL"
    assert payload["confirmed_degradation_expressions"][0]["expression"] == "DEGRADATION_SENTINEL"
    for sentinel in (
        "STYLE_SENTINEL",
        "BODY_SENTINEL",
        "ATTACHMENT_SENTINEL",
        "CHART_SENTINEL",
        "IMAGE_REQUIREMENT_SENTINEL",
        "DEGRADATION_SENTINEL",
        "REFERENCE_ROLE_SENTINEL",
    ):
        assert prompt.count(sentinel) == 1


def test_prompt_filters_non_authoritative_metadata_and_describes_reference_boundaries():
    prompt = compile_confirmed_page_prompt({"visual_style": "minimal"}, _confirmed_page())
    payload = json.loads(prompt)

    forbidden = (
        "FIXED_TITLE_MUST_NOT_RENDER",
        "RAW_WORD_DUPLICATE_MUST_NOT_RETURN",
        "RAW_COMMENT_SENTINEL",
        "SEARCH_PROCESS_SENTINEL",
        "ACQUISITION_PROCESS_SENTINEL",
        "BACKEND_CONCLUSION_SENTINEL",
        "CANDIDATE_PATH_SENTINEL",
        "02_v6/private/model-input.png",
        "a" * 64,
        "b" * 64,
        "UNAVAILABLE_SENTINEL",
    )
    assert all(value not in prompt for value in forbidden)
    assert payload["confirmed_usable_references"] == [{
        "reference_id": "usable-reference",
        "role": "REFERENCE_ROLE_SENTINEL",
        "preservation": "required_presence",
        "allow_crop": False,
        "allow_restyle": False,
    }]
    constraints = json.dumps(payload["system_generation_constraints"], ensure_ascii=False)
    geometry = json.dumps(payload["geometry_and_fixed_layer_exclusions"], ensure_ascii=False)
    assert "independent reference materials" in constraints
    assert "not a base canvas" in constraints
    assert "high-fidelity best effort" in constraints
    assert "not pixel-perfect" in constraints
    assert "unreferenced real identity, brand, event, product, or evidence" in constraints
    assert all(layer in geometry for layer in ("main title", "fixed SVG logo", "footer", "page number"))


def test_retry_adds_actionable_qa_feedback_as_the_tenth_and_only_new_section():
    page = _confirmed_page()
    initial = json.loads(compile_confirmed_page_prompt({"visual_style": "minimal"}, page))
    retry_prompt = compile_confirmed_page_prompt(
        {"visual_style": "minimal"}, page, ["Increase contrast in the lower chart"]
    )
    retry = json.loads(retry_prompt)

    assert list(initial) == EXPECTED_INITIAL_SECTIONS
    assert list(retry) == [*EXPECTED_INITIAL_SECTIONS, "actionable_qa_feedback"]
    assert retry["actionable_qa_feedback"] == ["Increase contrast in the lower chart"]
    assert retry_prompt.count("Increase contrast in the lower chart") == 1


def test_prompt_is_deterministic_for_equivalent_mapping_key_order():
    first = compile_confirmed_page_prompt(
        {"visual_style": "minimal", "colors": {"secondary": "blue", "primary": "red"}},
        _confirmed_page(),
    )
    second = compile_confirmed_page_prompt(
        {"colors": {"primary": "red", "secondary": "blue"}, "visual_style": "minimal"},
        _confirmed_page(),
    )
    assert first == second


def test_user_editable_json_sections_preserve_deep_keys_that_look_like_local_metadata():
    page = _confirmed_page()
    legitimate = {
        "path": "PATH_IS_USER_CONTENT",
        "metadata": {"revision": "REVISION_IS_USER_CONTENT"},
        "hash": "HASH_IS_USER_CONTENT",
        "nested": {
            "candidate_path": "CANDIDATE_IS_USER_CONTENT",
            "receipt": "RECEIPT_IS_USER_CONTENT",
        },
    }
    page["attachment_extracts"] = [{"content": legitimate}]
    page["chart_facts"] = [{"data": legitimate}]
    page["image_requirements"] = [{"instruction": legitimate}]
    page["degradations"] = [{"expression": legitimate}]

    payload = json.loads(compile_confirmed_page_prompt({"visual_style": "minimal"}, page))

    assert payload["confirmed_attachment_extracts"] == page["attachment_extracts"]
    assert payload["confirmed_chart_facts"] == page["chart_facts"]
    assert payload["confirmed_image_requirements"] == page["image_requirements"]
    assert payload["confirmed_degradation_expressions"] == page["degradations"]
    rendered = json.dumps(payload, ensure_ascii=False)
    for sentinel in (
        "PATH_IS_USER_CONTENT", "REVISION_IS_USER_CONTENT", "HASH_IS_USER_CONTENT",
        "CANDIDATE_IS_USER_CONTENT", "RECEIPT_IS_USER_CONTENT",
    ):
        assert rendered.count(sentinel) == 4


def test_global_contract_projects_real_visual_schema_and_excludes_runtime_controls():
    visual_fields = {
        "canvas": "ppt169",
        "direction": 2,
        "template_selection": {
            "id": "policy-project-brief", "label": "Policy", "version": "1.0",
            "substyle_id": None, "override_fields": ["color"],
        },
        "visual_style": "editorial",
        "color": {"primary": "#112233", "metadata": "LEGITIMATE_PALETTE_METADATA"},
        "icons": "outline",
        "typography": {
            "name_zh": "现代商务",
            "heading": {"cjk": "微软雅黑", "latin": "Arial", "css": "Arial, sans-serif"},
            "body": {"cjk": "微软雅黑", "latin": "Arial", "css": "Arial, sans-serif"},
            "body_size": 16,
            "type_scale_pt": {
                "page_title": 28, "section_title": 20, "body": 16, "caption": 10,
            },
        },
        "image_rendering": {"mode": "photographic", "path": "LEGITIMATE_RENDER_PATH"},
        "style_axes": {"formal": 80, "modern": 70, "minimal": 60},
        "layout_preferences": ["editorial", "data-led"],
        "information_density": "balanced",
        "regional_style": {"enabled": True, "region": "China"},
        "background_system": "light",
        "image_role": {"role": "evidence", "proportion": "medium"},
        "evidence_strength": "strict",
        "composition_tendency": "formal-consulting",
        "brand_device": "light",
        "additional_requirements": "Keep restrained whitespace",
        "image_usage_policy": "content-driven",
    }
    runtime_fields = {
        "stage": "final", "status": "confirmed", "confirmed_at": "2026-08-12",
        "page_count": 9, "pagination_mode": "word-pages", "one_page_to_one_slide": True,
        "production_profile": "speed", "formula_policy": "mixed",
        "generation_mode": "continuous", "refine_spec": True, "image_quality": "high",
        "max_concurrency": 3, "automatic_repair_budget": 2, "editable_output": True,
        "start_generation": True, "nonce": "PRIVATE_NONCE", "contract_hash": "e" * 64,
    }

    payload = json.loads(compile_confirmed_page_prompt(
        {**visual_fields, **runtime_fields}, _confirmed_page()
    ))

    assert payload["frozen_global_visual_contract"] == visual_fields
    rendered = json.dumps(payload["frozen_global_visual_contract"], ensure_ascii=False)
    assert "PRIVATE_NONCE" not in rendered
    assert "e" * 64 not in rendered


def test_empty_global_visual_contract_fails_before_compilation():
    with pytest.raises(ValueError, match="global visual contract"):
        compile_confirmed_page_prompt({}, _confirmed_page())

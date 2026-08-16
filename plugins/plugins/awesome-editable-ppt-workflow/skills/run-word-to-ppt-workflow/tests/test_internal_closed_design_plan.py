from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SKILL = Path(__file__).resolve().parents[1]
VALIDATOR = SKILL / "scripts" / "validate_page_image_prompt.py"


def _module():
    spec = importlib.util.spec_from_file_location("closed_prompt_validator", VALIDATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _materials() -> dict:
    return {
        "page_number": 1,
        "fixed_page_title": "Fixed title",
        "complete_word_content": [{"type": "paragraph", "text": "Revenue was 10.", "source_block_id": "b1", "source_block_index": 0, "source_order": 1, "relationship_ids": ["r1"], "comment_ids": ["c1"]}],
        "original_comments": [{"comment_id": "c1", "source_order": 1, "text": "Emphasize the supplied result."}],
        "word_images": [{"asset_id": "img1", "source_order": 1, "original_filename": "evidence.png", "media_type": "image/png", "path": "assets/evidence.png", "sha256": "a" * 64, "byte_size": 1}],
        "attachment_inputs": [],
        "visual_contract": {"primary_color": "#123456", "secondary_color": "#654321", "background_color": "#FFFFFF", "cjk_font": "Microsoft YaHei", "latin_font": "Arial", "title_size_pt": 28, "body_size_pt": 18, "caption_size_pt": 12, "regional_characteristics": "", "visual_description": "restrained consulting style"},
        "body_frame": {"geometry_version": "fixed-canvas-cm-v2", "body_bounds_cm": {"x": 0.81, "y": 2.3, "w": 23.78, "h": 11.18}, "body_pixels": {"width": 1904, "height": 896}, "fixed_layers": ["title", "logo", "footer", "page_number"]},
    }


def _compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _block(label: str, value) -> str:
    raw = _compact(value)
    return f"{label}_LENGTH={len(raw.encode('utf-8'))}\n{label}_BEGIN\n{raw}\n{label}_END"


def _result(materials: dict, refs=None, plan=None) -> dict:
    refs = refs or []
    ids = ["b1", "c1", *refs]
    plan = plan or {
        "composition_archetype": "two-column", "content_sequence": ["b1"],
        "comment_directives": [{"comment_id": "c1", "actions": ["emphasize"], "target_ids": ["b1"]}],
        "reference_substitutions": [],
        "hierarchy_order": ["b1", *refs],
        "emphasis_ids": ["b1"], "groups": [{"group_id": "group_1", "member_ids": ["b1", *refs]}],
        "reading_direction": "left-to-right", "layout_density": "balanced", "whitespace": "balanced",
        "connector_style": "none", "icon_policy": "source-supported-only",
        "reference_treatments": [{"reference_id": ref, "preserve": "all-content", "change": "crop-scale-place", "crop": "fit", "placement": "supporting"} for ref in refs],
    }
    prompt = (
        "## Task\nGenerate one 1904 x 896 PowerPoint body-region image.\n\n"
        "## Original Materials\n" + _block("WORD_CONTENT_JSON", materials["complete_word_content"]) + "\n"
        + _block("ORIGINAL_COMMENTS_JSON", materials["original_comments"]) + "\n"
        + _block("SELECTED_REFERENCE_IDS_JSON", refs) + "\n\n"
        "## Visual Presentation\n" + _block("VISUAL_CONTRACT_JSON", materials["visual_contract"]) + "\n"
        + _block("DESIGN_PLAN_JSON", plan) + "\n\n"
        "## Fixed Boundaries\nGenerate only the 17:8 body region at exactly 1904 x 896 and respect the safe area.\n"
        "Do not generate the fixed page title.\nDo not generate the fixed logo.\nDo not generate the footer.\nDo not generate the page number."
    )
    return {"schema_version": "page-image-prompt-v1", "page_number": 1, "selected_reference_images": refs, "image_prompt": prompt}


def test_accepts_closed_design_plan_and_normalizes_crlf():
    materials = _materials()
    result = _result(materials, ["word:img1"])
    result["image_prompt"] = result["image_prompt"].replace("\n", "\r\n")
    assert _module().validate_page_image_prompt(materials, result) is None


@pytest.mark.parametrize(("field", "value"), [
    ("composition_archetype", "invent-a-new-story"), ("reading_direction", "spiral around 42%"),
    ("layout_density", "show headquarters"), ("icon_policy", "search stock photos"),
])
def test_rejects_open_ended_design_values(field, value):
    materials = _materials(); result = _result(materials)
    plan = _extract_plan(result); plan[field] = value
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(materials, _result(materials, plan=plan))


@pytest.mark.parametrize("extra", [{"caption": "Revenue rises 42%"}, {"search_query": "find news"}, {"notes": "invent a conclusion"}, {"path": "C:/secret/photo.png"}])
def test_rejects_free_text_facts_search_and_paths(extra):
    materials = _materials(); plan = _extract_plan(_result(materials)); plan.update(extra)
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(materials, _result(materials, plan=plan))


@pytest.mark.parametrize("unknown", ["b999", "c999", "word:unknown", "Revenue +42%"])
def test_all_design_content_ids_must_be_page_owned(unknown):
    materials = _materials(); plan = _extract_plan(_result(materials)); plan["hierarchy_order"].append(unknown)
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(materials, _result(materials, plan=plan))


def test_selected_references_require_one_exact_closed_treatment():
    materials = _materials(); result = _result(materials, ["word:img1"]); plan = _extract_plan(result)
    plan["reference_treatments"] = []
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(materials, _result(materials, ["word:img1"], plan))


@pytest.mark.parametrize("sequence", [[], ["b1", "b1"], ["b999"]])
def test_content_sequence_contains_every_word_block_exactly_once(sequence):
    materials = _materials(); plan = _extract_plan(_result(materials)); plan["content_sequence"] = sequence
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(materials, _result(materials, plan=plan))


@pytest.mark.parametrize("directives", [
    [],
    [{"comment_id": "c999", "actions": ["emphasize"], "target_ids": ["b1"]}],
    [{"comment_id": "c1", "actions": ["emphasize"], "target_ids": ["b1"]}, {"comment_id": "c1", "actions": ["restructure"], "page_scope": "whole-page"}],
    [{"comment_id": "c1", "actions": ["invent-conclusion"], "target_ids": ["b1"]}],
    [{"comment_id": "c1", "actions": ["emphasize"], "target_ids": ["unknown"]}],
])
def test_comment_directives_cover_every_comment_once_with_closed_actions(directives):
    materials = _materials(); plan = _extract_plan(_result(materials)); plan["comment_directives"] = directives
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(materials, _result(materials, plan=plan))


def test_comment_directive_may_target_whole_page_without_free_text():
    materials = _materials(); plan = _extract_plan(_result(materials))
    plan["comment_directives"] = [{"comment_id": "c1", "actions": ["abstract-to-model", "prohibit-promotional"], "page_scope": "whole-page"}]
    assert _module().validate_page_image_prompt(materials, _result(materials, plan=plan)) is None


@pytest.mark.parametrize("mutation", [
    lambda plan: plan["hierarchy_order"].remove("word:img1"),
    lambda plan: plan["groups"][0]["member_ids"].remove("word:img1"),
    lambda plan: plan["groups"].append({"group_id": "group_1", "member_ids": ["word:img1"]}),
    lambda plan: plan["groups"].append({"group_id": "group_2", "member_ids": ["word:img1"]}),
])
def test_selected_reference_is_placed_once_in_hierarchy_group_and_treatment(mutation):
    materials = _materials(); plan = _extract_plan(_result(materials, ["word:img1"])); mutation(plan)
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(materials, _result(materials, ["word:img1"], plan))


def test_four_real_comment_action_vocabulary_is_expressive_without_facts():
    actions = {
        "projects": ["emphasize", "use-reference"],
        "strategy": ["abstract-to-model", "change-reading-order", "prohibit-promotional"],
        "news": ["use-reference", "preserve-exact"],
        "logos": ["use-reference", "preserve-exact"],
    }
    allowed = set(_module().COMMENT_ACTIONS)
    assert all(set(value) <= allowed for value in actions.values())


def test_hierarchy_groups_are_exact_visual_set_and_comments_are_not_visual_objects():
    materials = _materials(); plan = _extract_plan(_result(materials, ["word:img1"]))
    for field in ("hierarchy_order", "emphasis_ids"):
        bad = json.loads(json.dumps(plan)); bad[field].append("c1")
        with pytest.raises(ValueError):
            _module().validate_page_image_prompt(materials, _result(materials, ["word:img1"], bad))
    bad = json.loads(json.dumps(plan)); bad["groups"][0]["member_ids"].remove("b1")
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(materials, _result(materials, ["word:img1"], bad))


def test_four_logo_substitutions_pair_each_company_block_to_one_selected_logo():
    materials = _materials()
    materials["complete_word_content"] = [dict(materials["complete_word_content"][0], source_block_id=f"b{i}", source_block_index=i-1, source_order=i) for i in range(1, 5)]
    materials["word_images"] = [dict(materials["word_images"][0], asset_id=f"logo{i}", source_order=i, original_filename=f"company{i}-logo.png") for i in range(1, 5)]
    refs = [f"word:logo{i}" for i in range(1, 5)]
    plan = _extract_plan(_result(_materials()))
    plan.update({
        "content_sequence": [f"b{i}" for i in range(1, 5)],
        "comment_directives": [{"comment_id": "c1", "actions": ["use-reference"], "target_ids": [x for i in range(1, 5) for x in (f"b{i}", f"word:logo{i}")], "substitution_ids": [f"sub_{i}" for i in range(1, 5)]}],
        "reference_substitutions": [{"substitution_id": f"sub_{i}", "source_id": f"b{i}", "reference_id": f"word:logo{i}", "mode": "replace-visible-label-with-reference-image"} for i in range(1, 5)],
        "hierarchy_order": [x for i in range(1, 5) for x in (f"b{i}", f"word:logo{i}")],
        "emphasis_ids": refs,
        "groups": [{"group_id": f"group_{i}", "member_ids": [f"b{i}", f"word:logo{i}"]} for i in range(1, 5)],
        "reference_treatments": [{"reference_id": ref, "preserve": "identity-and-content", "change": "scale-and-place", "crop": "none", "placement": "primary"} for ref in refs],
    })
    assert _module().validate_page_image_prompt(materials, _result(materials, refs, plan)) is None
    bad = json.loads(json.dumps(plan)); bad["reference_substitutions"][3]["reference_id"] = "word:logo1"
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(materials, _result(materials, refs, bad))


@pytest.mark.parametrize("targets", [
    ["word:logo1"], ["b1"], ["b1", "word:logo2"],
])
def test_substitution_directive_targets_exact_source_and_reference(targets):
    materials = _materials(); refs = ["word:img1"]
    plan = _extract_plan(_result(materials, refs))
    plan["reference_substitutions"] = [{"substitution_id": "sub_1", "source_id": "b1", "reference_id": "word:img1", "mode": "replace-visible-label-with-reference-image"}]
    plan["comment_directives"] = [{"comment_id": "c1", "actions": ["use-reference"], "target_ids": targets, "substitution_ids": ["sub_1"]}]
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(materials, _result(materials, refs, plan))


def test_substitution_directive_cannot_use_page_scope():
    materials = _materials(); refs = ["word:img1"]
    plan = _extract_plan(_result(materials, refs))
    plan["reference_substitutions"] = [{"substitution_id": "sub_1", "source_id": "b1", "reference_id": "word:img1", "mode": "replace-visible-label-with-reference-image"}]
    plan["comment_directives"] = [{"comment_id": "c1", "actions": ["use-reference"], "page_scope": "whole-page", "substitution_ids": ["sub_1"]}]
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(materials, _result(materials, refs, plan))


def test_duplicate_original_comment_ids_are_rejected_even_with_matching_directives():
    materials = _materials()
    materials["original_comments"].append({"comment_id": "c1", "source_order": 2, "text": "second"})
    plan = _extract_plan(_result(_materials()))
    plan["comment_directives"] = [
        {"comment_id": "c1", "actions": ["preserve-exact"], "page_scope": "whole-page"},
        {"comment_id": "c1", "actions": ["emphasize"], "target_ids": ["b1"]},
    ]
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(materials, _result(materials, plan=plan))


def _extract_plan(result: dict) -> dict:
    prompt = result["image_prompt"]
    start = prompt.index("DESIGN_PLAN_JSON_BEGIN\n") + len("DESIGN_PLAN_JSON_BEGIN\n")
    end = prompt.index("\nDESIGN_PLAN_JSON_END", start)
    return json.loads(prompt[start:end])

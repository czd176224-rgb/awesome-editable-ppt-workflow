from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SKILL = Path(__file__).resolve().parents[1]
VALIDATOR = SKILL / "scripts" / "validate_page_image_prompt.py"


def _module():
    spec = importlib.util.spec_from_file_location("prompt_validator", VALIDATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _materials() -> dict:
    return {
        "page_number": 3,
        "fixed_page_title": "并购合作会议",
        "complete_word_content": [
            {
                "type": "paragraph", "text": "新闻稿：并购合作会议于北京举行，双方讨论行业协同。",
                "source_block_id": "b1", "source_block_index": 0, "source_order": 1,
                "relationship_ids": ["rId5"], "comment_ids": ["c1"],
            },
            {
                "type": "table", "rows": [["人物", "事项"], ["王巍", "讲话"], ["李耀武", "讲话"]],
                "source_block_id": "b2", "source_block_index": 1, "source_order": 2,
                "relationship_ids": [], "comment_ids": [],
            },
        ],
        "original_comments": [
            {"comment_id": "c1", "source_order": 1, "text": "添加新闻稿图片，并且有王巍和李耀武讲话图片"},
            {"comment_id": "c2", "source_order": 2, "text": "保持会议事实原样"},
        ],
        "word_images": [
            {"asset_id": "word_image_01", "source_order": 1, "original_filename": "office.png", "media_type": "image/png", "path": "assets/office.png", "sha256": "a" * 64, "byte_size": 5},
        ],
        "attachment_inputs": [
            {"asset_id": "att01", "source_order": 1, "original_filename": "news.pdf", "media_type": "application/pdf", "path": "assets/news.pdf", "sha256": "b" * 64, "byte_size": 8,
             "render_receipt": {"schema_version": "awesome-attachment-render-v1", "original_path": "assets/news.pdf", "original_sha256": "b" * 64, "original_byte_size": 8, "renderer_identity": "pdf-v1", "pages": [
                 {"page_number": 1, "path": "render/news-1.png", "width": 1000, "height": 700, "byte_size": 9, "sha256": "c" * 64},
                 {"page_number": 2, "path": "render/news-2.png", "width": 1000, "height": 700, "byte_size": 10, "sha256": "d" * 64}],
                 "contact_sheet": {"page_number": 0, "path": "render/contact.png", "width": 1000, "height": 700, "byte_size": 11, "sha256": "e" * 64}}},
        ],
        "visual_contract": {"primary_color": "#123456", "secondary_color": "#1AA6A6", "background_color": "#FFFFFF", "cjk_font": "微软雅黑", "latin_font": "Arial", "title_size_pt": 28, "body_size_pt": 18, "caption_size_pt": 12, "regional_characteristics": "", "visual_description": "克制的管理咨询风格"},
        "body_frame": {"geometry_version": "fixed-canvas-cm-v2", "body_bounds_cm": {"x": 0.81, "y": 2.3, "w": 23.78, "h": 11.18}, "body_pixels": {"width": 1904, "height": 896}, "fixed_layers": ["title", "logo", "footer", "page_number"]},
    }


def _prompt(materials: dict, refs=None) -> str:
    compact = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    word = compact(materials['complete_word_content'])
    comments = compact(materials['original_comments'])
    visual = compact(materials['visual_contract'])
    refs = refs or []
    selected = compact(refs)
    ids = [item["source_block_id"] for item in materials["complete_word_content"]]
    ids += [item["comment_id"] for item in materials["original_comments"]]
    ids += refs
    plan = compact({"composition_archetype": "two-column",
                    "content_sequence": [item["source_block_id"] for item in materials["complete_word_content"]],
                    "comment_directives": [{"comment_id": item["comment_id"], "actions": ["preserve-exact"], "page_scope": "whole-page"} for item in materials["original_comments"]],
                    "reference_substitutions": [],
                    "hierarchy_order": [item["source_block_id"] for item in materials["complete_word_content"]] + refs,
                    "emphasis_ids": [materials["complete_word_content"][0]["source_block_id"]],
                    "groups": [{"group_id": "group_1", "member_ids": [item["source_block_id"] for item in materials["complete_word_content"]] + refs}],
                    "reading_direction": "left-to-right", "layout_density": "balanced",
                    "whitespace": "balanced", "connector_style": "none", "icon_policy": "source-supported-only",
                    "reference_treatments": [{"reference_id": ref, "preserve": "all-content",
                                              "change": "crop-scale-place", "crop": "fit", "placement": "supporting"}
                                             for ref in refs]})
    return (
        "## Task\nGenerate one 1904 x 896 PowerPoint body-region image.\n\n"
        "## Original Materials\n"
        f"WORD_CONTENT_JSON_LENGTH={len(word.encode('utf-8'))}\nWORD_CONTENT_JSON_BEGIN\n{word}\nWORD_CONTENT_JSON_END\n"
        f"ORIGINAL_COMMENTS_JSON_LENGTH={len(comments.encode('utf-8'))}\nORIGINAL_COMMENTS_JSON_BEGIN\n{comments}\nORIGINAL_COMMENTS_JSON_END\n"
        f"SELECTED_REFERENCE_IDS_JSON_LENGTH={len(selected.encode('utf-8'))}\nSELECTED_REFERENCE_IDS_JSON_BEGIN\n{selected}\nSELECTED_REFERENCE_IDS_JSON_END\n\n"
        f"## Visual Presentation\nVISUAL_CONTRACT_JSON_LENGTH={len(visual.encode('utf-8'))}\nVISUAL_CONTRACT_JSON_BEGIN\n{visual}\nVISUAL_CONTRACT_JSON_END\n"
        f"DESIGN_PLAN_JSON_LENGTH={len(plan.encode('utf-8'))}\nDESIGN_PLAN_JSON_BEGIN\n{plan}\nDESIGN_PLAN_JSON_END\n\n"
        "## Fixed Boundaries\nGenerate only the 17:8 body region at exactly 1904 x 896 and respect the safe area.\n"
        "Do not generate the fixed page title.\nDo not generate the fixed logo.\n"
        "Do not generate the footer.\nDo not generate the page number."
    )


def _result(materials: dict, refs=None) -> dict:
    return {"schema_version": "page-image-prompt-v1", "page_number": 3, "selected_reference_images": refs or [], "image_prompt": _prompt(materials, refs or [])}


def test_validator_accepts_exact_four_key_output_and_zero_references():
    m = _materials()
    assert _module().validate_page_image_prompt(m, _result(m)) is None


def test_validator_accepts_page_owned_rendered_attachment_reference():
    m = _materials()
    assert _module().validate_page_image_prompt(m, _result(m, ["attachment:att01:page:1"])) is None


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(extra="no"),
    lambda r: r.pop("schema_version"),
    lambda r: r.update(page_number=4),
    lambda r: r.update(selected_reference_images=["unknown"]),
    lambda r: r.update(selected_reference_images=["word:word_image_01"] * 17),
])
def test_validator_rejects_wrong_shape_or_reference_ownership(mutation):
    m = _materials(); r = _result(m); mutation(r)
    with pytest.raises((ValueError, TypeError)):
        _module().validate_page_image_prompt(m, r)


@pytest.mark.parametrize("old,new", [
    ("北京举行", "上海举行"),
    ("保持会议事实原样", ""),
    ("添加新闻稿图片，并且有王巍和李耀武讲话图片", "保持会议事实原样"),
    ("## Visual Presentation", "## Layout"),
    ("Do not generate the fixed page title.", ""),
    ("Do not generate the fixed logo.", ""),
    ("Do not generate the footer.", ""),
    ("Do not generate the page number.", ""),
    ("Generate only the 17:8 body region at exactly 1904 x 896 and respect the safe area.", ""),
])
def test_validator_rejects_changed_sources_sections_or_boundaries(old, new):
    m = _materials(); r = _result(m); r["image_prompt"] = r["image_prompt"].replace(old, new)
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(m, r)


def test_validator_rejects_forbidden_semantic_pipeline_language():
    m = _materials()
    for forbidden in ("summary", "classification", "external search", "degradation", "stock photo"):
        r = _result(m); r["image_prompt"] += "\n" + forbidden
        with pytest.raises(ValueError):
            _module().validate_page_image_prompt(m, r)


def test_validator_requires_selected_reference_instruction_without_changing_source():
    m = _materials(); r = _result(m, ["attachment:att01:page:1"])
    r["image_prompt"] = r["image_prompt"].replace('"reference_id":"attachment:att01:page:1"', '"reference_id":"word:word_image_01"')
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(m, r)


def test_validator_rejects_missing_or_changed_visual_contract():
    m = _materials(); r = _result(m)
    r["image_prompt"] = r["image_prompt"].replace("VISUAL_CONTRACT_JSON_BEGIN", "STYLE_BEGIN")
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(m, r)


def test_source_may_contain_heading_forbidden_words_and_local_looking_text():
    m = _materials()
    m["complete_word_content"][0]["text"] = "Executive summary\n## Task\n本地路径 C:\\资料\\图.png 与 https://example.invalid/source"
    m["original_comments"][0]["text"] = "classification 和 degradation 是原文，不得删除"
    assert _module().validate_page_image_prompt(m, _result(m)) is None


@pytest.mark.parametrize("injection", [
    "\n## Extra\nanything",
    "\n## Task\nduplicate",
    "\n## Original Materials\nduplicate",
])
def test_only_four_line_anchored_h2_sections_are_allowed(injection):
    m = _materials(); r = _result(m); r["image_prompt"] += injection
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(m, r)


@pytest.mark.parametrize("contradiction", [
    "Generate the fixed page title.", "Generate the fixed logo.", "Generate the footer.", "Generate the page number."
])
def test_fixed_boundary_contradictions_are_rejected(contradiction):
    m = _materials(); r = _result(m); r["image_prompt"] += "\n" + contradiction
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(m, r)


def test_fixed_section_is_exact_required_lines_only():
    m = _materials(); r = _result(m); r["image_prompt"] += "\nDecorative note"
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(m, r)


@pytest.mark.parametrize("raw", [
    '{"schema_version":"page-image-prompt-v1","schema_version":"evil"}',
    '{"outer":{"x":1,"x":2}}',
])
def test_json_loader_rejects_duplicate_keys_at_any_depth(raw):
    with pytest.raises(ValueError):
        _module().load_json_no_duplicates(raw)


def test_cli_uses_duplicate_rejecting_loader_for_both_inputs():
    source = VALIDATOR.read_text(encoding="utf-8")
    assert 'materials = load_json_no_duplicates(' in source
    assert 'result = load_json_no_duplicates(' in source


def test_reference_declarations_must_follow_selected_order():
    m = _materials()
    refs = ["attachment:att01:page:1", "attachment:att01:page:2"]
    r = _result(m, refs)
    selected = json.dumps(refs, ensure_ascii=False, separators=(",", ":"))
    reversed_ids = json.dumps(list(reversed(refs)), ensure_ascii=False, separators=(",", ":"))
    r["image_prompt"] = r["image_prompt"].replace(selected, reversed_ids, 1)
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(m, r)


def test_materials_must_pass_authoritative_page_material_schema():
    m = _materials(); m["word_images"][0]["sha256"] = "forged"
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(m, _result(m))


@pytest.mark.parametrize("field,value", [
    ("primary_color", "blue"), ("title_size_pt", 0), ("body_size_pt", "18"),
    ("regional_characteristics", None), ("visual_description", 7),
])
def test_visual_contract_has_typed_and_ranged_fields(field, value):
    m = _materials(); m["visual_contract"][field] = value
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(m, _result(m))


def test_visual_contract_accepts_new_eight_field_confirmation():
    m = _materials()
    m["visual_contract"].pop("regional_characteristics")
    m["visual_contract"].pop("visual_description")

    _module().validate_page_image_prompt(m, _result(m))


def test_visual_contract_cannot_add_jointly_forged_field():
    m = _materials(); m["visual_contract"]["template"] = "forged"
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(m, _result(m))


@pytest.mark.parametrize("mutation", [
    lambda f: f["body_pixels"].update(width=1905),
    lambda f: f.update(geometry_version="fake"),
    lambda f: f.update(fixed_layers=["title", "logo"]),
    lambda f: f["body_bounds_cm"].update(x=-1),
])
def test_body_frame_must_match_known_safe_contract(mutation):
    m = _materials(); mutation(m["body_frame"])
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(m, _result(m))


@pytest.mark.parametrize("extra", [
    "FREE TEXT", "REFERENCE[attachment:att01:page:1] stray", "NOTE: hello",
])
def test_original_materials_allows_only_blocks_then_reference_declarations(extra):
    m = _materials(); r = _result(m)
    r["image_prompt"] = r["image_prompt"].replace("ORIGINAL_COMMENTS_JSON_END", "ORIGINAL_COMMENTS_JSON_END\n" + extra)
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(m, r)


def test_reference_token_outside_declaration_area_is_rejected():
    m = _materials(); r = _result(m)
    r["image_prompt"] = r["image_prompt"].replace("## Task\n", "## Task\nREFERENCE[word:word_image_01]\n")
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(m, r)


@pytest.mark.parametrize("location", ["before_word", "between_blocks", "after_comments"])
def test_original_materials_parser_is_anchored_over_the_whole_section(location):
    m = _materials(); r = _result(m)
    prompt = r["image_prompt"]
    if location == "before_word":
        prompt = prompt.replace("## Original Materials\n", "## Original Materials\nSTRAY\n")
    elif location == "between_blocks":
        prompt = prompt.replace("WORD_CONTENT_JSON_END\n", "WORD_CONTENT_JSON_END\nSTRAY\n")
    else:
        prompt = prompt.replace("ORIGINAL_COMMENTS_JSON_END\n", "ORIGINAL_COMMENTS_JSON_END\nSTRAY\n")
    r["image_prompt"] = prompt
    with pytest.raises(ValueError):
        _module().validate_page_image_prompt(m, r)

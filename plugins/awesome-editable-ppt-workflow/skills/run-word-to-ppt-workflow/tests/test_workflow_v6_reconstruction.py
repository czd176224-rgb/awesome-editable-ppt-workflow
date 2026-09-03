from __future__ import annotations

import json
import hashlib
import hmac
import sys
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_THEME_COLOR
from pptx.util import Cm


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v6_contract import new_page, new_project  # noqa: E402
from workflow_v6_reconstruction import (  # noqa: E402
    assemble_v6_deck,
    build_reconstruction_request,
    finalize_reconstructed_page,
)
from workflow_v6_state import create, load, save  # noqa: E402
from awesome_page_materials import publish_page_materials  # noqa: E402
from director_taskbook import project_emphasis_pages, taskbook_digest  # noqa: E402
from complex_page_experiment.loop import signing_key  # noqa: E402


def _page_plan(page_number: int) -> dict[str, object]:
    return {
        "page_purpose": f"Explain page {page_number}.",
        "primary_relationship": {
            "grammar": "flow",
            "description": "Source feeds destination.",
            "fact_ids": [f"body-{page_number}"],
            "visual_instruction": "Keep the accepted flow and its connectors.",
            "nodes": [
                {"node_id": "source", "label": "Source", "fact_ids": [f"body-{page_number}"]},
                {"node_id": "destination", "label": "Destination", "fact_ids": [f"body-{page_number}"]},
            ],
            "edges": [{
                "from_node": "source",
                "to_node": "destination",
                "label": "feeds",
                "fact_ids": [f"body-{page_number}"],
            }],
        },
        "core_exhibit": {
            "grammar": "flow",
            "description": "Accepted flow exhibit.",
            "fact_ids": [f"body-{page_number}"],
        },
        "support_groups": [],
        "reading_path": "Read source, then destination.",
        "local_visuals": [],
    }


def _write_signed_receipt(project: Path, page_number: int, value: dict | None = None) -> dict:
    state = load(project)
    image = project / "04_v6" / "images" / f"page_{page_number:03d}.png"
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    receipt = value or {
        "schema_version": "awesome-complex-page-acceptance-v1",
        "status": "accepted",
        "experiment_id": f"live-page-{page_number:03d}",
        "page_number": page_number,
        "source_snapshot_sha256": state["source_identity"],
        "source_identity": state["source_identity"],
        "ui_revision": 1,
        "ui_digest": "a" * 64,
        "material_view_sha256": "b" * 64,
        "page_plan": _page_plan(page_number),
        "candidate": {
            "attempt": 1,
            "path": image.relative_to(project).as_posix(),
            "sha256": digest,
            "byte_size": image.stat().st_size,
            "width": 1904,
            "height": 896,
            "trace_path": "04_v6/trace.json",
            "prompt_path": "04_v6/prompt.md",
            "operation": "generate",
            "quality": "high",
            "selected_reference_ids": [],
            "input_sha256s": [],
            "prompt_sha256": "c" * 64,
            "request_identity": "d" * 64,
            "duration_seconds": 1,
            "duration_unavailable_reason": None,
        },
        "candidate_history_sha256": "e" * 64,
        "prompt_history_sha256": "f" * 64,
        "selected_real_reference_ids": [],
        "accepted_review": {
            "decision": "accept",
            "problems": [],
            "model": "test-model",
            "effort": None,
            "duration_seconds": 1,
            "authority_path": "04_v6/review.json",
            "authority_sha256": "1" * 64,
        },
        "provider_authority": {
            "trace_path": "04_v6/trace.json",
            "trace_sha256": "2" * 64,
            "capability_path": "04_v6/capability.json",
            "capability_sha256": "3" * 64,
            "capability_nonce": "test-nonce",
            "journal_path": "04_v6/journal.jsonl",
            "journal_sha256": "4" * 64,
            "request_identity": "d" * 64,
        },
        "fixed_frame": {},
        "evidence_checkpoint": {
            "schema_version": "awesome-complex-page-candidate-acceptance-checkpoint-v1",
            "experiment_id": f"live-page-{page_number:03d}",
            "workspace_identity_sha256": "5" * 64,
            "source_snapshot_sha256": state["source_identity"],
            "page_number": page_number,
            "selected_attempt": 1,
            "candidate_sha256": digest,
            "request_identity": "d" * 64,
            "review_authority_sha256": "1" * 64,
            "event_count": 3,
            "terminal_event_index": 2,
            "evidence_prefix_sha256": "6" * 64,
            "causal_events": [
                {"event_index": index, "value": {}} for index in range(3)
            ],
            "checkpoint_sha256": "7" * 64,
        },
    }
    unsigned = {key: item for key, item in receipt.items() if key not in {"key_id", "hmac_sha256"}}
    key_id, key = signing_key()
    unsigned["key_id"] = key_id
    payload = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    signed = {**unsigned, "hmac_sha256": hmac.new(key, payload, hashlib.sha256).hexdigest()}
    path = project / "04_v6" / "images" / f"page_{page_number:03d}.json"
    path.write_text(
        json.dumps(signed, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return signed


def _style():
    return {
        "primary_color": "#17365D",
        "secondary_color": "#C7352B",
        "background_color": "#E7F1FA",
        "fixed_frame": {
            "geometry_version": "fixed-canvas-cm-v2",
            "body_bounds_cm": {"x": 0.81, "y": 2.3, "w": 23.78, "h": 11.18},
            "body_bounds": {"x": 0.81 / 25.4, "y": 2.3 / 14.288, "w": 23.78 / 25.4, "h": 11.18 / 14.288},
            "title_bounds_cm": {"x": 0.9, "y": 0.5, "w": 20.066, "h": 1.4288},
            "title_bounds": {"x": 0.9 / 25.4, "y": 0.5 / 14.288, "w": 20.066 / 25.4, "h": 1.4288 / 14.288},
            "logo_bounds_cm": {"x": 21.844, "y": 0.57152, "w": 2.667, "h": 1.0716},
            "logo_bounds": {"x": 21.844 / 25.4, "y": 0.57152 / 14.288, "w": 2.667 / 25.4, "h": 1.0716 / 14.288},
            "footer_line": {"x": 0.9, "y": 13.64504, "w": 23.6, "h": 0.028576, "color": "#B8C0CC"},
            "page_number_bounds_cm": {"x": 23.368, "y": 13.687904, "w": 1.143, "h": 0.3572},
            "page_number_bounds": {"x": 23.368 / 25.4, "y": 13.687904 / 14.288, "w": 1.143 / 25.4, "h": 0.3572 / 14.288},
            "page_number_style": {"font": "Microsoft YaHei", "size_pt": 9, "color": "#6B7280"},
            "title_color": "#0B1727",
        },
        "hard_constraints": {
            "title_color": "#0B1727",
            "typography": {
                "heading": {"cjk": "Microsoft YaHei"},
                "type_scale_pt": {"page_title": 28},
            },
        },
    }


def _body(path: Path, text: str, *, color: str | None = None, bold: bool = False):
    deck = Presentation()
    deck.slide_width = Cm(25.4)
    deck.slide_height = Cm(14.288)
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    box = slide.shapes.add_textbox(Cm(2), Cm(3), Cm(10), Cm(2))
    box.text = text
    run = box.text_frame.paragraphs[0].runs[0]
    run.font.bold = bold
    if color:
        run.font.color.rgb = RGBColor.from_string(color.lstrip("#"))
    deck.save(path)


def _project(tmp_path: Path, page_count: int = 2) -> Path:
    root = tmp_path / "project"
    (root / "00_source").mkdir(parents=True)
    (root / "00_source" / "logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 20"><rect width="100" height="20"/></svg>',
        encoding="utf-8",
    )
    pages = [new_page(number, title=f"标题{number}") for number in range(1, page_count + 1)]
    for page in pages:
        page["state"] = "accepted"
        image = root / "04_v6" / "images" / f"page_{page['page_number']:03d}.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(image)
        page["first_candidate"] = {"path": image.relative_to(root).as_posix(), "attempt": 1, "operation": "generate"}
        page["selected_candidate"] = dict(page["first_candidate"])
        effective = root / "02_v6" / "effective_pages" / f"page_{page['page_number']:03d}.json"
        effective.parent.mkdir(parents=True, exist_ok=True)
        effective.write_text(json.dumps({"page_number": page["page_number"], "word_original": "正文"}), encoding="utf-8")
    state = new_project(
        word_source={"path": "00_source/source.docx"},
        logo_source={"path": "00_source/logo.svg"},
        pages=pages,
    )
    material_style = {
        "primary_color": "#17365D", "secondary_color": "#C7352B",
        "background_color": "#FFFFFF", "cjk_font": "Microsoft YaHei",
        "latin_font": "Arial", "title_size_pt": 28, "body_size_pt": 12,
        "caption_size_pt": 9, "regional_characteristics": "",
        "visual_description": "Formal editorial presentation.",
    }
    state["style_confirmation"] = {"status": "confirmed", "contract": material_style}
    state["confirmed_ui_revision"] = 1
    state["confirmed_ui_digest"] = "a" * 64
    state["page_materials_status"] = "pending"
    create(root, state)
    for number in range(1, page_count + 1):
        _write_signed_receipt(root, number)
    source_dir = root / "02_v6"
    (source_dir / "paginated_word_source.json").write_text(json.dumps({"pages": [
        {"page_number": number, "fixed_page_title": f"标题{number}", "fixed_page_title_source_block_id": f"title-{number}", "page_comments": [], "blocks": [
            {"type": "paragraph", "text": f"标题{number}", "source_block_id": f"title-{number}", "source_block_index": (number - 1) * 2, "source_order": 1, "relationship_ids": [], "comment_ids": []},
            {"type": "paragraph", "text": "正文", "source_block_id": f"body-{number}", "source_block_index": (number - 1) * 2 + 1, "source_order": 2, "relationship_ids": [], "comment_ids": []},
        ]} for number in range(1, page_count + 1)
    ]}, ensure_ascii=False), encoding="utf-8")
    (source_dir / "page_composition.json").write_text(json.dumps({
        "artifact_version": "page-composition-v1", "page_count": page_count, "warnings": [],
        "pages": [
            {"output_page_number": number, "source_page_id": number, "page_role": "content", "role_source": "explicit", "chapter_title": "", "fixed_page_title": f"标题{number}", "source_page_number": number, "material_source_block_ids": [f"title-{number}", f"body-{number}"], "visible_page_number": True}
            for number in range(1, page_count + 1)
        ],
    }, ensure_ascii=False), encoding="utf-8")
    (source_dir / "source_assets.json").write_text('{"assets":[]}', encoding="utf-8")
    for number in range(1, page_count + 1):
        publish_page_materials(root, number, root / "02_v6" / "awesome_page_materials" / f"page_{number:03d}.json")
    state = load(root)
    state["style_confirmation"]["contract"] = _style()
    save(root, state)
    return root


def test_v6_reconstruction_request_has_no_exact_material_or_post_visual_qa(tmp_path: Path):
    project = _project(tmp_path, 1)
    receipt = json.loads((project / "04_v6/images/page_001.json").read_text(encoding="utf-8"))
    request = build_reconstruction_request(project, page_number=1)
    assert request["workflow_contract_version"] == "awesome-word-ppt-workflow-v1"
    assert request["requirements"]["exact_reference_material_custody"] is False
    assert request["requirements"]["post_reconstruction_visual_qa"] is False
    assert request["sealed_image_edits"] == []
    assert request["sealed_text_repairs"] == []
    assert request["page_plan"] == receipt["page_plan"]


def test_reconstruction_request_hash_changes_with_resigned_page_plan(tmp_path: Path):
    project = _project(tmp_path, 1)
    build_reconstruction_request(project, page_number=1)
    request_path = project / "05_v6/reconstruction_requests/page_001.json"
    first_hash = hashlib.sha256(request_path.read_bytes()).hexdigest()
    receipt_path = project / "04_v6/images/page_001.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["page_plan"]["primary_relationship"]["edges"][0]["to_node"] = "source"
    _write_signed_receipt(project, 1, receipt)

    second = build_reconstruction_request(project, page_number=1)
    second_hash = hashlib.sha256(request_path.read_bytes()).hexdigest()

    assert second["page_plan"] == receipt["page_plan"]
    assert second_hash != first_hash


def test_reconstruction_request_rejects_tampered_page_plan_before_copy(tmp_path: Path):
    project = _project(tmp_path, 1)
    receipt_path = project / "04_v6/images/page_001.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["page_plan"]["primary_relationship"]["nodes"][0]["node_id"] = "tampered"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="signature"):
        build_reconstruction_request(project, page_number=1)

    assert not (project / "05_v6/reconstruction_requests/page_001.json").exists()


def test_reconstruction_request_preserves_numeric_authority(tmp_path: Path):
    project = _project(tmp_path, 1)
    authority = {
        "title": "项目计划",
        "rendering_primitive": "time_interval",
        "period": "2026-09",
        "series": [{
            "name": "计划",
            "categories": ["尽调", "投决"],
            "start_dates": ["2026-09-01", "2026-09-11"],
            "end_dates": ["2026-09-10", "2026-09-15"],
        }],
    }
    path = project / "02_v6/page_materials/page_001.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"chart_facts": [authority]}, ensure_ascii=False), encoding="utf-8")

    request = build_reconstruction_request(project, page_number=1)

    assert request["numeric_authority"] == authority


def test_reconstruction_request_refuses_incomplete_or_ambiguous_authority(tmp_path: Path):
    project = _project(tmp_path, 1)
    complete = {
        "title": "Revenue",
        "rendering_primitive": "column_bar",
        "chart_variant": "column",
        "unit": "USD m",
        "basis": "FY2025 revenue",
        "series": [{"name": "Revenue", "categories": ["A"], "values": [10]}],
    }
    incomplete = {
        "title": "Priorities",
        "disabled_primitive": "column_bar",
        "fallback": "native_table",
        "source_wording": "A is high priority and B is medium priority.",
        "series": [{"name": "Priority", "categories": ["A", "B"], "values": ["high", "medium"]}],
    }
    path = project / "02_v6/page_materials/page_001.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(json.dumps({"chart_facts": [incomplete]}, ensure_ascii=False), encoding="utf-8")
    assert "numeric_authority" not in build_reconstruction_request(project, page_number=1)
    assert json.loads(path.read_text(encoding="utf-8"))["chart_facts"][0]["source_wording"]

    path.write_text(json.dumps({"chart_facts": [complete, complete]}, ensure_ascii=False), encoding="utf-8")
    assert "numeric_authority" not in build_reconstruction_request(project, page_number=1)


def test_reconstruction_request_refuses_legacy_times_numeric_authority(tmp_path: Path):
    project = _project(tmp_path, 1)
    legacy = {
        "title": "Revenue trend", "rendering_primitive": "line_point", "chart_variant": "line",
        "unit": "USD m", "basis": "FY2025 revenue",
        "series": [{"name": "Revenue", "times": ["2024", "2025"], "values": [12, 18]}],
    }
    path = project / "02_v6/page_materials/page_001.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"chart_facts": [legacy]}, ensure_ascii=False), encoding="utf-8")

    request = build_reconstruction_request(project, page_number=1)

    assert "numeric_authority" not in request


def test_reconstruction_request_carries_signed_text_repairs(tmp_path: Path):
    project = _project(tmp_path, 1)
    receipt_path = project / "04_v6/images/page_001.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["reconstruction_repairs"] = [
        {
            "category": "severe_usability",
            "detail": "将错字“清出”修正为“退出”，其余构图保持不变。",
        }
    ]
    _write_signed_receipt(project, 1, receipt)

    request = build_reconstruction_request(project, page_number=1)

    assert request["sealed_text_repairs"] == receipt["reconstruction_repairs"]


def test_finalize_requires_exact_native_text_repair(tmp_path: Path):
    project = _project(tmp_path, 1)
    receipt_path = project / "04_v6/images/page_001.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["reconstruction_repairs"] = [
        {
            "category": "severe_usability",
            "detail": "将错字“清出”修正为“退出”，其余构图保持不变。",
            "find": "清出",
            "replace": "退出",
        }
    ]
    _write_signed_receipt(project, 1, receipt)
    wrong = tmp_path / "wrong.pptx"
    _body(wrong, "清出表现挂钩")

    with pytest.raises(ValueError, match="did not apply a sealed repair"):
        finalize_reconstructed_page(project, page_number=1, reconstructed_body=wrong)

    correct = tmp_path / "correct.pptx"
    _body(correct, "退出表现挂钩")
    report = finalize_reconstructed_page(
        project, page_number=1, reconstructed_body=correct,
    )
    assert report["fixed_frame"]["passed"] is True


def test_finalize_and_assemble_add_fixed_layers_without_office_or_visual_qa(tmp_path: Path):
    project = _project(tmp_path, 2)
    for page in (1, 2):
        body = tmp_path / f"body-{page}.pptx"
        _body(body, f"可编辑正文{page}")
        report = finalize_reconstructed_page(project, page_number=page, reconstructed_body=body)
        assert report["post_reconstruction_visual_qa"] is False
        assert report["fixed_frame"]["passed"] is True

    report = assemble_v6_deck(project)
    output = project / report["output"]
    deck = Presentation(output)
    assert len(deck.slides) == 2
    assert report["office_render_required"] is False
    assert report["post_reconstruction_visual_qa"] is False
    assert all(page["state"] == "page_complete" for page in load(project)["pages"])


def test_finalize_sets_whole_slide_background_and_recolors_non_emphasis_text_only(tmp_path: Path):
    project = _project(tmp_path, 1)
    body = tmp_path / "accent-body.pptx"
    _body(body, "非重点页结论", color="#C7352B", bold=True)

    finalize_reconstructed_page(project, page_number=1, reconstructed_body=body)

    deck = Presentation(project / "06_v6/pages/page_001/page.pptx")
    slide = deck.slides[0]
    body_shape = next(shape for shape in slide.shapes if shape.name == "TextBox 1")
    run = body_shape.text_frame.paragraphs[0].runs[0]
    assert str(run.font.color.rgb) == "17365D"
    assert run.font.bold is True
    assert str(slide.background.fill.fore_color.rgb) == "E7F1FA"


def test_finalize_preserves_accent_text_on_automatically_matched_emphasis_page(tmp_path: Path):
    project = _project(tmp_path, 1)
    state = load(project)
    taskbook = {
        "use_scenario": "汇报",
        "presenter": "项目团队",
        "primary_audience": "管理层",
        "audience_prior_knowledge": "已阅读材料",
        "desired_outcome": "形成判断",
        "emphasis": "正文",
        "deemphasis": "重复背景",
    }
    state["director_confirmation"] = {
        "template_id": "investment-committee",
        "template_version": "1.0",
        "taskbook": taskbook,
        "taskbook_digest": taskbook_digest(taskbook),
    }
    save(project, state)
    body = tmp_path / "emphasis-body.pptx"
    _body(body, "重点页结论", color="#C7352B")

    finalize_reconstructed_page(project, page_number=1, reconstructed_body=body)

    deck = Presentation(project / "06_v6/pages/page_001/page.pptx")
    body_shape = next(shape for shape in deck.slides[0].shapes if shape.name == "TextBox 1")
    assert str(body_shape.text_frame.paragraphs[0].runs[0].font.color.rgb) == "C7352B"


def test_emphasis_pages_are_recomputed_after_paginated_word_changes(tmp_path: Path):
    project = _project(tmp_path, 2)
    state = load(project)
    taskbook = {
        "use_scenario": "汇报", "presenter": "项目团队", "primary_audience": "管理层",
        "audience_prior_knowledge": "已阅读材料", "desired_outcome": "形成判断",
        "emphasis": "新增成果", "deemphasis": "重复背景",
    }
    state["director_confirmation"] = {
        "template_id": "investment-committee", "template_version": "1.0",
        "taskbook": taskbook, "taskbook_digest": taskbook_digest(taskbook),
    }
    save(project, state)
    assert project_emphasis_pages(project) == set()
    source_path = project / "02_v6/paginated_word_source.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["pages"][1]["blocks"][1]["text"] = "新增成果"
    source_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    assert project_emphasis_pages(project) == {2}


def test_non_emphasis_theme_color_is_replaced_only_when_it_resolves_to_secondary_family(
    tmp_path: Path,
):
    project = _project(tmp_path, 2)
    for page, theme in ((1, MSO_THEME_COLOR.ACCENT_1), (2, MSO_THEME_COLOR.ACCENT_2)):
        body = tmp_path / f"theme-{page}.pptx"
        _body(body, f"主题文字{page}")
        deck = Presentation(body)
        run = deck.slides[0].shapes[0].text_frame.paragraphs[0].runs[0]
        run.font.color.theme_color = theme
        deck.save(body)
        finalize_reconstructed_page(project, page_number=page, reconstructed_body=body)

    primary_theme = Presentation(project / "06_v6/pages/page_001/page.pptx")
    secondary_theme = Presentation(project / "06_v6/pages/page_002/page.pptx")
    primary_color = primary_theme.slides[0].shapes[0].text_frame.paragraphs[0].runs[0].font.color
    secondary_color = secondary_theme.slides[0].shapes[0].text_frame.paragraphs[0].runs[0].font.color
    assert primary_color.theme_color == MSO_THEME_COLOR.ACCENT_1
    assert str(secondary_color.rgb) == "17365D"


def test_non_emphasis_replacement_uses_theme_fill_for_contrast(tmp_path: Path):
    project = _project(tmp_path, 1)
    body = tmp_path / "theme-fill.pptx"
    _body(body, "深色底文字", color="#C7352B")
    deck = Presentation(body)
    shape = deck.slides[0].shapes[0]
    shape.fill.solid()
    shape.fill.fore_color.theme_color = MSO_THEME_COLOR.ACCENT_1
    deck.save(body)

    finalize_reconstructed_page(project, page_number=1, reconstructed_body=body)

    final = Presentation(project / "06_v6/pages/page_001/page.pptx")
    run = final.slides[0].shapes[0].text_frame.paragraphs[0].runs[0]
    assert str(run.font.color.rgb) == "000000"


def test_assembly_reapplies_background_and_non_emphasis_text_guard(tmp_path: Path):
    project = _project(tmp_path, 1)
    body = tmp_path / "body.pptx"
    _body(body, "最终校验")
    finalize_reconstructed_page(project, page_number=1, reconstructed_body=body)
    page_path = project / "06_v6/pages/page_001/page.pptx"
    page_deck = Presentation(page_path)
    slide = page_deck.slides[0]
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = RGBColor.from_string("FFFFFF")
    slide.shapes[0].text_frame.paragraphs[0].runs[0].font.color.rgb = RGBColor.from_string("C7352B")
    page_deck.save(page_path)

    report = assemble_v6_deck(project)
    final = Presentation(project / report["output"])
    slide = final.slides[0]
    assert str(slide.background.fill.fore_color.rgb) == "E7F1FA"
    assert str(slide.shapes[0].text_frame.paragraphs[0].runs[0].font.color.rgb) == "17365D"


def test_current_confirmed_project_missing_composition_fails_closed_at_assembly(tmp_path: Path):
    project = _project(tmp_path, 1)
    body = tmp_path / "body.pptx"
    _body(body, "current body")
    finalize_reconstructed_page(project, page_number=1, reconstructed_body=body)
    (project / "02_v6/page_composition.json").unlink()

    with pytest.raises(ValueError, match="composition"):
        assemble_v6_deck(project)


def test_exact_legacy_content_only_confirmation_can_assemble_without_composition(tmp_path: Path):
    project = _project(tmp_path, 1)
    body = tmp_path / "legacy-body.pptx"
    _body(body, "legacy body")
    finalize_reconstructed_page(project, page_number=1, reconstructed_body=body)
    (project / "02_v6/page_composition.json").unlink()
    (project / "confirm_ui").mkdir(exist_ok=True)
    (project / "confirm_ui/result.json").write_text(json.dumps({
        "status": "confirmed", "revision": 1,
        "confirmed_at": "2026-08-23T00:00:00+08:00",
        "production_profile": "balanced", "global_visual_contract": {},
        "confirmed_pages": [{"page_number": 1, "effective_body": "legacy body"}],
    }), encoding="utf-8")

    report = assemble_v6_deck(project)

    assert report["page_count"] == 1


def test_assembly_accepts_role_specific_special_shapes_with_content_fixed_frame(tmp_path: Path):
    # Break caught: assembly requires the four content fixed-frame objects on a native cover.
    from workflow_v6_special_pages import render_special_page

    project = _project(tmp_path, 2)
    composition = {
        "artifact_version": "page-composition-v1", "page_count": 2, "warnings": [],
        "pages": [
            {"output_page_number": 1, "source_page_id": 1, "page_role": "cover", "role_source": "explicit", "chapter_title": "", "fixed_page_title": "标题1", "source_page_number": 1, "material_source_block_ids": ["title-1", "body-1"], "visible_page_number": False},
            {"output_page_number": 2, "source_page_id": 2, "page_role": "content", "role_source": "explicit", "chapter_title": "", "fixed_page_title": "标题2", "source_page_number": 2, "material_source_block_ids": ["title-2", "body-2"], "visible_page_number": True},
        ],
    }
    (project / "02_v6" / "page_composition.json").write_text(
        json.dumps(composition, ensure_ascii=False), encoding="utf-8",
    )
    render_special_page(project, 1)
    body = tmp_path / "body-2.pptx"
    _body(body, "可编辑正文2")
    finalize_reconstructed_page(project, page_number=2, reconstructed_body=body)

    report = assemble_v6_deck(project)
    deck = Presentation(project / report["output"])

    assert len(deck.slides) == 2
    assert any(shape.name.startswith("special-") for shape in deck.slides[0].shapes)
    assert len([shape for shape in deck.slides[1].shapes if shape.name.startswith("fixed-frame-")]) == 4


def test_finalize_allows_verified_same_accepted_page_repair(tmp_path: Path):
    project = _project(tmp_path, 1)
    first = tmp_path / "body-first.pptx"
    repaired = tmp_path / "body-repaired.pptx"
    _body(first, "first reconstruction")
    _body(repaired, "repaired reconstruction")

    initial = finalize_reconstructed_page(project, page_number=1, reconstructed_body=first)
    result = finalize_reconstructed_page(project, page_number=1, reconstructed_body=repaired)

    assert initial["sha256"] != result["sha256"]
    assert result["fixed_frame"]["passed"] is True
    assert load(project)["pages"][0]["state"] == "page_complete"
    output = project / result["page_pptx"]
    assert "repaired reconstruction" in "".join(
        shape.text for shape in Presentation(output).slides[0].shapes if hasattr(shape, "text")
    )

    receipt = output.with_name("page.json")
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["sha256"] = "0" * 64
    receipt.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="existing finalized page authority"):
        finalize_reconstructed_page(project, page_number=1, reconstructed_body=first)

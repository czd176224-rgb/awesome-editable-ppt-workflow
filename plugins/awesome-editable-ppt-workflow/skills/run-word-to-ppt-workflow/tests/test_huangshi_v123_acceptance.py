from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation


TESTS = Path(__file__).resolve().parent
PLUGIN = TESTS.parents[2]
REPO = TESTS.parents[4]
SCRIPTS = PLUGIN / "skills/run-word-to-ppt-workflow/scripts"
RUNTIME = PLUGIN / "skills/reconstruct-editable-slide/cli/editppt/runtime"
sys.path[:0] = [str(SCRIPTS), str(RUNTIME)]

from awesome_page_materials import publish_page_materials  # noqa: E402
from build_pptx_from_manifest import render_preview, write_pptx  # noqa: E402
from codex_subscription_runtime import CodexStructuredResult  # noqa: E402
from complex_page_experiment.director import direct_page  # noqa: E402
from complex_page_experiment.materials import build_complete_page_material_view  # noqa: E402
from complex_page_experiment.workspace import open_live_page_workspace  # noqa: E402
from director_taskbook import taskbook_digest  # noqa: E402
from extract_docx_pages import DEFAULT_MARKER, extract  # noqa: E402
from fixed_region_runtime import CONTENT_BOX, SLIDE  # noqa: E402
from workflow_v6_contract import new_page, new_project  # noqa: E402
from workflow_v6_reconstruction import (  # noqa: E402
    assemble_v6_deck,
    build_reconstruction_request,
    finalize_reconstructed_page,
)
from workflow_v6_state import create, load  # noqa: E402


DESKTOP = Path.home() / "Desktop"
WORD = DESKTOP / "黄石市产业创新与母基金专业化管理合作建议_PPT生成专用Word副本_V3.docx"
LOGO = DESKTOP / "尚融logo.png"
WORD_SHA256 = "519FC2C5DAA0B4A2E65954E6FA20DF461E04587749C69AFB5952C6535A4A4A11"
LOGO_SHA256 = "9681840BACFBA51E87E47D687C1CA1F9C542F9C235577280447E96070726BCF0"
SELECTED = (5, 10, 14, 20, 21, 40)
OUTPUT = REPO / "tmp/v1.2.3-acceptance/huangshi"
PREVIEW_FONT = str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc")

PAGE_CONTRACTS = {
    5: ("option_comparison", "comparison_table", ("科技成果40余项", "举办活动35场", "项目和人才团队30余个", "推动7个项目注册落地")),
    10: ("target_actual_variance", "goal_current_gap", ("420亿元", "100亿元", "450家", "100个以上", "50家", "3—5家")),
    14: ("project_stage_time", "roadmap_milestones", ("项目发现", "技术评价", "商业验证", "中试放大", "落地承接", "成长赋能", "并购退出")),
    20: ("market_size_share", "equal_width_hierarchy", ("1+4+N", "产业发展基金", "天使类", "科创类", "产业类", "专项类", "N只细分子基金")),
    21: ("option_comparison", "comparison_table", ("总规模百亿元", "总投资68.2亿元", "没有完整披露")),
    40: ("project_stage_time", "roadmap_milestones", ("0—30天", "31—60天", "61—90天", "12个月")),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _page_text(page: dict) -> str:
    return "\n".join(
        "\n".join(cell for row in block["rows"] for cell in row)
        if block["type"] == "table" else block.get("text", "")
        for block in page["blocks"]
    )


def _style() -> dict:
    return {
        "primary_color": "#17365D", "secondary_color": "#C7352B",
        "background_color": "#FFFFFF", "cjk_font": "Microsoft YaHei",
        "latin_font": "Arial", "title_size_pt": 28, "body_size_pt": 12,
        "caption_size_pt": 9, "regional_characteristics": "黄石产业投资",
        "visual_description": "Formal investment committee presentation.",
    }


def _director_value(page_number: int, material_ids: tuple[str, ...], source_text: str) -> dict:
    return {
        "schema_version": "awesome-consulting-page-director-v2", "page_number": page_number, "quality": "high",
        "machine_record": {
            "facts_and_sources": [source_text[:300]],
            "must_preserve_entities": ["Preserve every source-exact number and named entity."],
            "core_content_and_comment_direction": ["Use only the selected Word page."],
            "material_use": [{"material_id": item, "status": "background_understanding", "reason": "Source authority."} for item in material_ids],
            "selected_references": [], "fixed_layer_exclusions": ["title", "logo", "footer", "page_number"],
        },
        "creative_direction": {
            "business_proposition": "Present the selected source facts without inventing quantitative geometry.",
            "explanatory_lead": "Lead with the page's source-supported conclusion.",
            "analytical_backbone": "Use the named qualitative substitute required by incomplete evidence.",
            "evidence_interpretation_conclusion": "Move from exact Word evidence to its stated implication.",
            "content_hierarchy": "Conclusion, source facts, then limitation or implication.",
            "reading_path_and_density": "Use equal-weight editable objects and a restrained reading path.",
            "takeaway_statement": "Keep every quantitative claim tied to its disclosed basis.",
            "supporting_visual_policy": "Use structure, not decorative or proportional encoding.",
            "anti_ai_visual_policy": "Avoid invented scenes, fake charts, and ornamental 3D objects.",
        },
        "prompt_sections": {
            "task_and_canvas": "Arrange a calm source-led information canvas.",
            "core_proposition_and_content": "Preserve the exact Word facts and their relationship.",
            "consulting_information_architecture": "Use equal-weight native editable objects.",
            "visual_style_and_color": "Use restrained editorial styling.",
            "text_and_typography": "Keep Chinese labels and numbers crisp.",
            "strict_prohibitions": "No invented values, axes, areas, durations, comparisons, or arithmetic.",
        },
    }


def _director_result(value: dict) -> CodexStructuredResult:
    return CodexStructuredResult(
        value=value, thread_id="huangshi-test", turn_id="deterministic-director",
        model="deterministic-test-stub", model_provider="local-test", auth_mode="test",
        plan_type="test", usage={"input_tokens": 0, "output_tokens": 0},
        safe_trace={"boundary": "external-model-stub"}, effort="high",
        duration_seconds=0.01, startup_reused=True,
    )


def _manifest(source_page: int, fallback: str, labels: tuple[str, ...]) -> dict:
    if source_page == 10:
        boxes = [(1.0, 1.3, 3.8, 1.0), (5.2, 1.3, 3.8, 1.0), *[(0.4 + index * 2.35, 3.0, 2.1, 1.0) for index in range(4)]]
    elif source_page == 20:
        boxes = [(1.0, 0.95, 1.65, 0.8), (4.15, 0.95, 1.65, 0.8), *[(0.55 + index * 2.25, 2.4, 1.65, 0.8) for index in range(4)], (4.15, 3.9, 1.65, 0.8)]
    elif source_page == 21:
        boxes = [(0.8, 1.2, 4.0, 1.0), (5.2, 1.2, 4.0, 1.0), (0.8, 3.2, 8.4, 1.0)]
    elif source_page == 40:
        boxes = [*( (0.8 + index * 2.9, 1.4, 2.55, 1.0) for index in range(3)), (3.25, 3.5, 3.5, 1.0)]
    else:
        width = 8.4 / len(labels)
        boxes = [(0.8 + index * width, 1.75, width - 0.12, 1.5) for index in range(len(labels))]
    return {
        "workflow_contract_version": "fixed-canvas-cm-v2", "reconstruction_contract_version": "editable-image-v3",
        "slide": dict(SLIDE), "content_box": dict(CONTENT_BOX), "source": {"width_px": 1904, "height_px": 896},
        "text_inventory": [], "visual_inventory": [], "background_strategy": "native white body background",
        "quality_checks": {"font_size_calibrated": True, "visual_inventory_matched": True, "background_strategy_checked": True, "shape_corner_geometry_checked": True},
        "text_boxes": [
            {"object_id": f"page-{source_page}-{fallback}-label-{index}", "name": f"page-{source_page}-{fallback}-label-{index}", "left": x + 0.08, "top": y + 0.25, "width": w - 0.16, "height": min(0.7, h - 0.3), "text": label, "font_size": 12, "preview_font": PREVIEW_FONT, "align": "center"}
            for index, (label, (x, y, w, h)) in enumerate(zip(labels, boxes, strict=True))
        ],
        "tables": [],
        "shapes": [
            {"object_id": f"page-{source_page}-{fallback}-node-{index}", "name": f"page-{source_page}-{fallback}-node-{index}", "type": "roundRect", "left": x, "top": y, "width": w, "height": h, "fill": "#EAF2F8", "stroke": "#6B7A90"}
            for index, (x, y, w, h) in enumerate(boxes)
        ],
        "images": [], "charts": [], "asset_provenance": [],
    }


def _build_project(root: Path, source: dict, logo_svg: Path) -> tuple[Path, list[dict]]:
    project = root / "project"
    (project / "00_source").mkdir(parents=True)
    shutil.copy2(WORD, project / "00_source/source.docx")
    shutil.copy2(logo_svg, project / "00_source/logo.svg")
    selected_pages = [next(page for page in source["pages"] if page["page_number"] == number) for number in SELECTED]
    pages = []
    for output_number, selected in enumerate(selected_pages, start=1):
        page = new_page(output_number, title=selected["blocks"][1]["text"])
        page["state"] = "accepted"
        image = project / "04_v6/images" / f"page_{output_number:03d}.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(image)  # external Image2 boundary stub
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        candidate = {"path": image.relative_to(project).as_posix(), "attempt": 1, "operation": "generate"}
        page["first_candidate"] = dict(candidate)
        page["selected_candidate"] = dict(candidate)
        (project / "04_v6/images" / f"page_{output_number:03d}.json").write_text(
            json.dumps({"page_number": output_number, "selected": {**candidate, "sha256": digest}}), encoding="utf-8",
        )
        pages.append(page)

    state = new_project(word_source={"path": "00_source/source.docx"}, logo_source={"path": "00_source/logo.svg"}, pages=pages)
    state["style_confirmation"] = {"status": "confirmed", "contract": _style()}
    state["confirmed_ui_revision"] = 1
    state["confirmed_ui_digest"] = "a" * 64
    state["page_materials_status"] = "pending"
    taskbook = {
        "use_scenario": "黄石市产业创新与母基金专业化管理建议汇报", "presenter": "尚融财富项目团队",
        "primary_audience": "政府与基金决策者", "audience_prior_knowledge": "熟悉黄石产业与基金基础",
        "desired_outcome": "评估合作建议与实施路径", "emphasis": "母基金、实施路径、前90天", "deemphasis": "背景性重复介绍",
    }
    state["director_confirmation"] = {
        "template_id": "investment-committee", "template_version": "1.0",
        "taskbook": taskbook, "taskbook_digest": taskbook_digest(taskbook),
    }
    create(project, state)

    paginated = {"pages": []}
    composition = {"artifact_version": "page-composition-v1", "page_count": 6, "warnings": [], "pages": []}
    for output_number, selected in enumerate(selected_pages, start=1):
        blocks = [dict(block) for block in selected["blocks"]]
        paginated["pages"].append({**selected, "page_number": output_number, "source_page_number": selected["page_number"], "fixed_page_title": blocks[1]["text"], "fixed_page_title_source_block_id": blocks[1]["source_block_id"]})
        composition["pages"].append({"output_page_number": output_number, "source_page_id": selected["page_number"], "page_role": "content", "role_source": "explicit", "chapter_title": "", "fixed_page_title": blocks[1]["text"], "source_page_number": output_number, "material_source_block_ids": [block["source_block_id"] for block in blocks], "visible_page_number": True})
        effective = project / "02_v6/effective_pages" / f"page_{output_number:03d}.json"
        effective.parent.mkdir(parents=True, exist_ok=True)
        effective.write_text(json.dumps({"page_number": output_number, "word_original": _page_text(selected)}, ensure_ascii=False), encoding="utf-8")
    (project / "02_v6/paginated_word_source.json").write_text(json.dumps(paginated, ensure_ascii=False), encoding="utf-8")
    (project / "02_v6/page_composition.json").write_text(json.dumps(composition, ensure_ascii=False), encoding="utf-8")
    (project / "02_v6/source_assets.json").write_text('{"assets":[]}', encoding="utf-8")
    for output_number in range(1, 7):
        publish_page_materials(project, output_number, project / "02_v6/awesome_page_materials" / f"page_{output_number:03d}.json")
    return project, selected_pages


def test_huangshi_controlled_acceptance_runs_real_production_path_without_ui(tmp_path: Path) -> None:
    if not WORD.is_file() or not LOGO.is_file():
        pytest.skip("user-supplied Huangshi acceptance files are not present")
    assert _sha256(WORD) == WORD_SHA256
    assert _sha256(LOGO) == LOGO_SHA256
    source = extract(WORD, DEFAULT_MARKER)
    assert source["page_count"] == 42

    OUTPUT.mkdir(parents=True, exist_ok=True)
    svg = OUTPUT / "shangrong-logo-test-wrapper.svg"
    encoded_logo = base64.b64encode(LOGO.read_bytes()).decode("ascii")
    svg.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="220" height="46"><image width="220" height="46" href="data:image/png;base64,{encoded_logo}"/></svg>', encoding="utf-8")
    project, selected_pages = _build_project(tmp_path, source, svg)
    director_prompts = []

    for output_number, selected in enumerate(selected_pages, start=1):
        source_number = SELECTED[output_number - 1]
        relationship, fallback, labels = PAGE_CONTRACTS[source_number]
        source_text = _page_text(selected)
        assert all(label in source_text for label in labels)
        workspace = open_live_page_workspace(project, output_number)
        material_view = build_complete_page_material_view(workspace)
        value = _director_value(output_number, material_view.material_ids, source_text)
        artifact = direct_page(workspace, material_view, timeout=30, invoke=lambda *_args, v=value, **_kwargs: _director_result(v))
        director_prompts.append(artifact.actual_prompt)

        qualitative = {"title": selected["blocks"][1]["text"], "relationship": relationship, "source_wording": source_text, "disabled_primitive": "quantitative_encoding", "fallback": fallback, "series": []}
        materials = project / "02_v6/page_materials" / f"page_{output_number:03d}.json"
        materials.parent.mkdir(parents=True, exist_ok=True)
        materials.write_text(json.dumps({"chart_facts": [qualitative]}, ensure_ascii=False), encoding="utf-8")
        assert "numeric_authority" not in build_reconstruction_request(project, page_number=output_number)

        manifest = _manifest(source_number, fallback, labels)
        manifest_path = OUTPUT / f"page-{source_number:02d}-manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        body = tmp_path / f"body-{output_number:03d}.pptx"
        write_pptx(manifest, body, manifest_path)
        finalize_reconstructed_page(project, page_number=output_number, reconstructed_body=body)
        preview = OUTPUT / "previews" / f"page-{source_number:02d}.png"
        preview.parent.mkdir(parents=True, exist_ok=True)
        render_preview(manifest, manifest_path, preview, pptx_path=body)
        assert Image.open(preview).size == (1200, 675)

    assembly = assemble_v6_deck(project)
    deck_path = OUTPUT / "huangshi-selected-pages-v1.2.3.pptx"
    shutil.copy2(project / assembly["output"], deck_path)
    deck = Presentation(deck_path)
    assert len(deck.slides) == 6
    assert all(not any(shape.has_chart for shape in slide.shapes) for slide in deck.slides)
    for slide, source_number in zip(deck.slides, SELECTED, strict=True):
        fallback = PAGE_CONTRACTS[source_number][1]
        names = {shape.name for shape in slide.shapes}
        assert "fixed-frame-logo" in names
        assert any(fallback in name for name in names)
        assert not any(term in name.casefold() for name in names for term in ("axis", "gantt", "mekko", "target line", "difference arrow"))
        slide_text = "\n".join(shape.text for shape in slide.shapes if getattr(shape, "has_text_frame", False))
        assert all(label in slide_text for label in PAGE_CONTRACTS[source_number][2])

    with zipfile.ZipFile(deck_path) as package:
        embedded_svg = [package.read(name) for name in package.namelist() if name.startswith("ppt/media/") and name.endswith(".svg")]
    assert svg.read_bytes() in embedded_svg
    assert encoded_logo.encode("ascii") in svg.read_bytes()
    assert all("exact eight-row dual-mode relationship mapping" in prompt and "numeric axes" in prompt for prompt in director_prompts)

    findings = {
        "unsupported_from_real_manuscript": ["line", "scatter", "bubble", "waterfall", "true_mekko"],
        "reason": "selected manuscript pages do not supply the complete comparable dimensions required for these quantitative encodings",
        "production_path": ["extract_docx_pages.extract", "build_complete_page_material_view", "direct_page", "build_reconstruction_request", "write_pptx", "finalize_reconstructed_page", "assemble_v6_deck", "render_preview"],
        "remaining_limitations": ["external Image2 and director model calls are deterministic boundary stubs in this controlled acceptance"],
    }
    (OUTPUT / "acceptance-findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    assert load(project)["pages"][-1]["state"] == "page_complete"

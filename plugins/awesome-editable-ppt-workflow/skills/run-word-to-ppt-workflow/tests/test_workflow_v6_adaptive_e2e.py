from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Cm

from test_confirm_ui_contract import attach_deck_plan


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SERVER = SCRIPTS / "confirm_ui" / "server.py"
FIXTURE = Path(__file__).parent / "fixtures" / "v6_adaptive_project" / "fixture.json"
CONSULTING_REGRESSION_FIXTURE = (
    Path(__file__).parent / "fixtures" / "consulting_director_cases.json"
)
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import workflow_v6_source  # noqa: E402
from workflow_v6_contract import canonical_sha256  # noqa: E402
from workflow_v6_reconstruction import (  # noqa: E402
    assemble_v6_deck,
    build_reconstruction_request,
    finalize_reconstructed_page as _finalize_reconstructed_page,
)
from workflow_v6_source import initialize_v6_project  # noqa: E402
from workflow_v6_state import load, save  # noqa: E402
from director_taskbook import confirmed_taskbook_prompt  # noqa: E402
from workflow_v6_contract import transition_page  # noqa: E402
from workflow_v6_pipeline import (  # noqa: E402
    PipelineConfiguration,
    PipelineDependencies,
    run_pages,
)
from workflow_v6_special_pages import render_special_page  # noqa: E402
from awesome_page_materials import publish_page_materials  # noqa: E402
from fixed_region_contract import fixed_frame_execution  # noqa: E402


def finalize_reconstructed_page(*args, **kwargs):
    project = Path(args[0] if args else kwargs["project"])
    page_number = kwargs["page_number"]
    state = load(project)
    state["word_source"]["authority_mode"] = "legacy_non_word"
    state["source_identity"] = canonical_sha256({
        "word_source": state["word_source"], "logo_source": state["logo_source"],
    })
    state["pages"][page_number - 1]["selected_candidate"] = None
    save(project, state)
    (project / "04_v6" / "images" / f"page_{page_number:03d}.json").unlink(missing_ok=True)
    return _finalize_reconstructed_page(*args, authority_mode="native_direct", **kwargs)


def test_44_logical_markers_ignore_physical_pagination_and_keep_source_ids(tmp_path: Path) -> None:
    # Break caught: Word section/page breaks are counted instead of explicit logical markers.
    source_ids = list(range(1, 45))
    for index, source_id in zip((7, 18, 29, 40), (107, 218, 329, 440)):
        source_ids[index] = source_id
    word = tmp_path / "44-logical-pages.docx"
    document = Document()
    for logical_number, source_id in enumerate(source_ids, start=1):
        document.add_paragraph(f"第 {source_id} 页")
        document.add_paragraph(f"逻辑页 {logical_number}")
        document.add_paragraph(f"这是第 {logical_number} 个逻辑页的正文。")
        if logical_number % 3 == 0 and logical_number != 44:
            document.add_page_break()
    document.save(word)
    logo = tmp_path / "logo.svg"
    logo.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 20"><rect width="100" height="20"/></svg>',
        encoding="utf-8",
    )

    project = tmp_path / "project"
    initialize_v6_project(word, logo, project)
    extraction = json.loads(
        (project / "02_v6/paginated_word_source.json").read_text(encoding="utf-8")
    )
    composition = json.loads(
        (project / "02_v6/page_composition.json").read_text(encoding="utf-8")
    )

    assert extraction["page_count"] == 44
    assert [page["source_page_id"] for page in extraction["pages"]] == source_ids
    assert composition["page_count"] >= 44
    assert [page["output_page_number"] for page in composition["pages"]] == list(
        range(1, composition["page_count"] + 1)
    )


def test_one_confirmation_renders_and_assembles_special_page_insertions_in_order(
    tmp_path: Path,
) -> None:
    # Break caught: inserted native pages skip the normal render/assembly chain or break numbering.
    word = tmp_path / "complete-composition.docx"
    document = Document()
    toc_entries = tuple(f"PART {index}｜虚构章节{index}" for index in range(1, 14))
    pages = [
        ("cover", "虚构系统协作演示", ("非生产环境组合验证",)),
        ("toc", "目录", toc_entries),
        ("content", "PART 1｜虚构章节1", ("本页用于验证来源支持的章节增页。",)),
        ("content", "演练总结", ("最终目标：完成虚构流程演练", "所有测试状态已归档。")),
    ]
    for number, (role, title, body_lines) in enumerate(pages, start=1):
        document.add_paragraph(f"第 {number} 页")
        document.add_paragraph({
            "cover": "PPT页型：封面", "toc": "PPT页型：目录",
            "section": "PPT页型：章节", "content": "PPT页型：正文",
            "appendix": "PPT页型：附录", "closing": "PPT页型：尾页",
        }[role])
        document.add_paragraph(title)
        for line in body_lines:
            document.add_paragraph(line)
    document.save(word)
    logo = tmp_path / "logo.svg"
    logo.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 20"><rect width="100" height="20"/></svg>',
        encoding="utf-8",
    )
    project = tmp_path / "confirmed-deck"
    initialize_v6_project(word, logo, project)

    server = _load_server()
    client = server.create_app(project).test_client()
    recommendations = client.get("/api/recommendations").get_json()
    expected_roles = ["cover", "toc", "toc", "section", "content", "content", "closing"]
    proposed = json.loads((project / "02_v6/page_composition.json").read_text(encoding="utf-8"))
    assert [page["page_role"] for page in proposed["pages"]] == expected_roles
    assert [page["output_page_number"] for page in proposed["pages"]] == list(range(1, 8))
    assert proposed["pages"][2]["composition_page_id"].startswith("toc-continuation:")
    assert proposed["pages"][3]["composition_page_id"].startswith("synthesized-section:")
    assert proposed["pages"][6]["role_source"] == "synthesized"
    source = json.loads((project / "02_v6/paginated_word_source.json").read_text(encoding="utf-8"))
    source_ids = {
        block["source_block_id"]
        for page in source["pages"]
        for block in page["blocks"]
    }
    assert set(proposed["pages"][3]["material_source_block_ids"]) <= source_ids
    assert set(proposed["pages"][6]["material_source_block_ids"]) <= source_ids
    template = next(
        item for item in recommendations["templates"]
        if item["id"] == recommendations["recommended_template_id"]
    )
    payload = {
        "submission_id": "full-deck-e2e-0001",
        "revision": recommendations["revision"],
        **template["defaults"],
        "selected_director_template_id": template["id"],
        "director_taskbook": recommendations["director_taskbook"],
    }
    attach_deck_plan(project, payload, structured=True)
    first = client.post("/api/confirm", json=payload)
    assert first.status_code == 200, first.get_json()
    assert server._wait(project, "final", 1) == 0
    second = client.post("/api/confirm", json=payload)
    assert second.status_code == 409
    result = json.loads((project / "confirm_ui/result.json").read_text(encoding="utf-8"))
    assert result["revision"] == 1
    assert len(result["confirmed_pages"]) == result["confirmed_pages"][-1]["output_page_number"]
    assert [page["page_role"] for page in result["confirmed_pages"]] == expected_roles
    frozen = json.loads((project / "02_v6/page_composition.json").read_text(encoding="utf-8"))
    assert [page["page_role"] for page in frozen["pages"]] == expected_roles

    def open_workspace(root: Path, page_number: int):
        return SimpleNamespace(project_copy=root, page_number=page_number)

    content_pages = []
    native_pages = []

    def accept_content(workspace, **_kwargs):
        content_pages.append(workspace.page_number)
        state = load(workspace.project_copy)
        page = state["pages"][workspace.page_number - 1]
        for target in ("generating", "qa_review", "accepted"):
            page = transition_page(page, target)
        state["pages"][workspace.page_number - 1] = page
        save(workspace.project_copy, state)
        return SimpleNamespace(
            status="accepted", accepted=SimpleNamespace(candidate=object()),
            attempts=(), failure_problems=(), correction_count=0,
        )

    def reconstruct_content(workspace, _outcome):
        body = project / f"editable-body-{workspace.page_number}.pptx"
        _editable_body(body, workspace.page_number)
        return finalize_reconstructed_page(
            workspace.project_copy,
            page_number=workspace.page_number,
            reconstructed_body=body,
        )

    def render_native(root: Path, page_number: int):
        native_pages.append(page_number)
        return render_special_page(root, page_number)

    assembled_outcomes = []
    report = run_pages(
        project,
        list(range(1, len(result["confirmed_pages"]) + 1)),
        dependencies=PipelineDependencies(
            open_workspace=open_workspace,
            evidence_recorder=lambda _workspace: object(),
            candidate_loop=accept_content,
            reconstruct_page=reconstruct_content,
            native_page_renderer=render_native,
            assemble_project=lambda _root, outcomes: assembled_outcomes.append(outcomes),
        ),
        configuration=PipelineConfiguration(
            page_workers=1, initial_page_concurrency=1, maximum_page_concurrency=1,
        ),
    )

    assert report.failed_pages == {}
    assert native_pages == [1, 2, 3, 4, 7]
    assert content_pages == [5, 6]
    assert set(assembled_outcomes[0]) == set(range(1, len(result["confirmed_pages"]) + 1))
    assembly = assemble_v6_deck(project)
    composition = json.loads((project / "02_v6/page_composition.json").read_text(encoding="utf-8"))
    persisted_assembly = json.loads(
        (project / assembly["assembly_report"]).read_text(encoding="utf-8")
    )
    deck_path = (
        project / assembly["output"]
        if assembly["status"] == "complete"
        else project / assembly["candidate_output"]["relative_path"]
    )
    deck = Presentation(deck_path)
    assert all(page["state"] == "page_complete" for page in load(project)["pages"])
    assert persisted_assembly == assembly
    assert len(deck.slides) == composition["page_count"] == assembly["page_count"]
    assert assembly["page_order"] == list(range(1, composition["page_count"] + 1))
    assert assembly["structure_validation"]["passed"] is True
    if assembly["status"] == "validation_incomplete":
        assert assembly["release_status"] == "not_release_ready"
        assert assembly["final_output"] is None
        assert "output" not in assembly
    expected_number_visibility = {
        "cover": False, "toc": True, "section": True,
        "content": True, "appendix": True, "closing": False,
    }
    for slide, page, expected_role in zip(deck.slides, composition["pages"], expected_roles):
        assert page["page_role"] == expected_role
        names = {shape.name for shape in slide.shapes}
        has_number = "special-page-number" in names or "fixed-frame-page-number" in names
        assert has_number is expected_number_visibility[expected_role]


def test_confirmation_rejects_reorder_delete_and_preserves_word_authority(tmp_path: Path) -> None:
    word = tmp_path / "three-content-pages.docx"
    document = Document()
    image_sha256 = {}
    for source_position, source_id in enumerate((10, 30, 50), start=1):
        image = tmp_path / f"source-{source_position}.png"
        Image.new("RGB", (40, 20), (source_position * 60, 20, 120)).save(image)
        image_sha256[source_position] = hashlib.sha256(image.read_bytes()).hexdigest()
        document.add_paragraph(f"第 {source_id} 页")
        document.add_paragraph("PPT页型：正文")
        document.add_paragraph(f"标题-{source_position}")
        document.add_paragraph(f"BODY-{source_position}")
        document.add_picture(str(image))
    document.save(word)
    logo = tmp_path / "logo.svg"
    logo.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 20"><rect width="100" height="20"/></svg>',
        encoding="utf-8",
    )
    project = tmp_path / "reordered-project"
    initialize_v6_project(word, logo, project)
    server = _load_server()
    client = server.create_app(project).test_client()
    recommendations = client.get("/api/recommendations").get_json()
    template = next(
        item for item in recommendations["templates"]
        if item["id"] == recommendations["recommended_template_id"]
    )
    proposed = json.loads((project / "02_v6/page_composition.json").read_text(encoding="utf-8"))["pages"]
    confirmed_pages = []
    for output_number, original_index in enumerate((2, 0), start=1):
        page = {key: value for key, value in proposed[original_index].items() if key != "source_preview"}
        page["output_page_number"] = output_number
        confirmed_pages.append(page)
    payload = {
        "submission_id": "reorder-delete-e2e-0001", "revision": 0,
        **template["defaults"], "confirmed_pages": proposed,
    }
    attach_deck_plan(project, payload, structured=True)
    payload["confirmed_pages"] = confirmed_pages

    response = client.post("/api/confirm", json=payload)
    assert response.status_code == 400, response.get_json()
    assert "preserve every Word page in order" in response.get_json()["error"]
    source = json.loads((project / "02_v6/paginated_word_source.json").read_text(encoding="utf-8"))

    assert [page["page_number"] for page in source["pages"]] == [1, 2, 3]
    assert [page["source_page_id"] for page in source["pages"]] == [10, 30, 50]
    assert [page["source_asset_page_number"] for page in source["pages"]] == [1, 2, 3]


def test_adaptive_e2e_tracks_all_public_consulting_director_patterns() -> None:
    fixture = json.loads(CONSULTING_REGRESSION_FIXTURE.read_text(encoding="utf-8"))

    assert [case["id"] for case in fixture["cases"]] == [
        "three-lane-portfolio",
        "five-stage-capital-loop",
        "four-capability-transformation-chain",
        "four-row-investment-matrix",
    ]
    assert all(case["privacy_class"] == "public-synthetic" for case in fixture["cases"])


@pytest.mark.parametrize(
    ("expected_template_id", "signal_text"),
    [
        ("company-business-introduction", "公司介绍、业务介绍、核心能力与合作价值。"),
        ("investment-committee", "提交投委会审议，重点说明估值、投资回报与退出。"),
        ("project-initiation", "申请项目立项，说明初步尽调、可行性和工作计划。"),
        ("corporate-planning", "公司三年规划围绕战略目标、重点任务与实施路径。"),
        ("investment-project-bp", "投资项目 BP 说明融资需求、商业模式与资金用途。"),
    ],
)
def test_director_template_confirmation_preserves_word_and_automatic_pages(
    tmp_path: Path, expected_template_id: str, signal_text: str,
) -> None:
    word = tmp_path / f"{expected_template_id}.docx"
    document = Document()
    document.add_paragraph("第 1 页")
    document.add_paragraph("项目标题")
    document.add_paragraph(signal_text)
    document.save(word)
    logo = tmp_path / "logo.svg"
    logo.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 20"><rect width="100" height="20"/></svg>',
        encoding="utf-8",
    )
    project = tmp_path / "project"
    initialize_v6_project(word, logo, project)
    source_before = json.loads((project / "02_v6/page_sources/page_001.json").read_text(encoding="utf-8"))
    composition_before = json.loads((project / "02_v6/page_composition.json").read_text(encoding="utf-8"))
    client = _load_server().create_app(project).test_client()
    recommendations = client.get("/api/recommendations").get_json()
    assert recommendations["recommended_template_id"] == expected_template_id
    template = next(item for item in recommendations["templates"] if item["id"] == expected_template_id)
    payload = {
        "submission_id": f"director-template-{expected_template_id}",
        "revision": 0,
        **template["defaults"],
        "selected_director_template_id": expected_template_id,
        "director_taskbook": template["director_taskbook"],
    }
    attach_deck_plan(project, payload, structured=True)

    response = client.post("/api/confirm", json=payload)
    assert response.status_code == 200, response.get_json()
    result = json.loads((project / "confirm_ui/result.json").read_text(encoding="utf-8"))
    state = load(project)
    source_after = json.loads((project / "02_v6/page_sources/page_001.json").read_text(encoding="utf-8"))
    composition_after = json.loads((project / "02_v6/page_composition.json").read_text(encoding="utf-8"))
    taskbook_prompt = confirmed_taskbook_prompt(project)

    assert state["director_confirmation"] == result["director_confirmation"]
    assert result["director_confirmation"]["taskbook"] == template["director_taskbook"]
    assert source_after["word_original"] == source_before["word_original"]
    assert result["director_confirmation"]["deck_plan"]["plan"] == payload["deck_plan"]
    assert composition_after["pages"] == [
        {**page, "chapter_title": planned["chapter_title"], "fixed_page_title": planned["title"]}
        for page, planned in zip(composition_before["pages"], payload["deck_plan"]["pages"])
    ]
    assert all(value in taskbook_prompt for value in template["director_taskbook"].values())
    for forbidden in (expected_template_id, "template_version", "taskbook_digest", '"defaults"'):
        assert forbidden not in taskbook_prompt


def _load_server():
    spec = importlib.util.spec_from_file_location("v6_adaptive_e2e_server", SERVER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _editable_body(path: Path, page_number: int) -> None:
    deck = Presentation()
    deck.slide_width = Cm(25.4)
    deck.slide_height = Cm(14.288)
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_textbox(Cm(1.5), Cm(3), Cm(16), Cm(2)).text = f"Editable approved body {page_number}"
    deck.save(path)

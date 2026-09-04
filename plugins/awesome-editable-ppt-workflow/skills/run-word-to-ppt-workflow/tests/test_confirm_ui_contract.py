"""Contract tests for the Awesome three-step visual confirmation UI."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "scripts" / "confirm_ui" / "server.py"
STATIC_DIR = ROOT / "scripts" / "confirm_ui" / "static"
SCHEMA_PATH = ROOT / "schemas" / "style_confirmation.schema.json"
TASKBOOK_SCHEMA_PATH = ROOT / "schemas" / "director_taskbook_v1.schema.json"
TASKBOOK_MODULE_PATH = ROOT / "scripts" / "director_taskbook.py"
EXPECTED_DIRECTOR_TEMPLATE_IDS = [
    "company-business-introduction",
    "investment-committee",
    "project-initiation",
    "corporate-planning",
    "investment-project-bp",
]

REQUIRED_VISUAL_FIELDS = {
    "primary_color",
    "secondary_color",
    "background_color",
    "cjk_font",
    "latin_font",
    "title_size_pt",
    "body_size_pt",
    "caption_size_pt",
}
OPTIONAL_VISUAL_FIELDS = {"highlight_color"}
VISUAL_FIELDS = REQUIRED_VISUAL_FIELDS | OPTIONAL_VISUAL_FIELDS
IDENTITY_FIELDS = {"submission_id", "revision"}
ALL_FIELDS = VISUAL_FIELDS | IDENTITY_FIELDS
REQUIRED_FIELDS = REQUIRED_VISUAL_FIELDS | IDENTITY_FIELDS
FORBIDDEN_FIELDS = {
    "template_id",
    "template_selection",
    "page_materials",
    "confirmed_pages",
    "image_policy",
    "evidence_strength",
    "layout",
    "information_density",
    "production_profile",
    "max_concurrency",
    "automatic_repair_budget",
}


def load_server():
    spec = importlib.util.spec_from_file_location("awesome_confirm_ui_server", SERVER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_taskbook_module():
    assert TASKBOOK_MODULE_PATH.is_file(), "director taskbook module is missing"
    spec = importlib.util.spec_from_file_location("awesome_director_taskbook", TASKBOOK_MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_taskbook() -> dict[str, str]:
    return {
        "use_scenario": "投决会审议追加投资",
        "presenter": "项目投资团队",
        "primary_audience": "投资决策委员会",
        "audience_prior_knowledge": "已了解项目基本情况",
        "desired_outcome": "决定是否追加投资以及附加条件",
        "emphasis": "经营表现、资金用途、回报变化和新增风险",
        "deemphasis": "重复的公司基础介绍",
    }


def test_director_taskbook_contract_is_exact_and_digest_is_canonical():
    module = load_taskbook_module()
    value = valid_taskbook()

    assert TASKBOOK_SCHEMA_PATH.is_file()
    assert module.validate_taskbook(value) == value
    assert module.taskbook_digest(value) == module.taskbook_digest(dict(reversed(value.items())))
    assert module.TASKBOOK_FIELDS == tuple(value)


def test_director_taskbook_allows_no_emphasis_and_matches_repeated_pages_conservatively():
    module = load_taskbook_module()
    value = valid_taskbook()
    value["emphasis"] = ""
    assert module.validate_taskbook(value)["emphasis"] == ""
    Draft202012Validator(
        json.loads(TASKBOOK_SCHEMA_PATH.read_text(encoding="utf-8"))
    ).validate(value)
    pages = [
        {"page_number": 1, "blocks": [{"type": "paragraph", "text": "现金流改善"}]},
        {"page_number": 2, "blocks": [{"type": "paragraph", "text": "新增风险清单"}]},
        {"page_number": 3, "blocks": [{"type": "paragraph", "text": "现金流改善"}]},
        {"page_number": 4, "blocks": [{"type": "paragraph", "text": "一般背景"}]},
    ]
    assert module.identify_emphasis_pages("现金流改善；新增风险清单", pages) == {1, 2, 3}
    assert module.identify_emphasis_pages("相似但未出现的内容", pages) == set()
    assert module.identify_emphasis_pages("", pages) == set()
    composition = [
        {"output_page_number": 1, "page_role": "section"},
        {"output_page_number": 2, "page_role": "content"},
        {"output_page_number": 3, "page_role": "content"},
        {"output_page_number": 4, "page_role": "content"},
        {"output_page_number": 5, "page_role": "section"},
    ]
    assert module.expand_emphasis_sections({1}, composition) == {1, 2, 3, 4}
    assert module.expand_emphasis_sections({2, 4}, composition) == {1, 2, 3, 4}
    assert module.expand_emphasis_sections({2}, composition) == {2}


def test_emphasis_semantics_match_section_heading_then_expand_only_that_section():
    module = load_taskbook_module()
    pages = [
        {"page_number": 1, "blocks": [{"text": "七、核心合作：委托联合团队开展母基金专业化管理"}]},
        {"page_number": 2, "blocks": [{"text": "管理职责"}]},
        {"page_number": 3, "blocks": [{"text": "九、实施计划：形成产业成果"}]},
        {"page_number": 4, "blocks": [{"text": "十二个月成果与绩效评价"}]},
        {"page_number": 5, "blocks": [{"text": "普通附录"}]},
    ]
    composition = [
        {"output_page_number": 1, "source_page_number": 1, "page_role": "section", "fixed_page_title": "核心合作"},
        {"output_page_number": 2, "source_page_number": 2, "page_role": "content", "fixed_page_title": "管理职责"},
        {"output_page_number": 3, "source_page_number": 3, "page_role": "section", "fixed_page_title": "实施计划"},
        {"output_page_number": 4, "source_page_number": 4, "page_role": "content", "fixed_page_title": "成果与评价"},
        {"output_page_number": 5, "source_page_number": 5, "page_role": "closing", "fixed_page_title": "结语"},
    ]
    section_matches = module.identify_semantic_emphasis_pages(
        "母基金管理、合作机制、实施路径和预期成效", pages, composition,
    )
    assert section_matches == {1, 3}
    assert module.expand_emphasis_sections(section_matches, composition) == {1, 2, 3, 4}
    negatives = [
        {"page_number": 1, "blocks": [{"text": "产业概况"}]},
        {"page_number": 2, "blocks": [{"text": "合作历史"}]},
        {"page_number": 3, "blocks": [{"text": "实施背景"}]},
    ]
    negative_composition = [
        {
            "output_page_number": index,
            "source_page_number": index,
            "page_role": "section",
            "fixed_page_title": page["blocks"][0]["text"],
        }
        for index, page in enumerate(negatives, start=1)
    ]
    assert module.identify_semantic_emphasis_pages(
        "产业创新、合作机制、实施路径", negatives, negative_composition,
    ) == set()


def test_semantic_emphasis_matches_individual_content_pages_without_expanding_neighbors():
    module = load_taskbook_module()
    pages = [
        {"page_number": 1, "blocks": [{"text": "建立母基金专业化管理与委托关系"}]},
        {"page_number": 2, "blocks": [{"text": "普通背景介绍"}]},
        {"page_number": 3, "blocks": [{"text": "前90天开始实施计划并产出成果"}]},
    ]
    composition = [
        {"output_page_number": index, "source_page_number": index, "page_role": "content", "fixed_page_title": page["blocks"][0]["text"]}
        for index, page in enumerate(pages, start=1)
    ]
    matches = module.identify_semantic_emphasis_pages(
        "母基金管理、合作机制、实施路径和预期成效", pages, composition,
    )
    assert matches == {1, 3}
    assert module.expand_emphasis_sections(matches, composition) == {1, 3}


@pytest.mark.parametrize("mutation", ["missing", "extra", "blank", "non_string"])
def test_director_taskbook_rejects_invalid_fields(mutation: str):
    module = load_taskbook_module()
    value = valid_taskbook()
    if mutation == "missing":
        value.pop("presenter")
    elif mutation == "extra":
        value["meeting_duration"] = "30分钟"
    elif mutation == "blank":
        value["desired_outcome"] = "   "
    else:
        value["emphasis"] = 42

    with pytest.raises(ValueError):
        module.validate_taskbook(value)


def valid_contract(*, revision: int = 1, highlight_color: str | None = None) -> dict:
    result = {
        "submission_id": "submission-0001",
        "revision": revision,
        "primary_color": "#17365D",
        "secondary_color": "#C7352B",
        "background_color": "#FFFFFF",
        "cjk_font": "Microsoft YaHei",
        "latin_font": "Arial",
        "title_size_pt": 28,
        "body_size_pt": 12,
        "caption_size_pt": 9,
    }
    if highlight_color is not None:
        result["highlight_color"] = highlight_color
    return result


def visual_contract(payload: dict) -> dict:
    return {field: payload[field] for field in VISUAL_FIELDS if field in payload}


def confirmed_page(
    output_page_number: int,
    page_role: str,
    *,
    visible_page_number: bool,
    role_source: str = "explicit",
) -> dict:
    return {
        "output_page_number": output_page_number,
        "source_page_id": output_page_number,
        "page_role": page_role,
        "role_source": role_source,
        "chapter_title": "Chapter" if page_role == "section" else "",
        "fixed_page_title": f"Page {output_page_number}",
        "source_page_number": output_page_number,
        "material_source_block_ids": [f"block-{output_page_number}"],
        "visible_page_number": visible_page_number,
    }


def make_v6_project(tmp_path: Path, *, page_count: int) -> Path:
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from workflow_v6_contract import new_page, new_project
    from workflow_v6_state import create

    project = tmp_path / "v6-project"
    create(
        project,
        new_project(
            word_source={"path": "00_source/source.docx", "sha256": "a" * 64},
            logo_source={"path": "00_source/logo.svg", "sha256": "b" * 64},
            pages=[new_page(number, title=f"Page {number}") for number in range(1, page_count + 1)],
        ),
    )
    (project / "confirm_ui").mkdir()
    (project / "confirm_ui" / "recommendations.json").write_text("{}", encoding="utf-8")
    return project


def write_composition(project: Path, *, roles: list[str]) -> dict:
    pages = [
        confirmed_page(
            number,
            role,
            visible_page_number=role not in {"cover", "closing"},
            role_source="automatic",
        )
        for number, role in enumerate(roles, start=1)
    ]
    value = {
        "artifact_version": "page-composition-v1",
        "page_count": len(pages),
        "pages": pages,
        "warnings": [],
    }
    path = project / "02_v6" / "page_composition.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return value


def write_preconfirmation_files(project: Path, *, page_count: int) -> None:
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from workflow_v6_materials import new_page_materials
    from workflow_v6_source import compile_effective_page

    receipt_statuses = ["pending", "confirmed", "failed_no_retry"]
    records = {
        "page_sources": lambda number: {
            "artifact_version": "page-source-v6",
            "page_number": number,
            "fixed_page_title": f"Page {number}",
            "word_original": f"Source text {number}",
            "body_render_content": f"Source text {number}",
            "comments": [],
            "references": [],
        },
        "effective_pages": lambda number: compile_effective_page(
            page_number=number,
            word_text=f"Source text {number}",
            comments=[],
            references=[],
            attachment_links=[],
            fixed_page_title=f"Page {number}",
        ),
        "page_materials": lambda number: new_page_materials(
            page_number=number,
            fixed_page_title=f"Page {number}",
            word_original=f"Source text {number}",
            effective_body=f"Effective {number}",
        ),
        "reference_materials": lambda number: {
            "artifact_version": "reference-materials-v6",
            "page_number": number,
            "references": [],
            "search_requests": [],
            "reference_acquisitions": [{
                "request_id": f"request-{number}",
                "page_number": number,
                "purpose": f"Reference {number}",
                "identity_evidence_need": f"Evidence {number}",
                "status": receipt_statuses[(number - 1) % len(receipt_statuses)],
                "history": [receipt_statuses[(number - 1) % len(receipt_statuses)]],
            }],
        },
    }
    for directory_name, factory in records.items():
        directory = project / "02_v6" / directory_name
        directory.mkdir(parents=True, exist_ok=True)
        for number in range(1, page_count + 1):
            (directory / f"page_{number:03d}.json").write_text(
                json.dumps(factory(number), ensure_ascii=False), encoding="utf-8"
            )
        (directory / "page_999.json").write_bytes(b"unrelated-sentinel")
    (project / "02_v6/paginated_word_source.json").write_text(json.dumps({
        "pagination_mode": "explicit_text_markers",
        "page_count": page_count,
        "pages": [
            {
                "page_number": number, "source_page_id": number,
                "source_asset_page_number": number,
                "blocks": [{
                    "type": "paragraph", "text": f"Source text {number}",
                    "source_block_id": f"block-{number}",
                }],
                "page_comments": [],
            }
            for number in range(1, page_count + 1)
        ],
    }), encoding="utf-8")


def authority_bytes(project: Path) -> dict[str, bytes]:
    paths = [
        project / "workflow_v6.json",
        project / "02_v6" / "page_composition.json",
        project / "02_v6" / "paginated_word_source.json",
    ]
    for directory_name in (
        "page_sources", "effective_pages", "page_materials", "reference_materials"
    ):
        paths.extend(
            project / "02_v6" / directory_name / f"page_{number:03d}.json"
            for number in (1, 2, 3, 999)
        )
    return {path.relative_to(project).as_posix(): path.read_bytes() for path in paths if path.is_file()}


def same_toc_synthesized_project(tmp_path: Path) -> tuple[Path, dict]:
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from workflow_v6_composition import compose_pages

    raw_pages = [
        {
            "page_number": 1, "source_page_id": 1,
            "blocks": [
                {"type": "paragraph", "text": "PPT页型：目录", "source_block_id": "toc-role"},
                {"type": "paragraph", "text": "PART 1｜产业目标\nPART 2｜实施路径", "source_block_id": "toc-parts"},
            ],
        },
        {"page_number": 2, "source_page_id": 2, "blocks": [
            {"type": "paragraph", "text": "PPT页型：正文", "source_block_id": "p2-role"},
            {"type": "paragraph", "text": "PART 1｜产业目标", "source_block_id": "p2-title"},
        ]},
        {"page_number": 3, "source_page_id": 3, "blocks": [
            {"type": "paragraph", "text": "PPT页型：正文", "source_block_id": "p3-role"},
            {"type": "paragraph", "text": "PART 2｜实施路径", "source_block_id": "p3-title"},
        ]},
    ]
    proposed = compose_pages({"pages": raw_pages, "pagination_warnings": []})
    project = make_v6_project(tmp_path, page_count=proposed["page_count"])
    (project / "02_v6").mkdir(parents=True, exist_ok=True)
    (project / "02_v6/page_composition.json").write_text(
        json.dumps(proposed, ensure_ascii=False), encoding="utf-8",
    )
    write_preconfirmation_files(project, page_count=proposed["page_count"])
    source_path = project / "02_v6/paginated_word_source.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    for source_page, composition_page in zip(source["pages"], proposed["pages"]):
        source_page["source_page_id"] = composition_page["source_page_id"]
        source_page["source_asset_page_number"] = composition_page["source_page_number"]
    source_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    return project, proposed


def make_project(tmp_path: Path, recommendation: dict | None = None) -> Path:
    project = tmp_path / "project"
    confirm_dir = project / "confirm_ui"
    confirm_dir.mkdir(parents=True)
    if recommendation is not None:
        (confirm_dir / "recommendations.json").write_text(
            json.dumps(recommendation, ensure_ascii=False), encoding="utf-8"
        )
    return project


def test_v6_final_submission_freezes_visuals_and_complete_composition(tmp_path: Path):
    project = make_v6_project(tmp_path, page_count=3)
    write_composition(project, roles=["cover", "content", "closing"])
    payload = valid_contract(revision=0)
    payload["confirmed_pages"] = [
        confirmed_page(1, "cover", visible_page_number=False),
        confirmed_page(2, "content", visible_page_number=True, role_source="automatic"),
        confirmed_page(3, "closing", visible_page_number=False, role_source="automatic"),
    ]
    payload["confirmed_pages"][0]["role_source"] = "automatic"
    write_preconfirmation_files(project, page_count=3)

    result = load_server()._v6_final_submission(project, visual_contract(payload), payload)

    assert result["status"] == "confirmed"
    assert result["global_visual_contract"] == visual_contract(payload)
    assert result["confirmed_pages"][0]["page_role"] == "cover"
    assert result["confirmed_pages"][-1]["visible_page_number"] is False


def test_v6_confirmation_persists_director_and_uses_automatic_composition(tmp_path: Path):
    project = make_v6_project(tmp_path, page_count=3)
    proposed = write_composition(project, roles=["cover", "content", "closing"])
    write_preconfirmation_files(project, page_count=3)
    payload = valid_contract(revision=0)
    payload.update({
        "selected_director_template_id": "investment-committee",
        "director_taskbook": valid_taskbook(),
    })

    result = load_server()._save_visual_contract(project, payload)
    state = json.loads((project / "workflow_v6.json").read_text(encoding="utf-8"))

    assert result["confirmed_pages"] == proposed["pages"]
    assert result["director_confirmation"]["template_id"] == "investment-committee"
    assert result["director_confirmation"]["template_version"] == "1.0"
    assert len(result["director_confirmation"]["taskbook_digest"]) == 64
    assert state["director_confirmation"] == result["director_confirmation"]
    assert set(result["global_visual_contract"]) == REQUIRED_VISUAL_FIELDS


def test_v6_confirmation_preserves_optional_highlight_color(tmp_path: Path):
    project = make_v6_project(tmp_path, page_count=1)
    write_composition(project, roles=["content"])
    write_preconfirmation_files(project, page_count=1)
    payload = valid_contract(revision=0, highlight_color="#D3A62C")
    payload.update({
        "selected_director_template_id": "corporate-planning",
        "director_taskbook": valid_taskbook(),
    })

    result = load_server()._save_visual_contract(project, payload)

    assert result["global_visual_contract"]["highlight_color"] == "#D3A62C"


def test_v6_confirmation_rejects_browser_supplied_taskbook_digest(tmp_path: Path):
    project = make_v6_project(tmp_path, page_count=1)
    write_composition(project, roles=["content"])
    payload = valid_contract(revision=0)
    payload.update({
        "selected_director_template_id": "investment-committee",
        "director_taskbook": valid_taskbook(),
        "director_taskbook_digest": "0" * 64,
    })

    with pytest.raises(ValueError, match="confirmation must contain"):
        load_server()._save_visual_contract(project, payload)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda pages: pages.__setitem__(1, {**pages[1], "page_role": "cover", "visible_page_number": False}), "cover"),
        (lambda pages: pages.__setitem__(1, {**pages[1], "page_role": "closing", "visible_page_number": False}), "closing"),
        (lambda pages: pages.__setitem__(1, {**pages[1], "page_role": "section", "chapter_title": ""}), "section title"),
        (lambda pages: pages.__setitem__(1, {**pages[1], "output_page_number": 8}), "continuous"),
        (lambda pages: pages.__setitem__(1, {**pages[1], "page_role": "unknown"}), "role"),
        (lambda pages: pages.__setitem__(1, {**pages[1], "role_source": "synthesized", "material_source_block_ids": []}), "source block"),
    ],
    ids=["two_covers", "two_closings", "chapter_titles", "composition_positions", "unknown_role", "composition_trace"],
)
def test_v6_final_submission_rejects_invalid_composition(tmp_path: Path, mutate, message: str):
    project = make_v6_project(tmp_path, page_count=3)
    proposed = write_composition(project, roles=["cover", "content", "closing"])
    payload = valid_contract(revision=0)
    payload["confirmed_pages"] = [dict(page) for page in proposed["pages"]]
    mutate(payload["confirmed_pages"])

    with pytest.raises(ValueError, match=message):
        load_server()._v6_final_submission(project, visual_contract(payload), payload)


def make_awesome_project(tmp_path: Path, name: str = "awesome-project") -> Path:
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from workflow_v6_contract import new_page, new_project
    from workflow_v6_state import create

    project = tmp_path / name
    create(
        project,
        new_project(
            word_source={"path": "00_source/source.docx", "sha256": "a" * 64},
            logo_source={"path": "00_source/logo.svg", "sha256": "b" * 64},
            pages=[new_page(1, title="Title")],
        ),
    )
    confirm_dir = project / "confirm_ui"
    confirm_dir.mkdir()
    (confirm_dir / "recommendations.json").write_text("{}", encoding="utf-8")
    return project


def test_confirmed_visual_contract_schema_keeps_highlight_optional_and_rejects_invalid_values():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["title"] == "ConfirmedVisualContractV1"
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == ALL_FIELDS
    assert set(schema["required"]) == REQUIRED_FIELDS

    validator = Draft202012Validator(schema)
    assert list(validator.iter_errors(valid_contract())) == []
    assert list(validator.iter_errors(valid_contract(highlight_color="#D3A62C"))) == []
    assert list(validator.iter_errors(valid_contract(highlight_color="gold")))
    for forbidden in sorted(FORBIDDEN_FIELDS):
        payload = valid_contract()
        payload[forbidden] = [] if forbidden.endswith("s") else "forbidden"
        assert list(validator.iter_errors(payload)), forbidden


class _StepParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.steps: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "section" and values.get("data-step"):
            self.steps.append(values)


def test_static_document_has_exactly_three_steps_and_no_page_editor_fields():
    parser = _StepParser()
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    parser.feed(html)
    assert [step["data-step"] for step in parser.steps] == ["1", "2", "3"]
    assert "整页 PPT 背景色" in html
    assert '<input name="highlight_color" type="color" required>' in html
    assert '<textarea name="emphasis" maxlength="2000" rows="3"></textarea>' in html
    for forbidden in (
        "confirmed_pages", "page_role", "composition-warnings", "page-review",
        "regional_characteristics", "visual_description", "风险警告", "页面编排",
    ):
        assert forbidden not in html


def test_browser_state_has_three_steps_and_submits_only_director_taskbook_and_visuals():
    script = r"""
const assert = require('assert');
const ui = require(process.argv[1]);
const templates = [
  {id: 'calm', defaults: {
    primary_color:'#17365D', secondary_color:'#C7352B', background_color:'#FFFFFF',
    highlight_color:'#C7352B',
    cjk_font:'Microsoft YaHei', latin_font:'Arial', title_size_pt:28, body_size_pt:12,
    caption_size_pt:9}, director_taskbook:{
      use_scenario:'公司推介', presenter:'公司团队', primary_audience:'合作伙伴',
      audience_prior_knowledge:'基础认知', desired_outcome:'形成合作理解',
      emphasis:'业务价值', deemphasis:'内部细节'}},
  {id: 'bold', defaults: {
    primary_color:'#111111', secondary_color:'#FF2C00', background_color:'#F1F0EE',
    highlight_color:'#FF2C00',
    cjk_font:'Source Han Sans SC', latin_font:'Aptos', title_size_pt:34, body_size_pt:15,
    caption_size_pt:10}, director_taskbook:{
      use_scenario:'投决会', presenter:'投资团队', primary_audience:'投委会',
      audience_prior_knowledge:'已了解项目', desired_outcome:'形成投资决定',
      emphasis:'回报与风险', deemphasis:'重复背景'}}
];
assert.strictEqual(ui.VISUAL_FIELDS.length, 9);
assert.strictEqual(ui.TASKBOOK_FIELDS.length, 7);
let state = ui.createState(templates, 'calm', 0, templates[0].director_taskbook, '推荐理由', 'high');
assert.strictEqual(state.step, 1);
state = ui.applyTemplate(state, 'bold');
assert.strictEqual(state.values.primary_color, '#111111');
assert.strictEqual(state.values.cjk_font, 'Source Han Sans SC');
assert.strictEqual(state.taskbook.use_scenario, '投决会');
state = ui.goNext(state);
assert.strictEqual(state.step, 2);
const edits = {
  primary_color:'#222222', secondary_color:'#CC3300', background_color:'#FAFAFA',
  highlight_color:'#D3A62C',
  cjk_font:'Noto Sans CJK SC', latin_font:'Georgia', title_size_pt:32, body_size_pt:14,
  caption_size_pt:10
};
for (const [field, value] of Object.entries(edits)) state = ui.updateField(state, field, value);
assert.deepStrictEqual(state.values, edits);
state = ui.goNext(state);
assert.strictEqual(state.step, 3);
state = ui.updateTaskbook(state, 'desired_outcome', '决定是否投资及附加条件');
const payload = ui.buildSubmission(state, 'submission-0001');
assert.deepStrictEqual(new Set(Object.keys(payload)), new Set([...ui.VISUAL_FIELDS, 'submission_id', 'revision', 'selected_director_template_id', 'director_taskbook']));
assert.strictEqual(payload.revision, 0);
assert.strictEqual(payload.selected_director_template_id, 'bold');
assert.strictEqual(payload.director_taskbook.desired_outcome, '决定是否投资及附加条件');
for (const forbidden of ['template_id','template_selection','page_materials','confirmed_pages','page_role']) {
  assert.strictEqual(Object.prototype.hasOwnProperty.call(payload, forbidden), false);
}
"""
    completed = subprocess.run(
        ["node", "-e", script, str(STATIC_DIR / "app.js")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_v6_recommendations_hide_composition_and_one_post_freezes_it_automatically(tmp_path: Path):
    project = make_v6_project(tmp_path, page_count=3)
    composition = write_composition(project, roles=["cover", "content", "closing"])
    write_preconfirmation_files(project, page_count=3)
    client = load_server().create_app(str(project)).test_client()

    recommendation = client.get("/api/recommendations")

    assert recommendation.status_code == 200
    data = recommendation.get_json()
    assert data["step_count"] == 3
    assert "composition" not in data

    payload = valid_contract(revision=0)
    payload.update({
        "selected_director_template_id": data["recommended_template_id"],
        "director_taskbook": data["director_taskbook"],
    })
    response = client.post("/api/confirm", json=payload)

    assert response.status_code == 200
    stored = json.loads((project / "confirm_ui" / "result.json").read_text(encoding="utf-8"))
    assert stored["global_visual_contract"] == visual_contract(payload)
    assert stored["confirmed_pages"] == composition["pages"]
    assert all("source_preview" not in page for page in stored["confirmed_pages"])
    assert client.post("/api/confirm", json=payload).status_code == 409


def test_server_rejects_hidden_role_change_payload(tmp_path: Path):
    project = make_v6_project(tmp_path, page_count=1)
    proposed = write_composition(project, roles=["content"])
    write_preconfirmation_files(project, page_count=1)
    submitted = dict(proposed["pages"][0])
    submitted["role_source"] = "explicit"
    payload = valid_contract(revision=0)
    payload["confirmed_pages"] = [submitted]

    response = load_server().create_app(project).test_client().post("/api/confirm", json=payload)

    assert response.status_code == 400, response.get_json()
    frozen = json.loads((project / "02_v6/page_composition.json").read_text(encoding="utf-8"))
    assert frozen["pages"][0]["page_role"] == "content"
    assert frozen["pages"][0]["role_source"] == "automatic"


@pytest.mark.parametrize(
    ("page_role", "role_source"),
    [("section", "automatic"), ("content", "synthesized")],
)
def test_server_rejects_invalid_role_provenance(
    tmp_path: Path, page_role: str, role_source: str,
):
    project = make_v6_project(tmp_path, page_count=1)
    proposed = write_composition(project, roles=["content"])
    write_preconfirmation_files(project, page_count=1)
    submitted = dict(proposed["pages"][0])
    submitted["page_role"] = page_role
    submitted["role_source"] = role_source
    submitted["chapter_title"] = "Section" if page_role == "section" else ""
    payload = valid_contract(revision=0)
    payload["confirmed_pages"] = [submitted]

    response = load_server().create_app(project).test_client().post("/api/confirm", json=payload)

    assert response.status_code == 400, response.get_json()
    assert not (project / "confirm_ui/result.json").exists()


def test_v6_final_submission_transactionally_migrates_all_page_authority(tmp_path: Path, monkeypatch):
    project = make_v6_project(tmp_path, page_count=3)
    proposed = write_composition(project, roles=["cover", "content", "closing"])
    write_preconfirmation_files(project, page_count=3)
    reordered = [dict(proposed["pages"][2]), dict(proposed["pages"][1])]
    for number, page in enumerate(reordered, start=1):
        page["output_page_number"] = number
    payload = valid_contract(revision=0)
    payload["confirmed_pages"] = reordered
    server = load_server()
    original_replace = server._transaction_replace
    replaced = []

    def record_replace(source, target):
        replaced.append(target.relative_to(project).as_posix())
        return original_replace(source, target)

    monkeypatch.setattr(server, "_transaction_replace", record_replace)

    result = server._v6_final_submission(project, visual_contract(payload), payload)

    state = json.loads((project / "workflow_v6.json").read_text(encoding="utf-8"))
    frozen = json.loads((project / "02_v6/page_composition.json").read_text(encoding="utf-8"))
    assert [page["page_number"] for page in state["pages"]] == [1, 2]
    assert [page["title"] for page in state["pages"]] == ["Page 3", "Page 2"]
    assert [page["source_page_id"] for page in frozen["pages"]] == [3, 2]
    assert result["confirmed_pages"] == frozen["pages"]
    assert replaced[-1] == "confirm_ui/result.json"
    for directory_name in (
        "page_sources", "effective_pages", "page_materials", "reference_materials"
    ):
        directory = project / "02_v6" / directory_name
        assert (directory / "page_001.json").is_file()
        assert (directory / "page_002.json").is_file()
        assert not (directory / "page_003.json").exists()
        assert (directory / "page_999.json").read_bytes() == b"unrelated-sentinel"
        assert json.loads((directory / "page_001.json").read_text(encoding="utf-8"))["page_number"] == 1
    source = json.loads((project / "02_v6/page_sources/page_001.json").read_text(encoding="utf-8"))
    from workflow_v6_source import _load_reference_materials
    _materials, receipt = _load_reference_materials(project, 1)
    assert source["word_original"] == "Source text 3"
    assert receipt["reference_acquisitions"][0]["status"] == "failed_no_retry"
    assert receipt["reference_acquisitions"][0]["request_id"] == "request-3"
    assert json.loads((project / "02_v6/reference_materials/page_002.json").read_text(encoding="utf-8"))["reference_acquisitions"][0]["status"] == "confirmed"
    assert not (project / "02_v6/.confirm_composition_transaction").exists()


def test_same_toc_block_synthesized_sections_cannot_be_reordered_by_confirmation(tmp_path: Path):
    project, proposed = same_toc_synthesized_project(tmp_path)
    sections = [page for page in proposed["pages"] if page["page_role"] == "section"]
    assert len(sections) == 2
    reordered = [
        *[page for page in proposed["pages"] if page["page_role"] == "toc"],
        sections[1], sections[0],
        *[page for page in proposed["pages"] if page["page_role"] == "content"],
    ]
    for number, page in enumerate(reordered, start=1):
        page["output_page_number"] = number
        if page["page_role"] == "section":
            page["fixed_page_title"] = f"确认章节 {number}"
    payload = valid_contract(revision=0)
    payload["confirmed_pages"] = reordered

    response = load_server().create_app(project).test_client().post("/api/confirm", json=payload)

    assert response.status_code == 400, response.get_json()
    frozen = json.loads((project / "02_v6/page_composition.json").read_text(encoding="utf-8"))
    assert [page.get("composition_page_id") for page in frozen["pages"] if page["page_role"] == "section"] == [
        sections[0]["composition_page_id"], sections[1]["composition_page_id"],
    ]


@pytest.mark.parametrize("attack", [
    "copied_id", "forged_source_page_id", "forged_source_page_number",
    "forged_blocks", "duplicate_id",
])
def test_synthesized_identity_tampering_is_rejected_before_seal(tmp_path: Path, attack: str):
    project, proposed = same_toc_synthesized_project(tmp_path)
    pages = [dict(page) for page in proposed["pages"]]
    sections = [page for page in pages if page["page_role"] == "section"]
    content = next(page for page in pages if page["page_role"] == "content")
    if attack == "copied_id":
        pages.remove(sections[0])
        content["composition_page_id"] = sections[0]["composition_page_id"]
    elif attack == "forged_source_page_id":
        sections[0]["source_page_id"] = 999
    elif attack == "forged_source_page_number":
        sections[0]["source_page_number"] = 999
    elif attack == "forged_blocks":
        sections[0]["material_source_block_ids"] = ["forged-block"]
    else:
        sections[1]["composition_page_id"] = sections[0]["composition_page_id"]
    for number, page in enumerate(pages, start=1):
        page["output_page_number"] = number
    payload = valid_contract(revision=0)
    payload["confirmed_pages"] = pages

    response = load_server().create_app(project).test_client().post("/api/confirm", json=payload)

    assert response.status_code == 400, response.get_json()
    assert not (project / "confirm_ui/result.json").exists()


@pytest.mark.parametrize(
    "corruption", ["missing", "invalid_json", "noncontinuous", "identity_mismatch"],
)
def test_current_confirmation_requires_valid_word_source_authority_before_seal(
    tmp_path: Path, corruption: str,
):
    project = make_v6_project(tmp_path, page_count=2)
    proposed = write_composition(project, roles=["cover", "content"])
    write_preconfirmation_files(project, page_count=2)
    source = project / "02_v6/paginated_word_source.json"
    if corruption == "missing":
        source.unlink()
    elif corruption == "invalid_json":
        source.write_text("{invalid", encoding="utf-8")
    elif corruption == "noncontinuous":
        value = json.loads(source.read_text(encoding="utf-8"))
        value["pages"][1]["page_number"] = 3
        source.write_text(json.dumps(value), encoding="utf-8")
    else:
        value = json.loads(source.read_text(encoding="utf-8"))
        value["pages"][1]["source_page_id"] = 999
        source.write_text(json.dumps(value), encoding="utf-8")
    payload = valid_contract(revision=0)
    payload["confirmed_pages"] = proposed["pages"]

    response = load_server().create_app(project).test_client().post("/api/confirm", json=payload)

    assert response.status_code == 400, response.get_json()
    assert not (project / "confirm_ui/result.json").exists()


def test_current_confirmation_rejects_reparse_word_source_authority_before_seal(tmp_path: Path):
    project = make_v6_project(tmp_path, page_count=1)
    proposed = write_composition(project, roles=["content"])
    write_preconfirmation_files(project, page_count=1)
    source = project / "02_v6/paginated_word_source.json"
    outside = tmp_path / "outside-source.json"
    source.replace(outside)
    try:
        source.symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks are unavailable")
    payload = valid_contract(revision=0)
    payload["confirmed_pages"] = proposed["pages"]

    response = load_server().create_app(project).test_client().post("/api/confirm", json=payload)

    assert response.status_code == 400, response.get_json()
    assert outside.is_file()
    assert not (project / "confirm_ui/result.json").exists()


def test_current_confirmation_rejects_aliased_word_source_authority_before_seal(tmp_path: Path):
    project = make_v6_project(tmp_path, page_count=1)
    proposed = write_composition(project, roles=["content"])
    write_preconfirmation_files(project, page_count=1)
    source = project / "02_v6/paginated_word_source.json"
    outside = tmp_path / "outside-source-hardlink.json"
    source.replace(outside)
    os.link(outside, source)
    payload = valid_contract(revision=0)
    payload["confirmed_pages"] = proposed["pages"]

    response = load_server().create_app(project).test_client().post("/api/confirm", json=payload)

    assert response.status_code == 400, response.get_json()
    assert outside.is_file()
    assert not (project / "confirm_ui/result.json").exists()


def test_v6_transaction_write_failure_preserves_all_authority_bytes(tmp_path: Path, monkeypatch):
    project = make_v6_project(tmp_path, page_count=3)
    proposed = write_composition(project, roles=["cover", "content", "closing"])
    write_preconfirmation_files(project, page_count=3)
    before = authority_bytes(project)
    payload = valid_contract(revision=0)
    payload["confirmed_pages"] = proposed["pages"]
    server = load_server()
    original = server._transaction_write_bytes
    calls = 0

    def fail_write(path, content):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("injected staging write failure")
        return original(path, content)

    monkeypatch.setattr(server, "_transaction_write_bytes", fail_write)
    with pytest.raises(OSError, match="injected"):
        server._v6_final_submission(project, visual_contract(payload), payload)

    assert authority_bytes(project) == before
    assert not (project / "confirm_ui/result.json").exists()


def test_v6_transaction_replace_failure_rolls_back_and_retry_succeeds(tmp_path: Path, monkeypatch):
    project = make_v6_project(tmp_path, page_count=3)
    proposed = write_composition(project, roles=["cover", "content", "closing"])
    write_preconfirmation_files(project, page_count=3)
    before = authority_bytes(project)
    payload = valid_contract(revision=0)
    payload["confirmed_pages"] = proposed["pages"]
    server = load_server()
    original = server._transaction_replace
    calls = 0

    def fail_replace(source, target):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("injected replace failure")
        return original(source, target)

    monkeypatch.setattr(server, "_transaction_replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        server._v6_final_submission(project, visual_contract(payload), payload)

    assert authority_bytes(project) == before
    assert not (project / "confirm_ui/result.json").exists()
    monkeypatch.setattr(server, "_transaction_replace", original)
    assert server._v6_final_submission(project, visual_contract(payload), payload)["status"] == "confirmed"


def test_v6_first_preparing_manifest_write_interruption_leaves_no_formal_transaction_and_recovers(
    tmp_path: Path, monkeypatch
):
    project = make_v6_project(tmp_path, page_count=1)
    proposed = write_composition(project, roles=["content"])
    write_preconfirmation_files(project, page_count=1)
    payload = valid_contract(revision=0)
    payload["confirmed_pages"] = proposed["pages"]
    server = load_server()
    original = server._transaction_write_bytes
    calls = 0

    def interrupt_first_write(path, content):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise KeyboardInterrupt("simulated interruption before preparing manifest")
        return original(path, content)

    monkeypatch.setattr(server, "_transaction_write_bytes", interrupt_first_write)
    with pytest.raises(KeyboardInterrupt):
        server._v6_final_submission(project, visual_contract(payload), payload)
    assert not (project / "02_v6/.confirm_composition_transaction").exists()
    assert (project / "02_v6/.confirm_composition_transaction.preparing").is_dir()

    monkeypatch.setattr(server, "_transaction_write_bytes", original)
    assert server._v6_final_submission(project, visual_contract(payload), payload)["status"] == "confirmed"
    assert not (project / "02_v6/.confirm_composition_transaction").exists()
    assert not (project / "02_v6/.confirm_composition_transaction.preparing").exists()


def test_v6_leftover_literal_preparing_directory_is_cleaned_before_submission(tmp_path: Path):
    project = make_v6_project(tmp_path, page_count=1)
    proposed = write_composition(project, roles=["content"])
    write_preconfirmation_files(project, page_count=1)
    preparing = project / "02_v6/.confirm_composition_transaction.preparing"
    preparing.mkdir()
    (preparing / "partial.tmp").write_text("incomplete", encoding="utf-8")
    payload = valid_contract(revision=0)
    payload["confirmed_pages"] = proposed["pages"]

    assert load_server()._v6_final_submission(
        project, visual_contract(payload), payload
    )["status"] == "confirmed"

    assert not preparing.exists()
    assert not (project / "02_v6/.confirm_composition_transaction").exists()


def test_v6_preparing_directory_reparse_is_rejected_without_deleting_external_path(tmp_path: Path):
    project = make_v6_project(tmp_path, page_count=1)
    proposed = write_composition(project, roles=["content"])
    outside = tmp_path / "outside-preparing-transaction"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    link = project / "02_v6/.confirm_composition_transaction.preparing"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(f"junction creation unavailable: {completed.stderr}")
    payload = valid_contract(revision=0)
    payload["confirmed_pages"] = proposed["pages"]

    with pytest.raises(ValueError, match="literal project directory|reparse"):
        load_server()._v6_final_submission(project, visual_contract(payload), payload)

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert link.exists()
    assert not (project / "02_v6/.confirm_composition_transaction").exists()


def test_v6_interrupted_commit_is_recovered_by_next_submission(tmp_path: Path, monkeypatch):
    project = make_v6_project(tmp_path, page_count=3)
    proposed = write_composition(project, roles=["cover", "content", "closing"])
    write_preconfirmation_files(project, page_count=3)
    payload = valid_contract(revision=0)
    payload["confirmed_pages"] = proposed["pages"]
    server = load_server()
    original = server._transaction_replace
    calls = 0

    def interrupt_replace(source, target):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise KeyboardInterrupt("simulated process interruption")
        return original(source, target)

    monkeypatch.setattr(server, "_transaction_replace", interrupt_replace)
    with pytest.raises(KeyboardInterrupt):
        server._v6_final_submission(project, visual_contract(payload), payload)
    assert (project / "02_v6/.confirm_composition_transaction/manifest.json").is_file()

    monkeypatch.setattr(server, "_transaction_replace", original)
    assert server._v6_final_submission(project, visual_contract(payload), payload)["status"] == "confirmed"
    assert not (project / "02_v6/.confirm_composition_transaction").exists()


def test_v6_preparing_transaction_reparse_is_rejected_without_deleting_external_path(tmp_path: Path):
    project = make_v6_project(tmp_path, page_count=1)
    proposed = write_composition(project, roles=["content"])
    outside = tmp_path / "outside-transaction"
    outside.mkdir()
    (outside / "manifest.json").write_text(
        json.dumps({"version": 1, "phase": "preparing", "targets": []}), encoding="utf-8"
    )
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    link = project / "02_v6/.confirm_composition_transaction"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(f"junction creation unavailable: {completed.stderr}")
    payload = valid_contract(revision=0)
    payload["confirmed_pages"] = proposed["pages"]

    with pytest.raises(ValueError, match="literal project directory|reparse"):
        load_server()._v6_final_submission(project, visual_contract(payload), payload)

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert link.exists()


def test_v6_committed_manifest_continues_same_submission_after_cleanup_interruption(tmp_path: Path, monkeypatch):
    project = make_v6_project(tmp_path, page_count=1)
    proposed = write_composition(project, roles=["content"])
    write_preconfirmation_files(project, page_count=1)
    payload = valid_contract(revision=0)
    payload["confirmed_pages"] = proposed["pages"]
    server = load_server()
    original = server._remove_transaction

    def interrupt_cleanup(_project):
        raise KeyboardInterrupt("simulated cleanup interruption")

    monkeypatch.setattr(server, "_remove_transaction", interrupt_cleanup)
    with pytest.raises(KeyboardInterrupt):
        server._v6_final_submission(project, visual_contract(payload), payload)
    manifest = json.loads(
        (project / "02_v6/.confirm_composition_transaction/manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["phase"] == "committed"

    monkeypatch.setattr(server, "_remove_transaction", original)
    assert server._v6_final_submission(project, visual_contract(payload), payload)["status"] == "confirmed"
    assert not (project / "02_v6/.confirm_composition_transaction").exists()


def test_v6_rejects_reparse_page_authority_directory_outside_project(tmp_path: Path):
    project = make_v6_project(tmp_path, page_count=1)
    proposed = write_composition(project, roles=["content"])
    write_preconfirmation_files(project, page_count=1)
    outside = tmp_path / "outside-page-sources"
    outside.mkdir()
    (outside / "page_001.json").write_text(
        json.dumps({"page_number": 1, "word_original": "outside"}), encoding="utf-8"
    )
    link = project / "02_v6/page_sources"
    shutil.rmtree(link)
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(f"junction creation unavailable: {completed.stderr}")
    payload = valid_contract(revision=0)
    payload["confirmed_pages"] = proposed["pages"]

    with pytest.raises(ValueError, match="literal project directory|reparse"):
        load_server()._v6_final_submission(project, visual_contract(payload), payload)

    assert (outside / "page_001.json").read_text(encoding="utf-8")
    assert not (project / "confirm_ui/result.json").exists()


def test_server_templates_are_ephemeral_defaults_and_confirmation_is_exact(tmp_path: Path):
    project = make_project(tmp_path, {
        "recommended_template_id": "investment-committee",
        "recommendation_reason": "材料重点涉及估值与投资回报。",
        "recommendation_confidence": "high",
        "director_taskbook": valid_taskbook(),
    })
    server = load_server()
    client = server.create_app(str(project)).test_client()

    response = client.get("/api/recommendations")
    assert response.status_code == 200
    recommendation = response.get_json()
    assert recommendation["step_count"] == 3
    assert recommendation["recommended_template_id"] == "investment-committee"
    assert recommendation["recommendation_reason"] == "材料重点涉及估值与投资回报。"
    assert recommendation["recommendation_confidence"] == "high"
    assert recommendation["director_taskbook"] == valid_taskbook()
    assert [item["id"] for item in recommendation["templates"]] == EXPECTED_DIRECTOR_TEMPLATE_IDS
    for template in recommendation["templates"]:
        assert set(template["defaults"]) == VISUAL_FIELDS
    corporate = next(item for item in recommendation["templates"] if item["id"] == "corporate-planning")
    assert corporate["defaults"] == {
        "primary_color": "#17212B",
        "secondary_color": "#176B67",
        "highlight_color": "#D3A62C",
        "background_color": "#F7F6F2",
        "cjk_font": "Microsoft YaHei",
        "latin_font": "Arial",
        "title_size_pt": 30,
        "body_size_pt": 14,
        "caption_size_pt": 9,
    }
    catalogs = json.loads((STATIC_DIR / "catalogs.json").read_text(encoding="utf-8"))
    static_corporate = next(item for item in catalogs["template_presets"] if item["id"] == "corporate-planning")
    assert static_corporate["defaults"] == corporate["defaults"]

    payload = valid_contract()
    response = client.post("/api/confirm", json=payload)
    assert response.status_code == 200
    stored = json.loads((project / "confirm_ui" / "result.json").read_text(encoding="utf-8"))
    assert stored == payload
    assert set(stored) == REQUIRED_FIELDS
    assert not FORBIDDEN_FIELDS.intersection(stored)


def test_server_exposes_only_the_simplified_ui_and_lifecycle_routes(tmp_path: Path):
    rules = {rule.rule for rule in load_server().create_app(str(tmp_path)).url_map.iter_rules()}
    assert rules == {
        "/",
        "/static/<path:filename>",
        "/api/health",
        "/api/session",
        "/api/recommendations",
        "/api/confirm",
        "/api/shutdown",
    }


def test_existing_recommendation_direction_selects_only_an_ephemeral_template(tmp_path: Path):
    project = make_project(tmp_path, {"recommend": {"direction": 2}})
    client = load_server().create_app(str(project)).test_client()
    response = client.get("/api/recommendations")
    assert response.status_code == 200
    assert response.get_json()["recommended_template_id"] == "project-initiation"


def test_concurrent_same_revision_submissions_publish_only_one_contract(tmp_path: Path):
    project = make_project(tmp_path)
    server = load_server()

    def submit(index: int) -> int:
        payload = valid_contract()
        payload["submission_id"] = f"submission-{index:04d}"
        return server.create_app(str(project)).test_client().post("/api/confirm", json=payload).status_code

    with ThreadPoolExecutor(max_workers=2) as workers:
        statuses = list(workers.map(submit, (1, 2)))
    assert sorted(statuses) == [200, 409]
    stored = json.loads((project / "confirm_ui" / "result.json").read_text(encoding="utf-8"))
    assert stored["submission_id"] in {"submission-0001", "submission-0002"}
    assert set(stored) == REQUIRED_FIELDS


def test_first_final_post_is_immutable_even_before_wait_seals_workflow_state(tmp_path: Path):
    project = make_project(tmp_path)
    client = load_server().create_app(str(project)).test_client()
    assert client.post("/api/confirm", json=valid_contract()).status_code == 200
    result_path = project / "confirm_ui" / "result.json"
    result_before = result_path.read_bytes()
    replacement = valid_contract(revision=2)
    replacement["submission_id"] = "submission-0002"
    replacement["primary_color"] = "#000000"

    response = client.post("/api/confirm", json=replacement)

    assert response.status_code == 409
    assert "final" in response.get_json()["error"]
    assert result_path.read_bytes() == result_before


@pytest.mark.parametrize("forbidden", sorted(FORBIDDEN_FIELDS))
def test_server_rejects_forbidden_authority_and_stale_revisions(tmp_path: Path, forbidden: str):
    project = make_project(tmp_path)
    client = load_server().create_app(str(project)).test_client()
    payload = valid_contract()
    payload[forbidden] = "forbidden"
    assert client.post("/api/confirm", json=payload).status_code == 400
    assert not (project / "confirm_ui" / "result.json").exists()

    assert client.post("/api/confirm", json=valid_contract()).status_code == 200
    assert client.post("/api/confirm", json=valid_contract()).status_code == 409


def test_exact_visual_contract_is_recognized_as_final_for_wait_lifecycle(tmp_path: Path):
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(valid_contract()), encoding="utf-8")
    assert load_server()._confirmed_stage(result_path) == 4


def test_wait_seals_only_visual_fields_into_v6_state(tmp_path: Path):
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from workflow_v6_contract import new_page, new_project
    from workflow_v6_state import create, load

    project = tmp_path / "v6-project"
    create(
        project,
        new_project(
            word_source={"path": "00_source/source.docx", "sha256": "a" * 64},
            logo_source={"path": "00_source/logo.svg", "sha256": "b" * 64},
            pages=[new_page(1, title="Title")],
        ),
    )
    confirm_dir = project / "confirm_ui"
    confirm_dir.mkdir()
    payload = valid_contract()
    (confirm_dir / "result.json").write_text(json.dumps(payload), encoding="utf-8")

    geometry_before = load(project)["geometry"]
    assert load_server()._wait(project, "final", 1) == 0
    state = load(project)
    assert state["confirmed_ui_revision"] == 1
    assert state["confirmed_ui_digest"]
    assert state["page_materials_status"] == "pending"
    assert state["geometry"] == geometry_before
    assert state["style_confirmation"] == {
        "status": "confirmed",
        "contract": {field: payload[field] for field in VISUAL_FIELDS if field in payload},
    }


def test_repeat_final_wait_preserves_confirmed_materials_and_all_authority_bytes(tmp_path: Path):
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from workflow_v6_state import load, save

    project = make_awesome_project(tmp_path, "repeat-wait-project")
    result_path = project / "confirm_ui" / "result.json"
    result_path.write_text(json.dumps(valid_contract()), encoding="utf-8")
    server = load_server()
    assert server._wait(project, "final", 1) == 0
    state = load(project)
    from awesome_page_materials import publish_page_materials

    source_dir = project / "02_v6"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "paginated_word_source.json").write_text(json.dumps({
        "pages": [{
            "page_number": 1,
            "fixed_page_title": "Title",
            "fixed_page_title_source_block_id": "title",
            "blocks": [{"type": "paragraph", "text": "Title", "source_block_id": "title", "source_block_index": 0, "source_order": 1, "relationship_ids": [], "comment_ids": []}],
            "page_comments": [],
        }]
    }), encoding="utf-8")
    (source_dir / "source_assets.json").write_text(json.dumps({"assets": []}), encoding="utf-8")
    publish_page_materials(project, 1, project / "02_v6/awesome_page_materials/page_001.json")
    state_path = project / "workflow_v6.json"
    result_before = result_path.read_bytes()
    state_before = state_path.read_bytes()

    assert server._wait(project, "final", 1) == 0

    assert result_path.read_bytes() == result_before
    assert state_path.read_bytes() == state_before
    assert load(project)["page_materials_status"] == "confirmed"


def test_real_start_health_and_shutdown_accept_valid_awesome_project(tmp_path: Path):
    project = make_awesome_project(tmp_path, "start-project")
    server = load_server()
    with socket.socket() as reservation:
        reservation.bind((server.DEFAULT_HOST, 0))
        port = reservation.getsockname()[1]
    try:
        assert server._start(project, port, True, 60) == 0
        assert server._probe_health(port, project=project)
    finally:
        assert server._shutdown(project) == 0


def test_health_and_start_reject_incompatible_legacy_project_with_new_project_guidance(tmp_path: Path):
    project = tmp_path / "legacy-project"
    confirm_dir = project / "confirm_ui"
    confirm_dir.mkdir(parents=True)
    (confirm_dir / "recommendations.json").write_text("{}", encoding="utf-8")
    (project / "workflow_run.json").write_text(
        json.dumps({"pagination": {"mode": "physical", "page_count": 1}}), encoding="utf-8"
    )
    server = load_server()
    response = server.create_app(str(project)).test_client().get("/api/health")
    assert response.status_code == 409
    assert "Create a new project from the original Word document, SVG logo, and attachments" in response.get_json()["error"]
    with socket.socket() as reservation:
        reservation.bind((server.DEFAULT_HOST, 0))
        port = reservation.getsockname()[1]
    assert server._start(project, port, True, 60) == 1
    assert not (project / server.LOCK_NAME).exists()


def test_sealed_submission_rejects_replacement_and_preserves_result_and_state_bytes(tmp_path: Path):
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from workflow_v6_contract import new_page, new_project
    from workflow_v6_state import create

    project = tmp_path / "sealed-project"
    create(
        project,
        new_project(
            word_source={"path": "00_source/source.docx", "sha256": "a" * 64},
            logo_source={"path": "00_source/logo.svg", "sha256": "b" * 64},
            pages=[new_page(1, title="Title")],
        ),
    )
    confirm_dir = project / "confirm_ui"
    confirm_dir.mkdir()
    composition = write_composition(project, roles=["content"])
    write_preconfirmation_files(project, page_count=1)
    client = load_server().create_app(str(project)).test_client()
    first = valid_contract(revision=0)
    first["confirmed_pages"] = composition["pages"]
    assert client.post("/api/confirm", json=first).status_code == 200
    assert load_server()._wait(project, "final", 1) == 0
    result_path = confirm_dir / "result.json"
    state_path = project / "workflow_v6.json"
    result_before = result_path.read_bytes()
    state_before = state_path.read_bytes()

    replacement = valid_contract(revision=0)
    replacement["submission_id"] = "submission-0002"
    replacement["primary_color"] = "#000000"
    replacement["confirmed_pages"] = composition["pages"]
    response = client.post("/api/confirm", json=replacement)

    assert response.status_code == 409
    assert "sealed" in response.get_json()["error"]
    assert result_path.read_bytes() == result_before
    assert state_path.read_bytes() == state_before


def test_visual_confirmation_leaves_generation_at_actionable_materials_boundary(tmp_path: Path):
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from workflow_v6_contract import new_page, new_project
    from workflow_v6_image import generate_page_body
    from workflow_v6_reconstruction import build_reconstruction_request, finalize_reconstructed_page
    from workflow_v6_state import create

    project = tmp_path / "boundary-project"
    create(
        project,
        new_project(
            word_source={"path": "00_source/source.docx", "sha256": "a" * 64},
            logo_source={"path": "00_source/logo.svg", "sha256": "b" * 64},
            pages=[new_page(1, title="Title")],
        ),
    )
    confirm_dir = project / "confirm_ui"
    confirm_dir.mkdir()
    (confirm_dir / "result.json").write_text(json.dumps(valid_contract()), encoding="utf-8")
    assert load_server()._wait(project, "final", 1) == 0

    with pytest.raises(ValueError, match="page materials are not prepared; run prepare-page-materials"):
        generate_page_body(project, page_number=1, timeout=1)
    with pytest.raises(ValueError, match="selected Image2 body before reconstruction"):
        build_reconstruction_request(project, page_number=1)
    with pytest.raises(ValueError, match="reconstructed body must be an existing PPTX"):
        finalize_reconstructed_page(project, page_number=1, reconstructed_body=tmp_path / "missing.pptx")


def test_cli_status_reports_visual_confirmed_materials_pending(tmp_path: Path):
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from workflow_v6_contract import new_page, new_project
    from workflow_v6_state import create

    project = tmp_path / "status-project"
    create(
        project,
        new_project(
            word_source={"path": "00_source/source.docx", "sha256": "a" * 64},
            logo_source={"path": "00_source/logo.svg", "sha256": "b" * 64},
            pages=[new_page(1, title="Title")],
        ),
    )
    confirm_dir = project / "confirm_ui"
    confirm_dir.mkdir()
    (confirm_dir / "result.json").write_text(json.dumps(valid_contract()), encoding="utf-8")
    assert load_server()._wait(project, "final", 1) == 0
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "workflow_v6_cli.py"), "status", "--project", str(project)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    status = json.loads(completed.stdout)
    assert status["style_status"] == "confirmed"
    assert status["page_materials_status"] == "pending"
    assert status["next_action"] == "prepare_page_materials"


def test_v6_cli_has_no_alternate_confirm_style_mutation_command():
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "workflow_v6_cli.py"), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "confirm-style" not in completed.stdout

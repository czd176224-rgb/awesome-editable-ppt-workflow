"""Contract tests for the Awesome three-step visual confirmation UI."""

from __future__ import annotations

import importlib.util
import json
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

VISUAL_FIELDS = {
    "primary_color",
    "secondary_color",
    "background_color",
    "cjk_font",
    "latin_font",
    "title_size_pt",
    "body_size_pt",
    "caption_size_pt",
    "regional_characteristics",
    "visual_description",
}
IDENTITY_FIELDS = {"submission_id", "revision"}
ALL_FIELDS = VISUAL_FIELDS | IDENTITY_FIELDS
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


def valid_contract(*, revision: int = 1) -> dict:
    return {
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
        "regional_characteristics": "",
        "visual_description": "Formal editorial presentation with restrained visual evidence.",
    }


def make_project(tmp_path: Path, recommendation: dict | None = None) -> Path:
    project = tmp_path / "project"
    confirm_dir = project / "confirm_ui"
    confirm_dir.mkdir(parents=True)
    if recommendation is not None:
        (confirm_dir / "recommendations.json").write_text(
            json.dumps(recommendation, ensure_ascii=False), encoding="utf-8"
        )
    return project


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


def test_confirmed_visual_contract_schema_allows_only_ten_visual_fields_and_identity():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["title"] == "ConfirmedVisualContractV1"
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == ALL_FIELDS
    assert set(schema["required"]) == ALL_FIELDS

    validator = Draft202012Validator(schema)
    assert list(validator.iter_errors(valid_contract())) == []
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


def test_static_document_has_exactly_three_steps_and_read_only_review():
    parser = _StepParser()
    parser.feed((STATIC_DIR / "index.html").read_text(encoding="utf-8"))
    assert [step["data-step"] for step in parser.steps] == ["1", "2", "3"]
    assert parser.steps[2].get("data-read-only") == "true"


def test_browser_state_template_defaults_editing_review_and_back_navigation():
    script = r"""
const assert = require('assert');
const ui = require(process.argv[1]);
const templates = [
  {id: 'calm', defaults: {
    primary_color:'#17365D', secondary_color:'#C7352B', background_color:'#FFFFFF',
    cjk_font:'Microsoft YaHei', latin_font:'Arial', title_size_pt:28, body_size_pt:12,
    caption_size_pt:9, regional_characteristics:'', visual_description:'Calm editorial.'}},
  {id: 'bold', defaults: {
    primary_color:'#111111', secondary_color:'#FF2C00', background_color:'#F1F0EE',
    cjk_font:'Source Han Sans SC', latin_font:'Aptos', title_size_pt:34, body_size_pt:15,
    caption_size_pt:10, regional_characteristics:'East Asian grid rhythm',
    visual_description:'Bold evidence-led presentation.'}}
];
let state = ui.createState(templates, 'calm', 0);
assert.strictEqual(state.step, 1);
state = ui.applyTemplate(state, 'bold');
assert.strictEqual(state.values.primary_color, '#111111');
assert.strictEqual(state.values.cjk_font, 'Source Han Sans SC');
state = ui.goNext(state);
assert.strictEqual(state.step, 2);
const edits = {
  primary_color:'#222222', secondary_color:'#CC3300', background_color:'#FAFAFA',
  cjk_font:'Noto Sans CJK SC', latin_font:'Georgia', title_size_pt:32, body_size_pt:14,
  caption_size_pt:10, regional_characteristics:'Jiangnan restraint',
  visual_description:'Quiet, precise, evidence-led visual system.'
};
for (const [field, value] of Object.entries(edits)) state = ui.updateField(state, field, value);
assert.deepStrictEqual(state.values, edits);
state = ui.goNext(state);
assert.strictEqual(state.step, 3);
assert.strictEqual(ui.isEditable(state), false);
assert.throws(() => ui.updateField(state, 'primary_color', '#000000'), /read-only/);
state = ui.goBack(state);
assert.strictEqual(state.step, 2);
state = ui.goNext(state);
const payload = ui.buildSubmission(state, 'submission-0001');
assert.deepStrictEqual(new Set(Object.keys(payload)), new Set([...ui.VISUAL_FIELDS, 'submission_id', 'revision']));
assert.strictEqual(payload.revision, 1);
for (const forbidden of ['template_id','template_selection','page_materials','confirmed_pages']) {
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


def test_server_templates_are_ephemeral_defaults_and_confirmation_is_exact(tmp_path: Path):
    project = make_project(tmp_path, {"recommended_template_id": "evidence-investment"})
    server = load_server()
    client = server.create_app(str(project)).test_client()

    response = client.get("/api/recommendations")
    assert response.status_code == 200
    recommendation = response.get_json()
    assert recommendation["step_count"] == 3
    assert recommendation["recommended_template_id"] == "evidence-investment"
    assert len(recommendation["templates"]) == 3
    for template in recommendation["templates"]:
        assert set(template["defaults"]) == VISUAL_FIELDS

    payload = valid_contract()
    response = client.post("/api/confirm", json=payload)
    assert response.status_code == 200
    stored = json.loads((project / "confirm_ui" / "result.json").read_text(encoding="utf-8"))
    assert stored == payload
    assert set(stored) == ALL_FIELDS
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
    assert response.get_json()["recommended_template_id"] == "evidence-investment"


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
    assert set(stored) == ALL_FIELDS


def test_first_final_post_is_immutable_even_before_wait_seals_workflow_state(tmp_path: Path):
    project = make_project(tmp_path)
    client = load_server().create_app(str(project)).test_client()
    assert client.post("/api/confirm", json=valid_contract()).status_code == 200
    result_path = project / "confirm_ui" / "result.json"
    result_before = result_path.read_bytes()
    replacement = valid_contract(revision=2)
    replacement["submission_id"] = "submission-0002"
    replacement["visual_description"] = "Replacement must be rejected."

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
        "contract": {field: payload[field] for field in VISUAL_FIELDS},
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
    client = load_server().create_app(str(project)).test_client()
    assert client.post("/api/confirm", json=valid_contract()).status_code == 200
    assert load_server()._wait(project, "final", 1) == 0
    result_path = confirm_dir / "result.json"
    state_path = project / "workflow_v6.json"
    result_before = result_path.read_bytes()
    state_before = state_path.read_bytes()

    replacement = valid_contract(revision=2)
    replacement["submission_id"] = "submission-0002"
    replacement["visual_description"] = "Unauthorized replacement."
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

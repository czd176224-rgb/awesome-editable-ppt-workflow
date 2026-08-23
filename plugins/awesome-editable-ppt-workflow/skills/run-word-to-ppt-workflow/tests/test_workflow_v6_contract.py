from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v6_contract import (  # noqa: E402
    IMAGE_POLICY,
    WORKFLOW_VERSION,
    new_page,
    new_project,
    transition_page,
    validate_project,
)
from workflow_v6_state import create, load, save  # noqa: E402


def _project():
    return new_project(
        word_source={"path": "00_source/source.docx", "sha256": "a" * 64},
        logo_source={"path": "00_source/logo.svg", "sha256": "b" * 64},
        pages=[new_page(1, title="第一页"), new_page(2, title="第二页")],
    )


def test_v6_contract_is_adaptive_and_uses_fixed_geometry():
    project = _project()
    assert project["workflow_contract_version"] == WORKFLOW_VERSION
    assert project["image_policy"] == IMAGE_POLICY
    assert IMAGE_POLICY == "generate-without-refs-edit-with-confirmed-refs"
    assert project["geometry"]["slide_aspect"] == "16:9"
    assert project["geometry"]["body_aspect"] == "17:8"
    assert project["geometry"]["body_pixels"] == {"width": 1904, "height": 896}
    assert project["director_confirmation"] is None


def test_v6_director_confirmation_requires_server_digest():
    project = _project()
    taskbook = {
        "use_scenario": "投决会审议",
        "presenter": "项目投资团队",
        "primary_audience": "投资决策委员会",
        "audience_prior_knowledge": "已了解项目基本情况",
        "desired_outcome": "决定是否投资",
        "emphasis": "估值、回报与风险",
        "deemphasis": "重复背景",
    }
    from director_taskbook import taskbook_digest

    project["director_confirmation"] = {
        "template_id": "investment-committee",
        "template_version": "1.0",
        "taskbook": taskbook,
        "taskbook_digest": taskbook_digest(taskbook),
    }
    validate_project(project)

    project["director_confirmation"]["taskbook_digest"] = "0" * 64
    with pytest.raises(ValueError, match="taskbook digest"):
        validate_project(project)


def test_active_v6_entrypoints_do_not_claim_generate_only():
    active = [
        ROOT / "scripts" / "word_to_editable_ppt.py",
        ROOT / "scripts" / "workflow_v6_cli.py",
        ROOT / "scripts" / "workflow_v6_contract.py",
    ]
    for path in active:
        text = path.read_text(encoding="utf-8").lower()
        assert "generate-only" not in text
        assert "generate_only" not in text


def test_v6_rejects_legacy_fields():
    project = _project()
    project["workflow_run"] = {"version": "v5"}
    with pytest.raises(ValueError, match="project fields"):
        validate_project(project)


def test_material_unavailability_is_not_a_blocking_page_state():
    page = new_page(1, title="第一页")
    page["material_state"] = "unavailable"
    validate_project(new_project(
        word_source={"path": "source.docx"},
        logo_source={"path": "logo.svg"},
        pages=[page],
    ))
    assert "material_blocked" not in {page["state"]}


def test_qa_cannot_force_first_candidate_into_reconstruction():
    page = transition_page(new_page(1, title="第一页"), "generating")
    page = transition_page(page, "qa_review")
    page["first_candidate"] = {"path": "04_v6/images/page_001.first.png"}
    page["selected_candidate"] = copy.deepcopy(page["first_candidate"])
    page["degraded_reasons"] = ["qa_no_effective_improvement"]
    with pytest.raises(ValueError, match="invalid V6 page transition"):
        transition_page(page, "accepted_fallback_first")


def test_v6_state_round_trips_utf8_atomically(tmp_path: Path):
    project = _project()
    create(tmp_path, project)
    assert load(tmp_path) == project
    project["style_confirmation"] = {
        "status": "confirmed",
        "contract": {"name": "中文风格"},
    }
    save(tmp_path, project)
    assert load(tmp_path)["style_confirmation"]["contract"]["name"] == "中文风格"


def test_v6_requires_contiguous_word_page_order():
    with pytest.raises(ValueError, match="contiguous"):
        new_project(
            word_source={"path": "source.docx"},
            logo_source={"path": "logo.svg"},
            pages=[new_page(2, title="第二页")],
        )


def test_confirmed_v6_project_rejects_non_hex_ui_digest():
    project = _project()
    project.update({
        "confirmed_ui_revision": 1,
        "confirmed_ui_digest": "z" * 64,
        "page_materials_status": "confirmed",
    })

    with pytest.raises(ValueError, match="UI digest"):
        validate_project(project)

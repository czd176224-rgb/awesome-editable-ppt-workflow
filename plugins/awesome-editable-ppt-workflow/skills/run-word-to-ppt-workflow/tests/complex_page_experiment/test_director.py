from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from codex_subscription_runtime import CodexStructuredResult
from complex_page_experiment.director import _render_complete_source_block
from complex_page_experiment.director import (
    DirectorArtifact,
    _resolve_director_numeric_authorities,
    _validate_director_value,
    direct_page,
)
from workflow_v6_materials import index_source_numeric_candidates
from complex_page_experiment.materials import (
    CompletePageMaterialView,
    build_complete_page_material_view,
)
from complex_page_experiment.workspace import ExperimentWorkspace
from conftest import awesome_four_page_project as awesome_four_page_project_fixture
from test_materials import _prepare_complete_page_one


VISUAL_DIRECTOR_REFERENCE = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "complex_page_experiment"
    / "references"
    / "visual_director.md"
)
DIRECTOR_SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "schemas"
    / "page_design_v1.schema.json"
)
TASKBOOK_VALUES = (
    "董事会追加投资审议",
    "黄石项目投资团队",
    "基金投资决策委员会",
    "已阅读项目初步尽调",
    "决定是否追加投资及先决条件",
    "现金流、估值、回报变化和新增风险",
    "重复的公司基础介绍",
)
TASKBOOK_BOUNDARY = (
    "This taskbook is a user-confirmed presentation constraint, not factual source material. "
    "It may guide only how the complete Word context informs the whole-deck page purpose, "
    "information hierarchy, and continuity. Lossless within-page rewording and layering are "
    "allowed; the taskbook cannot authorize new facts, omitted information, altered meaning, "
    "or moving content between pages."
)


_TEST_VIEWS: dict[Path, CompletePageMaterialView] = {}


def _top_level_prompt_sections(prompt: str) -> dict[str, str]:
    headings = (
        "页面设计方法", "已确认任务书", "已确认页面任务与章节衔接",
        "完整全文（用于理解章节、关系及衔接，不限于邻页；其他页详细内容仍留在各自页面）",
        "已确认本页编排（完整适用UI字段，含章节、页面角色和原文对应关系）",
        "完整页面原文、已确认视觉设置与材料", "本次图片输入顺序", "交付一份可直接执行的画面设计说明",
    )
    positions = [prompt.index("\n" + heading + "\n") + 1 for heading in headings]
    assert positions == sorted(positions)
    return {
        heading: prompt[positions[index] + len(heading):positions[index + 1] if index + 1 < len(headings) else len(prompt)].strip()
        for index, heading in enumerate(headings)
    }


def _captured_director_request(tmp_path: Path) -> tuple[dict[str, object], dict[str, str]]:
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    calls: list[dict[str, object]] = []

    class CapturedRequest(Exception):
        pass

    def invoke(project: Path, **kwargs):
        calls.append({"project": project, **kwargs})
        raise CapturedRequest

    with pytest.raises(CapturedRequest):
        direct_page(workspace, view, timeout=60, invoke=invoke)
    assert len(calls) == 1
    prompt = str(calls[0]["prompt"])
    return calls[0], _top_level_prompt_sections(prompt)


def test_director_output_schema_types_every_const_and_enum() -> None:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "schemas"
        / "page_design_v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    missing: list[str] = []

    def visit(value: object, path: str = "$") -> None:
        if isinstance(value, dict):
            if ("const" in value or "enum" in value) and "type" not in value:
                missing.append(path)
            for key, child in value.items():
                visit(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(schema)
    assert missing == []


def test_director_output_schema_uses_only_scalar_constants() -> None:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "schemas"
        / "page_design_v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    non_scalar: list[str] = []

    def visit(value: object, path: str = "$") -> None:
        if isinstance(value, dict):
            if "const" in value and isinstance(value["const"], (dict, list)):
                non_scalar.append(path)
            for key, child in value.items():
                visit(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(schema)
    assert non_scalar == []


def test_director_output_schema_avoids_unsupported_unique_items() -> None:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "schemas"
        / "page_design_v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    def contains_unique_items(value: object) -> bool:
        if isinstance(value, dict):
            return "uniqueItems" in value or any(
                contains_unique_items(child) for child in value.values()
            )
        if isinstance(value, list):
            return any(contains_unique_items(child) for child in value)
        return False

    assert not contains_unique_items(schema)
    serialized = json.dumps(schema)
    assert all(f'"{keyword}"' not in serialized for keyword in ("allOf", "if", "then"))


def _workspace(tmp_path: Path) -> ExperimentWorkspace:
    source = awesome_four_page_project_fixture.__wrapped__(tmp_path)
    _prepare_complete_page_one(source)
    _prepare_compact_page_facts(source)
    from complex_page_experiment import create_experiment_copy

    workspace = create_experiment_copy(
        source,
        tmp_path / "experiment-unit",
        experiment_id=f"director-{tmp_path.name}",
    )
    _TEST_VIEWS[workspace.project_copy] = build_complete_page_material_view(workspace)
    return workspace


def _material_view(workspace: ExperimentWorkspace) -> CompletePageMaterialView:
    return _TEST_VIEWS[workspace.project_copy]


def _prepare_compact_page_facts(source: Path) -> None:
    paginated_path = source / "02_v6" / "paginated_word_source.json"
    paginated = json.loads(paginated_path.read_text(encoding="utf-8"))
    page = paginated["pages"][0]
    body = page["blocks"][1]
    facts = [
        {
            **body,
            "text": text,
            "source_block_id": f"body-{index}",
            "source_block_index": index,
            "source_order": index + 1,
        }
        for index, text in enumerate(
            (
                "Authoritative body 1",
                "Resources enter the fund platform.",
                "Operations support the entry path.",
                "The source does not quantify flow volume.",
            ),
            start=1,
        )
    ]
    page["blocks"] = [page["blocks"][0], *facts]
    paginated_path.write_text(
        json.dumps(paginated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    material_path = source / "02_v6" / "awesome_page_materials" / "page_001.json"
    material = json.loads(material_path.read_text(encoding="utf-8"))
    material["complete_word_content"] = page["blocks"]
    payload = (
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    material_path.write_bytes(payload)
    state_path = source / "workflow_v6.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["pages"][0]["material_receipt"]["digest"] = hashlib.sha256(payload).hexdigest()
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _compact_material_view() -> CompletePageMaterialView:
    facts = tuple(
        {
            "type": "paragraph",
            "text": text,
            "source_block_id": f"body-{index}",
            "source_block_index": index,
            "source_order": index,
            "relationship_ids": [],
            "comment_ids": [],
        }
        for index, text in enumerate(
            (
                "Regional resources are available.",
                "Resources enter the fund platform.",
                "Operations support the entry path.",
                "The source does not quantify flow volume.",
            ),
            start=1,
        )
    )
    return CompletePageMaterialView(
        {
            "page_number": 5,
            "complete_word_content": list(facts),
            "visual_contract": {
                "background_color": "#F7F7F7",
                "primary_color": "#161616",
                "secondary_color": "#CD202A",
            },
        },
        (),
        (),
        "compact-test-view",
    )


def _director_value(
    view: CompletePageMaterialView | None = None,
) -> dict[str, object]:
    material_view = view or _compact_material_view()
    blocks = material_view.value.get("complete_word_content")
    assert isinstance(blocks, list) and blocks, "director fixture requires source facts"
    inventory = [
        {"source_block_id": str(block["source_block_id"]),
         "source_quote": _render_complete_source_block(block),
         "display_copy": _render_complete_source_block(block), "target": "body"}
        for block in blocks
    ]
    material_id = next((str(item["material_id"]) for item in material_view.value.get("materials", [])
                        if item.get("viewable_image")), None)
    return {
        "schema_version": "awesome-page-design-v1",
        "page_number": int(material_view.value.get("page_number", 5)),
        "quality": "high",
        "selected_references": [] if material_id is None else [{
            "material_id": material_id, "use": "Anchor the real source evidence.",
            "preserve": "Preserve recognizable identity.",
        }],
        "page_plan": {
            "content_inventory": inventory,
            "context_bridges": [],
            "image_prompt": "以清楚的总体关系组织页面，完整显示以下内容：\n" +
                            "\n".join(item["display_copy"] for item in inventory),
        },
    }


def _compact_artifact(value: dict[str, object]) -> DirectorArtifact:
    return DirectorArtifact(
        value=value,
        actual_prompt="",
        selected_reference_ids=(),
        quality="high",
        model="gpt-test-current",
        effort="high",
        duration_seconds=1.0,
        model_provider="openai-test",
        usage={},
        runtime_trace={},
        thread_id="thread-compact",
        turn_id="turn-compact",
    )


def test_compact_director_value_exposes_only_the_page_plan_contract() -> None:
    value = _director_value()
    artifact = _compact_artifact(value)

    assert "creative_direction" not in value
    assert "prompt_sections" not in value
    assert "machine_record" not in value
    assert artifact.page_plan == value["page_plan"]


def test_director_selects_source_ids_then_freezes_resolved_numeric_authority() -> None:
    table = {
        "type": "table",
        "source_block_id": "metrics-table",
        "source_order": 1,
        "rows": [
            ["指标", "2024", "2025", "单位", "口径"],
            ["收入", "120", "150", "万元", "经审计"],
        ],
    }
    candidates = index_source_numeric_candidates([table])
    by_cell = {
        (item["row"], item["column"]): item["source_ref_id"]
        for item in candidates
    }
    view = CompletePageMaterialView(
        {
            "page_number": 5,
            "complete_word_content": [table],
            "numeric_source_candidates": candidates,
            "visual_contract": {
                "background_color": "#F7F7F7",
                "primary_color": "#161616",
                "secondary_color": "#CD202A",
            },
        },
        (), (), "numeric-view",
    )
    value = _director_value(view)
    value["page_plan"]["quantitative_exhibits"] = [{
        "chart_id": "chart:revenue",
        "rendering_primitive": "column_bar",
        "chart_variant": "column",
        "title_ref_id": by_cell[(1, 0)],
        "series_groups": [{
            "name_ref_id": by_cell[(1, 0)],
            "category_ref_ids": [by_cell[(0, 1)], by_cell[(0, 2)]],
            "value_ref_ids": [by_cell[(1, 1)], by_cell[(1, 2)]],
            "unit_ref_id": by_cell[(1, 3)],
            "basis_ref_id": by_cell[(1, 4)],
        }],
    }]

    resolved = _resolve_director_numeric_authorities(value, view)

    assert "numeric_authorities" not in value["page_plan"]
    assert resolved["page_plan"]["numeric_authorities"][0]["series"][0]["values"] == [120, 150]
    assert resolved["page_plan"]["image_prompt"] == value["page_plan"]["image_prompt"]
    _validate_director_value(resolved, view)
    forged = copy.deepcopy(resolved)
    forged["page_plan"]["numeric_authorities"][0]["series"][0]["values"][0] = 999
    with pytest.raises(ValueError, match="numeric authorities"):
        _validate_director_value(forged, view)


def test_empty_quantitative_selection_does_not_freeze_empty_plural_authority() -> None:
    view = _compact_material_view()
    value = _director_value(view)
    value["page_plan"]["quantitative_exhibits"] = []

    resolved = _resolve_director_numeric_authorities(value, view)

    assert "numeric_authorities" not in resolved["page_plan"]


def _result(value: dict[str, object]) -> CodexStructuredResult:
    return CodexStructuredResult(
        value=value,
        thread_id="thread-1",
        turn_id="turn-1",
        model="gpt-test-current",
        model_provider="openai-test",
        auth_mode="chatgpt",
        plan_type="plus",
        usage={"input_tokens": 123, "output_tokens": 45},
        safe_trace={"runtime": "codex-app-server", "startup_reused": True},
        effort="high",
        duration_seconds=3.25,
        startup_reused=True,
    )


def test_direct_page_rejects_existing_different_signed_authority(tmp_path: Path):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    first = direct_page(
        workspace,
        view,
        timeout=60,
        invoke=lambda *_args, **_kwargs: _result(_director_value(view)),
    )
    different = _director_value(view)
    different["page_plan"]["image_prompt"] += " 改为纵向阅读。"

    with pytest.raises(ValueError, match="published director authority"):
        direct_page(
            workspace,
            view,
            timeout=60,
            invoke=lambda *_args, **_kwargs: _result(different),
        )
    assert first.actual_prompt == first.page_plan["image_prompt"]


def test_direct_page_rejects_swapped_image_paths_before_codex(tmp_path: Path):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    swapped = CompletePageMaterialView(
        value=view.value,
        multimodal_images=tuple(reversed(view.multimodal_images)),
        material_ids=view.material_ids,
        sha256=view.sha256,
    )
    called = False

    def invoke(*args, **kwargs):
        nonlocal called
        called = True
        return _result(_director_value(swapped))

    with pytest.raises(ValueError, match="multimodal|authority|image mapping"):
        direct_page(workspace, swapped, timeout=60, invoke=invoke)
    assert called is False


def test_direct_page_rejects_missing_or_digest_changed_image_before_codex(tmp_path: Path):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    view.multimodal_images[0].write_bytes(b"changed-after-seal")

    with pytest.raises(ValueError, match="digest|byte|authority|image"):
        direct_page(
            workspace,
            view,
            timeout=60,
            invoke=lambda *args, **kwargs: pytest.fail("Codex must not be called"),
        )

    view.multimodal_images[0].unlink()
    with pytest.raises((ValueError, FileNotFoundError), match="missing|exist|authority|image|file"):
        direct_page(
            workspace,
            view,
            timeout=60,
            invoke=lambda *args, **kwargs: pytest.fail("Codex must not be called"),
        )


def test_direct_page_rejects_forged_or_incomplete_view_before_codex(tmp_path: Path):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    forged_value = copy.deepcopy(view.value)
    forged_value["complete_word_content"] = []
    forged_value["original_comments"] = []
    forged = CompletePageMaterialView(
        value=forged_value,
        multimodal_images=view.multimodal_images,
        material_ids=view.material_ids,
        sha256=view.sha256,
    )
    called = False

    def invoke(*args, **kwargs):
        nonlocal called
        called = True
        return _result(_director_value(forged))

    with pytest.raises(ValueError, match="complete|Word|comment|digest|view"):
        direct_page(workspace, forged, timeout=60, invoke=invoke)
    assert called is False


def test_direct_page_rejects_workspace_identity_mismatch_before_codex(tmp_path: Path):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    mismatched_value = copy.deepcopy(view.value)
    mismatched_value["experiment_id"] = "another-experiment"
    mismatched = CompletePageMaterialView(
        value=mismatched_value,
        multimodal_images=view.multimodal_images,
        material_ids=view.material_ids,
        sha256=view.sha256,
    )

    with pytest.raises(ValueError, match="experiment|workspace"):
        direct_page(
            workspace,
            mismatched,
            timeout=60,
            invoke=lambda *args, **kwargs: pytest.fail("Codex must not be called"),
        )


def test_direct_page_accepts_a_task2_sealed_complete_view(
    awesome_four_page_project: Path, tmp_path: Path
):
    _prepare_complete_page_one(awesome_four_page_project)
    from complex_page_experiment import create_experiment_copy

    workspace = create_experiment_copy(
        awesome_four_page_project,
        tmp_path / "experiment",
        experiment_id="director-real-view",
    )
    view = build_complete_page_material_view(workspace)
    value = _director_value(view)
    selected = next(
        item["material_id"]
        for item in view.value["materials"]
        if item["viewable_image"]
    )
    value["selected_references"] = [
        {
            "material_id": selected,
            "use": "Anchor the real source evidence.",
            "preserve": "Preserve recognizable identity.",
        }
    ]

    artifact = direct_page(
        workspace,
        view,
        timeout=60,
        invoke=lambda *args, **kwargs: _result(value),
    )

    assert artifact.selected_reference_ids == (selected,)


def test_direct_page_rejects_missing_task2_source_receipt_before_codex(
    awesome_four_page_project: Path, tmp_path: Path
):
    _prepare_complete_page_one(awesome_four_page_project)
    from complex_page_experiment import create_experiment_copy

    workspace = create_experiment_copy(
        awesome_four_page_project,
        tmp_path / "experiment",
        experiment_id="director-missing-source",
    )
    view = build_complete_page_material_view(workspace)
    (workspace.project_copy / "02_v6" / "source_assets.json").unlink()

    with pytest.raises(ValueError, match="source receipt|missing|source_assets"):
        direct_page(
            workspace,
            view,
            timeout=60,
            invoke=lambda *args, **kwargs: pytest.fail("Codex must not be called"),
        )


def _sealed_task2_view(
    awesome_four_page_project: Path, tmp_path: Path, experiment_id: str
):
    _prepare_complete_page_one(awesome_four_page_project)
    from complex_page_experiment import create_experiment_copy

    workspace = create_experiment_copy(
        awesome_four_page_project,
        tmp_path / "experiment",
        experiment_id=experiment_id,
    )
    return workspace, build_complete_page_material_view(workspace)


def test_direct_page_rejects_mutated_rehashed_view_not_equal_to_published_authority(
    awesome_four_page_project: Path, tmp_path: Path
):
    workspace, view = _sealed_task2_view(
        awesome_four_page_project, tmp_path, "director-rehashed-forgery"
    )
    value = copy.deepcopy(view.value)
    value["visual_contract"]["visual_description"] = "forged after publication"
    rehashed = CompletePageMaterialView(
        value=value,
        multimodal_images=view.multimodal_images,
        material_ids=view.material_ids,
        sha256=hashlib.sha256(
            (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
        ).hexdigest(),
    )

    with pytest.raises(ValueError, match="published|canonical|authority|sealed"):
        direct_page(
            workspace,
            rehashed,
            timeout=60,
            invoke=lambda *args, **kwargs: pytest.fail("Codex must not be called"),
        )


@pytest.mark.parametrize("mutation", ["delete", "tamper"])
def test_direct_page_rejects_missing_or_tampered_page_material_receipt(
    awesome_four_page_project: Path, tmp_path: Path, mutation: str
):
    workspace, view = _sealed_task2_view(
        awesome_four_page_project, tmp_path, f"director-page-receipt-{mutation}"
    )
    receipt = view.value["source_receipts"]["page_materials"]
    page_material = workspace.project_copy.joinpath(*receipt["path"].split("/"))
    if mutation == "delete":
        page_material.unlink()
    else:
        page_material.write_bytes(b'{"tampered":true}\n')

    with pytest.raises((ValueError, FileNotFoundError), match="page-material|receipt|missing|digest"):
        direct_page(
            workspace,
            view,
            timeout=60,
            invoke=lambda *args, **kwargs: pytest.fail("Codex must not be called"),
        )


def test_direct_page_rejects_noncanonical_published_material_view_bytes(
    awesome_four_page_project: Path, tmp_path: Path
):
    workspace, view = _sealed_task2_view(
        awesome_four_page_project, tmp_path, "director-wrong-published-view"
    )
    published = (
        workspace.project_copy
        / "02_v6"
        / "experiments"
        / workspace.experiment_id
        / "complete_page_material_view.json"
    )
    published.write_text(json.dumps(view.value, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="published|canonical|digest|sealed"):
        direct_page(
            workspace,
            view,
            timeout=60,
            invoke=lambda *args, **kwargs: pytest.fail("Codex must not be called"),
        )


def _republish_tampered_view(
    workspace: ExperimentWorkspace,
    view: CompletePageMaterialView,
    mutate,
) -> CompletePageMaterialView:
    value = copy.deepcopy(view.value)
    mutate(value)
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    published = (
        workspace.project_copy
        / "02_v6"
        / "experiments"
        / workspace.experiment_id
        / "complete_page_material_view.json"
    )
    published.write_bytes(payload)
    by_id = {item["material_id"]: item for item in value["materials"]}
    omitted = {item["material_id"] for item in value["deduplicated_derivatives"]}
    retained_ids = [
        item["material_id"]
        for item in value["materials"]
        if item["viewable_image"] and item["material_id"] not in omitted
    ]
    images = tuple(
        workspace.project_copy.joinpath(*by_id[item]["authority_path"].split("/"))
        for item in retained_ids
    )
    return CompletePageMaterialView(
        value=value,
        multimodal_images=images,
        material_ids=tuple(item["material_id"] for item in value["materials"]),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


@pytest.mark.parametrize("kind", ["attachment_render_page", "attachment_contact_sheet"])
def test_direct_page_rejects_republished_derivative_substitution(
    awesome_four_page_project: Path, tmp_path: Path, kind: str
):
    workspace, view = _sealed_task2_view(
        awesome_four_page_project, tmp_path, f"director-derivative-{kind}"
    )

    def mutate(value):
        target = next(item for item in value["materials"] if item["kind"] == kind)
        replacement = next(
            item
            for item in value["materials"]
            if item["kind"] == "word_image" and item["sha256"] != target["sha256"]
        )
        for field in ("authority_path", "sha256", "byte_size"):
            target[field] = replacement[field]
            target["original"][{"authority_path": "path"}.get(field, field)] = replacement[field]
        value["deduplicated_derivatives"] = []

    forged = _republish_tampered_view(workspace, view, mutate)
    with pytest.raises(ValueError, match="render|derivative|receipt|attachment|contact"):
        direct_page(
            workspace, forged, timeout=60,
            invoke=lambda *args, **kwargs: pytest.fail("Codex must not be called"),
        )


@pytest.mark.parametrize("mutation", ["missing", "extra", "cross_attachment"])
def test_direct_page_rejects_republished_derivative_set_or_parent_tamper(
    awesome_four_page_project: Path, tmp_path: Path, mutation: str
):
    workspace, view = _sealed_task2_view(
        awesome_four_page_project, tmp_path, f"director-derivative-{mutation}"
    )

    def mutate(value):
        derivatives = [
            item
            for item in value["materials"]
            if item["kind"] in {"attachment_render_page", "attachment_contact_sheet"}
        ]
        if mutation == "missing":
            target = derivatives[0]
            value["materials"].remove(target)
            value["deduplicated_derivatives"] = [
                item for item in value["deduplicated_derivatives"]
                if item["material_id"] != target["material_id"]
                and item["duplicate_of"] != target["material_id"]
            ]
        elif mutation == "extra":
            extra = copy.deepcopy(derivatives[0])
            extra["material_id"] += ":extra"
            value["materials"].append(extra)
        else:
            first_parent = derivatives[0]["attachment_material_id"]
            other_parent = next(
                item["material_id"]
                for item in value["materials"]
                if item["kind"] == "attachment_original" and item["material_id"] != first_parent
            )
            derivatives[0]["attachment_material_id"] = other_parent

    forged = _republish_tampered_view(workspace, view, mutate)
    with pytest.raises(ValueError, match="render|derivative|receipt|attachment|parent"):
        direct_page(
            workspace, forged, timeout=60,
            invoke=lambda *args, **kwargs: pytest.fail("Codex must not be called"),
        )


@pytest.mark.parametrize(("mutate", "message"), [
    (lambda v: v["page_plan"]["content_inventory"].pop(), "omits"),
    (lambda v: v["page_plan"]["content_inventory"][0].update(source_block_id="invented"), "unknown"),
    (lambda v: v["page_plan"]["content_inventory"][0].update(source_quote="invented fact"), "source-exact"),
    (lambda v: v["page_plan"]["content_inventory"][0].update(display_copy="absent copy"), "image_prompt"),
    (lambda v: v["page_plan"]["content_inventory"][0].update(target="fixed_title"), "fixed title"),
    (lambda v: v["page_plan"].update(image_prompt=" "), "image_prompt"),
    (lambda v: v["page_plan"].update(image_prompt=" " + v["page_plan"]["image_prompt"]), "outer whitespace"),
    (lambda v: v.update(page_number=999), "page_number"),
])
def test_direct_design_rejects_missing_or_unbound_source_content(mutate, message):
    view = _compact_material_view()
    value = _director_value(view)
    mutate(value)
    with pytest.raises(ValueError, match=message):
        _validate_director_value(value, view)


def test_source_block_can_split_into_multiple_visible_parts_without_slots():
    view = _compact_material_view()
    value = _director_value(view)
    item = value["page_plan"]["content_inventory"].pop(0)
    source = item["source_quote"]
    split = source.index(" ")
    value["page_plan"]["content_inventory"] += [
        {**item, "source_quote": source[:split], "display_copy": source[:split]},
        {**item, "source_quote": source[split:], "display_copy": source[split:]},
    ]
    assert _validate_director_value(value, view) == ()
    assert set(value["page_plan"]) == {"content_inventory", "context_bridges", "image_prompt"}


def test_fixed_title_target_requires_actual_confirmed_title_coverage():
    view = _compact_material_view()
    value = _director_value(view)
    item = value["page_plan"]["content_inventory"][0]
    view.value["fixed_page_title"] = item["display_copy"]
    item["target"] = "fixed_title"
    value["page_plan"]["image_prompt"] = value["page_plan"]["image_prompt"].replace(item["display_copy"], "")
    assert _validate_director_value(value, view) == ()


def test_direct_page_preserves_complete_materials_style_and_verbatim_design(tmp_path):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    value = _director_value(view)
    calls = []
    def invoke(project, **kwargs):
        calls.append({"project": project, **kwargs})
        return _result(value)
    artifact = direct_page(workspace, view, timeout=60, invoke=invoke)
    assert len(calls) == 1
    call = calls[0]
    assert call["project"] == workspace.project_copy
    assert call["role"] == "awesome-page-director"
    assert call["images"] == view.multimodal_images
    prompt = call["prompt"]
    assert json.dumps(view.value, ensure_ascii=False, sort_keys=True) in prompt
    assert "Authoritative body 1" in prompt
    assert "Keep this original direction exactly.  " in prompt
    assert "Image-1 = word-image:word-photo" in prompt
    assert "Image-2 = word-image:word-photo-copy" in prompt
    assert all(str(view.value["visual_contract"][key]) in prompt for key in ("primary_color", "secondary_color", "background_color", "cjk_font"))
    assert artifact.actual_prompt == value["page_plan"]["image_prompt"]
    assert artifact.page_plan == value["page_plan"]
    assert artifact.selected_reference_ids == ("word-image:word-photo",)
    assert artifact.model == "gpt-test-current"
    assert artifact.model_provider == "openai-test"
    assert artifact.duration_seconds == 3.25
    assert artifact.usage == {"input_tokens": 123, "output_tokens": 45}
    authority = json.loads((workspace.project_copy / "02_v6/experiments" / workspace.experiment_id / "director_v2.json").read_text(encoding="utf-8"))
    assert authority["actual_prompt"] == artifact.actual_prompt
    assert authority["material_view_sha256"] == view.sha256
    assert authority["value"] == value
    assert authority["selected_reference_ids"] == list(artifact.selected_reference_ids)
    assert authority["hmac_sha256"] and authority["key_id"]


def test_director_loads_one_method_and_complete_confirmed_taskbook(tmp_path):
    call, sections = _captured_director_request(tmp_path)
    reference = VISUAL_DIRECTOR_REFERENCE.read_text(encoding="utf-8").strip()
    assert call["prompt"].count(reference) == 1
    assert sections["页面设计方法"] == reference
    assert all(value in sections["已确认任务书"] for value in TASKBOOK_VALUES)
    assert TASKBOOK_BOUNDARY in sections["已确认任务书"]
    assert call["output_schema"] == json.loads(DIRECTOR_SCHEMA.read_text(encoding="utf-8"))
    assert "完整全文" in call["prompt"]
    assert "不是另一套固定设计标题" in call["prompt"]
    assert "原样交给 Image2" in call["prompt"]
    assert "numeric_authorities 固定返回 null" in call["prompt"]
    for forbidden in ("template_version", "taskbook_digest", '"defaults"'):
        assert forbidden not in sections["已确认任务书"]


@pytest.mark.parametrize("mutation", ["unknown", "duplicate", "blank_use", "input_order"])
def test_director_references_remain_bound_to_viewable_inputs(tmp_path, mutation):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    value = _director_value(view)
    if mutation == "unknown":
        value["selected_references"][0]["material_id"] = "attachment:non-viewable"
    elif mutation == "duplicate":
        value["selected_references"] *= 2
    elif mutation == "blank_use":
        value["selected_references"][0]["use"] = "  "
    else:
        value["page_plan"]["image_prompt"] += " Image-2"
    with pytest.raises(ValueError, match="reference"):
        direct_page(workspace, view, timeout=60, invoke=lambda *a, **kw: _result(value))


def test_new_director_method_keeps_joint_design_and_source_bound_title():
    text = VISUAL_DIRECTOR_REFERENCE.read_text(encoding="utf-8")
    for obligation in ("全文", "内部关系", "同一次设计", "字数统一", "不确定性", "已确认设置", "不得包含分析过程", "不重新选择布局或正文颜色"):
        assert obligation in text


def test_replanning_advances_signed_revision_without_rewriting_prior_authority(tmp_path):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    first = direct_page(workspace, view, timeout=60, invoke=lambda *a, **kw: _result(_director_value(view)))
    authority_path = workspace.project_copy / "02_v6/experiments" / workspace.experiment_id / "director_v2.json"
    original = authority_path.read_bytes()
    image = workspace.project_copy / "rejected.png"
    image.write_bytes(b"offline-diagnostic-candidate")
    calls = []
    revised = _director_value(view)
    revised["page_plan"]["image_prompt"] += " 改正关系方向。"
    def invoke(*args, **kwargs):
        calls.append(kwargs)
        return _result(revised)
    result = direct_page(workspace, view, timeout=60, invoke=invoke, revision=2,
                         previous_director=first, previous_image=image,
                         review_feedback={"category": "primary_relationship", "detail": "reverse arrow", "repair_route": "replan"})
    assert result.revision == 2
    assert result.actual_prompt == revised["page_plan"]["image_prompt"]
    assert authority_path.read_bytes() == original
    assert calls[0]["images"] == (*view.multimodal_images, image)
    assert "for diagnosis only" in calls[0]["prompt"]
    assert "Previous plan (not factual authority)" in calls[0]["prompt"]
    with pytest.raises(ValueError, match="three-candidate budget"):
        direct_page(workspace, view, timeout=60, invoke=lambda *a, **kw: pytest.fail("budget exceeded"), revision=4)

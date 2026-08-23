from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from awesome_page_materials import collect_page_materials
from codex_subscription_runtime import CodexStructuredResult
from complex_page_experiment.consulting_prompt import _color_constraints
from complex_page_experiment.director import (
    _correction_schema,
    compile_consulting_six_part_prompt,
    decide_correction,
    direct_page,
)
from complex_page_experiment.materials import (
    CompletePageMaterialView,
    build_complete_page_material_view,
)
from complex_page_experiment.workspace import ExperimentWorkspace
from conftest import awesome_four_page_project as awesome_four_page_project_fixture
from test_materials import _prepare_complete_page_one


HEADINGS = (
    "Task and Canvas",
    "Core Proposition and Content",
    "Consulting Information Architecture",
    "Visual Style and Color",
    "Text and Typography",
    "Strict Prohibitions",
)
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
    / "consulting_page_director_v2.schema.json"
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
    "It may change emphasis, hierarchy, reading path, evidence framing, and takeaway selection "
    "only; it must not add, omit, rewrite, or move Word content."
)


_TEST_VIEWS: dict[Path, CompletePageMaterialView] = {}


def _top_level_prompt_sections(prompt: str) -> dict[str, str]:
    headings = (
        "WORD BODY AND MATERIAL AUTHORITY",
        "HARD BOUNDARIES",
        "GENERAL VISUAL DIRECTOR PRINCIPLES",
        "CONFIRMED PRESENTATION TASKBOOK",
        "COMPLETE PAGE MATERIAL VIEW AND VIEWABLE IMAGES",
        "STRUCTURED OUTPUT REQUIREMENTS",
    )
    positions = [prompt.index(heading) for heading in headings]
    assert positions == sorted(positions)
    sections: dict[str, str] = {}
    for index, heading in enumerate(headings):
        start = positions[index] + len(heading)
        end = positions[index + 1] if index + 1 < len(headings) else len(prompt)
        sections[heading] = prompt[start:end].strip()
    return sections


def _compiled_prompt_sections(prompt: str) -> dict[str, str]:
    markers = tuple(f"## {heading}" for heading in HEADINGS)
    positions = [prompt.index(marker) for marker in markers]
    assert positions == sorted(positions)
    sections: dict[str, str] = {}
    for index, (heading, marker) in enumerate(zip(HEADINGS, markers, strict=True)):
        start = positions[index] + len(marker)
        end = positions[index + 1] if index + 1 < len(markers) else len(prompt)
        sections[heading] = prompt[start:end].strip()
    return sections


def _captured_director_request(tmp_path: Path) -> tuple[dict[str, object], dict[str, str]]:
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    calls: list[dict[str, object]] = []

    def invoke(project: Path, **kwargs):
        calls.append({"project": project, **kwargs})
        return _result(_director_value(view))

    direct_page(workspace, view, timeout=60, invoke=invoke)
    assert len(calls) == 1
    prompt = str(calls[0]["prompt"])
    return calls[0], _top_level_prompt_sections(prompt)


def _assert_semantic_groups(text: str, groups: tuple[tuple[str, ...], ...]) -> None:
    normalized = " ".join(text.casefold().split())
    missing = [group for group in groups if not any(term in normalized for term in group)]
    assert missing == []


def _unsupported_structured_output_paths(schema: object) -> list[str]:
    unsupported: list[str] = []

    def visit(value: object, path: str = "$") -> None:
        if isinstance(value, dict):
            if ("const" in value or "enum" in value) and "type" not in value:
                unsupported.append(f"{path}: missing type")
            if "const" in value and isinstance(value["const"], (dict, list)):
                unsupported.append(f"{path}: non-scalar const")
            if "uniqueItems" in value:
                unsupported.append(f"{path}: uniqueItems")
            for key, child in value.items():
                visit(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(schema)
    return unsupported


def test_correction_output_schema_uses_supported_structured_output_subset() -> None:
    assert _unsupported_structured_output_paths(_correction_schema()) == []


def test_director_output_schema_types_every_const_and_enum() -> None:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "schemas"
        / "consulting_page_director_v2.schema.json"
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
        / "consulting_page_director_v2.schema.json"
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
        / "consulting_page_director_v2.schema.json"
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


def _workspace(tmp_path: Path) -> ExperimentWorkspace:
    source = awesome_four_page_project_fixture.__wrapped__(tmp_path)
    _prepare_complete_page_one(source)
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


def _workspace_without_viewable_materials(
    tmp_path: Path,
) -> tuple[ExperimentWorkspace, CompletePageMaterialView]:
    source = awesome_four_page_project_fixture.__wrapped__(tmp_path)
    _prepare_complete_page_one(source)
    page_number = 2
    material = collect_page_materials(source, page_number)
    assert material["word_images"] == []
    assert material["attachment_inputs"] == []
    payload = (
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    material_path = (
        source
        / "02_v6"
        / "awesome_page_materials"
        / f"page_{page_number:03d}.json"
    )
    material_path.write_bytes(payload)
    state_path = source / "workflow_v6.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["pages"][page_number - 1]["material_receipt"]["digest"] = hashlib.sha256(
        payload
    ).hexdigest()
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    from complex_page_experiment import create_experiment_copy

    workspace = create_experiment_copy(
        source,
        tmp_path / "experiment-no-viewable-materials",
        experiment_id="director-no-viewable-materials",
        page_number=page_number,
    )
    view = build_complete_page_material_view(workspace)
    assert view.multimodal_images == ()
    return workspace, view


def _prompt_sections(*, suffix: str = "") -> dict[str, str]:
    return {
        "task_and_canvas": f"A calm luminous field with subtle depth{suffix}.",
        "core_proposition_and_content": f"Make the verified relationship immediately legible{suffix}.",
        "text_and_typography": f"Keep names and figures crisp and restrained{suffix}.",
        "visual_style_and_color": f"Use a wide editorial composition with a clear reading path{suffix}.",
        "consulting_information_architecture": f"Use the supplied photograph for identity and the rendered page for factual context{suffix}.",
        "strict_prohibitions": (
            "Preserve verified identities, source-exact visible copy, and source-supported relationships."
        ),
    }


def _compiler_material_view(
    background_color: str = "#FFFFFF",
    primary_color: str = "#17365D",
    secondary_color: str = "#CD202A",
) -> CompletePageMaterialView:
    return CompletePageMaterialView(
        {
            "visual_contract": {
                "background_color": background_color,
                "primary_color": primary_color,
                "secondary_color": secondary_color,
            }
        },
        (),
        (),
        "",
    )


def test_compile_prompt_injects_confirmed_color_roles_and_budgets_once():
    view = _compiler_material_view("#F7F7F7", "#161616", "#CD202A")
    value = _director_value(CompletePageMaterialView({}, (), (), ""))

    prompt = compile_consulting_six_part_prompt(value, view)
    sections = _compiled_prompt_sections(prompt)
    positive, prohibited = _color_constraints(view)

    assert prompt.count(positive) == 1
    assert prompt.count(prohibited) == 1
    assert positive in sections["Visual Style and Color"]
    assert prohibited in sections["Strict Prohibitions"]
    assert "primary color #161616 for primary text and structural hierarchy" in positive
    assert "secondary color #CD202A strictly as an accent" in positive
    for marker in (
        "70%-85%",
        "15%-25%",
        "3%-7%",
        "never above 10%",
        "continuous accent block above 2%",
        "full-width solid headers",
        "wide bands or paths",
        "large tinted regions",
    ):
        assert marker in positive + prohibited
    assert [line[3:] for line in prompt.splitlines() if line.startswith("## ")] == list(HEADINGS)


@pytest.mark.parametrize("secondary_color", ["#CD202A", "#1F5AA6", "#287A55"])
def test_compile_prompt_color_contract_is_hue_independent(secondary_color: str):
    value = _director_value(CompletePageMaterialView({}, (), (), ""))
    prompt = compile_consulting_six_part_prompt(
        value,
        _compiler_material_view(secondary_color=secondary_color),
    )
    baseline = compile_consulting_six_part_prompt(
        value,
        _compiler_material_view(secondary_color="#CD202A"),
    )

    assert prompt.replace(secondary_color, "#SECONDARY") == baseline.replace(
        "#CD202A", "#SECONDARY"
    )
    if secondary_color == "#1F5AA6":
        compiler_owned = " ".join(
            _color_constraints(
                _compiler_material_view(secondary_color=secondary_color)
            )
        ).casefold()
        assert " red " not in f" {compiler_owned} "
        assert not any(term in compiler_owned for term in ("红色", "scarlet", "crimson"))


def test_compile_prompt_deduplicates_owned_color_contract_without_deleting_facts():
    view = _compiler_material_view(secondary_color="#1F5AA6")
    value = _director_value(CompletePageMaterialView({}, (), (), ""))
    positive, prohibited = _color_constraints(view)
    value["prompt_sections"]["visual_style_and_color"] += (
        " " + positive
    )
    value["prompt_sections"]["strict_prohibitions"] += (
        " " + prohibited
    )
    source_fact = (
        "Source fact: Red Beacon, 红色标识, Scarlet Ledger, and Crimson Record are exact names."
    )
    value["prompt_sections"]["text_and_typography"] += " " + source_fact

    prompt = compile_consulting_six_part_prompt(value, view)

    assert prompt.count(positive) == 1
    assert prompt.count(prohibited) == 1
    assert prompt.count(source_fact) == 1


def _director_value(view: CompletePageMaterialView) -> dict[str, object]:
    return {
        "schema_version": "awesome-consulting-page-director-v2",
        "page_number": 1,
        "quality": "high",
        "machine_record": {
            "facts_and_sources": ["The body statement comes from word-block:body-1."],
            "must_preserve_entities": ["The named subject in word-image:word-photo."],
            "core_content_and_comment_direction": ["Follow the original comment direction."],
            "material_use": [
                {
                    "material_id": material_id,
                    "status": "used_in_image" if "image" in material_id else "background_understanding",
                    "reason": "Direct evidence or context.",
                }
                for material_id in view.material_ids
            ],
            "selected_references": [
                {
                    "material_id": "word-image:word-photo",
                    "identity": "The supplied real subject photograph",
                    "use": "Anchor identity",
                    "preserve": "Recognizable identity",
                    "allowed_changes": "Crop and tonal integration",
                    "composition_relationship": "Primary left-side visual anchor",
                }
            ],
            "fixed_layer_exclusions": ["title", "logo", "footer", "page_number"],
        },
        "creative_direction": {
            "business_proposition": "Turn the dense source into one confident visual argument.",
            "explanatory_lead": "Explain the source-supported decision context in two short lines.",
            "analytical_backbone": "A continuous evidence chain that resolves into a single insight.",
            "evidence_interpretation_conclusion": "Enter through the real subject, then move across evidence to the conclusion.",
            "content_hierarchy": "Lead, analytical evidence, interpretation, and takeaway.",
            "reading_path_and_density": "Wide editorial collage, quiet whitespace, tactile paper and glass.",
            "takeaway_statement": "End with the source-supported decision implication.",
            "supporting_visual_policy": "Use real references as evidence, not decoration.",
            "anti_ai_visual_policy": "Avoid miniature scenes, neon, and decorative 3D machinery.",
        },
        "prompt_sections": _prompt_sections(),
    }


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


def test_direct_page_sends_complete_authority_and_ordered_image_mapping(tmp_path: Path):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    calls: list[dict[str, object]] = []

    def invoke(project: Path, **kwargs):
        calls.append({"project": project, **kwargs})
        return _result(_director_value(view))

    artifact = direct_page(workspace, view, timeout=60, invoke=invoke)

    assert len(calls) == 1
    call = calls[0]
    assert call["project"] == workspace.project_copy
    assert call["role"] == "awesome-page-director"
    assert call["images"] == view.multimodal_images
    prompt = str(call["prompt"])
    assert json.dumps(view.value, ensure_ascii=False, sort_keys=True) in prompt
    assert "Authoritative body 1" in prompt
    assert "Keep this original direction exactly.  " in prompt
    assert "Image-1 = word-image:word-photo" in prompt
    assert "Image-2 = word-image:word-photo-copy" in prompt
    reference = VISUAL_DIRECTOR_REFERENCE.read_text(encoding="utf-8").strip()
    assert prompt.count(reference) == 1
    ordered_markers = (
        "WORD BODY AND MATERIAL AUTHORITY",
        "HARD BOUNDARIES",
        "GENERAL VISUAL DIRECTOR PRINCIPLES",
        "COMPLETE PAGE MATERIAL VIEW AND VIEWABLE IMAGES",
        "STRUCTURED OUTPUT REQUIREMENTS",
    )
    assert tuple(prompt.index(marker) for marker in ordered_markers) == tuple(
        sorted(prompt.index(marker) for marker in ordered_markers)
    )
    assert prompt.index("Word body text is the primary authority") < prompt.index(reference)
    assert prompt.index("central largest 17:8 content region") < prompt.index(reference)
    assert prompt.index(reference) < prompt.index("Image-1 = word-image:word-photo")
    assert prompt.index("COMPLETE PAGE MATERIAL VIEW") < prompt.index(
        "STRUCTURED OUTPUT REQUIREMENTS"
    )
    assert "after material completion" in prompt
    assert "source-exact formal name from the Word body" in prompt
    assert "neutral non-trademark marker" not in prompt
    assert "3:2" not in prompt
    schema_bytes = DIRECTOR_SCHEMA.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    assert hashlib.sha256(schema_bytes).hexdigest() == (
        "9d63ed6ea05379a3afc480eae4fedf700091d5ab92d352c69d2ade9da6ad1860"
    )
    assert artifact.selected_reference_ids == ("word-image:word-photo",)
    assert artifact.quality == "high"
    assert artifact.model == "gpt-test-current"
    assert artifact.model_provider == "openai-test"
    assert artifact.effort == "high"
    assert artifact.usage == {"input_tokens": 123, "output_tokens": 45}
    assert artifact.duration_seconds == 3.25
    assert artifact.creative_direction["analytical_backbone"].startswith("A continuous")
    assert artifact.actual_prompt == compile_consulting_six_part_prompt(artifact.value, view)
    authority_path = (
        workspace.project_copy
        / "02_v6"
        / "experiments"
        / workspace.experiment_id
        / "director_v2.json"
    )
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    assert authority["schema_version"] == "awesome-consulting-page-director-authority-v2"
    assert authority["material_view_sha256"] == view.sha256
    assert authority["actual_prompt"] == artifact.actual_prompt
    assert authority["selected_reference_ids"] == list(artifact.selected_reference_ids)
    assert authority["value"] == artifact.value
    assert isinstance(authority["key_id"], str)
    assert isinstance(authority["hmac_sha256"], str)


def test_director_request_injects_one_reference_between_authority_and_materials_without_schema_change(
    tmp_path: Path,
):
    call, sections = _captured_director_request(tmp_path)
    reference = VISUAL_DIRECTOR_REFERENCE.read_text(encoding="utf-8").strip()

    assert list(sections) == [
        "WORD BODY AND MATERIAL AUTHORITY",
        "HARD BOUNDARIES",
        "GENERAL VISUAL DIRECTOR PRINCIPLES",
        "CONFIRMED PRESENTATION TASKBOOK",
        "COMPLETE PAGE MATERIAL VIEW AND VIEWABLE IMAGES",
        "STRUCTURED OUTPUT REQUIREMENTS",
    ]
    assert sections["GENERAL VISUAL DIRECTOR PRINCIPLES"] == reference
    assert reference not in "\n".join(
        body
        for heading, body in sections.items()
        if heading != "GENERAL VISUAL DIRECTOR PRINCIPLES"
    )
    assert "Word body text is the primary authority" in sections[
        "WORD BODY AND MATERIAL AUTHORITY"
    ]
    assert "central largest 17:8 content region" in sections["HARD BOUNDARIES"]
    assert "COMPLETE PAGE MATERIAL VIEW" in sections[
        "COMPLETE PAGE MATERIAL VIEW AND VIEWABLE IMAGES"
    ]
    assert call["output_schema"] == json.loads(DIRECTOR_SCHEMA.read_text(encoding="utf-8"))


def test_director_request_uses_only_confirmed_taskbook_without_template_metadata(tmp_path: Path):
    call, sections = _captured_director_request(tmp_path)
    taskbook = sections["CONFIRMED PRESENTATION TASKBOOK"]

    assert all(value in taskbook for value in TASKBOOK_VALUES)
    assert TASKBOOK_BOUNDARY in taskbook
    assert "business_proposition" in taskbook
    assert "reading_path_and_density" in taskbook
    assert "takeaway_statement" in taskbook
    assert "supporting_visual_policy" in taskbook
    prompt = str(call["prompt"])
    for forbidden in ("investment-committee", "template_version", "taskbook_digest", '"defaults"'):
        assert forbidden not in prompt


def test_correction_taskbook_uses_same_boundary_without_template_metadata(tmp_path: Path):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    director = direct_page(
        workspace, view, timeout=60,
        invoke=lambda *args, **kwargs: _result(_director_value(view)),
    )
    candidate = workspace.project_copy / "candidate-taskbook.png"
    candidate.write_bytes(b"candidate-taskbook")
    problem = "The takeaway is unclear."
    value = {
        "schema_version": "awesome-page-correction-v2",
        "page_number": workspace.page_number,
        "strategy": "edit_previous",
        "problem_addressed": [problem],
        "preserve": ["Preserve source-exact facts."],
        "selected_reference_ids": ["word-image:word-photo"],
        "prompt_sections": _prompt_sections(suffix=" with a clearer takeaway"),
    }
    calls: list[dict[str, object]] = []

    def invoke(project: Path, **kwargs):
        calls.append({"project": project, **kwargs})
        return _result(value)

    decide_correction(
        workspace, view, director, previous_candidate=candidate,
        problems=[problem], timeout=60, invoke=invoke,
    )
    prompt = str(calls[0]["prompt"])
    assert all(value in prompt for value in TASKBOOK_VALUES)
    assert TASKBOOK_BOUNDARY in prompt
    for forbidden in ("investment-committee", "template_version", "taskbook_digest", '"defaults"'):
        assert forbidden not in prompt


def test_director_request_does_not_authorize_omitting_secondary_explanation(
    tmp_path: Path,
):
    _call, sections = _captured_director_request(tmp_path)
    requirements = sections["STRUCTURED OUTPUT REQUIREMENTS"].casefold()

    assert "omitting secondary explanation" not in requirements
    assert "source-exact" in requirements
    assert "do not authorize any other factual prose" in requirements


def test_director_request_reserves_canvas_background_for_the_compiler(
    tmp_path: Path,
):
    _call, sections = _captured_director_request(tmp_path)
    requirements = sections["STRUCTURED OUTPUT REQUIREMENTS"]

    _assert_semantic_groups(
        requirements,
        (
            (("task_and_canvas",)),
            (("foreground environment",)),
            (("spatial arrangement",)),
            (("any prompt_sections field", "every prompt_sections field")),
            (("canvas background",)),
            (("background color",)),
            (("grid",)),
            (("texture",)),
            (("gradient",)),
            (("glow",)),
            (("do not specify", "must not specify")),
        ),
    )


def test_director_request_uses_only_the_word_name_when_a_required_real_asset_remains_absent(
    tmp_path: Path,
):
    _call, sections = _captured_director_request(tmp_path)
    requirements = sections["STRUCTURED OUTPUT REQUIREMENTS"]

    _assert_semantic_groups(
        requirements,
        (
            (("after material completion", "after material supplementation")),
            (("specifically requested real asset", "explicitly requested real asset")),
            (("absent from the mapped images", "missing from the mapped images")),
            (("source-exact formal name",)),
            (("word body", "word's")),
            (("fake logo",)),
            (("fake person",)),
            (("fake factual image",)),
            (("do not claim", "must not claim")),
            (("comment",)),
            (("fully implemented", "completely implemented")),
        ),
    )
    assert "neutral non-trademark marker" not in requirements.casefold()
    assert "small identity caption" not in requirements.casefold()


def test_direct_page_compiler_uses_only_the_sealed_ui_canvas_background(
    tmp_path: Path,
):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    value = copy.deepcopy(_director_value(view))
    free_background = "Ultraviolet burlap vortex with noisy silver texture."
    value["prompt_sections"]["task_and_canvas"] = free_background

    artifact = direct_page(
        workspace,
        view,
        timeout=60,
        invoke=lambda *args, **kwargs: _result(value),
    )

    scene = _compiled_prompt_sections(artifact.actual_prompt)["Task and Canvas"]
    background_color = str(view.value["visual_contract"]["background_color"])
    assert background_color in scene
    assert artifact.actual_prompt.casefold().count(background_color.casefold()) == 1
    assert "entire canvas" in scene.casefold()
    assert free_background.casefold() not in artifact.actual_prompt.casefold()


def test_compiler_preserves_director_foreground_arrangement_in_scene_section():
    value = _director_value(CompletePageMaterialView({}, (), (), ""))
    value["prompt_sections"]["task_and_canvas"] = (
        "Arrange the foreground evidence around one central relationship arc. "
        "Set the canvas background to a blue textured grid."
    )

    prompt = compile_consulting_six_part_prompt(value, _compiler_material_view("#FFFFFF"))
    scene = _compiled_prompt_sections(prompt)["Task and Canvas"]

    assert "foreground evidence around one central relationship arc" in scene
    assert "blue textured grid" not in scene
    assert "#FFFFFF" in scene


def test_compiler_removes_only_explicit_canvas_background_clauses_from_other_sections():
    value = _director_value(CompletePageMaterialView({}, (), (), ""))
    value["prompt_sections"].update(
        {
            "core_proposition_and_content": (
                "Keep the foreground subject recognizable. "
                "Use a soft glow around the subject. "
                "Set the canvas background color to scarlet."
            ),
            "text_and_typography": (
                "Keep the exact figure visible. "
                "Align the facts in a compact grid. "
                "Add a background grid and texture."
            ),
            "visual_style_and_color": (
                "Keep tactile texture on the foreground garment. "
                "Add a gradient and glow across the canvas."
            ),
            "consulting_information_architecture": (
                "Preserve the supplied identity. "
                "Make the canvas background a cobalt texture."
            ),
            "strict_prohibitions": (
                "Preserve source-exact facts. Add a background glow."
            ),
        }
    )

    prompt = compile_consulting_six_part_prompt(value, _compiler_material_view("#F7F7F7"))
    sections = _compiled_prompt_sections(prompt)
    non_scene = "\n".join(
        body for heading, body in sections.items() if heading != "Task and Canvas"
    ).casefold()

    for preserved in (
        "foreground subject recognizable",
        "soft glow around the subject",
        "exact figure visible",
        "facts in a compact grid",
        "tactile texture on the foreground garment",
        "supplied identity",
        "source-exact facts",
    ):
        assert preserved in non_scene
    for removed in (
        "canvas background color to scarlet",
        "background grid and texture",
        "gradient and glow across the canvas",
        "canvas background a cobalt texture",
        "background glow",
    ):
        assert removed not in non_scene


def test_visual_direction_requires_one_page_specific_whole_with_asymmetric_hierarchy(
    tmp_path: Path,
):
    _call, sections = _captured_director_request(tmp_path)
    reference = sections["GENERAL VISUAL DIRECTOR PRINCIPLES"]

    _assert_semantic_groups(
        reference,
        (
            (("coherent whole-page", "coherent whole page")),
            (("disconnected", "module assembly")),
            (("word core conclusion", "word conclusion")),
            (("material relationships", "relationships among the materials")),
            (("focal point", "primary focus")),
            (("secondary content", "secondary material")),
            (("reading path",)),
            (("asymmetr", "unequal allocation")),
        ),
    )


def test_visual_direction_treats_reconstructability_as_a_preference_not_a_medium_gate(
    tmp_path: Path,
):
    _call, sections = _captured_director_request(tmp_path)
    reference = sections["GENERAL VISUAL DIRECTOR PRINCIPLES"]

    _assert_semantic_groups(
        reference,
        (
            (("visual preference",)),
            (("text zones",)),
            (("contrast",)),
            (("separable",)),
            (("background",)),
            (("complex photos", "complex photography")),
            (("illustrations",)),
            (("remain raster", "stay raster")),
            (("do not force", "never force")),
            (("vector",)),
        ),
    )


def test_visual_direction_allows_a_factual_screenshot_to_lead_when_it_is_core_evidence(
    tmp_path: Path,
):
    _call, sections = _captured_director_request(tmp_path)
    reference = sections["GENERAL VISUAL DIRECTOR PRINCIPLES"]

    _assert_semantic_groups(
        reference,
        (
            (("factual screenshot",)),
            (("evidence",)),
            (("main visual", "primary visual")),
            (("core evidence",)),
        ),
    )


def test_visual_direction_makes_repeated_modules_conditional_on_real_grouping_relationships(
    tmp_path: Path,
):
    _call, sections = _captured_director_request(tmp_path)
    reference = sections["GENERAL VISUAL DIRECTOR PRINCIPLES"]

    _assert_semantic_groups(
        reference,
        (
            (("cards",)),
            (("columns",)),
            (("icons",)),
            (("repeated modules",)),
            (("not defaults", "not default")),
            (("relationships",)),
            (("require grouping", "need grouping")),
        ),
    )


def test_visual_direction_preserves_reference_authority_and_keeps_image_prompt_pixel_specific(
    tmp_path: Path,
):
    _call, sections = _captured_director_request(tmp_path)
    reference = sections["GENERAL VISUAL DIRECTOR PRINCIPLES"]

    _assert_semantic_groups(
        reference,
        (
            (("fact evidence", "factual evidence")),
            (("identity preservation", "identity reference")),
            (("content material",)),
            (("style inspiration",)),
            (("layout reference",)),
            (("fact and identity", "facts and identities")),
            (("faithful",)),
            (("cannot override", "must not override")),
            (("concise",)),
            (("facts",)),
            (("composition",)),
            (("reference roles",)),
            (("fixed boundaries", "fixed-layer boundary")),
            (("17:8",)),
            (("change pixels", "affect pixels")),
            (("design-process", "design process")),
        ),
    )
    assert len(reference.split()) < 360


def test_visual_direction_binds_exact_facts_to_their_source_subject_before_showing_totals(
    tmp_path: Path,
):
    _call, sections = _captured_director_request(tmp_path)
    reference = sections["GENERAL VISUAL DIRECTOR PRINCIPLES"]
    sentences = [sentence.strip().casefold() for sentence in reference.split(".")]

    subject_rule = next(
        (sentence for sentence in sentences if "source-stated subject" in sentence),
        "",
    )
    _assert_semantic_groups(
        subject_rule,
        (
            (("number",)),
            (("date",)),
            (("count",)),
            (("name",)),
            (("relationship",)),
            (("bind", "attach", "map")),
        ),
    )

    total_rule = next(
        (sentence for sentence in sentences if "exact total" in sentence),
        "",
    )
    _assert_semantic_groups(
        total_rule,
        (
            (("all named members", "every named member")),
            (("represented", "shown")),
            (("otherwise", "else")),
            (("omit", "leave out")),
        ),
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
    different["creative_direction"]["analytical_backbone"] = "A different signed concept."

    with pytest.raises(ValueError, match="published director authority"):
        direct_page(
            workspace,
            view,
            timeout=60,
            invoke=lambda *_args, **_kwargs: _result(different),
        )
    assert first.actual_prompt == compile_consulting_six_part_prompt(first.value, view)


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
    value["machine_record"]["selected_references"][0]["material_id"] = selected

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


def test_compile_prompt_has_exact_natural_language_sections_and_single_fixed_exclusions():
    value = _director_value(
        CompletePageMaterialView({}, (), (), "")
    )
    prompt = compile_consulting_six_part_prompt(value, _compiler_material_view())

    assert [line[3:] for line in prompt.splitlines() if line.startswith("## ")] == list(HEADINGS)
    for term in ("title", "logo", "footer", "page number"):
        assert prompt.lower().count(term) == 1
    assert prompt.count("central largest 17:8 content region") == 1
    assert prompt.count("visibly empty perimeter") == 1
    assert "cinematic evidence wall" not in prompt  # creativity stays open, not flattened into the prompt compiler


@pytest.mark.parametrize(
    ("section", "text", "preserved"),
    [
        (
            "strict_prohibitions",
            "Preserve verified identities. Do not generate a title, logo, footer, or page number.",
            "Preserve verified identities.",
        ),
        (
            "visual_style_and_color",
            "Use a clear reading path. Put all content in the central largest 17:8 content region.",
            "Use a clear reading path.",
        ),
    ],
)
def test_compile_prompt_removes_model_restatement_and_keeps_source_preservation(
    section: str, text: str, preserved: str
):
    value = _director_value(CompletePageMaterialView({}, (), (), ""))
    value["prompt_sections"][section] = text

    prompt = compile_consulting_six_part_prompt(value, _compiler_material_view())

    assert preserved in prompt
    for term in ("title", "logo", "footer", "page number"):
        assert prompt.lower().count(term) == 1
    assert prompt.count("central largest 17:8 content region") == 1


def test_visual_director_reference_is_short_generic_and_template_free():
    text = VISUAL_DIRECTOR_REFERENCE.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]

    assert 6 <= len(lines) <= 14
    assert len(text) < 2_400
    assert all(line.startswith("- ") for line in lines)
    forbidden = (
        "的卢",
        "并购",
        "房地产",
        "金融",
        "科技行业",
        "dashboard",
        "timeline",
        "three-column",
        "page type",
        "template",
    )
    assert not any(term.casefold() in text.casefold() for term in forbidden)


def test_visual_director_reference_requires_a_coherent_reporting_argument():
    text = VISUAL_DIRECTOR_REFERENCE.read_text(encoding="utf-8").casefold()

    assert "one coherent argument" in text
    assert "explanatory copy" in text
    assert "evidence" in text
    assert "conclusion" in text
    assert "single panorama" in text


def test_visual_director_reference_treats_confirmed_secondary_color_as_an_accent():
    text = VISUAL_DIRECTOR_REFERENCE.read_text(encoding="utf-8").casefold()

    assert "confirmed secondary color" in text
    assert "semantic accent" in text
    assert "large filled region" in text
    assert "wide path" in text


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda value: value["machine_record"]["selected_references"][0].update(
                {"material_id": "attachment:non-viewable"}
            ),
            "selected reference",
        ),
        (
            lambda value: value["prompt_sections"].update(
                {"task_and_canvas": "Read C:\\private\\source.png"}
            ),
            "custody|path",
        ),
        (
            lambda value: value["prompt_sections"].update(
                {"task_and_canvas": "Read /private/source.png"}
            ),
            "custody|path",
        ),
        (
            lambda value: value["prompt_sections"].update(
                {"text_and_typography": "Use digest " + "f" * 64}
            ),
            "custody|digest|hash",
        ),
        (
            lambda value: value["prompt_sections"].update(
                {"task_and_canvas": "Do not generate the logo."}
            ),
            "compiler-owned|boundary",
        ),
        (
            lambda value: value["machine_record"]["facts_and_sources"].append("   "),
            "non-whitespace|blank|facts",
        ),
        (
            lambda value: value["machine_record"]["material_use"][0].update(
                {"reason": " \t "}
            ),
            "non-whitespace|blank|reason",
        ),
        (
            lambda value: value["machine_record"]["selected_references"][0].update(
                {"identity": "   "}
            ),
            "non-whitespace|blank|identity",
        ),
        (
            lambda value: value["creative_direction"].update(
                {"analytical_backbone": " \t "}
            ),
            "creative|non-whitespace|blank|analytical_backbone",
        ),
    ],
)
def test_direct_page_rejects_invalid_audit_or_prompt_output(
    tmp_path: Path, mutate, message: str
):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    value = copy.deepcopy(_director_value(view))
    mutate(value)

    with pytest.raises(ValueError, match=message):
        direct_page(workspace, view, timeout=60, invoke=lambda *args, **kwargs: _result(value))


def test_direct_page_completes_omitted_material_audit_without_retrying_codex(
    tmp_path: Path,
):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    value = copy.deepcopy(_director_value(view))
    missing_id = value["machine_record"]["material_use"].pop()["material_id"]
    calls = 0

    def invoke(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _result(value)

    artifact = direct_page(workspace, view, timeout=60, invoke=invoke)

    assert calls == 1
    material_use = artifact.value["machine_record"]["material_use"]
    assert [item["material_id"] for item in material_use] == list(view.material_ids)
    completed = next(item for item in material_use if item["material_id"] == missing_id)
    assert completed["status"] == "background_understanding"
    assert "not selected as an Image2 reference" in completed["reason"]


def test_decide_correction_compiler_reuses_the_sealed_ui_canvas_background(
    tmp_path: Path,
):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    director = direct_page(
        workspace,
        view,
        timeout=60,
        invoke=lambda *args, **kwargs: _result(_director_value(view)),
    )
    candidate = workspace.project_copy / "candidate-background.png"
    candidate.write_bytes(b"candidate-background")
    problem = "The hierarchy is unusable."
    free_background = "Crimson linen fog with a granular paper texture."
    value = {
        "schema_version": "awesome-page-correction-v2",
        "page_number": workspace.page_number,
        "strategy": "edit_previous",
        "problem_addressed": [problem],
        "preserve": ["Preserve source-exact facts."],
        "selected_reference_ids": ["word-image:word-photo"],
        "prompt_sections": _prompt_sections(suffix=" with a corrected hierarchy"),
    }
    value["prompt_sections"]["task_and_canvas"] = free_background

    decision = decide_correction(
        workspace,
        view,
        director,
        previous_candidate=candidate,
        problems=[problem],
        timeout=60,
        invoke=lambda *args, **kwargs: _result(value),
    )

    scene = _compiled_prompt_sections(decision.actual_prompt)["Task and Canvas"]
    initial_scene = _compiled_prompt_sections(director.actual_prompt)[
        "Task and Canvas"
    ]
    background_color = str(view.value["visual_contract"]["background_color"])
    assert "source-authoritative background color" in scene
    assert "source-authoritative background color" in initial_scene
    assert background_color in scene
    assert decision.actual_prompt.casefold().count(background_color.casefold()) == 1
    assert free_background.casefold() not in decision.actual_prompt.casefold()


def test_decide_correction_schema_allows_only_viewable_image_map_ids(tmp_path: Path):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    director = direct_page(
        workspace,
        view,
        timeout=60,
        invoke=lambda *args, **kwargs: _result(_director_value(view)),
    )
    candidate = workspace.project_copy / "candidate-schema.png"
    candidate.write_bytes(b"candidate-schema")
    problem = "The source relationship is not visibly correct."
    value = {
        "schema_version": "awesome-page-correction-v2",
        "page_number": workspace.page_number,
        "strategy": "regenerate_from_materials",
        "problem_addressed": [problem],
        "preserve": ["Preserve source-exact facts and identities."],
        "selected_reference_ids": ["word-image:word-photo"],
        "prompt_sections": _prompt_sections(suffix=" with corrected source relationships"),
    }
    calls: list[dict[str, object]] = []

    def invoke(project: Path, **kwargs):
        calls.append({"project": project, **kwargs})
        return _result(value)

    decide_correction(
        workspace,
        view,
        director,
        previous_candidate=candidate,
        problems=[problem],
        timeout=60,
        invoke=invoke,
    )

    assert len(calls) == 1
    prompt_lines = str(calls[0]["prompt"]).splitlines()
    mapped_ids = [
        line.split(" = ", 1)[1]
        for line in prompt_lines
        if line.startswith("Image-") and "previous-candidate" not in line
    ]
    assert mapped_ids
    schema = calls[0]["output_schema"]
    selected = schema["properties"]["selected_reference_ids"]
    assert selected["items"].get("enum") == mapped_ids
    assert "uniqueItems" not in selected
    assert _unsupported_structured_output_paths(schema) == []
    assert selected.get("maxItems") == len(mapped_ids)

    for invalid_id in ("previous-candidate", "attachment:appendix-pdf"):
        invalid = copy.deepcopy(value)
        invalid["selected_reference_ids"] = [invalid_id]
        errors = list(Draft202012Validator(schema).iter_errors(invalid))
        assert any(list(error.absolute_path) == ["selected_reference_ids", 0] for error in errors)


def test_decide_correction_rejects_duplicate_selected_references_locally(tmp_path: Path):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    director = direct_page(
        workspace,
        view,
        timeout=60,
        invoke=lambda *args, **kwargs: _result(_director_value(view)),
    )
    candidate = workspace.project_copy / "candidate-duplicate-refs.png"
    candidate.write_bytes(b"candidate-duplicate-refs")
    problem = "The source relationship is not visibly correct."
    duplicated = {
        "schema_version": "awesome-page-correction-v2",
        "page_number": workspace.page_number,
        "strategy": "regenerate_from_materials",
        "problem_addressed": [problem],
        "preserve": ["Preserve source-exact facts and identities."],
        "selected_reference_ids": [
            "word-image:word-photo",
            "word-image:word-photo",
        ],
        "prompt_sections": _prompt_sections(suffix=" with corrected source relationships"),
    }

    with pytest.raises(ValueError, match="duplicate selected reference"):
        decide_correction(
            workspace,
            view,
            director,
            previous_candidate=candidate,
            problems=[problem],
            timeout=60,
            invoke=lambda *args, **kwargs: _result(duplicated),
        )


def test_decide_correction_schema_requires_empty_selection_without_viewable_ids(
    tmp_path: Path,
):
    workspace, view = _workspace_without_viewable_materials(tmp_path)
    director_value = _director_value(view)
    director_value["page_number"] = workspace.page_number
    director_value["machine_record"]["selected_references"] = []
    director = direct_page(
        workspace,
        view,
        timeout=60,
        invoke=lambda *args, **kwargs: _result(director_value),
    )
    candidate = workspace.project_copy / "candidate-no-refs.png"
    candidate.write_bytes(b"candidate-no-refs")
    problem = "The hierarchy is unusable."
    value = {
        "schema_version": "awesome-page-correction-v2",
        "page_number": workspace.page_number,
        "strategy": "edit_previous",
        "problem_addressed": [problem],
        "preserve": ["Preserve source-exact facts."],
        "selected_reference_ids": [],
        "prompt_sections": _prompt_sections(suffix=" with a corrected hierarchy"),
    }
    calls: list[dict[str, object]] = []

    def invoke(project: Path, **kwargs):
        calls.append({"project": project, **kwargs})
        return _result(value)

    decide_correction(
        workspace,
        view,
        director,
        previous_candidate=candidate,
        problems=[problem],
        timeout=60,
        invoke=invoke,
    )

    assert len(calls) == 1
    schema = calls[0]["output_schema"]
    selected = schema["properties"]["selected_reference_ids"]
    assert selected.get("maxItems") == 0
    invalid = copy.deepcopy(value)
    invalid["selected_reference_ids"] = ["previous-candidate"]
    errors = list(Draft202012Validator(schema).iter_errors(invalid))
    assert any(list(error.absolute_path) == ["selected_reference_ids"] for error in errors)


@pytest.mark.parametrize("strategy", ["edit_previous", "regenerate_from_materials"])
def test_decide_correction_lets_codex_choose_directly_and_keeps_candidate_separate(
    tmp_path: Path, strategy: str
):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    director = direct_page(
        workspace, view, timeout=60, invoke=lambda *args, **kwargs: _result(_director_value(view))
    )
    candidate = workspace.project_copy / "candidate-1.png"
    candidate.write_bytes(b"candidate")
    calls: list[dict[str, object]] = []
    correction_value = {
        "schema_version": "awesome-page-correction-v2",
        "page_number": 1,
        "strategy": strategy,
        "problem_addressed": ["The composition is plainly unusable in the 17:8 body region."],
        "preserve": ["Preserve the real subject identity and verified figures."],
        "selected_reference_ids": ["word-image:word-photo"],
        "prompt_sections": _prompt_sections(suffix=" with a materially wider hierarchy"),
    }

    def invoke(project: Path, **kwargs):
        calls.append({"project": project, **kwargs})
        return _result(correction_value)

    decision = decide_correction(
        workspace,
        view,
        director,
        previous_candidate=candidate,
        problems=["The composition is plainly unusable in the 17:8 body region."],
        timeout=60,
        invoke=invoke,
    )

    assert len(calls) == 1
    assert calls[0]["role"] == "awesome-page-correction"
    assert calls[0]["images"] == (*view.multimodal_images, candidate)
    prompt = str(calls[0]["prompt"])
    assert "Image-6 = previous-candidate (not a source material ID)" in prompt
    assert "selected_reference_ids may contain only exact IDs from IMAGE INPUT MAP" in prompt
    assert "If IMAGE INPUT MAP has no source image, return an empty list" in prompt
    assert "Never invent, search for, or mint a reference ID" in prompt
    assert json.dumps(view.value, ensure_ascii=False, sort_keys=True) in prompt
    assert json.dumps(director.value, ensure_ascii=False, sort_keys=True) in prompt
    problem_items = calls[0]["output_schema"]["properties"]["problem_addressed"]["items"]
    assert problem_items["enum"] == [
        "The composition is plainly unusable in the 17:8 body region."
    ]
    assert decision.strategy == strategy
    assert decision.problem_addressed == tuple(correction_value["problem_addressed"])
    assert decision.model_provider == "openai-test"


def test_decide_correction_rejects_unknown_refs_unstated_problems_and_unchanged_request(
    tmp_path: Path,
):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    director = direct_page(
        workspace, view, timeout=60, invoke=lambda *args, **kwargs: _result(_director_value(view))
    )
    candidate = workspace.project_copy / "candidate-1.png"
    candidate.write_bytes(b"candidate")
    base = {
        "schema_version": "awesome-page-correction-v2",
        "page_number": 1,
        "strategy": "edit_previous",
        "problem_addressed": ["Wrong aspect ratio"],
        "preserve": ["Keep the subject"],
        "selected_reference_ids": ["word-image:word-photo"],
        "prompt_sections": _prompt_sections(suffix=" corrected"),
    }

    unknown = copy.deepcopy(base)
    unknown["selected_reference_ids"] = ["previous-candidate"]
    with pytest.raises(
        ValueError,
        match="selected reference|schema rejected selected_reference_ids",
    ):
        decide_correction(
            workspace, view, director, previous_candidate=candidate,
            problems=["Wrong aspect ratio"], timeout=60,
            invoke=lambda *args, **kwargs: _result(unknown),
        )

    with pytest.raises(ValueError, match="problem.*stated|review|schema rejected problem_addressed"):
        decide_correction(
            workspace, view, director, previous_candidate=candidate,
            problems=["Different explicit problem"], timeout=60,
            invoke=lambda *args, **kwargs: _result(base),
        )

    unchanged = copy.deepcopy(base)
    unchanged["selected_reference_ids"] = list(director.selected_reference_ids)
    unchanged["prompt_sections"] = copy.deepcopy(director.value["prompt_sections"])
    with pytest.raises(ValueError, match="unchanged"):
        decide_correction(
            workspace, view, director, previous_candidate=candidate,
            problems=["Wrong aspect ratio"], timeout=60,
            invoke=lambda *args, **kwargs: _result(unchanged),
        )


def test_second_correction_rejects_same_request_as_previous_correction(tmp_path: Path):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    director = direct_page(
        workspace, view, timeout=60, invoke=lambda *args, **kwargs: _result(_director_value(view))
    )
    candidate_one = workspace.project_copy / "candidate-1.png"
    candidate_one.write_bytes(b"candidate-one")
    correction_value = {
        "schema_version": "awesome-page-correction-v2",
        "page_number": 1,
        "strategy": "edit_previous",
        "problem_addressed": ["Wrong aspect ratio"],
        "preserve": ["Keep the subject"],
        "selected_reference_ids": ["word-image:word-photo"],
        "prompt_sections": _prompt_sections(suffix=" corrected once"),
    }
    first = decide_correction(
        workspace,
        view,
        director,
        previous_candidate=candidate_one,
        problems=["Wrong aspect ratio"],
        timeout=60,
        invoke=lambda *args, **kwargs: _result(correction_value),
    )
    with pytest.raises(ValueError, match="consecutive|unchanged|same request"):
        decide_correction(
            workspace,
            view,
            director,
            previous_candidate=candidate_one,
            problems=["Wrong aspect ratio"],
            timeout=60,
            invoke=lambda *args, **kwargs: _result(correction_value),
            previous_decision=first,
            previous_request_candidate=candidate_one,
        )


def test_second_correction_accepts_changed_strategy_or_candidate_identity(tmp_path: Path):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    director = direct_page(
        workspace, view, timeout=60, invoke=lambda *args, **kwargs: _result(_director_value(view))
    )
    candidate_one = workspace.project_copy / "candidate-1.png"
    candidate_one.write_bytes(b"candidate-one")
    first_value = {
        "schema_version": "awesome-page-correction-v2",
        "page_number": 1,
        "strategy": "edit_previous",
        "problem_addressed": ["Wrong aspect ratio"],
        "preserve": ["Keep the subject"],
        "selected_reference_ids": ["word-image:word-photo"],
        "prompt_sections": _prompt_sections(suffix=" corrected once"),
    }
    first = decide_correction(
        workspace, view, director, previous_candidate=candidate_one,
        problems=["Wrong aspect ratio"], timeout=60,
        invoke=lambda *args, **kwargs: _result(first_value),
    )
    candidate_two = workspace.project_copy / "candidate-2.png"
    candidate_two.write_bytes(b"candidate-two")
    second_value = copy.deepcopy(first_value)

    second = decide_correction(
        workspace,
        view,
        director,
        previous_candidate=candidate_two,
        problems=["Wrong aspect ratio"],
        timeout=60,
        invoke=lambda *args, **kwargs: _result(second_value),
        previous_decision=first,
        previous_request_candidate=candidate_one,
    )

    assert second.strategy == "edit_previous"


def test_correction_rejects_blank_preserve_text(tmp_path: Path):
    workspace = _workspace(tmp_path)
    view = _material_view(workspace)
    director = direct_page(
        workspace, view, timeout=60, invoke=lambda *args, **kwargs: _result(_director_value(view))
    )
    candidate = workspace.project_copy / "candidate-1.png"
    candidate.write_bytes(b"candidate")
    correction_value = {
        "schema_version": "awesome-page-correction-v2",
        "page_number": 1,
        "strategy": "edit_previous",
        "problem_addressed": ["Wrong aspect ratio"],
        "preserve": ["   "],
        "selected_reference_ids": ["word-image:word-photo"],
        "prompt_sections": _prompt_sections(suffix=" corrected"),
    }

    with pytest.raises(ValueError, match="preserve|non-whitespace|blank"):
        decide_correction(
            workspace,
            view,
            director,
            previous_candidate=candidate,
            problems=["Wrong aspect ratio"],
            timeout=60,
            invoke=lambda *args, **kwargs: _result(correction_value),
        )

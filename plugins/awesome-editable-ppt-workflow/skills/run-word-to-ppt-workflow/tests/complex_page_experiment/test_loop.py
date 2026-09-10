from __future__ import annotations

import json
import hashlib
import hmac
import base64
import threading
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

import complex_page_experiment.loop as loop_module
from codex_subscription_runtime import CodexStructuredResult
from complex_page_experiment import (
    EvidenceRecorder,
    build_complete_page_material_view,
    open_live_page_workspace,
    open_live_page_recovery_workspace,
    verify_signed_acceptance_receipt,
)
from complex_page_experiment.director import DirectorArtifact
from complex_page_experiment.loop import (
    _local_correction,
    _repair_value,
    load_accepted_image_seal,
    run_candidate_loop,
)
from complex_page_experiment.review import (
    ReviewProblem,
    VisualReview,
    preflight_candidate,
    review_candidate_once,
)
from provider_keyring import signing_key
from test_director import _director_value, _result
from test_provider import _real_worker_runner, provider_fixture  # noqa: F401
from workflow_v6_state import load, save


def _review_result(
    decision: str,
    category: str = "severe_usability",
    detail: str = "candidate is clearly off topic",
):
    problems = [] if decision == "accept" else [
        {"category": category, "detail": detail, "repair_route": "edit"}
    ]
    return CodexStructuredResult(
        value={
            "schema_version": "awesome-independent-visual-review-v1",
            "decision": decision,
            "problems": problems,
        },
        thread_id=f"review-{decision}", turn_id="turn", model="review-model",
        model_provider="test", auth_mode="chatgpt", plan_type="plus",
        usage={}, safe_trace={}, effort="high", duration_seconds=0.1,
        startup_reused=True,
    )


def _off_ratio_response(*, marker_in_safe_region: bool) -> bytes:
    image = Image.new("RGB", (1536, 1024), "white")
    if marker_in_safe_region:
        image.paste("#d02030", (500, 400, 1036, 624))
    else:
        image.paste("#d02030", (500, 10, 1036, 120))
    stream = BytesIO()
    image.save(stream, format="PNG")
    response = {
        "data": [{"b64_json": base64.b64encode(stream.getvalue()).decode("ascii")}],
        "quality": "medium",
        "size": "1536x1024",
    }
    return json.dumps(response, sort_keys=True).encode("utf-8")


def _director_only(view):
    def invoke(_project, **kwargs):
        assert kwargs["role"] == "awesome-page-director"
        return _result(_director_value(view))

    return invoke


HUANGSHI_PROBLEM_PAGES = (
    (3, (("fact_integrity", "删除来源未提供的能力扩写，仅保留来源明确授权的四项能力名称。"),)),
    (25, (("fact_integrity", "将可见错字“清出表现挂钩”修正为源文“退出表现挂钩”，其余构图保持不变。"),)),
    (9, (("severe_usability", "删除正文图顶部与固定页标题重复的标题，保留正文构图。"),)),
    (10, (("primary_relationship", "恢复来源定义的主关系，其余构图保持不变。"),)),
    (35, (("core_exhibit_prominence", "放大核心成果承接图，使其成为明确视觉中心。"),)),
)


@pytest.mark.parametrize("page_number,problems", HUANGSHI_PROBLEM_PAGES)
def test_huangshi_problem_pages_build_one_local_edit(
    page_number, problems
):
    director = DirectorArtifact(
        value={"page_plan": {"image_prompt": f"page {page_number} source prompt"}},
        actual_prompt=f"page {page_number} source prompt",
        selected_reference_ids=(), quality="high", model="director",
        effort="high", duration_seconds=1.0, model_provider="test", usage={},
        runtime_trace={}, thread_id="thread", turn_id="turn",
    )
    review = VisualReview(
        decision="correct", problems=tuple(detail for _category, detail in problems),
        model="reviewer", effort="high", duration_seconds=1.0,
        problem_records=tuple(ReviewProblem(category, detail) for category, detail in problems),
    )

    frozen = json.loads(json.dumps(director.value))
    prompt, selected, strategy = _local_correction(review, director, next_attempt=2)

    assert strategy == "edit_previous"
    assert selected == ()
    assert prompt.count(problems[0][1]) == 1
    assert prompt.startswith("VISIBLE DEFECT\n")
    assert "\n\nREQUIRED REPAIR\n" in prompt
    assert "\n\nMUST STAY UNCHANGED\n" in prompt
    for boundary in (
        "accepted composition", "already-correct region", "frozen page plan",
        "complete facts", "confirmed colors", "fixed title", "logo", "footer",
        "page-number boundaries",
    ):
        assert boundary in prompt
    assert director.actual_prompt not in prompt
    assert director.value == frozen


def test_local_correction_repeats_each_frozen_numeric_authority_verbatim():
    numeric_authorities = [
        {
            "object_id": "chart-revenue",
            "name": "chart-revenue",
            "rendering_primitive": "column_bar",
            "chart_variant": "column",
            "title": "Revenue",
            "unit": "RMB million",
            "basis": "reported",
            "series": [
                {"name": "Revenue", "categories": ["2024", "2025"], "values": [120, 150]}
            ],
        },
        {
            "object_id": "chart-profit",
            "name": "chart-profit",
            "rendering_primitive": "line_point",
            "chart_variant": "line",
            "title": "Profit",
            "unit": "RMB million",
            "basis": "reported",
            "series": [
                {"name": "Profit", "categories": ["2024", "2025"], "values": [24, 30]}
            ],
        },
    ]
    director = DirectorArtifact(
        value={"page_plan": {"numeric_authorities": numeric_authorities}},
        actual_prompt="initial prompt",
        selected_reference_ids=(), quality="high", model="director",
        effort="high", duration_seconds=1.0, model_provider="test", usage={},
        runtime_trace={}, thread_id="thread", turn_id="turn",
    )
    review = VisualReview(
        decision="correct", problems=("fix one visible label",),
        model="reviewer", effort="high", duration_seconds=1.0,
        problem_records=(ReviewProblem("quantitative_truth", "fix one visible label"),),
    )

    frozen = json.loads(json.dumps(director.value))
    prompt, selected, strategy = _local_correction(review, director, next_attempt=2)

    assert selected == ()
    assert strategy == "edit_previous"
    assert json.dumps(numeric_authorities, ensure_ascii=False, sort_keys=True, separators=(",", ":")) in prompt
    assert "do not change, omit, merge, or derive" in prompt
    assert director.actual_prompt not in prompt
    assert director.value == frozen


def test_huangshi_page_25_typo_becomes_an_exact_reconstruction_repair():
    problem = ReviewProblem(
        "fact_integrity",
        "第三行可见文字错误，请将“清出”明确修正为“退出”，其余构图与内容保持不变。",
    )

    assert _repair_value(problem) == {
        "category": "fact_integrity",
        "detail": problem.detail,
        "find": "清出",
        "replace": "退出",
    }


def test_local_correction_rejects_more_than_one_review_problem():
    director = DirectorArtifact(
        value={"page_plan": {"image_prompt": "full prompt"}}, actual_prompt="full prompt",
        selected_reference_ids=(), quality="high", model="director", effort="high",
        duration_seconds=1.0, model_provider="test", usage={}, runtime_trace={},
        thread_id="thread", turn_id="turn",
    )
    problems = (
        ReviewProblem("fact_integrity", "First defect."),
        ReviewProblem("severe_usability", "Second defect."),
    )
    review = VisualReview(
        "correct", tuple(problem.detail for problem in problems), "reviewer", "high", 1.0,
        problem_records=problems,
    )

    with pytest.raises(ValueError, match="exactly one signed review problem"):
        _local_correction(review, director, next_attempt=2)


def _run(provider_fixture, monkeypatch, reviews, *, max_corrections=2,
         material_factory=None, director_invoke=None):
    workspace, view, recorder, _refs = provider_fixture
    review_values = iter(reviews)
    runner = _real_worker_runner(monkeypatch, [])
    return workspace, view, recorder, run_candidate_loop(
        workspace,
        timeout=17,
        recorder=recorder,
        material_view_factory=material_factory or (lambda _workspace: view),
        director_invoke=director_invoke or _director_only(view),
        reviewer_invoke=lambda *_args, **_kwargs: next(review_values),
        provider_runner=runner,
        max_corrections=max_corrections,
    )


def test_first_valid_candidate_accepts_without_default_extra_candidates(
    provider_fixture, monkeypatch
):
    workspace, _view, recorder, outcome = _run(
        provider_fixture, monkeypatch, [_review_result("accept")]
    )

    assert outcome.status == "accepted"
    assert len(outcome.attempts) == 1
    assert outcome.correction_count == 0
    assert outcome.accepted is not None and outcome.accepted.candidate.attempt == 1
    assert load_accepted_image_seal(workspace).candidate.path == outcome.attempts[0].path
    assert (
        load(workspace.project_copy)["pages"][0]["selected_candidate"]["operation"]
        == outcome.attempts[0].operation
    )
    summary = recorder.finalize()
    assert summary["call_totals"]["image2"] == 1
    assert summary["call_totals"]["visual_review"] == 1
    assert summary["call_totals"]["correction_decision"] == 0


def test_loader_migrates_only_legacy_selected_candidate_missing_operation(
    provider_fixture, monkeypatch,
):
    workspace, _view, _recorder, outcome = _run(
        provider_fixture, monkeypatch, [_review_result("accept")]
    )
    assert outcome.accepted is not None
    state = load(workspace.project_copy)
    state["pages"][0]["selected_candidate"].pop("operation")
    save(workspace.project_copy, state)

    recovered = load_accepted_image_seal(workspace)

    assert recovered is not None
    assert (
        load(workspace.project_copy)["pages"][0]["selected_candidate"]["operation"]
        == outcome.attempts[0].operation
    )


def test_loader_rejects_existing_selected_candidate_with_wrong_operation(
    provider_fixture, monkeypatch,
):
    workspace, _view, _recorder, outcome = _run(
        provider_fixture, monkeypatch, [_review_result("accept")]
    )
    assert outcome.accepted is not None
    state = load(workspace.project_copy)
    actual = outcome.attempts[0].operation
    state["pages"][0]["selected_candidate"]["operation"] = (
        "generate" if actual == "edit" else "edit"
    )
    save(workspace.project_copy, state)

    with pytest.raises(ValueError, match="accepted state does not match"):
        load_accepted_image_seal(workspace)


@pytest.mark.parametrize("race", ["identity", "state"])
def test_legacy_operation_migration_rejects_raced_state_without_overwriting_it(
    provider_fixture, monkeypatch, race,
):
    workspace, _view, _recorder, outcome = _run(
        provider_fixture, monkeypatch, [_review_result("accept")]
    )
    assert outcome.accepted is not None
    state = load(workspace.project_copy)
    state["pages"][0]["selected_candidate"].pop("operation")
    save(workspace.project_copy, state)
    original_load = loop_module._copied_state_without_material_reads
    calls = 0
    raced_bytes = None

    def race_before_locked_reload(current_workspace):
        nonlocal calls, raced_bytes
        calls += 1
        if calls == 2:
            changed = load(current_workspace.project_copy)
            if race == "identity":
                changed["confirmed_ui_digest"] = "0" * 64
            else:
                changed["pages"][0]["state"] = "reconstructing"
            save(current_workspace.project_copy, changed)
            raced_bytes = (current_workspace.project_copy / "workflow_v6.json").read_bytes()
        return original_load(current_workspace)

    monkeypatch.setattr(
        loop_module, "_copied_state_without_material_reads", race_before_locked_reload,
    )
    with pytest.raises(ValueError, match="changed during operation migration"):
        load_accepted_image_seal(workspace)

    assert raced_bytes is not None
    assert (workspace.project_copy / "workflow_v6.json").read_bytes() == raced_bytes
    assert "operation" not in load(workspace.project_copy)["pages"][0]["selected_candidate"]


@pytest.mark.parametrize(
    "path,replacement",
    [
        (("content_inventory", 0, "source_block_id"), "tampered-source"),
        (("content_inventory", 0, "source_quote"), "tampered-quote"),
        (("content_inventory", 0, "target"), "fixed_title"),
        (("content_inventory", 0, "display_copy"), "tampered-fact"),
        (("image_prompt",), "tampered design prompt"),
    ],
)
def test_accepted_receipt_seals_exact_direct_design(
    provider_fixture, monkeypatch, path, replacement,
):
    workspace, view, _recorder, outcome = _run(
        provider_fixture, monkeypatch, [_review_result("accept")]
    )
    assert outcome.accepted is not None
    receipt = json.loads(outcome.accepted.receipt_path.read_text(encoding="utf-8"))
    assert receipt["page_plan"] == _director_value(view)["page_plan"]
    verify_signed_acceptance_receipt(
        workspace, outcome.accepted.receipt_path.read_bytes()
    )

    target = receipt["page_plan"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement

    with pytest.raises(ValueError, match="signature"):
        verify_signed_acceptance_receipt(
            workspace,
            (json.dumps(
                receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ) + "\n").encode(),
        )


@pytest.mark.parametrize(
    "category",
    [
        "fact_integrity",
        "primary_relationship",
        "core_exhibit_prominence",
        "quantitative_truth",
        "severe_usability",
    ],
)
def test_review_problem_uses_deterministic_local_edit_then_accepts(
    provider_fixture, monkeypatch, category
):
    workspace, view, recorder, outcome = _run(
        provider_fixture,
        monkeypatch,
        [_review_result("correct", category=category), _review_result("accept")],
        director_invoke=_director_only(provider_fixture[1]),
    )

    assert outcome.status == "accepted"
    assert [item.attempt for item in outcome.attempts] == [1, 2]
    assert outcome.correction_count == 1
    second = json.loads((
        workspace.project_copy / "04_v6/experiments" / workspace.experiment_id /
        "request_attempt_2.json"
    ).read_text(encoding="utf-8"))
    assert second["strategy"] == "edit_previous"
    assert second["request"]["correction_candidate_input"] is not None
    assert second["request"]["selected_material_reference_ids"] == []
    assert second["request"]["image_roles"] == ["previous-candidate-to-correct"]
    assert outcome.attempts[0].request_identity != outcome.attempts[1].request_identity
    summary = recorder.finalize()
    assert summary["call_totals"]["image2"] == 2
    assert summary["call_totals"]["visual_review"] == 2
    assert summary["call_totals"]["correction_decision"] == 1
    correction = next(
        call for call in summary["calls"] if call["kind"] == "correction_decision"
    )
    assert correction["model"] == "deterministic-local"
    assert correction["duration_seconds"] == 0.0
    assert correction["metadata"]["quota_bearing"] is False
def test_technical_failure_consumes_slot_edits_previous_without_review(
    provider_fixture, monkeypatch
):
    import complex_page_experiment.loop as loop

    real_preflight = preflight_candidate
    calls = 0

    def preflight(candidate):
        nonlocal calls
        calls += 1
        result = real_preflight(candidate)
        if candidate.attempt == 1:
            return type(result)(False, result.path, result.mime_type, result.width,
                                result.height, result.sha256,
                                ("Candidate dimensions must be exactly 1904x896 pixels.",))
        return result

    monkeypatch.setattr(loop, "preflight_candidate", preflight)
    _workspace, _view, recorder, outcome = _run(
        provider_fixture, monkeypatch, [_review_result("accept")]
    )

    assert outcome.status == "accepted"
    assert outcome.correction_count == 1
    assert len(outcome.attempts) == 2
    assert calls >= 2
    summary = recorder.finalize()
    assert summary["call_totals"]["visual_review"] == 1
    assert summary["call_totals"]["correction_decision"] == 0


@pytest.mark.parametrize("max_corrections,expected_attempts", [(0, 1), (1, 2), (2, 3)])
def test_budget_never_creates_fourth_candidate_or_fallback(
    provider_fixture, monkeypatch, max_corrections, expected_attempts
):
    workspace, _view, recorder, outcome = _run(
        provider_fixture,
        monkeypatch,
        [_review_result("correct")] * expected_attempts,
        max_corrections=max_corrections,
    )

    assert outcome.status == "failed"
    assert outcome.accepted is None
    assert len(outcome.attempts) == expected_attempts
    assert outcome.correction_count == max_corrections
    assert outcome.failure_problems == ("candidate is clearly off topic",)
    assert not list(provider_fixture[0].project_copy.glob("04_v6/images/page_001.json"))
    assert recorder.finalize()["call_totals"]["image2"] == expected_attempts
    for attempt in range(2, expected_attempts + 1):
        seal = json.loads((
            workspace.project_copy / "04_v6/experiments" / workspace.experiment_id /
            f"request_attempt_{attempt}.json"
        ).read_text(encoding="utf-8"))
        assert seal["strategy"] == "edit_previous"
        assert seal["request"]["selected_material_reference_ids"] == []
        assert seal["request"]["correction_candidate_input"]["attempt"] == attempt - 1


def test_recovery_happens_before_material_access_and_skips_every_call(
    provider_fixture, monkeypatch
):
    workspace, _view, recorder, first = _run(
        provider_fixture, monkeypatch, [_review_result("accept")]
    )
    recorder.finalize()
    director_root = workspace.project_copy / "02_v6/experiments" / workspace.experiment_id
    (director_root / "director_v2.json").replace(director_root / "director.json")
    material_path = workspace.project_copy / "02_v6/awesome_page_materials/page_001.json"
    material_path.unlink()
    evidence_root = workspace.project_copy / "04_v6/experiments" / workspace.experiment_id
    from complex_page_experiment import EvidenceRecorder
    resumed = EvidenceRecorder(
        evidence_root, project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id,
    )

    recovered = run_candidate_loop(
        workspace,
        timeout=17,
        recorder=resumed,
        material_view_factory=lambda _workspace: pytest.fail("material chain was read"),
        director_invoke=lambda *_a, **_k: pytest.fail("director was called"),
        reviewer_invoke=lambda *_a, **_k: pytest.fail("reviewer was called"),
        provider_runner=lambda *_a, **_k: pytest.fail("provider was called"),
    )

    assert recovered.accepted is not None and recovered.accepted.recovered is True
    assert recovered.accepted.candidate.path == first.accepted.candidate.path
    summary = resumed.finalize()
    assert summary["recovery"]["skipped_calls"] == [
        "page_director", "correction_decision", "image2", "visual_review", "reconstruct_edit"
    ]


def test_unfinished_legacy_director_state_is_rejected_before_any_call(
    provider_fixture,
):
    workspace, view, recorder, _refs = provider_fixture
    director_root = workspace.project_copy / "02_v6/experiments" / workspace.experiment_id
    director_root.mkdir(parents=True, exist_ok=True)
    legacy_path = director_root / "director.json"
    legacy_bytes = b'{"schema_version":"awesome-page-director-authority-v1"}\n'
    legacy_path.write_bytes(legacy_bytes)

    with pytest.raises(
        ValueError,
        match="unfinished v1 page",
    ):
        run_candidate_loop(
            workspace,
            timeout=17,
            recorder=recorder,
            material_view_factory=lambda _workspace: pytest.fail("material chain was read"),
            director_invoke=lambda *_a, **_k: pytest.fail("director was called"),
            reviewer_invoke=lambda *_a, **_k: pytest.fail("reviewer was called"),
            provider_runner=lambda *_a, **_k: pytest.fail("provider was called"),
        )

    assert legacy_path.read_bytes() == legacy_bytes
    assert not (director_root / "director_v2.json").exists()


def test_completed_page_keeps_the_same_accepted_seal_for_zero_call_recovery(
    provider_fixture, monkeypatch,
):
    workspace, _view, recorder, outcome = _run(
        provider_fixture, monkeypatch, [_review_result("accept")]
    )
    assert outcome.accepted is not None
    state = load(workspace.project_copy)
    state["pages"][0]["state"] = "page_complete"
    save(workspace.project_copy, state)

    recovered = load_accepted_image_seal(workspace)

    assert recovered is not None and recovered.recovered is True
    assert recovered.candidate.path == outcome.accepted.candidate.path
    assert load(workspace.project_copy)["pages"][0]["state"] == "page_complete"


def test_review_sees_center_cropped_candidate_rejects_lost_body_then_recovery_uses_zero_image2(
    provider_fixture, monkeypatch
):
    import codex_gpt_image
    from complex_page_experiment import EvidenceRecorder

    workspace, view, recorder, _refs = provider_fixture
    responses = iter([
        _off_ratio_response(marker_in_safe_region=False),
        _off_ratio_response(marker_in_safe_region=True),
    ])
    provider_calls = 0

    monkeypatch.setattr(
        codex_gpt_image,
        "load_or_login_codex_auth",
        lambda _args: codex_gpt_image.CodexAuth("test-access-token"),
    )
    monkeypatch.setenv("AWESOME_PROVIDER_TEST_BUILD", "1")

    def provider_runner(command: list[str], timeout: int) -> None:
        nonlocal provider_calls
        provider_calls += 1
        raw = next(responses)
        monkeypatch.setenv(
            "AWESOME_PROVIDER_TEST_RESPONSE_B64", base64.b64encode(raw).decode("ascii")
        )
        assert timeout == 17
        assert codex_gpt_image.main(command[2:]) == 0

    review_count = 0
    crop_problem = "The final 17:8 crop removed required body content from the outer canvas."

    def reviewer_invoke(_project: Path, **kwargs):
        nonlocal review_count
        review_count += 1
        candidate_snapshot = Path(kwargs["images"][0])
        with Image.open(candidate_snapshot).convert("RGB") as image:
            assert image.size == (1904, 896)
            red_survives = any(
                red > 180 and green < 80 and blue < 100
                for red, green, blue in image.getdata()
            )
        if not red_survives:
            return _review_result(
                "correct", category="severe_usability", detail=crop_problem
            )
        return _review_result("accept")

    first = run_candidate_loop(
        workspace,
        timeout=17,
        recorder=recorder,
        material_view_factory=build_complete_page_material_view,
        director_invoke=_director_only(view),
        reviewer_invoke=reviewer_invoke,
        provider_runner=provider_runner,
    )
    assert first.status == "accepted"
    assert len(first.attempts) == 2
    assert provider_calls == 2
    assert review_count == 2
    for candidate in first.attempts:
        trace = json.loads(candidate.trace_path.read_text(encoding="utf-8"))
        assert trace["size"] == "1904x896"
        assert trace["quality"] in {"medium", "high"}
        adaptation = trace["warnings"][0]
        assert adaptation["provider_original_size"] == {"width": 1536, "height": 1024}
        assert adaptation["provider_original_quality"] == "medium"
        assert adaptation["final_size"] == {"width": 1904, "height": 896}
        assert adaptation["scaling"] == {
            "mode": "uniform", "resampling": "lanczos", "stretched": False,
        }
    recorder.finalize()

    resumed = EvidenceRecorder(
        workspace.project_copy / "04_v6/experiments" / workspace.experiment_id,
        project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id,
    )
    recovered = run_candidate_loop(
        workspace,
        timeout=17,
        recorder=resumed,
        material_view_factory=lambda _workspace: pytest.fail("material chain was read"),
        director_invoke=lambda *_a, **_k: pytest.fail("director was called"),
        reviewer_invoke=lambda *_a, **_k: pytest.fail("reviewer was called"),
        provider_runner=lambda *_a, **_k: pytest.fail("Image2 was called during recovery"),
    )
    assert recovered.status == "accepted"
    assert recovered.accepted is not None and recovered.accepted.recovered is True
    assert provider_calls == 2


def test_corrupt_or_partial_acceptance_fails_closed(provider_fixture, monkeypatch):
    workspace, _view, recorder, _outcome = _run(
        provider_fixture, monkeypatch, [_review_result("accept")]
    )
    recorder.finalize()
    canonical = workspace.project_copy / "04_v6/images/page_001.json"
    canonical.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="accepted|seal|receipt|signature"):
        load_accepted_image_seal(workspace)


def test_accepted_state_without_either_receipt_fails_closed(provider_fixture, monkeypatch):
    workspace, _view, recorder, _outcome = _run(
        provider_fixture, monkeypatch, [_review_result("accept")]
    )
    recorder.finalize()
    (workspace.project_copy / "04_v6/images/page_001.json").unlink()
    (workspace.project_copy / "04_v6/experiments" / workspace.experiment_id /
     "accepted_image.json").unlink()

    with pytest.raises(ValueError, match="accepted|seal|receipt"):
        load_accepted_image_seal(workspace)


def test_valid_receipts_recover_interrupted_copied_state_without_material_chain(
    provider_fixture, monkeypatch
):
    workspace, _view, recorder, outcome = _run(
        provider_fixture, monkeypatch, [_review_result("accept")]
    )
    recorder.finalize()
    state = load(workspace.project_copy)
    page = state["pages"][0]
    page["state"] = "prepared"
    page["first_candidate"] = None
    page["selected_candidate"] = None
    page["qa_attempts"] = 0
    save(workspace.project_copy, state)
    (workspace.project_copy / "02_v6/awesome_page_materials/page_001.json").unlink()

    recovered = load_accepted_image_seal(workspace)

    assert recovered is not None and recovered.recovered is True
    assert recovered.candidate.path == outcome.accepted.candidate.path
    raw_state = json.loads(
        (workspace.project_copy / "workflow_v6.json").read_text(encoding="utf-8")
    )
    assert raw_state["pages"][0]["state"] == "accepted"


@pytest.mark.parametrize("value", [-1, 3, True, 1.5])
def test_max_corrections_accepts_only_integer_zero_through_two(
    provider_fixture, monkeypatch, value
):
    workspace, view, recorder, _refs = provider_fixture
    with pytest.raises(ValueError, match="max_corrections"):
        run_candidate_loop(
            workspace, timeout=17, recorder=recorder,
            material_view_factory=lambda _workspace: view,
            max_corrections=value,
        )


def test_same_page_concurrent_loops_single_flight_then_recover(
    provider_fixture, monkeypatch,
):
    workspace, view, _recorder, _refs = provider_fixture
    from complex_page_experiment import EvidenceRecorder
    evidence_root = workspace.project_copy / "04_v6/experiments" / workspace.experiment_id
    entered = threading.Event()
    release = threading.Event()
    counts = {"director": 0, "provider": 0, "review": 0}
    lock = threading.Lock()
    real_runner = _real_worker_runner(monkeypatch, [])

    def director(_project, **kwargs):
        with lock:
            counts["director"] += 1
        entered.set()
        assert release.wait(10)
        return _result(_director_value(view))

    def provider(args, timeout):
        with lock:
            counts["provider"] += 1
        return real_runner(args, timeout)

    def review(*_args, **_kwargs):
        with lock:
            counts["review"] += 1
        return _review_result("accept")

    outcomes = []
    errors = []

    def run_one():
        try:
            recorder = EvidenceRecorder(
                evidence_root, project_copy=workspace.project_copy,
                experiment_id=workspace.experiment_id,
            )
            outcomes.append(run_candidate_loop(
                workspace, timeout=17, recorder=recorder,
                material_view_factory=lambda _workspace: view,
                director_invoke=director, reviewer_invoke=review,
                provider_runner=provider,
            ))
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=run_one)
    second = threading.Thread(target=run_one)
    first.start()
    assert entered.wait(10)
    second.start()
    release.set()
    first.join(60); second.join(60)

    assert errors == []
    assert len(outcomes) == 2
    assert counts == {"director": 1, "provider": 1, "review": 1}
    assert sorted(item.accepted.recovered for item in outcomes) == [False, True]


def test_same_page_waiter_reuses_exhausted_failure_without_new_calls(
    provider_fixture, monkeypatch,
):
    workspace, view, _recorder, _refs = provider_fixture
    from complex_page_experiment import EvidenceRecorder
    evidence_root = workspace.project_copy / "04_v6/experiments" / workspace.experiment_id
    entered = threading.Event(); release = threading.Event()
    counts = {"director": 0, "provider": 0, "review": 0}
    guard = threading.Lock()
    real_runner = _real_worker_runner(monkeypatch, [])

    def director(_project, **_kwargs):
        with guard: counts["director"] += 1
        entered.set(); assert release.wait(10)
        return _result(_director_value(view))

    def provider(args, timeout):
        with guard: counts["provider"] += 1
        return real_runner(args, timeout)

    def review(*_args, **_kwargs):
        with guard: counts["review"] += 1
        return _review_result("correct")

    outcomes = []; errors = []

    def run_one():
        try:
            recorder = EvidenceRecorder(
                evidence_root, project_copy=workspace.project_copy,
                experiment_id=workspace.experiment_id,
            )
            outcomes.append(run_candidate_loop(
                workspace, timeout=17, recorder=recorder,
                material_view_factory=lambda _workspace: view,
                director_invoke=director, reviewer_invoke=review,
                provider_runner=provider, max_corrections=0,
            ))
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=run_one); second = threading.Thread(target=run_one)
    first.start(); assert entered.wait(10); second.start(); release.set()
    first.join(60); second.join(60)

    assert errors == []
    assert [item.status for item in outcomes] == ["failed", "failed"]
    assert all(len(item.attempts) == 1 for item in outcomes)
    assert all(item.failure_problems == ("candidate is clearly off topic",) for item in outcomes)
    assert counts == {"director": 1, "provider": 1, "review": 1}


def test_explicit_recovery_of_interrupted_review_adopts_candidate_and_only_re_reviews(
    provider_fixture, monkeypatch,
):
    copied_workspace, _view, _recorder, _refs = provider_fixture
    workspace = open_live_page_workspace(copied_workspace.project_copy, 1)
    view = build_complete_page_material_view(workspace)
    recorder = EvidenceRecorder(
        workspace.experiment_root, project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id, page_number=1,
        source_identity=workspace.source_snapshot_sha256,
    )
    provider = _real_worker_runner(monkeypatch, [])

    with pytest.raises(RuntimeError, match="review timeout"):
        run_candidate_loop(
            workspace, timeout=17, recorder=recorder,
            material_view_factory=lambda _workspace: view,
            director_invoke=_director_only(view),
            reviewer_invoke=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("review timeout")),
            provider_runner=provider,
        )
    resumed = EvidenceRecorder(
        workspace.experiment_root, project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id, page_number=1,
        source_identity=workspace.source_snapshot_sha256,
    )
    failed = run_candidate_loop(
        workspace, timeout=17, recorder=resumed,
        material_view_factory=lambda _workspace: pytest.fail("ordinary retry reread materials"),
        director_invoke=lambda *_a, **_k: pytest.fail("ordinary retry called director"),
        reviewer_invoke=lambda *_a, **_k: pytest.fail("ordinary retry called reviewer"),
        provider_runner=lambda *_a, **_k: pytest.fail("ordinary retry called Image2"),
    )
    assert failed.status == "failed"
    resumed.finalize()

    recovery = open_live_page_recovery_workspace(
        workspace.project_copy, 1, recovery_round=1,
    )
    recovery_recorder = EvidenceRecorder(
        recovery.experiment_root, project_copy=recovery.project_copy,
        experiment_id=recovery.experiment_id, page_number=1,
        source_identity=recovery.source_snapshot_sha256,
    )
    outcome = run_candidate_loop(
        recovery, timeout=17, recorder=recovery_recorder,
        material_view_factory=build_complete_page_material_view,
        director_invoke=lambda *_a, **_k: pytest.fail("recovery called director"),
        reviewer_invoke=lambda *_a, **_k: _review_result("accept"),
        provider_runner=lambda *_a, **_k: pytest.fail("recovery called Image2"),
    )

    assert outcome.status == "accepted"
    assert len(outcome.attempts) == 1
    summary = recovery_recorder.finalize()
    assert summary["image2_total_calls"] == 0
    assert summary["candidate_adoptions"][0]["attempt"] == 1
    assert summary["candidate_adoptions"][0]["prior_experiment_id"] == workspace.experiment_id
    receipt = json.loads(outcome.accepted.receipt_path.read_text(encoding="utf-8"))
    assert receipt["experiment_id"] == recovery.experiment_id
    assert receipt["evidence_checkpoint"]["candidate_origin"] == "adopted_candidate"
    state = load(workspace.project_copy)
    state["pages"][0]["state"] = "page_complete"
    save(workspace.project_copy, state)

    from workflow_v6_pipeline import PipelineConfiguration, PipelineDependencies, run_pages

    (workspace.project_copy / "02_v6/page_composition.json").write_text(
        json.dumps({
            "artifact_version": "page-composition-v1", "page_count": 4,
            "warnings": [],
            "pages": [{
                "output_page_number": number, "source_page_id": number,
                "page_role": "content", "role_source": "explicit",
                "chapter_title": "", "fixed_page_title": f"Page {number}",
                "source_page_number": number,
                "material_source_block_ids": [f"body-{number}"],
                "visible_page_number": True,
            } for number in range(1, 5)],
        }),
        encoding="utf-8",
    )
    ordinary = run_pages(
        workspace.project_copy, [1],
        dependencies=PipelineDependencies(
            candidate_loop=run_candidate_loop,
            director_invoke=lambda *_a, **_k: pytest.fail("ordinary reentry called director"),
            reviewer_invoke=lambda *_a, **_k: pytest.fail("ordinary reentry called reviewer"),
            provider_runner=lambda *_a, **_k: pytest.fail("ordinary reentry called Image2"),
        ),
        configuration=PipelineConfiguration(page_workers=1),
    )
    assert ordinary.failed_pages == {}
    assert ordinary.completed_pages == (1,)
    assert ordinary.page_outcomes[1].accepted.recovered is True
    assert ordinary.page_outcomes[1].accepted.receipt_path == outcome.accepted.receipt_path
    assert load(workspace.project_copy)["pages"][0]["state"] == "page_complete"


def test_explicit_recovery_of_exhausted_content_starts_from_prior_candidate_and_caps_two_edits(
    provider_fixture, monkeypatch,
):
    copied_workspace, _view, _recorder, _refs = provider_fixture
    workspace = open_live_page_workspace(copied_workspace.project_copy, 1)
    view = build_complete_page_material_view(workspace)
    recorder = EvidenceRecorder(
        workspace.experiment_root, project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id, page_number=1,
        source_identity=workspace.source_snapshot_sha256,
    )
    first = run_candidate_loop(
        workspace, timeout=17, recorder=recorder,
        material_view_factory=lambda _workspace: view,
        director_invoke=_director_only(view),
        reviewer_invoke=lambda *_a, **_k: _review_result("correct"),
        provider_runner=_real_worker_runner(monkeypatch, []),
        max_corrections=0,
    )
    assert first.status == "failed"
    recorder.finalize()
    recovery = open_live_page_recovery_workspace(
        workspace.project_copy, 1, recovery_round=1,
    )
    recovery_recorder = EvidenceRecorder(
        recovery.experiment_root, project_copy=recovery.project_copy,
        experiment_id=recovery.experiment_id, page_number=1,
        source_identity=recovery.source_snapshot_sha256,
    )
    provider_calls = 0
    real_provider = _real_worker_runner(monkeypatch, [])

    def provider(args, timeout):
        nonlocal provider_calls
        provider_calls += 1
        real_provider(args, timeout)

    outcome = run_candidate_loop(
        recovery, timeout=17, recorder=recovery_recorder,
        material_view_factory=build_complete_page_material_view,
        director_invoke=lambda *_a, **_k: pytest.fail("recovery called director"),
        reviewer_invoke=lambda *_a, **_k: _review_result("accept"),
        provider_runner=provider,
        max_corrections=2,
    )

    assert outcome.status == "accepted"
    assert provider_calls == 1
    assert outcome.correction_count == 1
    assert len(outcome.attempts) == 2
    request = json.loads((
        recovery.experiment_root / "request_attempt_2.json"
    ).read_text(encoding="utf-8"))
    assert request["strategy"] == "edit_previous"
    assert request["request"]["correction_candidate_input"]["path"] == (
        first.attempts[-1].path.relative_to(workspace.project_copy).as_posix()
    )
    summary = recovery_recorder.finalize()
    assert summary["image2_total_calls"] == 1
    assert len(summary["candidate_adoptions"]) == 1

    reopened = open_live_page_recovery_workspace(
        recovery.project_copy, 1, recovery_round=1,
    )
    reentry_recorder = EvidenceRecorder(
        reopened.experiment_root, project_copy=reopened.project_copy,
        experiment_id=reopened.experiment_id, page_number=1,
        source_identity=reopened.source_snapshot_sha256,
    )
    reentered = run_candidate_loop(
        reopened, timeout=17, recorder=reentry_recorder,
        material_view_factory=lambda *_a: pytest.fail("accepted reentry reread materials"),
        director_invoke=lambda *_a, **_k: pytest.fail("accepted reentry called director"),
        reviewer_invoke=lambda *_a, **_k: pytest.fail("accepted reentry called reviewer"),
        provider_runner=lambda *_a, **_k: pytest.fail("accepted reentry called Image2"),
    )
    assert reentered.status == "accepted"
    assert reentered.accepted.recovered is True


def _open_exhausted_recovery(provider_fixture, monkeypatch):
    copied_workspace, _view, _recorder, _refs = provider_fixture
    workspace = open_live_page_workspace(copied_workspace.project_copy, 1)
    view = build_complete_page_material_view(workspace)
    recorder = EvidenceRecorder(
        workspace.experiment_root, project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id, page_number=1,
        source_identity=workspace.source_snapshot_sha256,
    )
    failed = run_candidate_loop(
        workspace, timeout=17, recorder=recorder,
        material_view_factory=lambda _workspace: view,
        director_invoke=_director_only(view),
        reviewer_invoke=lambda *_a, **_k: _review_result("correct"),
        provider_runner=_real_worker_runner(monkeypatch, []),
        max_corrections=0,
    )
    assert failed.status == "failed"
    recorder.finalize()
    recovery = open_live_page_recovery_workspace(
        workspace.project_copy, 1, recovery_round=1,
    )
    return workspace, recovery, failed


def _open_interrupted_recovery(provider_fixture, monkeypatch):
    copied_workspace, _view, _recorder, _refs = provider_fixture
    workspace = open_live_page_workspace(copied_workspace.project_copy, 1)
    view = build_complete_page_material_view(workspace)
    recorder = EvidenceRecorder(
        workspace.experiment_root, project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id, page_number=1,
        source_identity=workspace.source_snapshot_sha256,
    )
    with pytest.raises(RuntimeError, match="review timeout"):
        run_candidate_loop(
            workspace, timeout=17, recorder=recorder,
            material_view_factory=lambda _workspace: view,
            director_invoke=_director_only(view),
            reviewer_invoke=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("review timeout")),
            provider_runner=_real_worker_runner(monkeypatch, []),
        )
    resumed = EvidenceRecorder(
        workspace.experiment_root, project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id, page_number=1,
        source_identity=workspace.source_snapshot_sha256,
    )
    assert run_candidate_loop(
        workspace, timeout=17, recorder=resumed,
        material_view_factory=lambda *_a: pytest.fail("ordinary retry reread materials"),
        director_invoke=lambda *_a, **_k: pytest.fail("ordinary retry called director"),
        reviewer_invoke=lambda *_a, **_k: pytest.fail("ordinary retry called reviewer"),
        provider_runner=lambda *_a, **_k: pytest.fail("ordinary retry called Image2"),
    ).status == "failed"
    resumed.finalize()
    recovery = open_live_page_recovery_workspace(
        workspace.project_copy, 1, recovery_round=1,
    )
    return workspace, recovery


def _open_interrupted_recovery_from_terminal_attempt(
    provider_fixture, monkeypatch, terminal_attempt,
):
    copied_workspace, _view, _recorder, _refs = provider_fixture
    workspace = open_live_page_workspace(copied_workspace.project_copy, 1)
    view = build_complete_page_material_view(workspace)
    recorder = EvidenceRecorder(
        workspace.experiment_root, project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id, page_number=1,
        source_identity=workspace.source_snapshot_sha256,
    )
    review_calls = 0

    def reviewer(*_args, **_kwargs):
        nonlocal review_calls
        review_calls += 1
        if review_calls == terminal_attempt:
            raise RuntimeError(f"review timeout at attempt {terminal_attempt}")
        return _review_result("correct")

    with pytest.raises(RuntimeError, match=f"review timeout at attempt {terminal_attempt}"):
        run_candidate_loop(
            workspace, timeout=17, recorder=recorder,
            material_view_factory=lambda _workspace: view,
            director_invoke=_director_only(view), reviewer_invoke=reviewer,
            provider_runner=_real_worker_runner(monkeypatch, []),
            max_corrections=2,
        )
    resumed = EvidenceRecorder(
        workspace.experiment_root, project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id, page_number=1,
        source_identity=workspace.source_snapshot_sha256,
    )
    failed = run_candidate_loop(
        workspace, timeout=17, recorder=resumed,
        material_view_factory=lambda *_a: pytest.fail("ordinary retry reread materials"),
        director_invoke=lambda *_a, **_k: pytest.fail("ordinary retry called director"),
        reviewer_invoke=lambda *_a, **_k: pytest.fail("ordinary retry called reviewer"),
        provider_runner=lambda *_a, **_k: pytest.fail("ordinary retry called Image2"),
    )
    assert failed.status == "failed"
    assert [candidate.attempt for candidate in failed.attempts] == list(
        range(1, terminal_attempt + 1)
    )
    resumed.finalize()
    recovery = open_live_page_recovery_workspace(
        workspace.project_copy, 1, recovery_round=1,
    )
    return workspace, recovery


def test_recovery_uses_signed_correct_review_after_interruption_before_next_candidate(
    provider_fixture, monkeypatch,
):
    # Break caught: a generic interrupted outcome relabels a terminal candidate
    # with a verified signed correct review as unreviewed, allowing re-review
    # instead of the already-authorized bounded edit.
    copied_workspace, _view, _recorder, _refs = provider_fixture
    workspace = open_live_page_workspace(copied_workspace.project_copy, 1)
    view = build_complete_page_material_view(workspace)
    recorder = EvidenceRecorder(
        workspace.experiment_root, project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id, page_number=1,
        source_identity=workspace.source_snapshot_sha256,
    )
    original_provider = _real_worker_runner(monkeypatch, [])
    real_record_correction_decision = loop_module._record_correction_decision

    def interrupted_after_correct_review(*args, **kwargs):
        real_record_correction_decision(*args, **kwargs)
        raise RuntimeError("interrupted after signed correct review")

    monkeypatch.setattr(
        loop_module, "_record_correction_decision", interrupted_after_correct_review,
    )

    with pytest.raises(RuntimeError, match="interrupted after signed correct review"):
        run_candidate_loop(
            workspace, timeout=17, recorder=recorder,
            material_view_factory=lambda _workspace: view,
            director_invoke=_director_only(view),
            reviewer_invoke=lambda *_a, **_k: _review_result("correct"),
            provider_runner=original_provider,
        )
    monkeypatch.setattr(
        loop_module, "_record_correction_decision", real_record_correction_decision,
    )
    review_path = workspace.experiment_root / "review_inputs/attempt_1/review_result.json"
    original_review = review_path.read_bytes()
    resumed = EvidenceRecorder(
        workspace.experiment_root, project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id, page_number=1,
        source_identity=workspace.source_snapshot_sha256,
    )
    failed = run_candidate_loop(
        workspace, timeout=17, recorder=resumed,
        material_view_factory=lambda *_a: pytest.fail("ordinary retry reread materials"),
        director_invoke=lambda *_a, **_k: pytest.fail("ordinary retry called director"),
        reviewer_invoke=lambda *_a, **_k: pytest.fail("ordinary retry called reviewer"),
        provider_runner=lambda *_a, **_k: pytest.fail("ordinary retry called Image2"),
    )
    assert failed.status == "failed"
    assert len(failed.attempts) == 1
    resumed.finalize()

    recovery = open_live_page_recovery_workspace(
        workspace.project_copy, 1, recovery_round=1,
    )
    recovery_recorder = EvidenceRecorder(
        recovery.experiment_root, project_copy=recovery.project_copy,
        experiment_id=recovery.experiment_id, page_number=1,
        source_identity=recovery.source_snapshot_sha256,
    )
    recovery_provider_calls = 0
    recovery_provider = _real_worker_runner(monkeypatch, [])

    def edit_once(args, timeout):
        nonlocal recovery_provider_calls
        recovery_provider_calls += 1
        recovery_provider(args, timeout)

    accepted = run_candidate_loop(
        recovery, timeout=17, recorder=recovery_recorder,
        material_view_factory=build_complete_page_material_view,
        director_invoke=lambda *_a, **_k: pytest.fail("recovery called director"),
        reviewer_invoke=lambda *_a, **_k: _review_result("accept"),
        provider_runner=edit_once,
        max_corrections=2,
    )

    assert accepted.status == "accepted"
    assert [candidate.attempt for candidate in accepted.attempts] == [1, 2]
    assert accepted.correction_count == 1
    assert recovery_provider_calls == 1
    assert review_path.read_bytes() == original_review
    summary = recovery_recorder.finalize()
    assert summary["candidate_adoptions"][0]["disposition"] == "correct"
    assert summary["image2_total_calls"] == 1


def _open_exhausted_recovery_from_terminal_attempt(
    provider_fixture, monkeypatch, terminal_attempt,
):
    copied_workspace, _view, _recorder, _refs = provider_fixture
    workspace = open_live_page_workspace(copied_workspace.project_copy, 1)
    view = build_complete_page_material_view(workspace)
    recorder = EvidenceRecorder(
        workspace.experiment_root, project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id, page_number=1,
        source_identity=workspace.source_snapshot_sha256,
    )
    failed = run_candidate_loop(
        workspace, timeout=17, recorder=recorder,
        material_view_factory=lambda _workspace: view,
        director_invoke=_director_only(view),
        reviewer_invoke=lambda *_a, **_k: _review_result("correct"),
        provider_runner=_real_worker_runner(monkeypatch, []),
        max_corrections=terminal_attempt - 1,
    )
    assert failed.status == "failed"
    assert [candidate.attempt for candidate in failed.attempts] == list(
        range(1, terminal_attempt + 1)
    )
    recorder.finalize()
    recovery = open_live_page_recovery_workspace(
        workspace.project_copy, 1, recovery_round=1,
    )
    return workspace, recovery


def _seal_adoption_only_interrupted_recovery(recovery):
    recorder = EvidenceRecorder(
        recovery.experiment_root, project_copy=recovery.project_copy,
        experiment_id=recovery.experiment_id, page_number=1,
        source_identity=recovery.source_snapshot_sha256,
    )
    with pytest.raises(RuntimeError, match="adopted review timeout"):
        run_candidate_loop(
            recovery, timeout=17, recorder=recorder,
            material_view_factory=build_complete_page_material_view,
            director_invoke=lambda *_a, **_k: pytest.fail("recovery called director"),
            reviewer_invoke=lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("adopted review timeout")
            ),
            provider_runner=lambda *_a, **_k: pytest.fail("adoption-only recovery called Image2"),
        )
    resumed = EvidenceRecorder(
        recovery.experiment_root, project_copy=recovery.project_copy,
        experiment_id=recovery.experiment_id, page_number=1,
        source_identity=recovery.source_snapshot_sha256,
    )
    failed = run_candidate_loop(
        recovery, timeout=17, recorder=resumed,
        material_view_factory=lambda *_a: pytest.fail("interrupted reentry reread materials"),
        director_invoke=lambda *_a, **_k: pytest.fail("interrupted reentry called director"),
        reviewer_invoke=lambda *_a, **_k: pytest.fail("interrupted reentry called reviewer"),
        provider_runner=lambda *_a, **_k: pytest.fail("interrupted reentry called Image2"),
    )
    assert failed.status == "failed"
    assert [candidate.attempt for candidate in failed.attempts] == [1]
    assert not (recovery.experiment_root / "attempt_1.json").exists()
    resumed.finalize()
    return failed


def _resign_test_receipt(value):
    value.pop("hmac_sha256", None)
    key_id, key = signing_key()
    value["key_id"] = key_id
    value["hmac_sha256"] = hmac.new(
        key,
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ) + "\n"


def test_explicit_recovery_allows_at_most_two_new_edits_and_uses_current_round_predecessor(
    provider_fixture, monkeypatch,
):
    workspace, recovery, failed = _open_exhausted_recovery(provider_fixture, monkeypatch)
    recorder = EvidenceRecorder(
        recovery.experiment_root, project_copy=recovery.project_copy,
        experiment_id=recovery.experiment_id, page_number=1,
        source_identity=recovery.source_snapshot_sha256,
    )
    calls = 0
    real_provider = _real_worker_runner(monkeypatch, [])

    def provider(args, timeout):
        nonlocal calls
        calls += 1
        real_provider(args, timeout)

    outcome = run_candidate_loop(
        recovery, timeout=17, recorder=recorder,
        material_view_factory=build_complete_page_material_view,
        director_invoke=lambda *_a, **_k: pytest.fail("recovery called director"),
        reviewer_invoke=lambda *_a, **_k: _review_result("correct"),
        provider_runner=provider, max_corrections=2,
    )

    assert outcome.status == "failed"
    assert calls == 2
    assert outcome.correction_count == 2
    assert [item.attempt for item in outcome.attempts] == [1, 2, 3]
    attempt_2 = json.loads((recovery.experiment_root / "request_attempt_2.json").read_text(encoding="utf-8"))
    attempt_3 = json.loads((recovery.experiment_root / "request_attempt_3.json").read_text(encoding="utf-8"))
    assert attempt_2["immediate_predecessor_authority"]["archive_path"] == (
        failed.attempts[-1].prompt_path.relative_to(workspace.project_copy).as_posix()
    )
    assert attempt_3["immediate_predecessor_authority"]["archive_path"] == (
        "04_v6/experiments/live-page-001-recovery-001/attempt_2.json"
    )
    summary = recorder.finalize()
    assert summary["image2_total_calls"] == 2
    assert summary["call_totals"]["visual_review"] == 2


def test_same_explicit_recovery_round_reuses_its_sealed_failure_without_calls(
    provider_fixture, monkeypatch,
):
    _workspace, recovery, _failed = _open_exhausted_recovery(provider_fixture, monkeypatch)
    first_recorder = EvidenceRecorder(
        recovery.experiment_root, project_copy=recovery.project_copy,
        experiment_id=recovery.experiment_id, page_number=1,
        source_identity=recovery.source_snapshot_sha256,
    )
    first = run_candidate_loop(
        recovery, timeout=17, recorder=first_recorder,
        material_view_factory=build_complete_page_material_view,
        director_invoke=lambda *_a, **_k: pytest.fail("recovery called director"),
        reviewer_invoke=lambda *_a, **_k: pytest.fail("recovery called reviewer"),
        provider_runner=lambda *_a, **_k: pytest.fail("recovery called Image2"),
        max_corrections=0,
    )
    assert first.status == "failed"
    first_recorder.finalize()

    second_recorder = EvidenceRecorder(
        recovery.experiment_root, project_copy=recovery.project_copy,
        experiment_id=recovery.experiment_id, page_number=1,
        source_identity=recovery.source_snapshot_sha256,
    )
    second = run_candidate_loop(
        recovery, timeout=17, recorder=second_recorder,
        material_view_factory=lambda *_a: pytest.fail("reentry reread materials"),
        director_invoke=lambda *_a, **_k: pytest.fail("reentry called director"),
        reviewer_invoke=lambda *_a, **_k: pytest.fail("reentry called reviewer"),
        provider_runner=lambda *_a, **_k: pytest.fail("reentry called Image2"),
        max_corrections=2,
    )
    assert second.status == "failed"
    assert second.failure_problems == first.failure_problems


def test_same_explicit_recovery_round_is_single_flight_and_waiter_reuses_acceptance(
    provider_fixture, monkeypatch,
):
    copied_workspace, _view, _recorder, _refs = provider_fixture
    workspace = open_live_page_workspace(copied_workspace.project_copy, 1)
    view = build_complete_page_material_view(workspace)
    original_recorder = EvidenceRecorder(
        workspace.experiment_root, project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id, page_number=1,
        source_identity=workspace.source_snapshot_sha256,
    )
    with pytest.raises(RuntimeError, match="review timeout"):
        run_candidate_loop(
            workspace, timeout=17, recorder=original_recorder,
            material_view_factory=lambda _workspace: view,
            director_invoke=_director_only(view),
            reviewer_invoke=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("review timeout")),
            provider_runner=_real_worker_runner(monkeypatch, []),
        )
    resumed = EvidenceRecorder(
        workspace.experiment_root, project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id, page_number=1,
        source_identity=workspace.source_snapshot_sha256,
    )
    assert run_candidate_loop(
        workspace, timeout=17, recorder=resumed,
        material_view_factory=lambda *_a: pytest.fail("ordinary retry reread materials"),
        director_invoke=lambda *_a, **_k: pytest.fail("ordinary retry called director"),
        reviewer_invoke=lambda *_a, **_k: pytest.fail("ordinary retry called reviewer"),
        provider_runner=lambda *_a, **_k: pytest.fail("ordinary retry called Image2"),
    ).status == "failed"
    resumed.finalize()
    recovery = open_live_page_recovery_workspace(
        workspace.project_copy, 1, recovery_round=1,
    )
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    guard = threading.Lock()
    outcomes: list[object] = []
    errors: list[BaseException] = []

    def reviewer(*_args, **_kwargs):
        nonlocal calls
        with guard:
            calls += 1
        entered.set()
        assert release.wait(10)
        return _review_result("accept")

    def run_one():
        try:
            recorder = EvidenceRecorder(
                recovery.experiment_root, project_copy=recovery.project_copy,
                experiment_id=recovery.experiment_id, page_number=1,
                source_identity=recovery.source_snapshot_sha256,
            )
            outcomes.append(run_candidate_loop(
                recovery, timeout=17, recorder=recorder,
                material_view_factory=build_complete_page_material_view,
                director_invoke=lambda *_a, **_k: pytest.fail("recovery called director"),
                reviewer_invoke=reviewer,
                provider_runner=lambda *_a, **_k: pytest.fail("recovery called Image2"),
            ))
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=run_one)
    second = threading.Thread(target=run_one)
    first.start()
    assert entered.wait(10)
    second.start()
    release.set()
    first.join(60)
    second.join(60)

    assert errors == []
    assert calls == 1
    assert len(outcomes) == 2
    assert all(getattr(item, "status") == "accepted" for item in outcomes)
    assert sorted(getattr(item, "accepted").recovered for item in outcomes) == [False, True]


def test_adoption_only_interrupted_recovery_preserves_candidate_for_next_explicit_round(
    provider_fixture, monkeypatch,
):
    _workspace, recovery_1 = _open_interrupted_recovery(provider_fixture, monkeypatch)
    recorder_1 = EvidenceRecorder(
        recovery_1.experiment_root, project_copy=recovery_1.project_copy,
        experiment_id=recovery_1.experiment_id, page_number=1,
        source_identity=recovery_1.source_snapshot_sha256,
    )
    with pytest.raises(RuntimeError, match="second review timeout"):
        run_candidate_loop(
            recovery_1, timeout=17, recorder=recorder_1,
            material_view_factory=build_complete_page_material_view,
            director_invoke=lambda *_a, **_k: pytest.fail("recovery called director"),
            reviewer_invoke=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("second review timeout")),
            provider_runner=lambda *_a, **_k: pytest.fail("adoption-only recovery called Image2"),
        )
    resumed_1 = EvidenceRecorder(
        recovery_1.experiment_root, project_copy=recovery_1.project_copy,
        experiment_id=recovery_1.experiment_id, page_number=1,
        source_identity=recovery_1.source_snapshot_sha256,
    )
    failed_1 = run_candidate_loop(
        recovery_1, timeout=17, recorder=resumed_1,
        material_view_factory=lambda *_a: pytest.fail("interrupted reentry reread materials"),
        director_invoke=lambda *_a, **_k: pytest.fail("interrupted reentry called director"),
        reviewer_invoke=lambda *_a, **_k: pytest.fail("interrupted reentry called reviewer"),
        provider_runner=lambda *_a, **_k: pytest.fail("interrupted reentry called Image2"),
    )
    assert failed_1.status == "failed"
    assert len(failed_1.attempts) == 1
    assert not (recovery_1.experiment_root / "attempt_1.json").exists()
    assert failed_1.attempts[0].prompt_path == (
        _workspace.experiment_root / "attempt_1.json"
    )
    resumed_1.finalize()

    recovery_2 = open_live_page_recovery_workspace(
        recovery_1.project_copy, 1, recovery_round=2,
    )
    recorder_2 = EvidenceRecorder(
        recovery_2.experiment_root, project_copy=recovery_2.project_copy,
        experiment_id=recovery_2.experiment_id, page_number=1,
        source_identity=recovery_2.source_snapshot_sha256,
    )
    accepted = run_candidate_loop(
        recovery_2, timeout=17, recorder=recorder_2,
        material_view_factory=build_complete_page_material_view,
        director_invoke=lambda *_a, **_k: pytest.fail("second recovery called director"),
        reviewer_invoke=lambda *_a, **_k: _review_result("accept"),
        provider_runner=lambda *_a, **_k: pytest.fail("second adoption-only recovery called Image2"),
    )
    assert accepted.status == "accepted"
    assert len(accepted.attempts) == 1
    assert recorder_2.finalize()["image2_total_calls"] == 0


@pytest.mark.parametrize("terminal_attempt", [2, 3])
def test_next_recovery_round_resolves_adopted_candidate_from_original_terminal_attempt(
    provider_fixture, monkeypatch, terminal_attempt,
):
    workspace, recovery_1 = _open_interrupted_recovery_from_terminal_attempt(
        provider_fixture, monkeypatch, terminal_attempt,
    )
    failed_1 = _seal_adoption_only_interrupted_recovery(recovery_1)
    assert failed_1.attempts[0].prompt_path == (
        workspace.experiment_root / f"attempt_{terminal_attempt}.json"
    )

    recovery_2 = open_live_page_recovery_workspace(
        recovery_1.project_copy, 1, recovery_round=2,
    )
    recorder_2 = EvidenceRecorder(
        recovery_2.experiment_root, project_copy=recovery_2.project_copy,
        experiment_id=recovery_2.experiment_id, page_number=1,
        source_identity=recovery_2.source_snapshot_sha256,
    )
    accepted = run_candidate_loop(
        recovery_2, timeout=17, recorder=recorder_2,
        material_view_factory=build_complete_page_material_view,
        director_invoke=lambda *_a, **_k: pytest.fail("second recovery called director"),
        reviewer_invoke=lambda *_a, **_k: _review_result("accept"),
        provider_runner=lambda *_a, **_k: pytest.fail("second recovery called Image2"),
    )

    assert accepted.status == "accepted"
    assert len(accepted.attempts) == 1
    assert accepted.attempts[0].attempt == 1
    assert accepted.attempts[0].prompt_path == (
        workspace.experiment_root / f"attempt_{terminal_attempt}.json"
    )
    assert recorder_2.finalize()["image2_total_calls"] == 0


def test_exhausted_terminal_attempt_three_is_strictly_preflighted_then_edited_in_recovery(
    provider_fixture, monkeypatch,
):
    workspace, recovery = _open_exhausted_recovery_from_terminal_attempt(
        provider_fixture, monkeypatch, 3,
    )
    recorder = EvidenceRecorder(
        recovery.experiment_root, project_copy=recovery.project_copy,
        experiment_id=recovery.experiment_id, page_number=1,
        source_identity=recovery.source_snapshot_sha256,
    )
    provider_calls = 0
    real_provider = _real_worker_runner(monkeypatch, [])

    def provider(args, timeout):
        nonlocal provider_calls
        provider_calls += 1
        real_provider(args, timeout)

    accepted = run_candidate_loop(
        recovery, timeout=17, recorder=recorder,
        material_view_factory=build_complete_page_material_view,
        director_invoke=lambda *_a, **_k: pytest.fail("recovery called director"),
        reviewer_invoke=lambda *_a, **_k: _review_result("accept"),
        provider_runner=provider,
    )

    assert accepted.status == "accepted"
    assert [candidate.attempt for candidate in accepted.attempts] == [1, 2]
    assert accepted.attempts[0].prompt_path == workspace.experiment_root / "attempt_3.json"
    assert accepted.attempts[1].prompt_path == recovery.experiment_root / "attempt_2.json"
    assert provider_calls == 1
    summary = recorder.finalize()
    assert summary["image2_total_calls"] == 1
    assert summary["candidate_adoptions"][0]["prior_attempt"] == 3


@pytest.mark.parametrize("tamper", ["path", "sha256", "request_identity"])
def test_next_recovery_round_rejects_adopted_candidate_binding_mismatch(
    provider_fixture, monkeypatch, tamper,
):
    _workspace, recovery_1 = _open_interrupted_recovery_from_terminal_attempt(
        provider_fixture, monkeypatch, 3,
    )
    _seal_adoption_only_interrupted_recovery(recovery_1)
    failed_path = recovery_1.experiment_root / "failed_outcome.json"
    failed = json.loads(failed_path.read_text(encoding="utf-8"))
    candidate = failed["attempts"][-1]
    if tamper == "path":
        alternate = recovery_1.experiment_root / "signed-alternate-candidate.png"
        alternate.write_bytes(
            (recovery_1.project_copy / Path(candidate["path"])).read_bytes()
        )
        candidate["path"] = alternate.relative_to(recovery_1.project_copy).as_posix()
    elif tamper == "sha256":
        candidate["sha256"] = "f" * 64
    else:
        candidate["request_identity"] = "f" * 64
    failed_path.write_text(_resign_test_receipt(failed), encoding="utf-8")

    recovery_2 = open_live_page_recovery_workspace(
        recovery_1.project_copy, 1, recovery_round=2,
    )
    recorder_2 = EvidenceRecorder(
        recovery_2.experiment_root, project_copy=recovery_2.project_copy,
        experiment_id=recovery_2.experiment_id, page_number=1,
        source_identity=recovery_2.source_snapshot_sha256,
    )
    with pytest.raises(ValueError, match="candidate|bytes changed"):
        run_candidate_loop(
            recovery_2, timeout=17, recorder=recorder_2,
            material_view_factory=build_complete_page_material_view,
            director_invoke=lambda *_a, **_k: pytest.fail("second recovery called director"),
            reviewer_invoke=lambda *_a, **_k: pytest.fail("binding mismatch reached reviewer"),
            provider_runner=lambda *_a, **_k: pytest.fail("binding mismatch reached Image2"),
        )


def test_interrupted_recovery_preserves_adoption_and_completed_new_edit(
    provider_fixture, monkeypatch,
):
    _workspace, recovery, _failed = _open_exhausted_recovery(provider_fixture, monkeypatch)
    recorder = EvidenceRecorder(
        recovery.experiment_root, project_copy=recovery.project_copy,
        experiment_id=recovery.experiment_id, page_number=1,
        source_identity=recovery.source_snapshot_sha256,
    )
    with pytest.raises(RuntimeError, match="edit review timeout"):
        run_candidate_loop(
            recovery, timeout=17, recorder=recorder,
            material_view_factory=build_complete_page_material_view,
            director_invoke=lambda *_a, **_k: pytest.fail("recovery called director"),
            reviewer_invoke=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("edit review timeout")),
            provider_runner=_real_worker_runner(monkeypatch, []),
        )
    resumed = EvidenceRecorder(
        recovery.experiment_root, project_copy=recovery.project_copy,
        experiment_id=recovery.experiment_id, page_number=1,
        source_identity=recovery.source_snapshot_sha256,
    )
    outcome = run_candidate_loop(
        recovery, timeout=17, recorder=resumed,
        material_view_factory=lambda *_a: pytest.fail("interrupted edit reread materials"),
        director_invoke=lambda *_a, **_k: pytest.fail("interrupted edit called director"),
        reviewer_invoke=lambda *_a, **_k: pytest.fail("interrupted edit called reviewer"),
        provider_runner=lambda *_a, **_k: pytest.fail("interrupted edit called Image2"),
    )
    assert outcome.status == "failed"
    assert [candidate.attempt for candidate in outcome.attempts] == [1, 2]
    assert not (recovery.experiment_root / "attempt_1.json").exists()
    assert outcome.attempts[1].prompt_path == recovery.experiment_root / "attempt_2.json"


@pytest.mark.parametrize("tamper", ["unsigned", "signed-cross-page"])
def test_recovery_predecessor_rejects_tampered_or_cross_page_authority_before_image2(
    provider_fixture, monkeypatch, tamper,
):
    _workspace, recovery, _failed = _open_exhausted_recovery(provider_fixture, monkeypatch)
    recorder = EvidenceRecorder(
        recovery.experiment_root, project_copy=recovery.project_copy,
        experiment_id=recovery.experiment_id, page_number=1,
        source_identity=recovery.source_snapshot_sha256,
    )
    real_attempt = loop_module.run_provider_attempt

    def intercepted(workspace, request, **kwargs):
        path = workspace.experiment_root / "recovery_authority.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        if tamper == "unsigned":
            value["candidate"]["request_identity"] = "f" * 64
        else:
            value["page_number"] = 2
            value.pop("hmac_sha256")
            key_id, key = signing_key()
            value["key_id"] = key_id
            value["hmac_sha256"] = hmac.new(
                key,
                json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        path.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        return real_attempt(workspace, request, **kwargs)

    monkeypatch.setattr(loop_module, "run_provider_attempt", intercepted)
    with pytest.raises(ValueError, match="recovery authority|recovery predecessor"):
        run_candidate_loop(
            recovery, timeout=17, recorder=recorder,
            material_view_factory=build_complete_page_material_view,
            director_invoke=lambda *_a, **_k: pytest.fail("recovery called director"),
            reviewer_invoke=lambda *_a, **_k: _review_result("accept"),
            provider_runner=lambda *_a, **_k: pytest.fail("tampered recovery reached Image2"),
            max_corrections=2,
        )


def test_zero_image2_adoption_rejects_recovery_authority_tamper_before_acceptance(
    provider_fixture, monkeypatch,
):
    _workspace, recovery = _open_interrupted_recovery(provider_fixture, monkeypatch)
    recorder = EvidenceRecorder(
        recovery.experiment_root, project_copy=recovery.project_copy,
        experiment_id=recovery.experiment_id, page_number=1,
        source_identity=recovery.source_snapshot_sha256,
    )

    def reviewer(*_args, **_kwargs):
        authority = recovery.experiment_root / "recovery_authority.json"
        authority.write_bytes(authority.read_bytes() + b"changed")
        return _review_result("accept")

    with pytest.raises(ValueError, match="recovery authority"):
        run_candidate_loop(
            recovery, timeout=17, recorder=recorder,
            material_view_factory=build_complete_page_material_view,
            director_invoke=lambda *_a, **_k: pytest.fail("recovery called director"),
            reviewer_invoke=reviewer,
            provider_runner=lambda *_a, **_k: pytest.fail("recovery called Image2"),
        )
    assert not (recovery.experiment_root / "accepted-image.json").exists()


@pytest.mark.parametrize("tamper", ["candidate", "director", "material", "source"])
def test_recovery_rejects_changed_frozen_authority_before_external_calls(
    provider_fixture, monkeypatch, tamper,
):
    _workspace, recovery, failed = _open_exhausted_recovery(provider_fixture, monkeypatch)
    recorder = EvidenceRecorder(
        recovery.experiment_root, project_copy=recovery.project_copy,
        experiment_id=recovery.experiment_id, page_number=1,
        source_identity=recovery.source_snapshot_sha256,
    )
    if tamper == "candidate":
        failed.attempts[-1].path.write_bytes(failed.attempts[-1].path.read_bytes() + b"changed")
    elif tamper == "director":
        director = recovery.project_copy / "02_v6/experiments/live-page-001/director_v2.json"
        director.write_bytes(director.read_bytes() + b"changed")
    elif tamper == "source":
        recovery = replace(recovery, source_snapshot_sha256="f" * 64)
    material = build_complete_page_material_view(recovery)
    if tamper == "material":
        material = replace(material, value={**material.value, "fixed_page_title": "changed"})
    with pytest.raises((ValueError, RuntimeError), match="candidate|director|material|source identity"):
        run_candidate_loop(
            recovery, timeout=17, recorder=recorder,
            material_view_factory=lambda _workspace: material,
            director_invoke=lambda *_a, **_k: pytest.fail("recovery called director"),
            reviewer_invoke=lambda *_a, **_k: pytest.fail("changed authority reached reviewer"),
            provider_runner=lambda *_a, **_k: pytest.fail("changed authority reached Image2"),
            max_corrections=2,
        )


@pytest.mark.parametrize("fault", ["director", "provider", "review"])
def test_interrupted_durable_call_blocks_automatic_resubmission(
    provider_fixture, monkeypatch, fault,
):
    import complex_page_experiment.loop as loop
    workspace, view, recorder, _refs = provider_fixture
    calls = {"director": 0, "provider": 0, "review": 0}
    real_record_director = loop._record_director
    real_provider_attempt = loop.run_provider_attempt
    real_runner = _real_worker_runner(monkeypatch, [])

    def director(project, **kwargs):
        calls["director"] += 1
        return _result(_director_value(view))

    def record_director(*args, **kwargs):
        real_record_director(*args, **kwargs)
        if fault == "director":
            raise RuntimeError("crash after durable director call")

    def provider(args, timeout):
        calls["provider"] += 1
        real_runner(args, timeout)

    def durable_provider(*args, **kwargs):
        result = real_provider_attempt(*args, **kwargs)
        if fault == "provider":
            raise RuntimeError("crash after durable provider call")
        return result

    def review(*_args, **_kwargs):
        calls["review"] += 1
        if fault == "review":
            result = _review_result("accept")
            raise RuntimeError("crash after durable review call")
        return _review_result("accept")

    if fault == "review":
        real_review = loop.review_candidate_once

        def durable_review(*args, **kwargs):
            result = real_review(*args, **kwargs)
            calls["review"] += 1
            raise RuntimeError("crash after durable review call")

        monkeypatch.setattr(loop, "review_candidate_once", durable_review)
        reviewer = lambda *_a, **_k: _review_result("accept")
    else:
        reviewer = review
    monkeypatch.setattr(loop, "_record_director", record_director)
    monkeypatch.setattr(loop, "run_provider_attempt", durable_provider)

    with pytest.raises(RuntimeError, match="crash after durable"):
        run_candidate_loop(
            workspace, timeout=17, recorder=recorder,
            material_view_factory=lambda _workspace: view,
            director_invoke=director, reviewer_invoke=reviewer,
            provider_runner=provider,
        )
    before = dict(calls)
    from complex_page_experiment import EvidenceRecorder
    resumed = EvidenceRecorder(
        workspace.project_copy / "04_v6/experiments" / workspace.experiment_id,
        project_copy=workspace.project_copy, experiment_id=workspace.experiment_id,
    )
    outcome = run_candidate_loop(
        workspace, timeout=17, recorder=resumed,
        material_view_factory=lambda _workspace: pytest.fail("material was reread"),
        director_invoke=lambda *_a, **_k: pytest.fail("director repeated"),
        reviewer_invoke=lambda *_a, **_k: pytest.fail("review repeated"),
        provider_runner=lambda *_a, **_k: pytest.fail("provider repeated"),
    )

    assert outcome.status == "failed"
    assert outcome.failure_problems == ("interrupted prior candidate run; automatic resubmission blocked",)
    assert calls == before


def test_rejected_candidate_cannot_be_forged_accepted_without_new_review(
    provider_fixture, monkeypatch,
):
    import complex_page_experiment.loop as loop
    prior = None

    def one_real_review(*args, **kwargs):
        nonlocal prior
        if prior is None:
            prior = review_candidate_once(*args, **kwargs)
            return prior
        return replace(prior, decision="accept", problems=(), problem_records=())

    monkeypatch.setattr(loop, "review_candidate_once", one_real_review)
    workspace, view, recorder, _refs = provider_fixture
    reviews = iter([_review_result("correct")])
    with pytest.raises(ValueError, match="review.*authority|VisualReview"):
        run_candidate_loop(
            workspace, timeout=17, recorder=recorder,
            material_view_factory=lambda _workspace: view,
            director_invoke=_director_only(view),
            reviewer_invoke=lambda *_args, **_kwargs: next(reviews),
            provider_runner=_real_worker_runner(monkeypatch, []),
        )
    assert not (workspace.project_copy / "04_v6/images/page_001.json").exists()


def test_recovery_rejects_signed_receipt_pointing_to_wrong_review_candidate(
    provider_fixture, monkeypatch,
):
    workspace, _view, recorder, outcome = _run(
        provider_fixture, monkeypatch,
        [_review_result("correct"), _review_result("accept")],
    )
    assert outcome.accepted is not None
    root = workspace.project_copy
    accepted_path = root / "04_v6/experiments" / workspace.experiment_id / "accepted_image.json"
    value = json.loads(accepted_path.read_text(encoding="utf-8"))
    wrong_review = root / "04_v6/experiments" / workspace.experiment_id / "review_inputs/attempt_1/review_result.json"
    value["accepted_review"]["authority_path"] = wrong_review.relative_to(root).as_posix()
    value["accepted_review"]["authority_sha256"] = hashlib.sha256(wrong_review.read_bytes()).hexdigest()
    value["evidence_checkpoint"]["review_authority_sha256"] = value["accepted_review"]["authority_sha256"]
    checkpoint = dict(value["evidence_checkpoint"])
    checkpoint.pop("checkpoint_sha256")
    value["evidence_checkpoint"]["checkpoint_sha256"] = hashlib.sha256(
        json.dumps(checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    value.pop("hmac_sha256")
    key_id, key = signing_key()
    value["key_id"] = key_id
    value["hmac_sha256"] = hmac.new(
        key, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    accepted_path.write_text(payload, encoding="utf-8")
    (root / "04_v6/images/page_001.json").write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match="review|candidate|checkpoint"):
        load_accepted_image_seal(workspace)

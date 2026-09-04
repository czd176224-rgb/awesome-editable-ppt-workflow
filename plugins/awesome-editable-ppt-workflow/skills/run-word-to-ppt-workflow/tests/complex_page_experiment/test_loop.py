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

from codex_subscription_runtime import CodexStructuredResult
from complex_page_experiment import (
    build_complete_page_material_view,
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
        {"category": category, "detail": detail}
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
        value={"page_plan": {"page_purpose": f"frozen page {page_number}"}},
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
        value={"page_plan": {"page_purpose": "frozen"}}, actual_prompt="full prompt",
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


@pytest.mark.parametrize(
    "path,replacement",
    [
        (("primary_relationship", "nodes", 0, "node_id"), "tampered-node"),
        (("primary_relationship", "edges", 0, "to_node"), "tampered-endpoint"),
        (("primary_relationship", "grammar"), "flow"),
        (("core_exhibit", "fact_ids", 0), "tampered-fact"),
        (("reading_path",), "tampered reading path"),
    ],
)
def test_accepted_receipt_seals_exact_v3_page_plan(
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


def test_unfinished_v1_director_state_requires_clean_v3_page_restart_before_any_call(
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
        match="unfinished v1 page.*restart this page from the compact consulting director v3",
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
        material_view_factory=lambda _workspace: view,
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

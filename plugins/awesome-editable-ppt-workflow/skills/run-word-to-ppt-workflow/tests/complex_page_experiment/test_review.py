from __future__ import annotations

import json
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from codex_subscription_runtime import CodexStructuredResult
from complex_page_experiment.director import DirectorArtifact, direct_page
from complex_page_experiment.materials import CompletePageMaterialView
from complex_page_experiment.provider import (
    build_experiment_image_request,
    run_provider_attempt,
)
from complex_page_experiment.review import (
    preflight_candidate,
    review_candidate_once,
    validate_published_review_authority,
)
from test_director import _director_value, _result
from test_provider import _real_worker_runner, provider_fixture


def test_review_output_schema_uses_supported_structured_output_subset() -> None:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "schemas"
        / "complex_page_review_v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    unsupported: list[str] = []

    def visit(value: object, path: str = "$") -> None:
        if isinstance(value, dict):
            if ("const" in value or "enum" in value) and "type" not in value:
                unsupported.append(f"{path}: missing type")
            if "const" in value and isinstance(value["const"], (dict, list)):
                unsupported.append(f"{path}: non-scalar const")
            if "uniqueItems" in value:
                unsupported.append(f"{path}: uniqueItems")
            if "allOf" in value:
                unsupported.append(f"{path}: allOf")
            for key, child in value.items():
                visit(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(schema)
    assert unsupported == []


@pytest.fixture
def review_fixture(provider_fixture, monkeypatch: pytest.MonkeyPatch):
    workspace, view, recorder, _refs = provider_fixture
    director = direct_page(
        workspace,
        view,
        timeout=30,
        invoke=lambda *_args, **_kwargs: _result(_director_value(view)),
    )
    request = build_experiment_image_request(
        workspace,
        view,
        attempt=1,
        prompt=director.actual_prompt,
        quality=director.quality,
        selected_reference_ids=director.selected_reference_ids,
        strategy="initial",
        previous_candidate=None,
    )
    candidate = run_provider_attempt(
        workspace,
        request,
        attempt=1,
        timeout=17,
        recorder=recorder,
        runner=_real_worker_runner(monkeypatch, []),
    )
    preflight = preflight_candidate(candidate)
    assert preflight.passed is True
    assert preflight.sha256 is not None
    recorder.record_candidate_preflight(
        attempt=1,
        candidate_sha256=preflight.sha256,
        request_identity=candidate.request_identity,
        passed=True,
        problems=(),
    )
    return workspace, view, director, candidate, recorder


def _png(size: tuple[int, int], colour: str = "navy") -> bytes:
    stream = BytesIO()
    Image.new("RGB", size, colour).save(stream, format="PNG")
    return stream.getvalue()


def _jpeg(size: tuple[int, int]) -> bytes:
    stream = BytesIO()
    Image.new("RGB", size, "white").save(stream, format="JPEG")
    return stream.getvalue()


def _review_result(
    *, decision: str = "accept", problems: list[dict[str, str]] | None = None
) -> CodexStructuredResult:
    return CodexStructuredResult(
        value={
            "schema_version": "awesome-independent-visual-review-v1",
            "decision": decision,
            "problems": [] if problems is None else problems,
        },
        thread_id="independent-thread",
        turn_id="independent-turn",
        model="gpt-current-review",
        model_provider="openai-test",
        auth_mode="chatgpt",
        plan_type="plus",
        usage={"input_tokens": 456, "output_tokens": 32},
        safe_trace={"runtime": "codex-app-server", "startup_reused": True},
        effort="high",
        duration_seconds=4.5,
        startup_reused=True,
    )


def test_preflight_accepts_only_native_exact_size_sealed_png(review_fixture):
    _workspace, _view, _director, candidate, _recorder = review_fixture

    result = preflight_candidate(candidate)

    assert result.passed is True
    assert result.path == candidate.path
    assert result.mime_type == "image/png"
    assert (result.width, result.height) == (1904, 896)
    assert len(result.sha256 or "") == 64
    assert result.problems == ()


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("corrupt", "decoding or corruption"),
        ("jpeg", "PNG format"),
        ("wrong-size", "1904x896"),
    ],
)
def test_preflight_rejects_corrupt_non_png_and_wrong_dimensions(
    review_fixture, kind: str, expected: str
):
    _workspace, _view, _director, candidate, _recorder = review_fixture
    replacement = {
        "corrupt": b"not-an-image",
        "jpeg": _jpeg((1904, 896)),
        "wrong-size": _png((800, 600)),
    }[kind]
    candidate.path.write_bytes(replacement)

    result = preflight_candidate(candidate)

    assert result.passed is False
    assert any(expected in problem for problem in result.problems)


def test_preflight_rejects_missing_and_unreadable_candidate(review_fixture):
    _workspace, _view, _director, candidate, _recorder = review_fixture
    candidate.path.unlink()

    missing = preflight_candidate(candidate)
    assert missing.passed is False
    assert any("readable regular file" in problem for problem in missing.problems)

    candidate.path.mkdir()
    unreadable = preflight_candidate(candidate)
    assert unreadable.passed is False
    assert any("readable regular file" in problem for problem in unreadable.problems)


def test_preflight_rejects_same_shape_candidate_replacement(review_fixture):
    _workspace, _view, _director, candidate, _recorder = review_fixture
    candidate.path.write_bytes(_png((1904, 896), "red"))

    result = preflight_candidate(candidate)

    assert result.passed is False
    assert result.mime_type == "image/png"
    assert (result.width, result.height) == (1904, 896)
    assert any("completed attempt authority" in problem for problem in result.problems)


def test_failed_preflight_never_invokes_codex(review_fixture):
    workspace, view, director, candidate, recorder = review_fixture
    candidate.path.write_bytes(_png((100, 100)))
    preflight = preflight_candidate(candidate)
    called = False

    def invoke(*_args, **_kwargs):
        nonlocal called
        called = True
        return _review_result()

    with pytest.raises(ValueError, match="preflight"):
        review_candidate_once(
            workspace,
            view,
            director,
            candidate,
            preflight,
            timeout=30,
            recorder=recorder,
            invoke=invoke,
        )
    assert called is False
    assert recorder.has_call(kind="visual_review", attempt=1) is False


def test_review_sends_candidate_first_complete_authority_and_selected_mapping(
    review_fixture,
):
    workspace, view, director, candidate, recorder = review_fixture
    calls: list[dict[str, object]] = []

    def invoke(project: Path, **kwargs):
        calls.append({"project": project, **kwargs})
        return _review_result()

    result = review_candidate_once(
        workspace,
        view,
        director,
        candidate,
        preflight_candidate(candidate),
        timeout=30,
        recorder=recorder,
        invoke=invoke,
    )

    assert result.decision == "accept"
    assert len(calls) == 1
    snapshot_root = (
        workspace.project_copy
        / "04_v6"
        / "experiments"
        / workspace.experiment_id
        / "review_inputs"
        / "attempt_1"
    )
    assert all(path.is_relative_to(snapshot_root) for path in calls[0]["images"])
    receipt = json.loads((snapshot_root / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["ordered_inputs"][0]["role"] == "candidate_under_review"
    assert receipt["ordered_inputs"][1]["role"].startswith("page_material:")
    assert isinstance(receipt["hmac_sha256"], str)
    assert result.problems == ()
    assert result.model == "gpt-current-review"
    assert result.effort == "high"
    assert result.duration_seconds == 4.5
    assert len(calls) == 1
    call = calls[0]
    assert call["project"] == workspace.project_copy
    assert call["role"] == "awesome-independent-visual-review"
    assert call["images"][0].name == "input_00.png"
    assert call["images"][0].read_bytes() == candidate.path.read_bytes()
    assert call["images"][1].read_bytes() == view.multimodal_images[0].read_bytes()
    assert tuple(path.read_bytes() for path in call["images"][1:]) == tuple(
        path.read_bytes() for path in view.multimodal_images
    )
    prompt = str(call["prompt"])
    assert "Candidate-1 = actual candidate under review" in prompt
    assert "Selected-Reference-1 = word-image:word-photo" in prompt
    assert "Authoritative body 1" in prompt
    assert "Keep this original direction exactly.  " in prompt
    assert json.dumps(view.value, ensure_ascii=False, sort_keys=True) in prompt
    assert json.dumps(director.value, ensure_ascii=False, sort_keys=True) in prompt
    assert director.actual_prompt in prompt
    assert "accept reasonable Image2 randomness" in prompt
    assert recorder.has_call(kind="visual_review", attempt=1) is True


def test_review_allows_only_actionable_serious_correction_prompt_classes(
    review_fixture,
):
    problems = [
        {"category": "technical_output", "detail": "The PNG is visibly damaged; regenerate a clean native PNG."},
        {"category": "fixed_layer_violation", "detail": "A fixed page title appears in the body; remove it."},
        {"category": "clear_subject_departure", "detail": "The image depicts an unrelated subject; return to this page's theme."},
        {"category": "misleading_fabrication", "detail": "An invented institution attribution is misleading; remove it."},
        {"category": "severe_identity_distortion", "detail": "The must-preserve real person's identity is severely distorted; restore it."},
        {"category": "core_comment_absent", "detail": "The core original comment direction is entirely absent; express it directly."},
        {"category": "unusable_17_8_composition", "detail": "The composition is unusable in the 17:8 body area; rebuild the hierarchy."},
    ]
    workspace, view, director, candidate, recorder = review_fixture
    captured: list[str] = []

    def invoke(_project: Path, **kwargs):
        captured.append(str(kwargs["prompt"]))
        return _review_result(decision="correct", problems=problems)

    result = review_candidate_once(
        workspace,
        view,
        director,
        candidate,
        preflight_candidate(candidate),
        timeout=30,
        recorder=recorder,
        invoke=invoke,
    )

    assert result.decision == "correct"
    assert result.problems == tuple(problem["detail"] for problem in problems)
    assert tuple(problem.category for problem in result.problem_records) == tuple(
        problem["category"] for problem in problems
    )
    assert "ONLY these seven serious grounds" in captured[0]
    assert "Style variance" in captured[0]
    assert "noncritical omission" in captured[0]
    assert "possible polish" in captured[0]


def test_review_does_not_spend_correction_for_authoritatively_unavailable_real_asset(
    review_fixture,
):
    workspace, view, director, candidate, recorder = review_fixture
    captured: list[str] = []

    def invoke(_project: Path, **kwargs):
        captured.append(str(kwargs["prompt"]))
        return _review_result()

    result = review_candidate_once(
        workspace,
        view,
        director,
        candidate,
        preflight_candidate(candidate),
        timeout=30,
        recorder=recorder,
        invoke=invoke,
    )

    assert result.decision == "accept"
    assert len(captured) == 1
    prompt = captured[0]
    assert "completed project material search/import stage" in prompt
    assert "do not classify core_comment_absent solely because that unavailable real asset is missing" in prompt
    assert "all mapped Context-Images, not merely the selected references" in prompt
    assert "fake, synthesized, mismatched, or severely distorted identity assets" in prompt


@pytest.mark.parametrize(
    "value",
    [
        {"schema_version": "awesome-independent-visual-review-v1", "decision": "accept", "problems": [{"category": "technical_output", "detail": "fix it"}]},
        {"schema_version": "awesome-independent-visual-review-v1", "decision": "correct", "problems": []},
        {"schema_version": "awesome-independent-visual-review-v1", "decision": "correct", "problems": [{"category": "technical_output", "detail": "  "}]},
        {"schema_version": "awesome-independent-visual-review-v1", "decision": "correct", "problems": [{"category": "polish", "detail": "Make the colors nicer."}]},
        {"schema_version": "awesome-independent-visual-review-v1", "decision": "correct", "problems": [{"category": "color", "detail": "Change the palette."}]},
        {"schema_version": "awesome-independent-visual-review-v1", "decision": "correct", "problems": [{"category": "score", "detail": "The score is too low."}]},
        {"schema_version": "awesome-independent-visual-review-v1", "decision": "accept", "problems": [], "score": 4},
        {"schema_version": "awesome-independent-visual-review-v1", "decision": "accept", "problems": [], "coverage": 1.0},
    ],
)
def test_review_rejects_invalid_decision_problem_and_extra_quality_fields(
    review_fixture, value: dict[str, object]
):
    workspace, view, director, candidate, recorder = review_fixture
    result = _review_result()
    malformed = replace(result, value=value)

    with pytest.raises(ValueError, match="review"):
        review_candidate_once(
            workspace,
            view,
            director,
            candidate,
            preflight_candidate(candidate),
            timeout=30,
            recorder=recorder,
            invoke=lambda *_args, **_kwargs: malformed,
        )
    assert recorder.has_call(kind="visual_review", attempt=1) is True


def test_review_rejects_forged_material_or_director_before_codex(review_fixture):
    workspace, view, director, candidate, recorder = review_fixture
    forged_value = dict(view.value)
    forged_value["complete_word_content"] = []
    forged_view = CompletePageMaterialView(
        value=forged_value,
        multimodal_images=view.multimodal_images,
        material_ids=view.material_ids,
        sha256=view.sha256,
    )
    called = False

    def invoke(*_args, **_kwargs):
        nonlocal called
        called = True
        return _review_result()

    with pytest.raises(ValueError):
        review_candidate_once(
            workspace,
            forged_view,
            director,
            candidate,
            preflight_candidate(candidate),
            timeout=30,
            recorder=recorder,
            invoke=invoke,
        )
    assert called is False

    forged_value = json.loads(json.dumps(director.value))
    forged_value["creative_direction"]["analytical_backbone"] = "A different but schema-valid visual concept."
    from complex_page_experiment.director import compile_consulting_six_part_prompt

    forged_value["prompt_sections"]["task_and_canvas"] = "A different but valid calm luminous field."
    forged_director = replace(
        director,
        value=forged_value,
        actual_prompt=compile_consulting_six_part_prompt(forged_value, view),
    )
    with pytest.raises(ValueError, match="published director"):
        review_candidate_once(
            workspace,
            view,
            forged_director,
            candidate,
            preflight_candidate(candidate),
            timeout=30,
            recorder=recorder,
            invoke=invoke,
        )
    assert called is False

    forged_director = replace(director, actual_prompt="forged prompt")
    with pytest.raises(ValueError):
        review_candidate_once(
            workspace,
            view,
            forged_director,
            candidate,
            preflight_candidate(candidate),
            timeout=30,
            recorder=recorder,
            invoke=invoke,
        )
    assert called is False


def test_review_rechecks_candidate_and_prompt_bytes_after_preflight(review_fixture):
    workspace, view, director, candidate, recorder = review_fixture
    valid = preflight_candidate(candidate)
    candidate.path.write_bytes(_png((1904, 896), "orange"))
    called = False

    def invoke(*_args, **_kwargs):
        nonlocal called
        called = True
        return _review_result()

    with pytest.raises(ValueError):
        review_candidate_once(
            workspace,
            view,
            director,
            candidate,
            valid,
            timeout=30,
            recorder=recorder,
            invoke=invoke,
        )
    assert called is False


def test_review_rejects_stale_actual_prompt_bridge_before_codex(review_fixture):
    workspace, view, director, candidate, recorder = review_fixture
    prompt_path = workspace.project_copy / "02_v6/page_image_prompts/page_001.output.json"
    prompt_path.write_text("stale prompt", encoding="utf-8")
    called = False

    def invoke(*_args, **_kwargs):
        nonlocal called
        called = True
        return _review_result()

    with pytest.raises(ValueError, match="prompt"):
        review_candidate_once(
            workspace,
            view,
            director,
            candidate,
            preflight_candidate(candidate),
            timeout=30,
            recorder=recorder,
            invoke=invoke,
        )
    assert called is False


def test_review_records_failed_codex_call_honestly(review_fixture):
    workspace, view, director, candidate, recorder = review_fixture

    def invoke(*_args, **_kwargs):
        raise RuntimeError("review transport failed")

    with pytest.raises(RuntimeError, match="transport failed"):
        review_candidate_once(
            workspace,
            view,
            director,
            candidate,
            preflight_candidate(candidate),
            timeout=30,
            recorder=recorder,
            invoke=invoke,
        )

    assert recorder.has_call(kind="visual_review", attempt=1) is True
    evidence = (recorder.experiment_root / "evidence.jsonl").read_text(encoding="utf-8")
    events = [json.loads(line) for line in evidence.splitlines()]
    review_calls = [event for event in events if event.get("kind") == "visual_review"]
    assert review_calls[-1]["status"] == "error"
    assert review_calls[-1]["model"] == "unavailable"


def test_review_invokes_only_signed_snapshot_paths_when_originals_change_at_boundary(
    review_fixture,
):
    workspace, view, director, candidate, recorder = review_fixture
    original_paths = (candidate.path, *view.multimodal_images)
    expected_bytes = tuple(path.read_bytes() for path in original_paths)
    captured: list[tuple[Path, ...]] = []

    def invoke(_project: Path, **kwargs):
        images = tuple(kwargs["images"])
        captured.append(images)
        candidate.path.write_bytes(_png((1904, 896), "purple"))
        view.multimodal_images[0].write_bytes(_png((32, 16), "purple"))
        assert tuple(path.read_bytes() for path in images) == expected_bytes
        return _review_result()

    result = review_candidate_once(
        workspace,
        view,
        director,
        candidate,
        preflight_candidate(candidate),
        timeout=30,
        recorder=recorder,
        invoke=invoke,
    )

    assert result.decision == "accept"


def test_review_publishes_signed_result_authority_and_rejects_forged_projection(
    review_fixture,
):
    workspace, view, director, candidate, recorder = review_fixture
    review = review_candidate_once(
        workspace, view, director, candidate, preflight_candidate(candidate),
        timeout=30, recorder=recorder,
        invoke=lambda *_args, **_kwargs: _review_result(),
    )

    assert review.authority_path.is_file()
    validate_published_review_authority(
        workspace, view, director, candidate, review, recorder=recorder,
    )
    forged = replace(review, model="forged-reviewer")
    with pytest.raises(ValueError, match="review.*authority|published review"):
        validate_published_review_authority(
            workspace, view, director, candidate, forged, recorder=recorder,
        )


def test_review_result_authority_binds_candidate_snapshot_and_detects_tamper(
    review_fixture,
):
    workspace, view, director, candidate, recorder = review_fixture
    review = review_candidate_once(
        workspace, view, director, candidate, preflight_candidate(candidate),
        timeout=30, recorder=recorder,
        invoke=lambda *_args, **_kwargs: _review_result(),
    )
    payload = bytearray(review.authority_path.read_bytes())
    payload[-2] ^= 1
    review.authority_path.write_bytes(payload)

    with pytest.raises(ValueError, match="review.*authority|signature|invalid"):
        validate_published_review_authority(
            workspace, view, director, candidate, review, recorder=recorder,
        )

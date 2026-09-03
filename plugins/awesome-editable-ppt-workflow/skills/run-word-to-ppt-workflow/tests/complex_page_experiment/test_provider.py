from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from complex_page_experiment import (
    EvidenceRecorder,
    build_complete_page_material_view,
    create_experiment_copy,
    verify_source_unchanged,
)
from complex_page_experiment.provider import (
    CandidateArtifact,
    build_experiment_image_request,
    run_provider_attempt,
)
from complex_page_experiment.review import preflight_candidate
from test_materials import _canonical, _prepare_complete_page_one


ROOT = Path(__file__).resolve().parents[2]
IMAGE_SCRIPTS = ROOT.parents[1] / "generate-slide-body-image" / "scripts"
if str(IMAGE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(IMAGE_SCRIPTS))

import codex_gpt_image  # noqa: E402


def _png(colour: str) -> bytes:
    stream = BytesIO()
    Image.new("RGB", (32, 16), colour).save(stream, format="PNG")
    return stream.getvalue()


def _replace_fixture_images(project: Path) -> None:
    replacements = {
        "01_source_assets/photo.png": _png("red"),
        "01_source_assets/photo-copy.png": _png("red"),
        "02_v6/attachment_renders/appendix/page_0001.png": _png("blue"),
        "02_v6/attachment_renders/appendix/page_0002.png": _png("green"),
        "02_v6/attachment_renders/facts/page_0001.png": _png("yellow"),
    }
    for relative, data in replacements.items():
        (project / relative).write_bytes(data)

    def update(record: dict[str, object]) -> None:
        relative = str(record["path"])
        if relative in replacements:
            data = replacements[relative]
            record["sha256"] = hashlib.sha256(data).hexdigest()
            record["byte_size"] = len(data)

    assets_path = project / "02_v6/source_assets.json"
    assets = json.loads(assets_path.read_text(encoding="utf-8"))
    for asset in assets["assets"]:
        relative = f"01_source_assets/{asset['relative_path']}"
        if relative in replacements:
            asset["sha256"] = hashlib.sha256(replacements[relative]).hexdigest()
            asset["byte_size"] = len(replacements[relative])
    assets_path.write_bytes(_canonical(assets))

    state_path = project / "workflow_v6.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    for number in range(1, 5):
        material_path = project / f"02_v6/awesome_page_materials/page_{number:03d}.json"
        material = json.loads(material_path.read_text(encoding="utf-8"))
        for image in material["word_images"]:
            update(image)
        for attachment in material["attachment_inputs"]:
            receipt = attachment.get("render_receipt")
            if isinstance(receipt, dict):
                for page in receipt["pages"]:
                    update(page)
                update(receipt["contact_sheet"])
        payload = _canonical(material)
        material_path.write_bytes(payload)
        state["pages"][number - 1]["material_receipt"]["digest"] = hashlib.sha256(payload).hexdigest()
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def provider_fixture(awesome_four_page_project: Path, tmp_path: Path):
    _prepare_complete_page_one(awesome_four_page_project)
    _replace_fixture_images(awesome_four_page_project)
    workspace = create_experiment_copy(
        awesome_four_page_project,
        tmp_path / "experiment",
        experiment_id="complex-page-provider",
    )
    view = build_complete_page_material_view(workspace)
    evidence_root = workspace.project_copy / "04_v6/experiments" / workspace.experiment_id
    evidence_root.mkdir(parents=True)
    recorder = EvidenceRecorder(
        evidence_root,
        project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id,
    )
    refs = tuple(
        record["material_id"]
        for record in view.value["materials"]
        if record["viewable_image"] and record["material_id"] not in {
            item["material_id"] for item in view.value["deduplicated_derivatives"]
        }
    )
    return workspace, view, recorder, refs


def _response_png() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (1904, 896), "white").save(stream, format="PNG")
    return stream.getvalue()


def _sealed_previous(
    workspace, view, recorder,
    *,
    prompt: str = "previous prompt",
    selected_reference_ids: tuple[str, ...] = (),
) -> CandidateArtifact:
    request = build_experiment_image_request(
        workspace, view, attempt=1, prompt=prompt, quality="medium",
        selected_reference_ids=selected_reference_ids, strategy="initial",
        previous_candidate=None,
    )
    monkeypatch = pytest.MonkeyPatch()
    try:
        return run_provider_attempt(
            workspace, request, attempt=1, timeout=17, recorder=recorder,
            runner=_real_worker_runner(monkeypatch, []),
        )
    finally:
        monkeypatch.undo()


def _record_passed_preflight(recorder: EvidenceRecorder, candidate: CandidateArtifact) -> None:
    preflight = preflight_candidate(candidate)
    assert preflight.passed is True
    assert preflight.sha256 is not None
    recorder.record_candidate_preflight(
        attempt=candidate.attempt,
        candidate_sha256=preflight.sha256,
        request_identity=candidate.request_identity,
        passed=True,
        problems=(),
    )


def _real_worker_runner(monkeypatch: pytest.MonkeyPatch, captured: list[list[str]]):
    monkeypatch.setattr(
        codex_gpt_image,
        "load_or_login_codex_auth",
        lambda _args: codex_gpt_image.CodexAuth("test-access-token"),
    )
    raw = json.dumps(
        {"data": [{"b64_json": base64.b64encode(_response_png()).decode("ascii")}]}
    ).encode("utf-8")
    monkeypatch.setenv("AWESOME_PROVIDER_TEST_BUILD", "1")
    monkeypatch.setenv("AWESOME_PROVIDER_TEST_RESPONSE_B64", base64.b64encode(raw).decode("ascii"))

    def run(command: list[str], timeout: int) -> None:
        captured.append(list(command))
        assert timeout == 17
        assert codex_gpt_image.main(command[2:]) == 0

    return run


@pytest.mark.parametrize(
    ("strategy", "selected_count", "expected_operation"),
    [
        ("initial", 0, "generate"),
        ("initial", 2, "edit"),
    ],
)
def test_request_matrix_resolves_only_selected_page_owned_images(
    provider_fixture, strategy: str, selected_count: int, expected_operation: str
):
    workspace, view, recorder, refs = provider_fixture
    selected = refs[:selected_count]
    previous = _sealed_previous(workspace, view, recorder) if strategy != "initial" else None
    request = build_experiment_image_request(
        workspace,
        view,
        attempt=1 if strategy == "initial" else 2,
        prompt="A changed page-specific prompt",
        quality="high",
        selected_reference_ids=selected,
        strategy=strategy,
        previous_candidate=previous,
    )

    assert request.operation == expected_operation
    assert request.model == "gpt-image-2"
    assert request.size == "1904x896"
    assert request.quality == "high"
    assert request.selected_reference_ids == selected
    assert len(request.input_images) == selected_count
    assert all(path.is_relative_to(workspace.project_copy) for path in request.input_images)
    assert all(
        hashlib.sha256(path.read_bytes()).hexdigest() == digest
        for path, digest in zip(request.input_images, request.input_sha256s)
    )


def test_edit_previous_prepends_candidate_transport_identity_without_recasting_material(
    provider_fixture, tmp_path: Path
):
    workspace, view, recorder, refs = provider_fixture
    previous = _sealed_previous(workspace, view, recorder)
    candidate_path = previous.path
    digest = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    request = build_experiment_image_request(
        workspace,
        view,
        attempt=2,
        prompt="Correct only the explicit issue",
        quality="medium",
        selected_reference_ids=refs[:2],
        strategy="edit_previous",
        previous_candidate=previous,
    )

    assert request.operation == "edit"
    assert request.input_images[0] == candidate_path
    assert request.image_roles[0] == "previous-candidate-to-correct"
    assert request.selected_reference_ids[0] == f"candidate:1:{digest}"
    assert request.selected_reference_ids[1:] == refs[:2]
    assert request.input_sha256s[0] == digest


def test_run_attempt_uses_real_frozen_worker_verifier_with_no_network_transport(
    provider_fixture, monkeypatch: pytest.MonkeyPatch
):
    workspace, view, recorder, refs = provider_fixture
    request = build_experiment_image_request(
        workspace,
        view,
        attempt=1,
        prompt="Use the selected evidence in a formal page body.",
        quality="medium",
        selected_reference_ids=refs[:2],
        strategy="initial",
        previous_candidate=None,
    )
    captured: list[list[str]] = []
    candidate = run_provider_attempt(
        workspace,
        request,
        attempt=1,
        timeout=17,
        recorder=recorder,
        runner=_real_worker_runner(monkeypatch, captured),
    )

    assert len(captured) == 1
    command = captured[0]
    assert command[2] == "edit"
    assert command.count("--image") == 2
    assert command[command.index("--model") + 1] == "gpt-image-2"
    assert command[command.index("--size") + 1] == "1904x896"
    assert command[command.index("--quality") + 1] == "medium"
    assert "--allow-off-ratio-for-downstream-repair" in command
    assert "--count" not in command
    assert "--base-url" not in command
    assert candidate.path.is_file()
    assert Image.open(candidate.path).size == (1904, 896)
    assert candidate.trace_path.is_file()

    capability_path = Path(command[command.index("--request-capability") + 1])
    capability = json.loads(capability_path.read_text(encoding="utf-8"))
    assert capability["official_endpoint"] == "https://chatgpt.com/backend-api/codex/images/edits"
    assert capability["selected_reference_ids"] == list(refs[:2])
    assert [item["reference_id"] for item in capability["selected_references"]] == list(refs[:2])
    attempt = json.loads(candidate.prompt_path.read_text(encoding="utf-8"))
    assert attempt["actual_prompt"] == request.prompt
    assert attempt["selected_material_reference_ids"] == list(refs[:2])
    assert attempt["correction_candidate_input"] is None
    assert attempt["ordered_transport_input_ids"] == list(refs[:2])
    unselected = next(
        record for record in view.value["materials"]
        if record["viewable_image"] and record["material_id"] not in refs[:2]
    )
    assert unselected["sha256"] not in capability["input_sha256s"]
    verify_source_unchanged(workspace)


def test_edit_previous_candidate_is_first_input_to_real_frozen_worker_verifier(
    provider_fixture, monkeypatch: pytest.MonkeyPatch
):
    workspace, view, recorder, refs = provider_fixture
    captured: list[list[str]] = []
    first = build_experiment_image_request(
        workspace, view, attempt=1, prompt="Initial prompt", quality="medium",
        selected_reference_ids=(), strategy="initial", previous_candidate=None,
    )
    candidate_1 = run_provider_attempt(
        workspace, first, attempt=1, timeout=17, recorder=recorder,
        runner=_real_worker_runner(monkeypatch, captured),
    )
    _record_passed_preflight(recorder, candidate_1)
    recorder.record_call(
        kind="visual_review", attempt=1, model="test-reviewer", effort=None,
        operation=None, duration_seconds=0.1, status="ok", metadata={"result": "correct"},
    )
    recorder.record_call(
        kind="correction_decision", attempt=1, model="test-director", effort=None,
        operation="edit_previous", duration_seconds=0.1, status="ok",
        metadata={"result": "targeted"},
    )
    second = build_experiment_image_request(
        workspace, view, attempt=2, prompt="Correct only the identified issue",
        quality="high", selected_reference_ids=refs[:1], strategy="edit_previous",
        previous_candidate=candidate_1,
    )
    candidate_2 = run_provider_attempt(
        workspace, second, attempt=2, timeout=17, recorder=recorder,
        runner=_real_worker_runner(monkeypatch, captured),
    )

    capability_path = Path(captured[1][captured[1].index("--request-capability") + 1])
    capability = json.loads(capability_path.read_text(encoding="utf-8"))
    candidate_digest = hashlib.sha256(candidate_1.path.read_bytes()).hexdigest()
    assert capability["selected_reference_ids"] == [
        f"candidate:1:{candidate_digest}", refs[0]
    ]
    assert capability["selected_references"][0]["role"] == "previous-candidate-to-correct"
    assert capability["selected_references"][0]["sha256"] == candidate_digest
    assert candidate_2.attempt == 2
    archive = json.loads(candidate_2.prompt_path.read_text(encoding="utf-8"))
    assert archive["selected_material_reference_ids"] == [refs[0]]
    assert archive["correction_candidate_input"]["transport_id"] == capability["selected_reference_ids"][0]


def test_generate_route_passes_real_worker_without_network(
    provider_fixture, monkeypatch: pytest.MonkeyPatch,
):
    workspace, view, recorder, _refs = provider_fixture
    captured: list[list[str]] = []
    request = build_experiment_image_request(
        workspace, view, attempt=1, prompt="Generate without references",
        quality="medium", selected_reference_ids=(),
        strategy="initial", previous_candidate=None,
    )
    result = run_provider_attempt(
        workspace, request, attempt=1, timeout=17, recorder=recorder,
        runner=_real_worker_runner(monkeypatch, captured),
    )
    command = captured[-1]
    assert command[2] == "generate"
    capability_path = Path(command[command.index("--request-capability") + 1])
    capability = json.loads(capability_path.read_text(encoding="utf-8"))
    assert capability["selected_reference_ids"] == []
    assert result.path.is_file()


def test_deleted_regenerate_strategy_is_rejected(provider_fixture):
    workspace, view, recorder, refs = provider_fixture
    previous = _sealed_previous(workspace, view, recorder)
    with pytest.raises(ValueError, match="strategy is invalid"):
        build_experiment_image_request(
            workspace, view, attempt=2, prompt="changed", quality="medium",
            selected_reference_ids=refs[:1], strategy="regenerate_from_materials",
            previous_candidate=previous,
        )


@pytest.mark.parametrize("attempt", [0, 4, True])
def test_attempt_is_strictly_bounded(provider_fixture, attempt: object):
    workspace, view, _recorder, _refs = provider_fixture
    with pytest.raises(ValueError, match="attempt"):
        build_experiment_image_request(
            workspace,
            view,
            attempt=attempt,
            prompt="prompt",
            quality="medium",
            selected_reference_ids=(),
            strategy="initial",
            previous_candidate=None,
        )


def test_edit_previous_requires_immediate_unchanged_candidate(provider_fixture, tmp_path: Path):
    workspace, view, recorder, refs = provider_fixture
    previous = _sealed_previous(workspace, view, recorder)
    path = previous.path
    with pytest.raises(ValueError, match="immediately preceding"):
        build_experiment_image_request(
            workspace, view, attempt=3, prompt="changed", quality="medium",
            selected_reference_ids=refs[:1], strategy="edit_previous", previous_candidate=previous,
        )
    path.write_bytes(b"mutated")
    with pytest.raises(ValueError, match="candidate.*changed|digest|archive.*binding"):
        build_experiment_image_request(
            workspace, view, attempt=2, prompt="changed", quality="medium",
            selected_reference_ids=refs[:1], strategy="edit_previous", previous_candidate=previous,
        )


def test_edit_previous_rejects_attempt_authority_outside_copy(provider_fixture, tmp_path: Path):
    workspace, view, recorder, refs = provider_fixture
    previous = _sealed_previous(workspace, view, recorder)
    outside = tmp_path / "outside-attempt.json"
    outside.write_bytes(previous.prompt_path.read_bytes())
    previous = replace(previous, prompt_path=outside)
    with pytest.raises(ValueError, match="authority.*inside|isolated project copy"):
        build_experiment_image_request(
            workspace, view, attempt=2, prompt="changed", quality="medium",
            selected_reference_ids=refs[:1], strategy="edit_previous", previous_candidate=previous,
        )


def test_total_input_limit_includes_previous_candidate(provider_fixture, tmp_path: Path):
    workspace, view, recorder, refs = provider_fixture
    previous = _sealed_previous(workspace, view, recorder)
    with pytest.raises(ValueError, match="16"):
        build_experiment_image_request(
            workspace, view, attempt=2, prompt="changed", quality="medium",
            selected_reference_ids=tuple(f"extra:{index}" for index in range(16)),
            strategy="edit_previous", previous_candidate=previous,
        )


def test_material_or_candidate_outside_copy_and_changed_selected_bytes_fail(provider_fixture):
    workspace, view, _recorder, refs = provider_fixture
    selected = refs[0]
    record = next(item for item in view.value["materials"] if item["material_id"] == selected)
    (workspace.project_copy / record["authority_path"]).write_bytes(b"mutated")
    with pytest.raises(ValueError, match="changed|digest|authority"):
        build_experiment_image_request(
            workspace, view, attempt=1, prompt="prompt", quality="medium",
            selected_reference_ids=(selected,), strategy="initial", previous_candidate=None,
        )


def test_runner_failure_records_error_not_success(provider_fixture):
    workspace, view, recorder, _refs = provider_fixture
    request = build_experiment_image_request(
        workspace, view, attempt=1, prompt="prompt", quality="medium",
        selected_reference_ids=(), strategy="initial", previous_candidate=None,
    )

    def fail(_command: list[str], _timeout: int) -> None:
        raise RuntimeError("provider failed")

    with pytest.raises(RuntimeError, match="provider failed"):
        run_provider_attempt(
            workspace, request, attempt=1, timeout=17, recorder=recorder, runner=fail
        )
    events = [json.loads(line) for line in (
        workspace.project_copy / "04_v6/experiments/complex-page-provider/evidence.jsonl"
    ).read_text(encoding="utf-8").splitlines()]
    calls = [event for event in events if event["event"] == "call"]
    assert calls[-1]["status"] == "error"


def test_missing_provider_output_is_recorded_as_error(provider_fixture):
    workspace, view, recorder, _refs = provider_fixture
    request = build_experiment_image_request(
        workspace, view, attempt=1, prompt="prompt", quality="medium",
        selected_reference_ids=(), strategy="initial", previous_candidate=None,
    )
    with pytest.raises(ValueError, match="candidate and trace"):
        run_provider_attempt(
            workspace, request, attempt=1, timeout=17, recorder=recorder,
            runner=lambda _command, _timeout: None,
        )
    events = [json.loads(line) for line in (
        workspace.project_copy / "04_v6/experiments/complex-page-provider/evidence.jsonl"
    ).read_text(encoding="utf-8").splitlines()]
    calls = [event for event in events if event["event"] == "call"]
    assert calls[-1]["status"] == "error"


def test_run_rejects_forged_request_inputs_before_bridge_or_runner(provider_fixture):
    workspace, view, recorder, refs = provider_fixture
    request = build_experiment_image_request(
        workspace, view, attempt=1, prompt="prompt", quality="medium",
        selected_reference_ids=refs[:1], strategy="initial", previous_candidate=None,
    )
    forged = replace(request, selected_reference_ids=(refs[1],))
    calls = 0

    def forbidden(_command: list[str], _timeout: int) -> None:
        nonlocal calls
        calls += 1

    with pytest.raises(ValueError, match="request.*authority|selected.*input"):
        run_provider_attempt(
            workspace, forged, attempt=1, timeout=17, recorder=recorder, runner=forbidden
        )
    assert calls == 0
    assert not (workspace.project_copy / "02_v6/page_image_prompts/page_001.output.json").exists()


def test_build_publishes_attempt_bound_request_seal_and_run_rejects_attempt_reuse(
    provider_fixture,
):
    workspace, view, recorder, refs = provider_fixture
    request = build_experiment_image_request(
        workspace, view, attempt=1, prompt="sealed prompt", quality="medium",
        selected_reference_ids=refs[:1], strategy="initial", previous_candidate=None,
    )
    seal = workspace.project_copy / "04_v6/experiments/complex-page-provider/request_attempt_1.json"
    value = json.loads(seal.read_text(encoding="utf-8"))
    assert value["attempt"] == 1
    assert value["strategy"] == "initial"
    assert value["material_view_sha256"] == view.sha256
    assert value["request"]["selected_material_reference_ids"] == [refs[0]]
    with pytest.raises(ValueError, match="attempt.*seal|sealed.*attempt"):
        run_provider_attempt(
            workspace, request, attempt=2, timeout=17, recorder=recorder,
            runner=lambda *_args: pytest.fail("must reject before runner"),
        )


def test_run_rejects_fully_replaced_project_owned_input_not_in_build_seal(provider_fixture):
    workspace, view, recorder, refs = provider_fixture
    request = build_experiment_image_request(
        workspace, view, attempt=1, prompt="sealed prompt", quality="medium",
        selected_reference_ids=refs[:1], strategy="initial", previous_candidate=None,
    )
    arbitrary = workspace.project_copy / "01_source_assets/arbitrary.png"
    arbitrary.write_bytes(_png("black"))
    digest = hashlib.sha256(arbitrary.read_bytes()).hexdigest()
    forged = replace(
        request,
        input_images=(arbitrary,),
        input_sha256s=(digest,),
        selected_reference_ids=("arbitrary-copy",),
        image_roles=("page-material:arbitrary-copy",),
    )
    with pytest.raises(ValueError, match="request.*seal|sealed request"):
        run_provider_attempt(
            workspace, forged, attempt=1, timeout=17, recorder=recorder,
            runner=lambda *_args: pytest.fail("must reject before runner"),
        )


def test_runner_candidate_then_failure_blocks_resume_without_resubmission(provider_fixture):
    workspace, view, recorder, _refs = provider_fixture
    request = build_experiment_image_request(
        workspace, view, attempt=1, prompt="prompt", quality="medium",
        selected_reference_ids=(), strategy="initial", previous_candidate=None,
    )

    def partial(command: list[str], _timeout: int) -> None:
        capability = json.loads(Path(command[command.index("--request-capability") + 1]).read_text())
        target = workspace.project_copy / capability["output_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_response_png())
        raise RuntimeError("crash after candidate")

    with pytest.raises(RuntimeError, match="crash after candidate"):
        run_provider_attempt(workspace, request, attempt=1, timeout=17, recorder=recorder, runner=partial)
    resumed = EvidenceRecorder(
        recorder.experiment_root, project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id,
    )
    calls = 0
    with pytest.raises(RuntimeError, match="outcome_unknown"):
        run_provider_attempt(
            workspace, request, attempt=1, timeout=17, recorder=resumed,
            runner=lambda *_args: pytest.fail("must not resubmit"),
        )
    assert calls == 0


def test_malformed_trace_is_error_and_never_archived_as_completed(provider_fixture):
    workspace, view, recorder, _refs = provider_fixture
    request = build_experiment_image_request(
        workspace, view, attempt=1, prompt="prompt", quality="medium",
        selected_reference_ids=(), strategy="initial", previous_candidate=None,
    )

    def malformed(command: list[str], _timeout: int) -> None:
        capability = json.loads(Path(command[command.index("--request-capability") + 1]).read_text())
        output = workspace.project_copy / capability["output_path"]
        trace = workspace.project_copy / capability["trace_path"]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(_response_png())
        trace.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="trace"):
        run_provider_attempt(workspace, request, attempt=1, timeout=17, recorder=recorder, runner=malformed)
    assert not (workspace.project_copy / "04_v6/experiments/complex-page-provider/attempt_1.json").exists()
    events = [json.loads(line) for line in recorder.experiment_root.joinpath("evidence.jsonl").read_text().splitlines()]
    assert [e for e in events if e["event"] == "call"][-1]["status"] == "error"


def test_mismatched_trace_fields_are_rejected_before_completed_archive(provider_fixture):
    workspace, view, recorder, refs = provider_fixture
    request = build_experiment_image_request(
        workspace, view, attempt=1, prompt="prompt", quality="medium",
        selected_reference_ids=refs[:1], strategy="initial", previous_candidate=None,
    )

    def mismatched(command: list[str], _timeout: int) -> None:
        capability = json.loads(Path(command[command.index("--request-capability") + 1]).read_text())
        output = workspace.project_copy / capability["output_path"]
        trace = workspace.project_copy / capability["trace_path"]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(_response_png())
        trace.write_text(json.dumps({
            "operation": "generate", "endpoint": "images/generations", "model": "gpt-image-2",
            "size": "1904x896", "quality": "high", "auth": "codex_oauth",
            "input_images": [], "outputs": [{"path": str(output.resolve()),
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "mime_type": "image/png"}],
        }), encoding="utf-8")

    with pytest.raises(ValueError, match="trace"):
        run_provider_attempt(
            workspace, request, attempt=1, timeout=17, recorder=recorder, runner=mismatched,
        )
    assert not (workspace.project_copy / "04_v6/experiments/complex-page-provider/attempt_1.json").exists()


def test_postverify_completed_archive_resume_skips_provider(
    provider_fixture, monkeypatch: pytest.MonkeyPatch,
):
    workspace, view, recorder, _refs = provider_fixture
    request = build_experiment_image_request(
        workspace, view, attempt=1, prompt="prompt", quality="medium",
        selected_reference_ids=(), strategy="initial", previous_candidate=None,
    )
    original = recorder.record_call

    def crash_after_archive(**kwargs):
        if kwargs["kind"] == "image2" and kwargs["status"] == "ok":
            raise RuntimeError("postverify crash")
        return original(**kwargs)

    monkeypatch.setattr(recorder, "record_call", crash_after_archive)
    with pytest.raises(RuntimeError, match="postverify crash"):
        run_provider_attempt(
            workspace, request, attempt=1, timeout=17, recorder=recorder,
            runner=_real_worker_runner(monkeypatch, []),
        )
    resumed = EvidenceRecorder(
        recorder.experiment_root, project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id,
    )
    recovered = run_provider_attempt(
        workspace, request, attempt=1, timeout=17, recorder=resumed,
        runner=lambda *_args: pytest.fail("completed attempt must not call Provider"),
    )
    assert recovered.path.is_file()


def test_previous_candidate_artifact_fields_must_exactly_match_archive(provider_fixture):
    workspace, view, recorder, refs = provider_fixture
    previous = _sealed_previous(workspace, view, recorder)
    assert previous.duration_seconds is not None
    forged = replace(previous, duration_seconds=previous.duration_seconds + 1)
    with pytest.raises(ValueError, match="CandidateArtifact|archive"):
        build_experiment_image_request(
            workspace, view, attempt=2, prompt="changed", quality="medium",
            selected_reference_ids=refs[:1], strategy="edit_previous", previous_candidate=forged,
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda seal: seal.__setitem__("strategy", "regenerate_from_materials"),
        lambda seal: seal.__setitem__("experiment_id", "other-experiment"),
        lambda seal: seal.__setitem__("page_number", 2),
        lambda seal: seal.__setitem__("source_snapshot_sha256", "f" * 64),
        lambda seal: seal.__setitem__("material_view_sha256", "e" * 64),
        lambda seal: seal.__setitem__("request_identity", "d" * 64),
        lambda seal: seal.__setitem__("unexpected", True),
        lambda seal: seal.pop("strategy"),
        lambda seal: seal["request"].__setitem__("unexpected", True),
        lambda seal: seal["request"].pop("quality"),
    ],
)
def test_signed_request_seal_rejects_top_nested_and_coordinated_tamper_before_provider(
    provider_fixture, mutate,
):
    workspace, view, recorder, refs = provider_fixture
    request = build_experiment_image_request(
        workspace, view, attempt=1, prompt="prompt", quality="medium",
        selected_reference_ids=refs[:1], strategy="initial", previous_candidate=None,
    )
    seal_path = workspace.project_copy / "04_v6/experiments/complex-page-provider/request_attempt_1.json"
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    mutate(seal)
    # Coordinated ordinary-JSON attackers can update public hashes but cannot sign.
    seal_path.write_text(json.dumps(seal, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="seal|authority|signature|schema"):
        run_provider_attempt(
            workspace, request, attempt=1, timeout=17, recorder=recorder,
            runner=lambda *_args: pytest.fail("tampered seal must fail before Provider"),
        )


def test_request_seal_revalidates_exact_published_material_view_bytes(provider_fixture):
    workspace, view, recorder, _refs = provider_fixture
    request = build_experiment_image_request(
        workspace, view, attempt=1, prompt="prompt", quality="medium",
        selected_reference_ids=(), strategy="initial", previous_candidate=None,
    )
    published = workspace.project_copy / "02_v6/experiments/complex-page-provider/complete_page_material_view.json"
    value = json.loads(published.read_text(encoding="utf-8"))
    value["fixed_page_title"] += " tampered"
    published.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="material view|material.*authority|seal"):
        run_provider_attempt(
            workspace, request, attempt=1, timeout=17, recorder=recorder,
            runner=lambda *_args: pytest.fail("changed material view must fail before Provider"),
        )


def test_completed_archive_resume_reconciles_exactly_one_durable_image2_call(
    provider_fixture, monkeypatch: pytest.MonkeyPatch,
):
    workspace, view, recorder, _refs = provider_fixture
    request = build_experiment_image_request(
        workspace, view, attempt=1, prompt="prompt", quality="medium",
        selected_reference_ids=(), strategy="initial", previous_candidate=None,
    )
    original = recorder.record_call
    monkeypatch.setattr(
        recorder, "record_call",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("crash before evidence"))
        if kwargs["kind"] == "image2" else original(**kwargs),
    )
    with pytest.raises(RuntimeError, match="crash before evidence"):
        run_provider_attempt(
            workspace, request, attempt=1, timeout=17, recorder=recorder,
            runner=_real_worker_runner(monkeypatch, []),
        )
    for _ in range(2):
        resumed = EvidenceRecorder(
            recorder.experiment_root, project_copy=workspace.project_copy,
            experiment_id=workspace.experiment_id,
        )
        run_provider_attempt(
            workspace, request, attempt=1, timeout=17, recorder=resumed,
            runner=lambda *_args: pytest.fail("completed recovery must not call Provider"),
        )
    events = [json.loads(line) for line in recorder.experiment_root.joinpath("evidence.jsonl").read_text().splitlines()]
    calls = [event for event in events if event.get("event") == "call" and event.get("kind") == "image2"]
    assert len(calls) == 1
    assert calls[0]["status"] == "recovered"
    archive = json.loads((workspace.project_copy / "04_v6/experiments/complex-page-provider/attempt_1.json").read_text())
    assert calls[0]["duration_seconds"] == archive["duration_seconds"]
    assert calls[0]["unavailable_reason"] is None


def test_outcome_unknown_journal_is_counted_once_and_never_resubmitted(
    provider_fixture, monkeypatch: pytest.MonkeyPatch,
):
    workspace, view, recorder, _refs = provider_fixture
    request = build_experiment_image_request(
        workspace, view, attempt=1, prompt="prompt", quality="medium",
        selected_reference_ids=(), strategy="initial", previous_candidate=None,
    )
    monkeypatch.setattr(codex_gpt_image, "load_or_login_codex_auth", lambda _args: codex_gpt_image.CodexAuth("test"))
    monkeypatch.setattr(
        codex_gpt_image, "_invoke_provider_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(codex_gpt_image.CliError("network", network=True)),
    )
    runner = lambda command, _timeout: codex_gpt_image.main(command[2:])
    with pytest.raises(BaseException):
        run_provider_attempt(workspace, request, attempt=1, timeout=17, recorder=recorder, runner=runner)
    for _ in range(2):
        resumed = EvidenceRecorder(
            recorder.experiment_root, project_copy=workspace.project_copy,
            experiment_id=workspace.experiment_id,
        )
        with pytest.raises(RuntimeError, match="outcome_unknown"):
            run_provider_attempt(
                workspace, request, attempt=1, timeout=17, recorder=resumed,
                runner=lambda *_args: pytest.fail("outcome_unknown must not resubmit"),
            )
    events = [json.loads(line) for line in recorder.experiment_root.joinpath("evidence.jsonl").read_text().splitlines()]
    assert len([e for e in events if e.get("event") == "call" and e.get("kind") == "image2"]) == 1


def test_correction_seal_binds_full_immediate_predecessor_and_run_revalidates_it(
    provider_fixture, monkeypatch: pytest.MonkeyPatch,
):
    workspace, view, recorder, refs = provider_fixture
    previous = _sealed_previous(workspace, view, recorder)
    _record_passed_preflight(recorder, previous)
    recorder.record_call(
        kind="visual_review", attempt=1, model="review", effort=None,
        operation=None, duration_seconds=0.1, status="ok", metadata={},
    )
    recorder.record_call(
        kind="correction_decision", attempt=1, model="director", effort=None,
        operation="edit_previous", duration_seconds=0.1, status="ok", metadata={},
    )
    request = build_experiment_image_request(
        workspace, view, attempt=2, prompt="changed", quality="medium",
        selected_reference_ids=refs[:1], strategy="edit_previous", previous_candidate=previous,
    )
    seal = json.loads((workspace.project_copy / "04_v6/experiments/complex-page-provider/request_attempt_2.json").read_text())
    predecessor = seal["immediate_predecessor_authority"]
    assert predecessor["attempt"] == 1
    assert predecessor["archive_path"] == "04_v6/experiments/complex-page-provider/attempt_1.json"
    assert predecessor["candidate_path"] == previous.path.relative_to(workspace.project_copy).as_posix()
    assert predecessor["trace_path"] == previous.trace_path.relative_to(workspace.project_copy).as_posix()
    assert predecessor["request_identity"] == previous.request_identity
    assert predecessor["capability_nonce"]
    assert predecessor["journal_path"]
    assert seal["request"]["correction_candidate_input"] is not None
    assert request.selected_reference_ids[0].startswith("candidate:")
    previous.trace_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="predecessor|trace|archive"):
        run_provider_attempt(
            workspace, request, attempt=2, timeout=17, recorder=recorder,
            runner=lambda *_args: pytest.fail("corrupt predecessor must fail before Provider"),
        )


def test_attempt_one_seal_forbids_predecessor_authority(provider_fixture):
    workspace, view, _recorder, _refs = provider_fixture
    build_experiment_image_request(
        workspace, view, attempt=1, prompt="prompt", quality="medium",
        selected_reference_ids=(), strategy="initial", previous_candidate=None,
    )
    seal = json.loads((workspace.project_copy / "04_v6/experiments/complex-page-provider/request_attempt_1.json").read_text())
    assert seal["immediate_predecessor_authority"] is None


def test_submitted_recovery_archive_uses_unknown_duration_not_zero(
    provider_fixture, monkeypatch: pytest.MonkeyPatch,
):
    workspace, view, recorder, _refs = provider_fixture
    request = build_experiment_image_request(
        workspace, view, attempt=1, prompt="prompt", quality="medium",
        selected_reference_ids=(), strategy="initial", previous_candidate=None,
    )
    original = recorder.record_call
    monkeypatch.setattr(
        recorder, "record_call",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("crash before call evidence"))
        if kwargs["kind"] == "image2" else original(**kwargs),
    )
    with pytest.raises(RuntimeError):
        run_provider_attempt(
            workspace, request, attempt=1, timeout=17, recorder=recorder,
            runner=_real_worker_runner(monkeypatch, []),
        )
    archive_path = workspace.project_copy / "04_v6/experiments/complex-page-provider/attempt_1.json"
    archive_path.unlink()
    resumed = EvidenceRecorder(
        recorder.experiment_root, project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id,
    )
    candidate = run_provider_attempt(
        workspace, request, attempt=1, timeout=17, recorder=resumed,
        runner=lambda *_args: pytest.fail("submitted recovery must not call Provider"),
    )
    archive = json.loads(candidate.prompt_path.read_text())
    assert candidate.duration_seconds is None
    assert candidate.duration_unavailable_reason == "process ended before Image2 duration evidence was committed"
    assert archive["duration_seconds"] is None
    assert archive["duration_unavailable_reason"] == candidate.duration_unavailable_reason


def test_completed_archive_known_duration_is_preserved_into_reconciled_evidence(
    provider_fixture, monkeypatch: pytest.MonkeyPatch,
):
    workspace, view, recorder, _refs = provider_fixture
    request = build_experiment_image_request(
        workspace, view, attempt=1, prompt="prompt", quality="medium",
        selected_reference_ids=(), strategy="initial", previous_candidate=None,
    )
    original = recorder.record_call
    monkeypatch.setattr(
        recorder, "record_call",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("post archive"))
        if kwargs["kind"] == "image2" else original(**kwargs),
    )
    with pytest.raises(RuntimeError):
        run_provider_attempt(
            workspace, request, attempt=1, timeout=17, recorder=recorder,
            runner=_real_worker_runner(monkeypatch, []),
        )
    archive = json.loads((workspace.project_copy / "04_v6/experiments/complex-page-provider/attempt_1.json").read_text())
    assert isinstance(archive["duration_seconds"], float)
    resumed = EvidenceRecorder(
        recorder.experiment_root, project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id,
    )
    run_provider_attempt(
        workspace, request, attempt=1, timeout=17, recorder=resumed,
        runner=lambda *_args: pytest.fail("completed recovery must not call Provider"),
    )
    calls = [json.loads(line) for line in recorder.experiment_root.joinpath("evidence.jsonl").read_text().splitlines()]
    image_call = [item for item in calls if item.get("kind") == "image2"][-1]
    assert image_call["duration_seconds"] == archive["duration_seconds"]
    assert image_call["unavailable_reason"] is None


@pytest.mark.parametrize("target", ["capability", "journal"])
@pytest.mark.parametrize("mutation", ["delete", "tamper"])
def test_correction_rejects_missing_or_tampered_predecessor_signed_authority_before_provider(
    provider_fixture, target: str, mutation: str,
):
    workspace, view, recorder, refs = provider_fixture
    previous = _sealed_previous(workspace, view, recorder)
    _record_passed_preflight(recorder, previous)
    recorder.record_call(
        kind="visual_review", attempt=1, model="review", effort=None,
        operation=None, duration_seconds=0.1, status="ok", metadata={},
    )
    recorder.record_call(
        kind="correction_decision", attempt=1, model="deterministic-local", effort=None,
        operation="edit_previous", duration_seconds=0.0, status="ok", metadata={},
    )
    request = build_experiment_image_request(
        workspace, view, attempt=2, prompt="changed", quality="medium",
        selected_reference_ids=refs[:1], strategy="edit_previous",
        previous_candidate=previous,
    )
    seal = json.loads((workspace.project_copy / "04_v6/experiments/complex-page-provider/request_attempt_2.json").read_text())
    authority = seal["immediate_predecessor_authority"]
    path = workspace.project_copy / authority[f"{target}_path"]
    if mutation == "delete":
        path.unlink()
    else:
        value = json.loads(path.read_text())
        value["attempt"] = 3
        path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises((ValueError, FileNotFoundError), match="predecessor|capability|journal|signature|authority"):
        run_provider_attempt(
            workspace, request, attempt=2, timeout=17, recorder=recorder,
            runner=lambda *_args: pytest.fail("invalid predecessor authority must fail pre-provider"),
        )


def test_correction_rejects_predecessor_journal_status_output_binding_mismatch_before_provider(
    provider_fixture,
):
    workspace, view, recorder, refs = provider_fixture
    previous = _sealed_previous(workspace, view, recorder)
    _record_passed_preflight(recorder, previous)
    recorder.record_call(
        kind="visual_review", attempt=1, model="review", effort=None,
        operation=None, duration_seconds=0.1, status="ok", metadata={},
    )
    recorder.record_call(
        kind="correction_decision", attempt=1, model="deterministic-local", effort=None,
        operation="edit_previous", duration_seconds=0.0, status="ok", metadata={},
    )
    request = build_experiment_image_request(
        workspace, view, attempt=2, prompt="changed", quality="medium",
        selected_reference_ids=refs[:1], strategy="edit_previous",
        previous_candidate=previous,
    )
    seal = json.loads((workspace.project_copy / "04_v6/experiments/complex-page-provider/request_attempt_2.json").read_text())
    authority = seal["immediate_predecessor_authority"]
    journal_path = workspace.project_copy / authority["journal_path"]
    journal = json.loads(journal_path.read_text())
    journal["state"] = "response_received"
    journal["outputs"] = ["f" * 64]
    key_id, key = codex_gpt_image.signing_key()
    journal["key_id"] = key_id
    journal.pop("journal_hmac_sha256", None)
    journal["journal_hmac_sha256"] = hmac.new(
        key, json.dumps(journal, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256,
    ).hexdigest()
    journal_path.write_text(json.dumps(journal, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(ValueError, match="predecessor|journal|state|output"):
        run_provider_attempt(
            workspace, request, attempt=2, timeout=17, recorder=recorder,
            runner=lambda *_args: pytest.fail("wrong predecessor journal binding must fail pre-provider"),
        )

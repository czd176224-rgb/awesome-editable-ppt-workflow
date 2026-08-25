from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PLUGIN = ROOT.parents[1]
EDITPPT_RUNTIME = PLUGIN / "skills" / "reconstruct-editable-slide" / "cli" / "editppt" / "runtime"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from test_workflow_v6_reconstruction import _body, _project  # noqa: E402
import workflow_v6_pipeline  # noqa: E402
import workflow_v6_reconstruction_worker as worker_module  # noqa: E402
from workflow_v6_pipeline import (  # noqa: E402
    PipelineConfiguration,
    PipelineDependencies,
    production_pipeline_dependencies,
    run_pages,
)
from workflow_v6_reconstruction_worker import (  # noqa: E402
    PageWorkerResult,
    reconstruct_accepted_page,
)
from workflow_v6_state import load, save  # noqa: E402


def _accepted_outcome(project: Path, page_number: int = 1):
    receipt = json.loads(
        (project / "04_v6" / "images" / f"page_{page_number:03d}.json").read_text(
            encoding="utf-8"
        )
    )
    selected = receipt.get("candidate", receipt.get("selected"))
    candidate = SimpleNamespace(
        path=project / selected["path"],
        attempt=selected["attempt"],
    )
    return SimpleNamespace(
        status="accepted",
        accepted=SimpleNamespace(candidate=candidate),
    )


def _workspace(project: Path, page_number: int = 1):
    return SimpleNamespace(project_copy=project, page_number=page_number)


def _successful_worker(calls: list, text: str = "Editable worker output"):
    def invoke(request):
        calls.append(request)
        body = request.page_dir / "worker-body.pptx"
        _body(body, text)
        return PageWorkerResult(status="completed", reconstructed_body=body)

    return invoke


def test_direct_codex_worker_success_uses_zero_paddle_and_recovers_with_zero_calls(
    tmp_path: Path,
):
    project = _project(tmp_path, 1)
    worker_calls: list = []
    paddle_calls: list = []

    first = reconstruct_accepted_page(
        _workspace(project),
        _accepted_outcome(project),
        page_worker=_successful_worker(worker_calls),
        paddle_runner=lambda request: paddle_calls.append(request),
    )
    recovered = reconstruct_accepted_page(
        _workspace(project),
        _accepted_outcome(project),
        page_worker=lambda request: pytest.fail("recovery called the page worker"),
        paddle_runner=lambda request: pytest.fail("recovery called Paddle"),
    )

    assert first["reconstruction_mode"] == "codex_direct_reconstruction"
    assert recovered["recovered"] is True
    assert len(worker_calls) == 1
    assert paddle_calls == []
    prompt = worker_calls[0].prompt_file.read_text(encoding="utf-8")
    assert str(EDITPPT_RUNTIME / "main.py") in prompt
    assert "do not rely on a separately installed CLI" in prompt
    assert load(project)["pages"][0]["state"] == "page_complete"


def test_page_worker_prompt_enforces_sealed_text_repairs(tmp_path: Path):
    project = _project(tmp_path, 1)
    receipt_path = project / "04_v6/images/page_001.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["reconstruction_repairs"] = [
        {
            "category": "misleading_fabrication",
            "detail": "将错字“清出”修正为“退出”，其余构图保持不变。",
        }
    ]
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    calls: list = []

    reconstruct_accepted_page(
        _workspace(project),
        _accepted_outcome(project),
        page_worker=_successful_worker(calls, text="退出"),
    )

    prompt = calls[0].prompt_file.read_text(encoding="utf-8")
    assert "SEALED TEXT REPAIRS" in prompt
    assert "将错字“清出”修正为“退出”" in prompt


def test_unreadable_text_uses_paddle_once_then_same_page_worker(tmp_path: Path):
    project = _project(tmp_path, 1)
    worker_calls: list = []
    paddle_calls: list = []

    def worker(request):
        worker_calls.append(request)
        if len(worker_calls) == 1:
            return PageWorkerResult(
                status="needs_paddle",
                reason="accepted image text is too small to transcribe reliably",
            )
        body = request.page_dir / "paddle-assisted.pptx"
        _body(body, "Paddle assisted editable output")
        return PageWorkerResult(status="completed", reconstructed_body=body)

    def paddle(request):
        paddle_calls.append(request)
        hints = request.page_dir / "text_hints.json"
        hints.write_text('{"backend":"paddleocr-vl","lines":[]}', encoding="utf-8")
        return hints

    result = reconstruct_accepted_page(
        _workspace(project),
        _accepted_outcome(project),
        page_worker=worker,
        paddle_runner=paddle,
        paddle_token="configured-token",
        paddle_authorized=True,
    )

    assert result["reconstruction_mode"] == "paddle_assisted_reconstruction"
    assert len(worker_calls) == 2
    assert len(paddle_calls) == 1
    assert worker_calls[0].page_number == worker_calls[1].page_number == 1
    assert worker_calls[0].page_dir == worker_calls[1].page_dir
    assert worker_calls[0].text_hints is None
    assert worker_calls[1].text_hints.name == "text_hints.json"


@pytest.mark.parametrize(
    ("token", "authorized", "paddle_fails"),
    [
        ("", True, False),
        ("configured-token", False, False),
        ("configured-token", True, True),
    ],
)
def test_unreadable_text_without_permitted_working_paddle_stops_page(
    tmp_path: Path, token: str | None, authorized: bool, paddle_fails: bool,
):
    project = _project(tmp_path, 1)
    paddle_calls: list = []

    def worker(request):
        return PageWorkerResult(status="needs_paddle", reason="text unreadable")

    def paddle(request):
        paddle_calls.append(request)
        if paddle_fails:
            raise RuntimeError("Paddle unavailable")
        return request.page_dir / "text_hints.json"

    with pytest.raises(RuntimeError, match="Paddle|text unreadable"):
        reconstruct_accepted_page(
            _workspace(project),
            _accepted_outcome(project),
            page_worker=worker,
            paddle_runner=paddle,
            paddle_token=token,
            paddle_authorized=authorized,
        )

    assert not (project / "06_v6" / "pages" / "page_001" / "page.pptx").exists()
    assert len(paddle_calls) == (1 if token and authorized and paddle_fails else 0)


def test_failed_paddle_assisted_worker_does_not_publish_page(tmp_path: Path):
    project = _project(tmp_path, 1)
    calls = 0

    def worker(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return PageWorkerResult(status="needs_paddle", reason="dense unreadable text")
        return PageWorkerResult(status="failed", reason="could not reconstruct after Paddle")

    def paddle(request):
        hints = request.page_dir / "text_hints.json"
        hints.write_text('{"backend":"paddleocr-vl","lines":[]}', encoding="utf-8")
        return hints

    with pytest.raises(RuntimeError, match="could not reconstruct"):
        reconstruct_accepted_page(
            _workspace(project),
            _accepted_outcome(project),
            page_worker=worker,
            paddle_runner=paddle,
            paddle_token="configured-token",
            paddle_authorized=True,
        )

    assert calls == 2
    assert not (project / "06_v6" / "pages" / "page_001" / "page.pptx").exists()


def test_failed_dispatched_worker_requires_explicit_reset_before_resubmission(tmp_path: Path):
    project = _project(tmp_path, 1)

    def failed_worker(request):
        jobs_path = request.run_dir / "page_jobs.json"
        jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
        jobs["pages"][0]["status"] = "dispatched"
        jobs["pages"][0]["dispatch"] = {"agent_id": "codex-page-worker"}
        jobs_path.write_text(json.dumps(jobs), encoding="utf-8")
        (request.page_dir / "worker-last-message.txt").write_text(
            "worker could not execute commands", encoding="utf-8",
        )
        return PageWorkerResult(status="failed", reason="worker unavailable")

    with pytest.raises(RuntimeError, match="worker unavailable"):
        reconstruct_accepted_page(
            _workspace(project), _accepted_outcome(project), page_worker=failed_worker,
        )
    with pytest.raises(RuntimeError, match="reset required"):
        reconstruct_accepted_page(
            _workspace(project), _accepted_outcome(project),
            page_worker=lambda request: pytest.fail("failed worker must not be resubmitted"),
        )


def test_nonaccepted_and_legacy_fallback_states_cannot_reconstruct(tmp_path: Path):
    project = _project(tmp_path, 1)
    outcome = _accepted_outcome(project)
    outcome.status = "failed"
    outcome.accepted = None
    with pytest.raises(ValueError, match="independently accepted"):
        reconstruct_accepted_page(
            _workspace(project), outcome,
            page_worker=lambda request: pytest.fail("worker must not run"),
        )

    state = load(project)
    state["pages"][0]["state"] = "accepted_fallback_first"
    with pytest.raises(ValueError, match="state is invalid"):
        save(project, state)


def test_production_pipeline_defaults_auto_reconstruct_and_assemble():
    dependencies = production_pipeline_dependencies()
    assert dependencies.reconstruct_page is reconstruct_accepted_page
    assert callable(dependencies.assemble_project)


def test_run_pages_without_dependency_override_automatically_reconstructs_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    project = _project(tmp_path, 1)
    reconstructed: list[tuple[object, object]] = []
    assembled: list[dict[int, object]] = []
    accepted = SimpleNamespace(status="accepted", accepted=SimpleNamespace(candidate=object()))
    dependencies = PipelineDependencies(
        open_workspace=lambda root, page: SimpleNamespace(project_copy=root, page_number=page),
        evidence_recorder=lambda workspace: object(),
        candidate_loop=lambda workspace, **kwargs: accepted,
        reconstruct_page=lambda workspace, outcome: reconstructed.append((workspace, outcome)),
        assemble_project=lambda root, outcomes: assembled.append(outcomes),
    )
    monkeypatch.setattr(workflow_v6_pipeline, "production_pipeline_dependencies", lambda: dependencies)

    report = run_pages(
        project,
        [1],
        configuration=PipelineConfiguration(
            page_workers=1,
            initial_page_concurrency=1,
            maximum_page_concurrency=1,
        ),
    )

    assert report.completed_pages == (1,)
    assert len(reconstructed) == 1
    assert reconstructed[0][1] is accepted
    assert assembled == [{1: accepted}]


def test_single_page_prepare_dispatches_worker_without_ocr_or_local_mode(tmp_path: Path):
    source = tmp_path / "accepted.png"
    Image.new("RGB", (1904, 896), "white").save(source)
    run_dir = tmp_path / "run"
    prepare = subprocess.run(
        [
            sys.executable,
            str(EDITPPT_RUNTIME / "main.py"),
            "prepare",
            str(source),
            "--job-dir", str(run_dir),
            "--max-concurrent-pages", "1",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert prepare.returncode == 0, prepare.stderr
    assert not (run_dir / "pages" / "page_001" / "text_hints.json").exists()
    assert not (run_dir / "pages" / "page_001" / "text_hints.png").exists()

    next_step = subprocess.run(
        [sys.executable, str(EDITPPT_RUNTIME / "main.py"), "run", "next", str(run_dir), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert next_step.returncode == 0, next_step.stderr
    payload = json.loads(next_step.stdout)
    assert payload["stage"] == "dispatch_pages"
    assert payload["suggested_pages"] == ["page_001"]
    assert "--local" not in next_step.stdout


def test_shipped_runtime_has_no_retired_low_quality_or_api_fallback_surface():
    forbidden = (
        "builtin-ink",
        "rebuild_page_locally",
        "--local",
        "accepted_fallback_first",
        "codex_fallback",
        "--no-text-hints",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "IMAGE_TO_EDITABLE_PPT_IMAGE_MODEL",
        "openai-compatible-api",
    )
    findings: list[str] = []
    for path in PLUGIN.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or "tests" in path.parts:
            continue
        if path.suffix.lower() not in {".py", ".md", ".json", ".yaml", ".toml"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in forbidden:
            if token in text:
                findings.append(f"{path.relative_to(PLUGIN)}: {token}")
    assert findings == []

    help_result = subprocess.run(
        [sys.executable, str(EDITPPT_RUNTIME / "main.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_result.returncode == 0
    assert all(token not in help_result.stdout for token in forbidden)


def test_page_worker_must_preserve_source_line_fitting_before_passing():
    prompt = (
        PLUGIN
        / "skills"
        / "reconstruct-editable-slide"
        / "prompts"
        / "page-worker.md"
    ).read_text(encoding="utf-8")

    assert "no clipped, truncated, or unintended wrapped text" in prompt
    assert "line breaks visible in source.png" in prompt
    assert "preview.png at the source image dimensions" in prompt


def test_codex_process_failure_reports_transport_error_before_missing_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    run_dir = tmp_path / "run"
    page_dir = run_dir / "pages" / "page_001"
    page_dir.mkdir(parents=True)
    (run_dir / "page_jobs.json").write_text(
        json.dumps({"pages": [{"status": "dispatched"}]}), encoding="utf-8",
    )
    prompt = page_dir / "worker-prompt.md"
    source = page_dir / "source.png"
    prompt.write_text("reconstruct", encoding="utf-8")
    Image.new("RGB", (1904, 896), "white").save(source)
    monkeypatch.setattr(worker_module, "_codex_executable", lambda: "codex")
    monkeypatch.setattr(
        worker_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, stdout="", stderr="remote worker transport failed",
        ),
    )
    request = worker_module.PageWorkerRequest(
        project=tmp_path,
        page_number=1,
        run_dir=run_dir,
        page_dir=page_dir,
        source_image=source,
        prompt_file=prompt,
        text_hints=None,
        timeout=30,
    )

    result = worker_module._default_page_worker(request)

    assert result.status == "failed"
    assert result.reason == "remote worker transport failed"

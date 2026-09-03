"""Automatic accepted-image reconstruction through one Codex page worker."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from workflow_v6_reconstruction import (
    assemble_v6_deck,
    build_reconstruction_request,
    finalize_reconstructed_page,
)
from workflow_v6_state import load


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
RECONSTRUCTION_SKILL = PLUGIN_ROOT / "skills" / "reconstruct-editable-slide"
RUNTIME = RECONSTRUCTION_SKILL / "cli" / "editppt" / "runtime"
PROMPT_BUILDER = RECONSTRUCTION_SKILL / "scripts" / "build-page-worker-prompt.py"


@dataclass(frozen=True)
class PageWorkerRequest:
    project: Path
    page_number: int
    run_dir: Path
    page_dir: Path
    source_image: Path
    prompt_file: Path
    text_hints: Path | None
    timeout: int


@dataclass(frozen=True)
class PageWorkerResult:
    status: Literal["completed", "needs_paddle", "failed"]
    reconstructed_body: Path | None = None
    reason: str | None = None


PageWorker = Callable[[PageWorkerRequest], PageWorkerResult]
PaddleRunner = Callable[[PageWorkerRequest], Path]


def _python() -> str:
    return sys.executable


def _run_script(script: Path, *args: object, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (
            str(RUNTIME), str(Path(__file__).resolve().parent), env.get("PYTHONPATH"),
        ) if value
    )
    completed = subprocess.run(
        [_python(), str(script), *(str(value) for value in args)],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
        env=env,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or script.name
        raise RuntimeError(detail)
    return completed


def _prepare_run(project: Path, reconstruction_request: dict[str, Any], page_number: int) -> tuple[Path, Path, Path]:
    run_dir = project / "05_v6" / "reconstruction_runs" / f"page_{page_number:03d}"
    page_dir = run_dir / "pages" / "page_001"
    if not (run_dir / "deck_manifest.json").is_file():
        if run_dir.exists() and any(run_dir.iterdir()):
            raise RuntimeError("interrupted reconstruction run must be inspected before resubmission")
        source = project / reconstruction_request["source_body"]["path"]
        _run_script(
            RUNTIME / "prepare_deck_run.py",
            source,
            "--job-dir", run_dir,
            "--max-concurrent-pages", 1,
        )
    if not page_dir.is_dir():
        raise RuntimeError("reconstruction page directory is missing")
    jobs_path = run_dir / "page_jobs.json"
    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
    page_job = jobs.get("pages", [{}])[0]
    if (
        page_job.get("status") == "dispatched"
        and (page_dir / "worker-last-message.txt").is_file()
        and not (page_dir / "validation.json").is_file()
    ):
        raise RuntimeError(
            "previous Codex page worker ended without validation; explicit reset required before resubmission"
        )
    request_copy = page_dir / "accepted_reconstruction_request.json"
    encoded = (json.dumps(reconstruction_request, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if request_copy.exists() and request_copy.read_bytes() != encoded:
        raise RuntimeError("accepted reconstruction request changed after preparation")
    if not request_copy.exists():
        request_copy.write_bytes(encoded)
    page_request_path = page_dir / "page_request.json"
    page_request = json.loads(page_request_path.read_text(encoding="utf-8"))
    for authority in ("numeric_authority", "page_plan"):
        value = reconstruction_request.get(authority)
        if value is None:
            page_request.pop(authority, None)
        else:
            page_request[authority] = value
    page_request_path.write_text(
        json.dumps(page_request, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    prompt_file = page_dir / "worker-prompt.md"
    _run_script(PROMPT_BUILDER, run_dir, "--page", "page_001", "--out", prompt_file)
    runtime_command = f'"{_python()}" "{RUNTIME / "main.py"}"'
    prompt = prompt_file.read_text(encoding="utf-8")
    repairs = reconstruction_request.get("sealed_text_repairs", [])
    repair_instructions = ""
    if repairs:
        repair_instructions = (
            "SEALED TEXT REPAIRS FROM INDEPENDENT REVIEW:\n"
            + "\n".join(
                f"- [{item['category']}] {item['detail']}" for item in repairs
            )
            + "\nApply these repairs in editable native text objects. Do not preserve rejected "
            "wording in editable text. Preserve all unaffected composition.\n\n"
        )
    prompt_file.write_text(
        "EDITPPT COMMAND FOR THIS PAGE WORKER:\n"
        f"{runtime_command}\n"
        "Use this exact command prefix everywhere the instructions show `editppt`; "
        "do not rely on a separately installed CLI.\n\n"
        + repair_instructions
        + prompt,
        encoding="utf-8",
    )
    return run_dir, page_dir, prompt_file


def _codex_executable() -> str:
    executable = os.environ.get("EDITABLE_PPT_CODEX_EXECUTABLE") or shutil.which("codex")
    if not executable:
        raise RuntimeError("Codex page worker is unavailable; reconstruction cannot continue")
    return executable


def _validation_result(page_dir: Path) -> PageWorkerResult:
    validation_path = page_dir / "validation.json"
    if not validation_path.is_file():
        return PageWorkerResult("failed", reason="Codex page worker produced no validation.json")
    try:
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return PageWorkerResult("failed", reason=f"Codex page worker validation is invalid: {exc}")
    if validation.get("passed") is True:
        body = page_dir / "page.pptx"
        if not body.is_file():
            return PageWorkerResult("failed", reason="Codex page worker passed without page.pptx")
        return PageWorkerResult("completed", reconstructed_body=body)
    reason = str(validation.get("reason") or validation.get("failure_reason") or "page reconstruction failed")
    if validation.get("failure_code") == "text_unreadable":
        return PageWorkerResult("needs_paddle", reason=reason)
    return PageWorkerResult("failed", reason=reason)


def _default_page_worker(request: PageWorkerRequest) -> PageWorkerResult:
    jobs = json.loads((request.run_dir / "page_jobs.json").read_text(encoding="utf-8"))
    page = jobs["pages"][0]
    if page.get("status") == "pending":
        _run_script(
            RUNTIME / "record_page_dispatch.py",
            request.run_dir,
            "--page", "page_001",
            "--agent-id", "codex-page-worker",
            "--prompt-file", request.prompt_file,
        )
    elif page.get("status") != "dispatched":
        raise RuntimeError("reconstruction page is not dispatchable")

    prompt = request.prompt_file.read_text(encoding="utf-8")
    command = [
        _codex_executable(), "exec",
        "-C", str(request.page_dir),
        "-s", "workspace-write",
        "--ephemeral",
        "--skip-git-repo-check",
        "-i", str(request.source_image),
        "--output-last-message", str(request.page_dir / "worker-last-message.txt"),
        "-",
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(RUNTIME), str(Path(__file__).resolve().parent), env.get("PYTHONPATH")) if value
    )
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            env=env,
            timeout=request.timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Codex page worker could not complete: {exc}") from exc
    result = _validation_result(request.page_dir)
    if result.status == "completed":
        _run_script(
            RUNTIME / "record_manifest_page_result.py",
            request.run_dir,
            "--page", "page_001",
            "--agent-id", "codex-page-worker",
            timeout=request.timeout,
        )
        return result
    if result.status == "needs_paddle":
        return result
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or result.reason
    else:
        detail = result.reason or completed.stderr.strip() or completed.stdout.strip()
    return PageWorkerResult("failed", reason=detail)


def _default_paddle(request: PageWorkerRequest) -> Path:
    token = _paddle_token()
    if not token:
        raise RuntimeError("Paddle token is not configured")
    out = request.page_dir / "text_hints.json"
    _run_script(
        RUNTIME / "paddle_text_hints.py",
        request.page_dir,
        "--out", out.name,
        "--overlay", "text_hints.png",
        "--token", token,
        "--timeout", request.timeout,
        timeout=request.timeout + 30,
    )
    if not out.is_file():
        raise RuntimeError("Paddle did not produce text hints")
    return out


def _paddle_token() -> str | None:
    value = os.environ.get("PADDLE_OCR_TOKEN", "").strip()
    if value:
        return value
    try:
        sys.path.insert(0, str(RUNTIME))
        from runtime_env import config_path, read_config_file
        value = str(read_config_file(config_path()).get("PADDLE_OCR_TOKEN", "")).strip()
    except Exception:
        value = ""
    return value or None


def _paddle_authorized() -> bool:
    return os.environ.get("EDITABLE_PPT_ALLOW_PADDLE_UPLOAD", "").strip() == "1"


def _reset_failed_worker(run_dir: Path) -> None:
    _run_script(
        RUNTIME / "reset_page_job.py",
        run_dir,
        "--page", "page_001",
        "--agent-id", "codex-page-worker",
        "--confirm-lost",
    )


def _recovery(project: Path, page_number: int) -> dict[str, Any] | None:
    state = load(project)
    page = state["pages"][page_number - 1]
    if page.get("state") != "page_complete":
        return None
    final_receipt = project / "06_v6" / "pages" / f"page_{page_number:03d}" / "page.json"
    reconstruction_receipt = project / "05_v6" / "reconstruction_runs" / f"page_{page_number:03d}" / "reconstruction.json"
    if not final_receipt.is_file() or not reconstruction_receipt.is_file():
        raise RuntimeError("completed reconstruction authority is incomplete")
    final = json.loads(final_receipt.read_text(encoding="utf-8"))
    value = json.loads(reconstruction_receipt.read_text(encoding="utf-8"))
    page_pptx = project / str(final.get("page_pptx", ""))
    if not page_pptx.is_file() or hashlib.sha256(page_pptx.read_bytes()).hexdigest() != final.get("sha256"):
        raise RuntimeError("completed reconstructed page changed")
    if value.get("final_page_sha256") != final.get("sha256"):
        raise RuntimeError("reconstruction receipt does not match the final page")
    return {**value, "recovered": True}


def reconstruct_accepted_page(
    workspace: Any,
    outcome: Any,
    *,
    page_worker: PageWorker | None = None,
    paddle_runner: PaddleRunner | None = None,
    paddle_token: str | None = None,
    paddle_authorized: bool | None = None,
    timeout: int = 1800,
) -> dict[str, Any]:
    """Reconstruct one accepted page, with at most one explicit Paddle-assisted retry."""
    project = Path(workspace.project_copy).resolve()
    page_number = int(workspace.page_number)
    if getattr(outcome, "status", None) != "accepted" or getattr(outcome, "accepted", None) is None:
        raise ValueError("only an independently accepted candidate can be reconstructed")
    recovered = _recovery(project, page_number)
    if recovered is not None:
        return recovered
    state = load(project)
    if state["pages"][page_number - 1].get("state") != "accepted":
        raise ValueError("only an independently accepted candidate can be reconstructed")

    reconstruction_request = build_reconstruction_request(project, page_number=page_number)
    accepted_candidate = getattr(outcome.accepted, "candidate", None)
    expected_source = (project / reconstruction_request["source_body"]["path"]).resolve()
    if accepted_candidate is None or Path(accepted_candidate.path).resolve() != expected_source:
        raise ValueError("accepted loop outcome does not match reconstruction authority")
    run_dir, page_dir, prompt_file = _prepare_run(project, reconstruction_request, page_number)
    worker = page_worker or _default_page_worker
    request = PageWorkerRequest(
        project=project,
        page_number=page_number,
        run_dir=run_dir,
        page_dir=page_dir,
        source_image=page_dir / "source.png",
        prompt_file=prompt_file,
        text_hints=None,
        timeout=timeout,
    )
    result = worker(request)
    mode = "codex_direct_reconstruction"
    paddle_calls = 0
    worker_calls = 1
    if result.status == "needs_paddle":
        token = paddle_token if paddle_token is not None else _paddle_token()
        authorized = paddle_authorized if paddle_authorized is not None else _paddle_authorized()
        if not token or not authorized:
            raise RuntimeError(f"{result.reason or 'text unreadable'}; Paddle is not both configured and authorized")
        try:
            hints = (paddle_runner or _default_paddle)(request)
        except Exception as exc:
            raise RuntimeError(f"Paddle failed; page reconstruction stopped: {exc}") from exc
        paddle_calls = 1
        if not Path(hints).is_file():
            raise RuntimeError("Paddle failed; page reconstruction stopped without hints")
        if page_worker is None:
            _reset_failed_worker(run_dir)
        request = PageWorkerRequest(**{**request.__dict__, "text_hints": Path(hints)})
        result = worker(request)
        worker_calls = 2
        mode = "paddle_assisted_reconstruction"
    if result.status != "completed" or result.reconstructed_body is None:
        raise RuntimeError(result.reason or "Codex page reconstruction failed")
    body = Path(result.reconstructed_body).resolve()
    try:
        body.relative_to(page_dir.resolve())
    except ValueError as exc:
        raise RuntimeError("Codex page worker output is outside its page directory") from exc
    final = finalize_reconstructed_page(project, page_number=page_number, reconstructed_body=body)
    receipt = {
        "artifact_version": "accepted-image-worker-reconstruction-v1",
        "page_number": page_number,
        "accepted_receipt": reconstruction_request["accepted_receipt"],
        "accepted_image_sha256": reconstruction_request["source_body"]["sha256"],
        "reconstruction_mode": mode,
        "page_worker_calls": worker_calls,
        "paddle_calls": paddle_calls,
        "final_page": final["page_pptx"],
        "final_page_sha256": final["sha256"],
        "recovered": False,
    }
    receipt_path = run_dir / "reconstruction.json"
    encoded = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    if receipt_path.exists() and receipt_path.read_text(encoding="utf-8") != encoded:
        raise RuntimeError("reconstruction receipt already contains different authority")
    receipt_path.write_text(encoded, encoding="utf-8")
    return receipt


def assemble_reconstructed_project(project: Path, outcomes: dict[int, Any]) -> dict[str, Any]:
    """Assemble once every project page has a finalized reconstructed body."""
    state = load(Path(project).resolve())
    if not all(page.get("state") == "page_complete" for page in state["pages"]):
        return {"status": "deferred", "reason": "not every accepted page is reconstructed"}
    return assemble_v6_deck(Path(project).resolve())


__all__ = [
    "PageWorkerRequest",
    "PageWorkerResult",
    "assemble_reconstructed_project",
    "reconstruct_accepted_page",
]

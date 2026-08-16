from __future__ import annotations

import ctypes
import gc
import hashlib
import json
import multiprocessing
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ALL_COMPLETED
from dataclasses import replace
from pathlib import Path
from typing import Any


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


from complex_page_experiment.loop import AcceptedImageSeal, LoopOutcome
from complex_page_experiment.provider import CandidateArtifact
from complex_page_experiment.workspace import ExperimentWorkspace


DIRECTOR_DELAYS = (0.012, 0.018, 0.024)
PROVIDER_DELAYS = (0.016, 0.024, 0.032)
REVIEW_DELAYS = (0.010, 0.018, 0.026)
RECONSTRUCTION_DELAYS = (0.014, 0.022, 0.030)
ATTACHMENT_DELAY = 0.050
ASSEMBLY_DELAY = 0.040
RATE_LIMIT_PAGE = 2
ISOLATED_FAILURE_PAGE = 47
INTERRUPTED_RECOVERY_PAGE = 1


class TooManyRequests(Exception):
    status_code = 429


def _attempt_count(page: int) -> int:
    if page % 10 == 0:
        return 3
    if page % 5 == 0:
        return 2
    return 1


def _delay(values: tuple[float, ...], page: int, attempt: int = 1) -> float:
    return values[(page + attempt) % len(values)]


def _candidate(project: Path, page: int, attempt: int) -> CandidateArtifact:
    identity = hashlib.sha256(f"page={page};attempt={attempt}".encode()).hexdigest()
    return CandidateArtifact(
        attempt=attempt,
        path=project / "04_v6" / "images" / f"page_{page:03d}_attempt_{attempt}.png",
        trace_path=project / "04_v6" / "traces" / f"page_{page:03d}_attempt_{attempt}.json",
        prompt_path=project / "04_v6" / "prompts" / f"page_{page:03d}_attempt_{attempt}.txt",
        operation="generate" if attempt == 1 else "edit",
        quality="medium",
        selected_reference_ids=("shared-attachment-page-1",),
        input_sha256s=("a" * 64,),
        prompt_sha256=hashlib.sha256(f"prompt-{page}-{attempt}".encode()).hexdigest(),
        request_identity=identity,
        duration_seconds=_delay(PROVIDER_DELAYS, page, attempt),
        duration_unavailable_reason=None,
    )


def _accepted_outcome(
    project: Path, page: int, attempts: int, *, recovered: bool,
) -> LoopOutcome:
    candidates = tuple(_candidate(project, page, attempt) for attempt in range(1, attempts + 1))
    selected = candidates[-1]
    seal = AcceptedImageSeal(
        receipt_path=project / "04_v6" / "images" / f"page_{page:03d}.json",
        candidate=selected,
        receipt_sha256=hashlib.sha256(f"seal-{page}".encode()).hexdigest(),
        recovered=recovered,
    )
    return LoopOutcome("accepted", candidates, seal, (), attempts - 1)


def _handle_count() -> int:
    if sys.platform == "win32":
        count = ctypes.c_ulong()
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.GetProcessHandleCount.argtypes = (
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong),
        )
        kernel32.GetProcessHandleCount.restype = ctypes.c_int
        process = kernel32.GetCurrentProcess()
        if not kernel32.GetProcessHandleCount(process, ctypes.byref(count)):
            raise OSError("GetProcessHandleCount failed")
        return int(count.value)
    descriptor_root = Path("/proc/self/fd")
    return len(tuple(descriptor_root.iterdir())) if descriptor_root.is_dir() else 0


class DeterministicScaleWorkload:
    """External-stage double; scheduling, gates, executor, and limits stay real."""

    def __init__(self, project: Path) -> None:
        self.project = project
        self.lock = threading.Lock()
        self.accepted = {
            INTERRUPTED_RECOVERY_PAGE: _accepted_outcome(
                project, INTERRUPTED_RECOVERY_PAGE, 1, recovered=True,
            )
        }
        self.reconstructed: set[int] = set()
        self.run_number = 1
        self.submissions: dict[int, Counter[int]] = defaultdict(Counter)
        self.calls: dict[int, Counter[str]] = defaultdict(Counter)
        self.active = Counter()
        self.peaks = Counter()
        self.page_active = 0
        self.page_peak = 0
        self.attachment_renders = 0
        self.attachment_rendering = False
        self.attachment_ready = threading.Event()
        self.assembly_calls = 0
        self.executor_thread_peak = 0
        self.director_completion_times: list[float] = []
        self.simultaneous_directors = threading.Barrier(3)

    def _enter(self, stage: str) -> None:
        with self.lock:
            self.active[stage] += 1
            self.peaks[stage] = max(self.peaks[stage], self.active[stage])
            self.executor_thread_peak = max(
                self.executor_thread_peak,
                sum(thread.name.startswith("awesome-page") for thread in threading.enumerate()),
            )

    def _leave(self, stage: str) -> None:
        with self.lock:
            self.active[stage] -= 1

    def _wait_stage(self, stage: str, delay: float) -> None:
        self._enter(stage)
        try:
            time.sleep(delay)
        finally:
            self._leave(stage)

    def open_workspace(self, root: Path, page: int) -> ExperimentWorkspace:
        with self.lock:
            self.submissions[self.run_number][page] += 1
        return ExperimentWorkspace(
            experiment_id=f"scale-page-{page:03d}",
            source_project=root,
            experiment_root=root / "04_v6" / "experiments" / f"page-{page:03d}",
            project_copy=root,
            page_number=page,
            source_snapshot_sha256="f" * 64,
        )

    @staticmethod
    def evidence_recorder(_workspace: ExperimentWorkspace) -> object:
        return object()

    def _shared_attachment(self) -> None:
        render_owner = False
        with self.lock:
            if self.attachment_ready.is_set():
                return
            if not self.attachment_rendering:
                self.attachment_rendering = True
                self.attachment_renders += 1
                render_owner = True
        if render_owner:
            time.sleep(ATTACHMENT_DELAY)
            self.attachment_ready.set()
        else:
            assert self.attachment_ready.wait(5), "shared attachment render deadlocked"

    def director(self, *, page: int, attempt: int, **_kwargs: Any) -> dict[str, object]:
        with self.lock:
            self.calls[page]["director"] += 1
        self._enter("director")
        try:
            if attempt == 1 and page in {3, 4, 5}:
                self.simultaneous_directors.wait(timeout=5)
                time.sleep(0.020)
                with self.lock:
                    self.director_completion_times.append(time.monotonic())
            else:
                time.sleep(_delay(DIRECTOR_DELAYS, page, attempt))
        finally:
            self._leave("director")
        return {"page": page, "attempt": attempt, "role": "director"}

    def provider(self, request: list[str], _timeout: int) -> None:
        page, attempt = (int(value) for value in request)
        with self.lock:
            self.calls[page]["provider"] += 1
        self._wait_stage("image2", _delay(PROVIDER_DELAYS, page, attempt))
        if page == RATE_LIMIT_PAGE and attempt == 1:
            raise TooManyRequests("scripted contraction")

    def reviewer(self, *, page: int, attempt: int, **_kwargs: Any) -> dict[str, object]:
        with self.lock:
            self.calls[page]["review"] += 1
        self._wait_stage("review", _delay(REVIEW_DELAYS, page, attempt))
        return {
            "page": page,
            "attempt": attempt,
            "decision": "accept" if attempt == _attempt_count(page) else "correct",
        }

    def candidate_loop(
        self,
        workspace: ExperimentWorkspace,
        *,
        director_invoke: Any,
        provider_runner: Any,
        reviewer_invoke: Any,
        **_kwargs: Any,
    ) -> LoopOutcome:
        page = workspace.page_number
        with self.lock:
            self.page_active += 1
            self.page_peak = max(self.page_peak, self.page_active)
        try:
            with self.lock:
                recovered = self.accepted.get(page)
            if recovered is not None:
                assert recovered.accepted is not None
                return replace(
                    recovered,
                    accepted=replace(recovered.accepted, recovered=True),
                )
            self._shared_attachment()
            attempts = 3 if page == ISOLATED_FAILURE_PAGE else _attempt_count(page)
            for attempt in range(1, attempts + 1):
                director_invoke(page=page, attempt=attempt)
                provider_runner([str(page), str(attempt)], 1)
                reviewer_invoke(page=page, attempt=attempt)
            if page == ISOLATED_FAILURE_PAGE:
                return LoopOutcome(
                    "failed",
                    tuple(_candidate(self.project, page, attempt) for attempt in range(1, 4)),
                    None,
                    ("private scripted review detail",),
                    2,
                )
            outcome = _accepted_outcome(self.project, page, attempts, recovered=False)
            with self.lock:
                self.accepted[page] = outcome
            return outcome
        finally:
            with self.lock:
                self.page_active -= 1

    def reconstruct_page(self, workspace: ExperimentWorkspace, outcome: LoopOutcome) -> dict[str, object]:
        page = workspace.page_number
        assert outcome.status == "accepted" and outcome.accepted is not None
        with self.lock:
            if page in self.reconstructed:
                return {"page": page, "status": "recovered"}
            self.calls[page]["reconstruction"] += 1
        self._wait_stage("reconstruction", _delay(RECONSTRUCTION_DELAYS, page))
        with self.lock:
            self.reconstructed.add(page)
        return {"page": page, "status": "page_complete"}

    def assemble_project(self, project: Path, outcomes: dict[int, LoopOutcome]) -> dict[str, object]:
        assert project == self.project.resolve()
        assert all(outcome.status == "accepted" for outcome in outcomes.values())
        with self.lock:
            self.assembly_calls += 1
        self._wait_stage("assembly", ASSEMBLY_DELAY)
        return {"status": "assembled", "page_count": len(outcomes)}


def _scripted_serial_baseline() -> float:
    # Hand-derived from the deterministic stage script, independent of runner logic.
    total = ATTACHMENT_DELAY + ASSEMBLY_DELAY
    for page in range(1, 101):
        if page == INTERRUPTED_RECOVERY_PAGE:
            total += _delay(RECONSTRUCTION_DELAYS, page)
            continue
        attempts = 3 if page == ISOLATED_FAILURE_PAGE else _attempt_count(page)
        for attempt in range(1, attempts + 1):
            total += _delay(DIRECTOR_DELAYS, page, attempt)
            total += _delay(PROVIDER_DELAYS, page, attempt)
            if page == RATE_LIMIT_PAGE and attempt == 1:
                break
            total += _delay(REVIEW_DELAYS, page, attempt)
        if page not in {RATE_LIMIT_PAGE, ISOLATED_FAILURE_PAGE}:
            total += _delay(RECONSTRUCTION_DELAYS, page)
    return total


def _run_with_deadlock_guard(action: Any, timeout: float = 20.0) -> tuple[Any, float]:
    result: list[Any] = []
    failure: list[BaseException] = []

    def invoke() -> None:
        try:
            result.append(action())
        except BaseException as exc:  # preserve the production exception in the test thread
            failure.append(exc)

    started = time.monotonic()
    thread = threading.Thread(target=invoke, name="scale-deadlock-guard", daemon=True)
    thread.start()
    thread.join(timeout)
    elapsed = time.monotonic() - started
    assert not thread.is_alive(), "production run_pages deadlocked"
    if failure:
        raise failure[0]
    return result[0], elapsed


def test_returned_failed_outcome_is_not_a_success_or_recovery_signal(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    # Break caught: a returned terminal failure is reported/computed as success,
    # leaks its private reason, reconstructs, assembles, or restores 429 capacity.
    import workflow_v6_pipeline as pipeline

    project = (tmp_path / "terminal-failure").resolve()
    project.mkdir()
    assembled: list[dict[int, LoopOutcome]] = []
    reconstructed: list[int] = []
    rounds: list[Any] = []
    real_record_round = pipeline.AdaptiveScheduler.record_round

    def record_round(scheduler: Any, outcome: Any, *, allow_recovery: bool = True) -> Any:
        rounds.append(outcome)
        return real_record_round(scheduler, outcome, allow_recovery=allow_recovery)

    monkeypatch.setattr(pipeline.AdaptiveScheduler, "record_round", record_round)

    def provider(request: list[str], _timeout: int) -> None:
        if request == ["1"]:
            raise TooManyRequests("scripted contraction")

    def loop(
        workspace: ExperimentWorkspace, *, provider_runner: Any, **_kwargs: Any,
    ) -> LoopOutcome:
        if workspace.page_number == 1:
            provider_runner(["1"], 1)
        if workspace.page_number == 2:
            provider_runner(["2"], 1)
            return LoopOutcome(
                "failed",
                (_candidate(project, 2, 1),),
                None,
                ("private customer review detail",),
                0,
            )
        return _accepted_outcome(project, workspace.page_number, 1, recovered=False)

    report = pipeline.run_pages(
        project,
        [1, 2, 3],
        dependencies=pipeline.PipelineDependencies(
            open_workspace=lambda root, page: ExperimentWorkspace(
                f"terminal-{page}", root, root / "experiments" / str(page), root,
                page, "e" * 64,
            ),
            evidence_recorder=lambda _workspace: object(),
            candidate_loop=loop,
            provider_runner=provider,
            reconstruct_page=lambda workspace, _outcome: reconstructed.append(
                workspace.page_number
            ),
            assemble_project=lambda _root, outcomes: assembled.append(outcomes),
        ),
        configuration=pipeline.PipelineConfiguration(
            page_workers=1, initial_page_concurrency=1, maximum_page_concurrency=2,
            provider_profile="speed",
        ),
    )

    assert report.completed_pages == (3,)
    assert report.failed_pages == {
        1: "TooManyRequests: scripted contraction",
        2: "LoopOutcome: failed after 1 attempt(s) with 1 problem(s)",
    }
    assert "private customer" not in json.dumps(report.to_dict())
    assert reconstructed == [3]
    assert [tuple(outcomes) for outcomes in assembled] == [(3,)]
    assert any(
        outcome.successes == 0 and outcome.failures == 1 and outcome.rate_limits == 0
        for outcome in rounds
    )
    assert report.scheduler_concurrency == 1


def test_coordinator_accounts_for_two_futures_completed_in_one_wait_batch(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    # Break caught: processing only one future from a multi-completion batch loses,
    # duplicates, or misclassifies a page and reports the wrong scheduler round.
    import workflow_v6_pipeline as pipeline

    project = (tmp_path / "simultaneous-batch").resolve()
    project.mkdir()
    finish_barrier = threading.Barrier(2)
    directors = threading.Barrier(2)
    providers = threading.Barrier(2)
    returned = 0
    returned_lock = threading.Lock()
    both_returning = threading.Event()
    rounds: list[Any] = []
    real_wait = pipeline.wait
    real_record_round = pipeline.AdaptiveScheduler.record_round
    first_wait = True

    def wait_for_batch(futures: Any, *, return_when: Any) -> Any:
        nonlocal first_wait
        assert return_when is pipeline.FIRST_COMPLETED
        if first_wait:
            first_wait = False
            assert both_returning.wait(5)
            done, pending = real_wait(futures, timeout=5, return_when=ALL_COMPLETED)
            assert not pending
            return done, pending
        return real_wait(futures, return_when=return_when)

    def record_round(scheduler: Any, outcome: Any, *, allow_recovery: bool = True) -> Any:
        rounds.append(outcome)
        return real_record_round(scheduler, outcome, allow_recovery=allow_recovery)

    monkeypatch.setattr(pipeline, "wait", wait_for_batch)
    monkeypatch.setattr(pipeline.AdaptiveScheduler, "record_round", record_round)

    def stage_barrier(barrier: threading.Barrier):
        def invoke(*_args: Any, **_kwargs: Any) -> None:
            barrier.wait(timeout=5)
        return invoke

    def loop(
        workspace: ExperimentWorkspace,
        *, director_invoke: Any,
        provider_runner: Any,
        reviewer_invoke: Any,
        **_kwargs: Any,
    ) -> LoopOutcome:
        nonlocal returned
        director_invoke()
        provider_runner([], 1)
        reviewer_invoke()
        finish_barrier.wait(timeout=5)
        with returned_lock:
            returned += 1
            if returned == 2:
                both_returning.set()
        return _accepted_outcome(project, workspace.page_number, 1, recovered=False)

    report = pipeline.run_pages(
        project,
        [1, 1, 2],
        dependencies=pipeline.PipelineDependencies(
            open_workspace=lambda root, page: ExperimentWorkspace(
                f"batch-{page}", root, root / "experiments" / str(page), root,
                page, "d" * 64,
            ),
            evidence_recorder=lambda _workspace: object(),
            candidate_loop=loop,
            director_invoke=stage_barrier(directors),
            provider_runner=stage_barrier(providers),
            reviewer_invoke=lambda: None,
        ),
        configuration=pipeline.PipelineConfiguration(
            page_workers=2,
            initial_page_concurrency=2,
            maximum_page_concurrency=2,
            director_concurrency=2,
            image2_concurrency=2,
            review_concurrency=1,
        ),
    )

    assert report.completed_pages == (1, 2)
    assert report.failed_pages == {}
    assert set(report.page_outcomes) == {1, 2}
    assert len(rounds) == 1
    assert rounds[0].successes == rounds[0].completed == rounds[0].expected == 2
    assert rounds[0].failures == rounds[0].rate_limits == 0
    assert report.stage_peaks == {
        "director": 2,
        "image2": 2,
        "review": 1,
        "reconstruction": 0,
        "assembly": 0,
    }


def test_production_run_pages_scales_100_pages_with_bounded_recovery(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    # Break caught: run_pages omits post-accept reconstruction/final assembly or
    # loses bounded overlap, throttle recovery, result isolation, or resume safety.
    import adaptive_scheduler
    from workflow_v6_pipeline import PipelineConfiguration, PipelineDependencies, run_pages

    project = (tmp_path / "scale-project").resolve()
    project.mkdir()
    workload = DeterministicScaleWorkload(project)
    scheduler_history: list[int] = []
    gate_history: list[int] = []
    history_lock = threading.Lock()
    real_note_failure = adaptive_scheduler.AdaptiveScheduler.note_failure
    real_record_round = adaptive_scheduler.AdaptiveScheduler.record_round
    real_throttle = adaptive_scheduler.ProjectGenerationGate.throttle_on_429
    real_gate_recovery = adaptive_scheduler.ProjectGenerationGate.record_stable_success

    def note_failure(scheduler: Any, error: BaseException) -> bool:
        result = real_note_failure(scheduler, error)
        with history_lock:
            scheduler_history.append(scheduler.active_concurrency)
        return result

    def record_round(scheduler: Any, outcome: Any, *, allow_recovery: bool = True) -> Any:
        result = real_record_round(scheduler, outcome, allow_recovery=allow_recovery)
        with history_lock:
            scheduler_history.append(scheduler.active_concurrency)
        return result

    def throttle(gate: Any) -> None:
        real_throttle(gate)
        with history_lock:
            gate_history.append(gate._load()["active_limit"])

    def recover_gate(gate: Any) -> None:
        real_gate_recovery(gate)
        with history_lock:
            gate_history.append(gate._load()["active_limit"])

    monkeypatch.setattr(adaptive_scheduler.AdaptiveScheduler, "note_failure", note_failure)
    monkeypatch.setattr(adaptive_scheduler.AdaptiveScheduler, "record_round", record_round)
    monkeypatch.setattr(adaptive_scheduler.ProjectGenerationGate, "throttle_on_429", throttle)
    monkeypatch.setattr(adaptive_scheduler.ProjectGenerationGate, "record_stable_success", recover_gate)

    configuration = PipelineConfiguration(
        page_workers=8,
        initial_page_concurrency=8,
        maximum_page_concurrency=8,
        director_concurrency=4,
        image2_concurrency=3,
        review_concurrency=3,
        reconstruction_concurrency=2,
        assembly_concurrency=1,
        provider_profile="speed",
    )
    dependencies = PipelineDependencies(
        open_workspace=workload.open_workspace,
        evidence_recorder=workload.evidence_recorder,
        candidate_loop=workload.candidate_loop,
        director_invoke=workload.director,
        provider_runner=workload.provider,
        reviewer_invoke=workload.reviewer,
        reconstruct_page=workload.reconstruct_page,
        assemble_project=workload.assemble_project,
    )
    requested_pages = [*range(1, 101), 20, 20, 50]
    gc.collect()
    baseline_threads = threading.active_count()
    baseline_children = len(multiprocessing.active_children())
    baseline_handles = _handle_count()

    first, elapsed = _run_with_deadlock_guard(
        lambda: run_pages(
            project,
            requested_pages,
            dependencies=dependencies,
            configuration=configuration,
        )
    )
    first_call_totals = Counter(
        {stage: sum(calls[stage] for calls in workload.calls.values()) for stage in (
            "director", "provider", "review", "reconstruction",
        )}
    )
    first_scheduler_history = list(scheduler_history)
    first_gate_history = list(gate_history)
    workload.run_number = 2
    recovery, recovery_elapsed = _run_with_deadlock_guard(
        lambda: run_pages(
            project,
            list(first.completed_pages),
            dependencies=dependencies,
            configuration=configuration,
        )
    )
    second_call_totals = Counter(
        {stage: sum(calls[stage] for calls in workload.calls.values()) for stage in first_call_totals}
    )
    gc.collect()
    resource_delta = {
        "threads": threading.active_count() - baseline_threads,
        "child_processes": len(multiprocessing.active_children()) - baseline_children,
        "handles": _handle_count() - baseline_handles,
        "temporary_files": len(tuple(project.rglob("*.tmp"))),
        "attachment_cache_entries": workload.attachment_renders,
    }
    serial_baseline = _scripted_serial_baseline()
    elapsed_ratio = elapsed / serial_baseline

    assert set(first.completed_pages).isdisjoint(first.failed_pages)
    assert set(first.completed_pages) | set(first.failed_pages) == set(range(1, 101))
    assert first.failed_pages == {
        RATE_LIMIT_PAGE: "TooManyRequests: scripted contraction",
        ISOLATED_FAILURE_PAGE: "LoopOutcome: failed after 3 attempt(s) with 1 problem(s)",
    }
    assert len(first.completed_pages) == 98
    assert all(outcome.status == "accepted" for outcome in first.page_outcomes.values())
    assert all(count == 1 for count in workload.submissions[1].values())
    assert len(workload.submissions[1]) == 100
    assert all(count == 1 for count in workload.submissions[2].values())
    assert recovery.completed_pages == first.completed_pages
    assert recovery.failed_pages == {}
    assert second_call_totals == first_call_totals, "accepted recovery made an external/stage call"
    assert workload.calls[INTERRUPTED_RECOVERY_PAGE]["director"] == 0
    assert workload.calls[INTERRUPTED_RECOVERY_PAGE]["provider"] == 0
    assert workload.calls[INTERRUPTED_RECOVERY_PAGE]["review"] == 0
    assert workload.attachment_renders == 1
    assert workload.assembly_calls == 2

    assert first.stage_peaks == dict(workload.peaks)
    assert first.stage_peaks["director"] <= configuration.director_concurrency
    assert first.stage_peaks["image2"] <= configuration.image2_concurrency
    assert first.stage_peaks["review"] <= configuration.review_concurrency
    assert first.stage_peaks["reconstruction"] <= configuration.reconstruction_concurrency
    assert first.stage_peaks["assembly"] <= configuration.assembly_concurrency
    assert workload.page_peak > 1
    assert workload.executor_thread_peak <= configuration.page_workers
    assert len(workload.director_completion_times) == 3
    assert max(workload.director_completion_times) - min(workload.director_completion_times) < 0.04

    contraction_index = first_scheduler_history.index(1)
    assert max(first_scheduler_history[contraction_index + 1 :]) >= 4
    assert first.scheduler_concurrency > 1
    assert 1 in first_gate_history and 3 in first_gate_history
    assert max(len(outcome.attempts) for outcome in first.page_outcomes.values()) == 3
    assert {len(outcome.attempts) for outcome in first.page_outcomes.values()} == {1, 2, 3}
    assert all(len(outcome.attempts) <= 3 for outcome in first.page_outcomes.values())
    assert elapsed_ratio < 0.70
    assert resource_delta["threads"] <= 1
    assert resource_delta["child_processes"] == 0
    assert resource_delta["handles"] <= 8
    assert resource_delta["temporary_files"] == 0

    evidence = {
        "schema_version": "awesome-production-100-page-scale-v1",
        "requested_page_count": 100,
        "completed_page_count": len(first.completed_pages),
        "failed_pages": first.failed_pages,
        "elapsed_seconds": round(elapsed, 6),
        "recovery_elapsed_seconds": round(recovery_elapsed, 6),
        "serial_baseline_seconds": round(serial_baseline, 6),
        "elapsed_to_serial_ratio": round(elapsed_ratio, 6),
        "throughput_pages_per_second": round(100 / elapsed, 6),
        "stage_peaks": first.stage_peaks,
        "page_peak": workload.page_peak,
        "executor_thread_peak": workload.executor_thread_peak,
        "first_run_scheduler_concurrency_history": first_scheduler_history,
        "first_run_gate_concurrency_history": first_gate_history,
        "recovery_scheduler_concurrency_history": scheduler_history[len(first_scheduler_history) :],
        "recovery_gate_concurrency_history": gate_history[len(first_gate_history) :],
        "attempt_counts": {
            str(page): len(outcome.attempts) for page, outcome in first.page_outcomes.items()
        },
        "first_run_call_totals": dict(first_call_totals),
        "recovery_added_call_totals": {
            stage: second_call_totals[stage] - first_call_totals[stage]
            for stage in first_call_totals
        },
        "attachment_renders": workload.attachment_renders,
        "assembly_calls_across_two_runs": workload.assembly_calls,
        "resource_delta": resource_delta,
    }
    repo = next(parent for parent in Path(__file__).resolve().parents if (parent / ".superpowers").is_dir())
    evidence_path = repo / ".superpowers" / "sdd" / "2026-08-15-awesome-production-pipeline-performance" / "task-4-evidence" / "awesome-100-page-scale.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")

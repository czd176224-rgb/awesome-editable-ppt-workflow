"""Bounded production runner for independent Awesome creative pages."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Condition, Lock, RLock, Semaphore
from typing import Any

from adaptive_scheduler import AdaptiveScheduler, ProjectGenerationGate, RoundOutcome
from complex_page_experiment.evidence import EvidenceRecorder
from complex_page_experiment.loop import run_candidate_loop
from complex_page_experiment.workspace import open_live_page_workspace
from workflow_v6_reconstruction_worker import (
    assemble_reconstructed_project,
    reconstruct_accepted_page,
)


def _default_recorder(workspace: Any) -> EvidenceRecorder:
    return EvidenceRecorder(
        workspace.experiment_root,
        project_copy=workspace.project_copy,
        experiment_id=workspace.experiment_id,
        page_number=workspace.page_number,
        source_identity=workspace.source_snapshot_sha256,
    )


@dataclass(frozen=True)
class PipelineConfiguration:
    """Concurrency bounds for the stages currently run by the creative loop."""

    page_workers: int = 12
    initial_page_concurrency: int = 2
    maximum_page_concurrency: int = 2
    director_concurrency: int = 3
    image2_concurrency: int = 2
    review_concurrency: int = 3
    reconstruction_concurrency: int = 2
    assembly_concurrency: int = 1
    provider_profile: str = "balanced"
    timeout: int = 120
    max_corrections: int = 2

    def __post_init__(self) -> None:
        for name in (
            "page_workers", "initial_page_concurrency", "maximum_page_concurrency",
            "director_concurrency", "image2_concurrency", "review_concurrency",
            "reconstruction_concurrency", "assembly_concurrency", "timeout",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.initial_page_concurrency > self.maximum_page_concurrency:
            raise ValueError("initial_page_concurrency must not exceed maximum_page_concurrency")
        if type(self.max_corrections) is not int or self.max_corrections < 0:
            raise ValueError("max_corrections must be a non-negative integer")
        if self.provider_profile not in {"quality", "balanced", "speed"}:
            raise ValueError("provider_profile must be quality, balanced, or speed")


@dataclass(frozen=True)
class PipelineDependencies:
    """Dependency injection boundary used by production and deterministic tests."""

    open_workspace: Callable[[Path, int], Any] = open_live_page_workspace
    evidence_recorder: Callable[[Any], Any] = _default_recorder
    candidate_loop: Callable[..., Any] = run_candidate_loop
    director_invoke: Callable[..., Any] | None = None
    provider_runner: Callable[..., Any] | None = None
    reviewer_invoke: Callable[..., Any] | None = None
    reconstruct_page: Callable[[Any, Any], Any] | None = None
    assemble_project: Callable[[Path, dict[int, Any]], Any] | None = None


def production_pipeline_dependencies() -> PipelineDependencies:
    """Return the single public pipeline with automatic reconstruction/assembly."""
    return PipelineDependencies(
        reconstruct_page=reconstruct_accepted_page,
        assemble_project=assemble_reconstructed_project,
    )


@dataclass(frozen=True)
class PipelineReport:
    completed_pages: tuple[int, ...]
    failed_pages: dict[int, str]
    page_outcomes: dict[int, Any]
    stage_peaks: dict[str, int]
    scheduler_concurrency: int

    def to_dict(self) -> dict[str, Any]:
        """Return the stable public CLI summary without creative artifacts."""
        return {
            "completed_pages": list(self.completed_pages),
            "failed_pages": dict(sorted(self.failed_pages.items())),
            "page_outcomes": {
                str(page_number): _outcome_summary(outcome)
                for page_number, outcome in sorted(self.page_outcomes.items())
            },
            "scheduler_concurrency": self.scheduler_concurrency,
            "stage_peaks": dict(sorted(self.stage_peaks.items())),
        }


def _outcome_summary(outcome: Any) -> dict[str, Any]:
    """Reduce a loop outcome to operation status, never its prompt or paths."""
    if all(hasattr(outcome, field) for field in (
        "status", "attempts", "accepted", "failure_problems", "correction_count",
    )):
        return {
            "accepted": getattr(outcome, "accepted") is not None,
            "attempt_count": len(getattr(outcome, "attempts")),
            "correction_count": getattr(outcome, "correction_count"),
            "failure_problem_count": len(getattr(outcome, "failure_problems")),
            "status": getattr(outcome, "status"),
        }
    return {"status": "completed"}


def _terminal_failure_summary(outcome: Any) -> str | None:
    """Describe a returned failed loop without exposing creative problem text."""
    if getattr(outcome, "status", None) != "failed":
        return None
    attempts = getattr(outcome, "attempts", ())
    problems = getattr(outcome, "failure_problems", ())
    return (
        f"LoopOutcome: failed after {len(attempts)} attempt(s) "
        f"with {len(problems)} problem(s)"
    )


class _StageLimits:
    def __init__(self, configuration: PipelineConfiguration) -> None:
        self._semaphores = {
            "director": Semaphore(configuration.director_concurrency),
            "image2": Semaphore(configuration.image2_concurrency),
            "review": Semaphore(configuration.review_concurrency),
            "reconstruction": Semaphore(configuration.reconstruction_concurrency),
            "assembly": Semaphore(configuration.assembly_concurrency),
        }
        self._active = {name: 0 for name in self._semaphores}
        self._peaks = {name: 0 for name in self._semaphores}
        self._lock = Lock()

    @contextmanager
    def bounded(self, name: str):
        semaphore = self._semaphores[name]
        with semaphore:
            with self._lock:
                self._active[name] += 1
                self._peaks[name] = max(self._peaks[name], self._active[name])
            try:
                yield
            finally:
                with self._lock:
                    self._active[name] -= 1

    @property
    def peaks(self) -> dict[str, int]:
        with self._lock:
            return dict(self._peaks)


@dataclass(frozen=True)
class _PageExecution:
    outcome: Any | None
    error: Exception | None
    provider_success_generations: tuple[int, ...]
    rate_limits: int


@dataclass(frozen=True)
class _ProviderCall:
    identifier: int
    generation: int


class _ThrottleEpoch:
    """Track live provider generations and apply throttle changes atomically."""

    def __init__(self, scheduler: AdaptiveScheduler) -> None:
        self._scheduler = scheduler
        self._generation = 0
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._gate_transition = False
        self._next_provider_call = 0
        self._active_provider_calls: dict[int, int] = {}

    def provider_started(self) -> _ProviderCall:
        with self._condition:
            while self._gate_transition:
                self._condition.wait()
            self._next_provider_call += 1
            call = _ProviderCall(self._next_provider_call, self._generation)
            self._active_provider_calls[call.identifier] = call.generation
            return call

    def _complete_provider(self, call: _ProviderCall) -> int:
        generation = self._active_provider_calls.pop(call.identifier, None)
        if generation != call.generation:
            raise RuntimeError("provider call completion is not active")
        return generation

    def provider_succeeded(self, call: _ProviderCall) -> int:
        with self._lock:
            return self._complete_provider(call)

    def provider_failed(self, call: _ProviderCall) -> None:
        with self._lock:
            self._complete_provider(call)

    def rate_limited(
        self,
        gate: ProjectGenerationGate,
        error: BaseException,
        call: _ProviderCall,
    ) -> None:
        with self._condition:
            while self._gate_transition:
                self._condition.wait()
            self._complete_provider(call)
            self._generation += 1
            self._scheduler.note_failure(error)
            self._gate_transition = True
        try:
            gate.throttle_on_429()
        finally:
            with self._condition:
                self._gate_transition = False
                self._condition.notify_all()

    def permits_recovery(self, generations: Sequence[int]) -> bool:
        with self._lock:
            return self._generation > 0 and self._generation in generations

    def record_round(
        self,
        gate: ProjectGenerationGate,
        outcome: RoundOutcome,
        provider_success_generations: Sequence[int],
    ) -> bool:
        """Atomically decide and apply recovery against concurrent 429s."""
        with self._condition:
            recovery_allowed = (
                outcome.rate_limits == 0
                and not self._gate_transition
                and self.permits_recovery(provider_success_generations)
                and not self._active_provider_calls
            )
            if not recovery_allowed:
                self._scheduler.record_round(outcome, allow_recovery=False)
                return False
            self._gate_transition = True
        try:
            gate.record_stable_success()
        except Exception:
            with self._condition:
                self._gate_transition = False
                self._condition.notify_all()
            raise
        with self._condition:
            try:
                self._scheduler.record_round(outcome, allow_recovery=True)
            finally:
                self._gate_transition = False
                self._condition.notify_all()
        return True


def _status_code(error: BaseException) -> int | None:
    value = getattr(error, "status_code", getattr(error, "code", None))
    return value if type(value) is int else None


def _page_numbers(page_numbers: Sequence[int]) -> list[int]:
    unique: list[int] = []
    seen: set[int] = set()
    for page_number in page_numbers:
        if type(page_number) is not int or page_number < 1:
            raise ValueError("page_numbers must contain positive integers")
        if page_number not in seen:
            unique.append(page_number)
            seen.add(page_number)
    return unique


def run_pages(
    project: Path,
    page_numbers: Sequence[int],
    *,
    dependencies: PipelineDependencies | None = None,
    configuration: PipelineConfiguration,
) -> PipelineReport:
    """Run independent live page loops in a bounded window without a project lock."""
    dependencies = dependencies or production_pipeline_dependencies()
    root = Path(project).resolve()
    pages = _page_numbers(page_numbers)
    if not pages:
        return PipelineReport((), {}, {}, {name: 0 for name in ("director", "image2", "review", "reconstruction", "assembly")}, 0)

    limits = _StageLimits(configuration)
    scheduler = AdaptiveScheduler(
        len(pages),
        initial_concurrency=configuration.initial_page_concurrency,
        maximum_concurrency=configuration.maximum_page_concurrency,
    )
    throttle_epoch = _ThrottleEpoch(scheduler)
    gate = ProjectGenerationGate(root, profile=configuration.provider_profile)
    completed: dict[int, Any] = {}
    failures: dict[int, str] = {}

    def run_one(page_number: int) -> _PageExecution:
        workspace = dependencies.open_workspace(root, page_number)
        recorder = dependencies.evidence_recorder(workspace)
        provider_success_generations: list[int] = []
        rate_limits = 0

        def director(*args: Any, **kwargs: Any) -> Any:
            if dependencies.director_invoke is None:
                from codex_subscription_runtime import invoke_structured
                invoke = invoke_structured
            else:
                invoke = dependencies.director_invoke
            with limits.bounded("director"):
                return invoke(*args, **kwargs)

        def provider(*args: Any, **kwargs: Any) -> Any:
            nonlocal rate_limits
            if dependencies.provider_runner is None:
                from workflow_v6_image import _run
                invoke = _run
            else:
                invoke = dependencies.provider_runner
            provider_call = throttle_epoch.provider_started()
            provider_completed = False
            try:
                # The project gate intentionally covers only the Image2 call.
                with gate.lease(page_number=page_number):
                    with limits.bounded("image2"):
                        try:
                            result = invoke(*args, **kwargs)
                        except Exception as exc:
                            if _status_code(exc) == 429:
                                rate_limits += 1
                                try:
                                    throttle_epoch.rate_limited(
                                        gate, exc, provider_call,
                                    )
                                finally:
                                    provider_completed = True
                            else:
                                throttle_epoch.provider_failed(provider_call)
                                provider_completed = True
                            raise
                        success_generation = throttle_epoch.provider_succeeded(
                            provider_call,
                        )
                        provider_completed = True
                        provider_success_generations.append(success_generation)
            except Exception:
                if not provider_completed:
                    throttle_epoch.provider_failed(provider_call)
                raise
            return result

        def reviewer(*args: Any, **kwargs: Any) -> Any:
            if dependencies.reviewer_invoke is None:
                from codex_subscription_runtime import invoke_structured
                invoke = invoke_structured
            else:
                invoke = dependencies.reviewer_invoke
            with limits.bounded("review"):
                return invoke(*args, **kwargs)

        try:
            outcome = dependencies.candidate_loop(
                workspace,
                timeout=configuration.timeout,
                recorder=recorder,
                director_invoke=director,
                provider_runner=provider,
                reviewer_invoke=reviewer,
                max_corrections=configuration.max_corrections,
            )
            if (
                dependencies.reconstruct_page is not None
                and getattr(outcome, "status", None) == "accepted"
                and getattr(outcome, "accepted", None) is not None
            ):
                with limits.bounded("reconstruction"):
                    dependencies.reconstruct_page(workspace, outcome)
        except Exception as exc:
            return _PageExecution(
                None, exc, tuple(provider_success_generations), rate_limits,
            )
        return _PageExecution(
            outcome, None, tuple(provider_success_generations), rate_limits,
        )

    pending = list(pages)
    running: dict[Future[Any], int] = {}
    workers = min(configuration.page_workers, len(pages))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="awesome-page") as executor:
        while pending or running:
            while pending and len(running) < min(configuration.page_workers, scheduler.active_concurrency):
                page_number = pending.pop(0)
                running[executor.submit(run_one, page_number)] = page_number
            done, _ = wait(tuple(running), return_when=FIRST_COMPLETED)
            successes = 0
            rate_limits = 0
            provider_success_generations: list[int] = []
            for future in done:
                page_number = running.pop(future)
                try:
                    execution = future.result()
                    rate_limits += execution.rate_limits
                    if execution.error is not None:
                        raise execution.error
                    terminal_failure = _terminal_failure_summary(
                        execution.outcome,
                    )
                    if terminal_failure is not None:
                        failures[page_number] = terminal_failure
                        continue
                    provider_success_generations.extend(
                        execution.provider_success_generations,
                    )
                    completed[page_number] = execution.outcome
                    successes += 1
                except Exception as exc:  # one page must never abandon its siblings
                    failures[page_number] = f"{type(exc).__name__}: {exc}"
                    if _status_code(exc) == 429 and not rate_limits:
                        rate_limits = 1
            throttle_epoch.record_round(
                gate,
                RoundOutcome(
                    successes=successes,
                    completed=successes,
                    expected=len(done),
                    failures=len(done) - successes,
                    rate_limits=rate_limits,
                ),
                provider_success_generations,
            )

    if dependencies.assemble_project is not None:
        accepted_outcomes = {
            page_number: outcome
            for page_number, outcome in sorted(completed.items())
            if (
                getattr(outcome, "status", None) == "accepted"
                and getattr(outcome, "accepted", None) is not None
            )
        }
        with limits.bounded("assembly"):
            dependencies.assemble_project(root, accepted_outcomes)

    return PipelineReport(
        completed_pages=tuple(sorted(completed)),
        failed_pages=dict(sorted(failures.items())),
        page_outcomes=dict(sorted(completed.items())),
        stage_peaks=limits.peaks,
        scheduler_concurrency=scheduler.active_concurrency,
    )


__all__ = [
    "PipelineConfiguration", "PipelineDependencies", "PipelineReport",
    "production_pipeline_dependencies", "run_pages",
]

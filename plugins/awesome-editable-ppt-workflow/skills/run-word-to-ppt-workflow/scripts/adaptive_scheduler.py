"""Bounded adaptive scheduling for independent Word-page work."""

from __future__ import annotations

import json
import math
import os
import random
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence


PROFILE_CONCURRENCY = {"balanced": 2, "quality": 2, "speed": 3}
SCHEDULER_STATE_FILE = "04_v6/generation_scheduler.json"
SCHEDULER_ARTIFACT_VERSION = "v6-generation-scheduler-v1"
PAGE_OWNERSHIP_STATE_FILE = "04_v6/generation_page_owners.json"
PAGE_OWNERSHIP_ARTIFACT_VERSION = "v6-page-ownership-v1"


def _status_code(error: BaseException) -> int | None:
    value = getattr(error, "status_code", getattr(error, "code", None))
    return value if type(value) is int else None


def should_retry(error: BaseException) -> bool:
    status = _status_code(error)
    return status == 429 or (status is not None and 500 <= status <= 599) or isinstance(
        error, (ConnectionError, TimeoutError)
    ) or getattr(error, "network", False) is True


def jittered_retry_delay(attempt: int, *, jitter: float) -> float:
    if type(attempt) is not int or attempt < 0:
        raise ValueError("attempt must be a non-negative integer")
    if not isinstance(jitter, (int, float)) or not 0.0 <= float(jitter) <= 1.0:
        raise ValueError("jitter must be between 0 and 1")
    base = min(30.0, float(2 ** attempt))
    return base * (0.75 + 0.5 * float(jitter))


class ProjectGenerationGate:
    """Cross-process V6 provider lease gate with a persisted 429 throttle."""

    def __init__(
        self,
        project: Path,
        *,
        profile: str,
        stale_after: float = 1_200.0,
        poll_interval: float = 0.05,
    ) -> None:
        try:
            configured = PROFILE_CONCURRENCY[profile]
        except KeyError as exc:
            raise ValueError("profile must be quality, balanced, or speed") from exc
        if not isinstance(stale_after, (int, float)) or not 0 < float(stale_after) <= 86_400:
            raise ValueError("stale_after must be between 0 and 86400 seconds")
        if not isinstance(poll_interval, (int, float)) or not 0 < float(poll_interval) <= 1:
            raise ValueError("poll_interval must be between 0 and 1 second")
        self.project = Path(project).resolve()
        self.path = self.project / SCHEDULER_STATE_FILE
        self.profile = profile
        self.configured_max = configured
        self.stale_after = float(stale_after)
        self.poll_interval = float(poll_interval)

    def _default_state(self) -> dict[str, Any]:
        return {
            "artifact_version": SCHEDULER_ARTIFACT_VERSION,
            "profile": self.profile,
            "configured_max": self.configured_max,
            "active_limit": self.configured_max,
            "leases": {},
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._default_state()
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("V6 generation scheduler state must be an object")
        if (
            value.get("artifact_version") != SCHEDULER_ARTIFACT_VERSION
            or value.get("profile") != self.profile
            or value.get("configured_max") != self.configured_max
            or type(value.get("active_limit")) is not int
            or not 1 <= value["active_limit"] <= self.configured_max
            or not isinstance(value.get("leases"), dict)
        ):
            raise ValueError("V6 generation scheduler state is invalid")
        for lease_id, lease in value["leases"].items():
            if (
                not isinstance(lease_id, str)
                or not 1 <= len(lease_id) <= 96
                or not isinstance(lease, dict)
                or lease.get("owner") != lease_id
                or type(lease.get("page_number")) is not int
                or lease["page_number"] < 1
                or not isinstance(lease.get("acquired_at"), (int, float))
                or not math.isfinite(float(lease["acquired_at"]))
            ):
                raise ValueError("V6 generation scheduler lease is invalid")
        return value

    def _purge_stale(self, state: dict[str, Any], now: float) -> bool:
        before = len(state["leases"])
        state["leases"] = {
            lease_id: lease
            for lease_id, lease in state["leases"].items()
            if -300.0 <= now - float(lease["acquired_at"]) <= self.stale_after
        }
        return len(state["leases"]) != before

    def _write(self, state: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        try:
            for attempt in range(10):
                try:
                    os.replace(temporary, self.path)
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(0.05)
        finally:
            temporary.unlink(missing_ok=True)

    def throttle_on_429(self) -> None:
        from workflow_v6_state import mutation_lock

        with mutation_lock(self.project):
            state = self._load()
            self._purge_stale(state, time.time())
            state["active_limit"] = 1
            self._write(state)

    def record_stable_success(self) -> None:
        """Gradually restore future provider leases after a 429 throttle."""
        from workflow_v6_state import mutation_lock

        with mutation_lock(self.project):
            state = self._load()
            self._purge_stale(state, time.time())
            state["active_limit"] = min(
                self.configured_max, int(state["active_limit"]) + 1,
            )
            self._write(state)

    @contextmanager
    def lease(self, *, page_number: int, wait_timeout: float = 960.0):
        from workflow_v6_state import mutation_lock

        if type(page_number) is not int or page_number < 1:
            raise ValueError("page_number must be positive")
        if not isinstance(wait_timeout, (int, float)) or float(wait_timeout) <= 0:
            raise ValueError("wait_timeout must be positive")
        lease_id = f"{os.getpid()}-{uuid.uuid4().hex}"
        deadline = time.monotonic() + float(wait_timeout)
        while True:
            acquired = False
            with mutation_lock(self.project):
                now = time.time()
                state = self._load()
                changed = self._purge_stale(state, now)
                if len(state["leases"]) < state["active_limit"]:
                    state["leases"][lease_id] = {
                        "page_number": page_number,
                        "owner": lease_id,
                        "acquired_at": now,
                    }
                    acquired = True
                    changed = True
                if changed:
                    self._write(state)
            if acquired:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("timed out waiting for a V6 generation concurrency lease")
            time.sleep(self.poll_interval)
        try:
            yield lease_id
        finally:
            with mutation_lock(self.project):
                state = self._load()
                changed = self._purge_stale(state, time.time())
                lease = state["leases"].get(lease_id)
                if isinstance(lease, dict) and lease.get("owner") == lease_id:
                    del state["leases"][lease_id]
                    changed = True
                if changed:
                    self._write(state)


@dataclass(frozen=True)
class PageOwnershipLease:
    page_number: int
    owner: str
    generation: int


class ProjectPageOwnership:
    """Exclusive, fenced ownership of one complete page-generation transaction."""

    def __init__(
        self,
        project: Path,
        *,
        stale_after: float = 7_200.0,
        poll_interval: float = 0.05,
    ) -> None:
        if not isinstance(stale_after, (int, float)) or not 0 < float(stale_after) <= 86_400:
            raise ValueError("stale_after must be between 0 and 86400 seconds")
        if not isinstance(poll_interval, (int, float)) or not 0 < float(poll_interval) <= 1:
            raise ValueError("poll_interval must be between 0 and 1 second")
        self.project = Path(project).resolve()
        self.path = self.project / PAGE_OWNERSHIP_STATE_FILE
        self.stale_after = float(stale_after)
        self.poll_interval = float(poll_interval)

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "artifact_version": PAGE_OWNERSHIP_ARTIFACT_VERSION,
            "next_generation": {},
            "owners": {},
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._default_state()
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or value.get("artifact_version") != PAGE_OWNERSHIP_ARTIFACT_VERSION
            or not isinstance(value.get("next_generation"), dict)
            or not isinstance(value.get("owners"), dict)
        ):
            raise ValueError("V6 page ownership state is invalid")
        for page_key, generation in value["next_generation"].items():
            if not str(page_key).isdigit() or int(page_key) < 1 or type(generation) is not int or generation < 0:
                raise ValueError("V6 page ownership generation is invalid")
        for page_key, owner in value["owners"].items():
            if (
                not str(page_key).isdigit()
                or int(page_key) < 1
                or not isinstance(owner, dict)
                or not isinstance(owner.get("owner"), str)
                or not 1 <= len(owner["owner"]) <= 96
                or type(owner.get("generation")) is not int
                or owner["generation"] < 1
                or value["next_generation"].get(str(page_key), 0) < owner["generation"]
                or not isinstance(owner.get("acquired_at"), (int, float))
                or not math.isfinite(float(owner["acquired_at"]))
            ):
                raise ValueError("V6 page ownership owner is invalid")
        return value

    def _purge_stale(self, state: dict[str, Any], now: float) -> bool:
        before = len(state["owners"])
        state["owners"] = {
            page_key: owner
            for page_key, owner in state["owners"].items()
            if -300.0 <= now - float(owner["acquired_at"]) <= self.stale_after
        }
        return len(state["owners"]) != before

    def _write(self, state: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        try:
            for attempt in range(10):
                try:
                    os.replace(temporary, self.path)
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(0.05)
        finally:
            temporary.unlink(missing_ok=True)

    def assert_current(self, lease: PageOwnershipLease, *, refresh: bool = True) -> None:
        """Fence stale owners and optionally renew the current transaction heartbeat."""
        from workflow_v6_state import mutation_lock

        with mutation_lock(self.project):
            state = self._load()
            changed = self._purge_stale(state, time.time())
            current = state["owners"].get(str(lease.page_number))
            if not isinstance(current, dict) or (
                current.get("owner") != lease.owner
                or current.get("generation") != lease.generation
            ):
                if changed:
                    self._write(state)
                raise RuntimeError("V6 page ownership was superseded")
            if refresh:
                current["acquired_at"] = time.time()
                changed = True
            if changed:
                self._write(state)

    def commit_if_current(
        self, lease: PageOwnershipLease, action: Callable[[], Any],
    ) -> Any:
        """Run one short artifact commit atomically with the ownership fence."""
        from workflow_v6_state import mutation_lock

        with mutation_lock(self.project):
            state = self._load()
            changed = self._purge_stale(state, time.time())
            current = state["owners"].get(str(lease.page_number))
            if not isinstance(current, dict) or (
                current.get("owner") != lease.owner
                or current.get("generation") != lease.generation
            ):
                if changed:
                    self._write(state)
                raise RuntimeError("V6 page ownership was superseded")
            result = action()
            current["acquired_at"] = time.time()
            self._write(state)
            return result

    @contextmanager
    def own(self, *, page_number: int, wait_timeout: float = 7_200.0):
        from workflow_v6_state import mutation_lock

        if type(page_number) is not int or page_number < 1:
            raise ValueError("page_number must be positive")
        if not isinstance(wait_timeout, (int, float)) or float(wait_timeout) <= 0:
            raise ValueError("wait_timeout must be positive")
        owner_id = f"{os.getpid()}-{uuid.uuid4().hex}"
        page_key = str(page_number)
        deadline = time.monotonic() + float(wait_timeout)
        lease: PageOwnershipLease | None = None
        while lease is None:
            with mutation_lock(self.project):
                now = time.time()
                state = self._load()
                changed = self._purge_stale(state, now)
                if page_key not in state["owners"]:
                    generation = int(state["next_generation"].get(page_key, 0)) + 1
                    state["next_generation"][page_key] = generation
                    state["owners"][page_key] = {
                        "owner": owner_id,
                        "generation": generation,
                        "acquired_at": now,
                    }
                    lease = PageOwnershipLease(page_number, owner_id, generation)
                    changed = True
                if changed:
                    self._write(state)
            if lease is not None:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("timed out waiting for V6 page ownership")
            time.sleep(self.poll_interval)
        try:
            yield lease
        finally:
            with mutation_lock(self.project):
                state = self._load()
                changed = self._purge_stale(state, time.time())
                current = state["owners"].get(page_key)
                if isinstance(current, dict) and (
                    current.get("owner") == lease.owner
                    and current.get("generation") == lease.generation
                ):
                    del state["owners"][page_key]
                    changed = True
                if changed:
                    self._write(state)


ReadyState = Literal["queued", "repair", "accepted"]


@dataclass(frozen=True)
class PageJob:
    page_number: int
    complexity_weight: int
    state: ReadyState


@dataclass(frozen=True)
class RoundOutcome:
    successes: int = 0
    completed: int = 0
    expected: int = 0
    failures: int = 0
    rate_limits: int = 0
    timeouts: int = 0
    infrastructure_errors: int = 0


@dataclass(frozen=True)
class SchedulerSnapshot:
    concurrency: int
    launch_capacity: int
    window_size: int
    active_count: int
    active_weight: int
    queued_depth: int
    repair_depth: int
    accepted_depth: int


class AdaptiveScheduler:
    minimum_concurrency = 1
    maximum_concurrency = 8
    maximum_active_weight = 8

    def __init__(
        self,
        page_count: int,
        initial_concurrency: int | None = None,
        maximum_concurrency: int | None = None,
    ):
        if type(page_count) is not int or page_count < 1:
            raise ValueError("page_count must be a positive integer")
        configured_max = self.maximum_concurrency if maximum_concurrency is None else maximum_concurrency
        if type(configured_max) is not int or not self.minimum_concurrency <= configured_max <= self.maximum_concurrency:
            raise ValueError("maximum_concurrency must be between 1 and 8")
        self.configured_maximum_concurrency = configured_max
        default = min(configured_max, 2 if page_count <= 10 else (3 if page_count <= 40 else 4))
        concurrency = default if initial_concurrency is None else initial_concurrency
        if type(concurrency) is not int or not self.minimum_concurrency <= concurrency <= configured_max:
            raise ValueError("initial_concurrency must be within the configured concurrency bound")
        self.page_count = page_count
        self._concurrency = concurrency
        self._launch_capacity = concurrency
        self._active_count = 0
        self._active_weight = 0
        self._depths = {"queued": 0, "repair": 0, "accepted": 0}
        self.last_trigger_code = "initial"
        self.completed_receipts: dict[int, dict[str, Any]] = {}

    @classmethod
    def for_profile(cls, profile: str, *, page_count: int = 1) -> "AdaptiveScheduler":
        try:
            concurrency = PROFILE_CONCURRENCY[profile]
        except KeyError as exc:
            raise ValueError("profile must be quality, balanced, or speed") from exc
        return cls(
            page_count=page_count,
            initial_concurrency=concurrency,
            maximum_concurrency=concurrency,
        )

    @property
    def active_concurrency(self) -> int:
        return self._concurrency

    def note_failure(self, error: BaseException) -> bool:
        if not should_retry(error):
            return False
        if _status_code(error) == 429:
            self._concurrency = 1
            self._launch_capacity = 1
            self.last_trigger_code = "rate_limit"
        return True

    def mark_completed(self, page_number: int, receipt: Mapping[str, Any]) -> None:
        if type(page_number) is not int or page_number < 1:
            raise ValueError("page_number must be positive")
        self.completed_receipts[page_number] = dict(receipt)

    def pending_pages(self, page_numbers: Sequence[int]) -> list[int]:
        return [number for number in page_numbers if number not in self.completed_receipts]

    def run_page(
        self,
        page_number: int,
        action: Callable[[], Mapping[str, Any]],
        *,
        max_attempts: int = 3,
        sleep: Callable[[float], None] | None = None,
        jitter: Callable[[], float] | None = None,
    ) -> dict[str, Any]:
        """Run one not-yet-completed page with narrowly classified retries."""
        if page_number in self.completed_receipts:
            return dict(self.completed_receipts[page_number])
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        sleeper = sleep if sleep is not None else time.sleep
        jitter_source = jitter if jitter is not None else random.random
        for attempt in range(max_attempts):
            try:
                receipt = dict(action())
            except Exception as exc:
                if attempt + 1 >= max_attempts or not self.note_failure(exc):
                    raise
                sleeper(jittered_retry_delay(attempt, jitter=float(jitter_source())))
                continue
            self.mark_completed(page_number, receipt)
            return receipt
        raise RuntimeError("unreachable retry state")

    def run_transient(
        self,
        action: Callable[[], Any],
        *,
        max_attempts: int = 3,
        sleep: Callable[[float], None] | None = None,
        jitter: Callable[[], float] | None = None,
    ) -> Any:
        """Retry one provider operation without treating it as another candidate."""
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        sleeper = sleep if sleep is not None else time.sleep
        jitter_source = jitter if jitter is not None else random.random
        for attempt in range(max_attempts):
            try:
                return action()
            except Exception as exc:
                if attempt + 1 >= max_attempts or not self.note_failure(exc):
                    raise
                sleeper(jittered_retry_delay(attempt, jitter=float(jitter_source())))
        raise RuntimeError("unreachable retry state")

    @property
    def window_size(self) -> int:
        if self.page_count > 100:
            return min(12, max(8, self._concurrency * 2))
        return min(12, self.page_count)

    def snapshot(self) -> SchedulerSnapshot:
        return SchedulerSnapshot(
            concurrency=self._concurrency,
            launch_capacity=self._launch_capacity,
            window_size=self.window_size,
            active_count=self._active_count,
            active_weight=self._active_weight,
            queued_depth=self._depths["queued"],
            repair_depth=self._depths["repair"],
            accepted_depth=self._depths["accepted"],
        )

    @staticmethod
    def _validated(job: PageJob) -> PageJob:
        if job.state not in {"queued", "repair", "accepted"}:
            raise ValueError("page job state must be queued, repair, or accepted")
        if type(job.page_number) is not int or job.page_number < 1:
            raise ValueError("page_number must be positive")
        if job.complexity_weight not in {1, 2, 3}:
            raise ValueError("complexity_weight must be 1, 2, or 3")
        return job

    def next_batch(
        self,
        jobs: Sequence[PageJob],
        *,
        active_count: int = 0,
        active_weight: int = 0,
    ) -> list[int]:
        """Select only launchable work, preferring accepted and fresh queued pages.

        Repair remains eligible whenever capacity remains, but can never consume a
        slot ahead of an accepted or never-attempted page.
        """
        if type(active_count) is not int or active_count < 0:
            raise ValueError("active_count must not be negative")
        if type(active_weight) is not int or active_weight < 0:
            raise ValueError("active_weight must not be negative")
        validated = [self._validated(job) for job in jobs]
        numbers = [job.page_number for job in validated]
        if len(numbers) != len(set(numbers)):
            raise ValueError("page jobs must be unique")
        self._active_count = active_count
        self._active_weight = active_weight
        self._depths = {
            state: sum(job.state == state for job in validated)
            for state in ("queued", "repair", "accepted")
        }
        slots = max(0, self._concurrency - active_count)
        weight_budget = max(0, self.maximum_active_weight - active_weight)
        ordered = sorted(
            validated,
            key=lambda job: (
                {"accepted": 0, "queued": 1, "repair": 2}[job.state],
                -job.complexity_weight,
                job.page_number,
            ),
        )
        selected: list[int] = []
        selected_weight = 0
        for job in ordered:
            if len(selected) >= slots:
                break
            if selected_weight + job.complexity_weight > weight_budget:
                continue
            selected.append(job.page_number)
            selected_weight += job.complexity_weight
        self._launch_capacity = len(selected)
        return selected

    def record_round(
        self, outcome: RoundOutcome, *, allow_recovery: bool = True,
    ) -> SchedulerSnapshot:
        values = (
            outcome.successes,
            outcome.completed,
            outcome.expected,
            outcome.failures,
            outcome.rate_limits,
            outcome.timeouts,
            outcome.infrastructure_errors,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("round outcome counts must be non-negative integers")
        if outcome.rate_limits:
            self._concurrency = self.minimum_concurrency
            self.last_trigger_code = "rate_limit"
        elif outcome.timeouts:
            self._concurrency = max(self.minimum_concurrency, self._concurrency // 2)
            self.last_trigger_code = "timeout"
        elif outcome.infrastructure_errors:
            self._concurrency = max(self.minimum_concurrency, self._concurrency // 2)
            self.last_trigger_code = "infrastructure_error"
        elif outcome.failures:
            self._concurrency = max(self.minimum_concurrency, self._concurrency // 2)
            self.last_trigger_code = "failure"
        elif (
            allow_recovery
            and outcome.expected > 0
            and outcome.completed == outcome.expected == outcome.successes
        ):
            self._concurrency = min(self.configured_maximum_concurrency, self._concurrency + 1)
            self.last_trigger_code = "full_success"
        else:
            self.last_trigger_code = "no_change"
        self._active_count = 0
        self._active_weight = 0
        self._launch_capacity = self._concurrency
        return self.snapshot()

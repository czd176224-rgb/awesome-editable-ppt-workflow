from __future__ import annotations

import atexit
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

from jsonschema import Draft202012Validator


class CodexRuntimeUnavailable(RuntimeError):
    """A retryable failure of the local Codex subscription runtime."""


class _TransportUnavailable(CodexRuntimeUnavailable):
    """The current App Server process can no longer be trusted or reused."""


@dataclass(frozen=True)
class CodexStructuredResult:
    value: Mapping[str, Any]
    thread_id: str
    turn_id: str
    model: str
    model_provider: str
    auth_mode: str
    plan_type: str | None
    usage: Mapping[str, Any]
    safe_trace: Mapping[str, Any]
    effort: str | None = None
    duration_seconds: float | None = None
    startup_reused: bool | None = None


class _JsonlProcess:
    def __init__(self, command: Sequence[str], deadline: float) -> None:
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.process = subprocess.Popen(
                list(command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
        except (OSError, ValueError) as exc:
            raise _TransportUnavailable(f"Codex App Server could not start: {exc}") from exc
        self.deadline = deadline
        self.stdout: queue.Queue[str | None] = queue.Queue()
        self.stderr: queue.Queue[str | None] = queue.Queue()
        self.deferred: list[Mapping[str, Any]] = []
        self._close_lock = threading.Lock()
        self._closed = False
        threading.Thread(
            target=self._read_lines,
            args=(self.process.stdout, self.stdout),
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_lines,
            args=(self.process.stderr, self.stderr),
            daemon=True,
        ).start()

    @staticmethod
    def _read_lines(stream: Any, output: queue.Queue[str | None]) -> None:
        try:
            for line in stream:
                output.put(line)
        finally:
            output.put(None)

    def send(self, message: Mapping[str, Any]) -> None:
        if self.process.stdin is None or self.process.poll() is not None:
            raise _TransportUnavailable("Codex App Server exited before the request completed")
        try:
            self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise _TransportUnavailable("Codex App Server exited while receiving a request") from exc

    def receive_raw(self) -> Mapping[str, Any]:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise _TransportUnavailable("Codex App Server timeout")
        try:
            line = self.stdout.get(timeout=remaining)
        except queue.Empty as exc:
            raise _TransportUnavailable("Codex App Server timeout") from exc
        if line is None:
            detail = self._stderr_tail()
            suffix = f": {detail}" if detail else ""
            raise _TransportUnavailable(f"Codex App Server exited unexpectedly{suffix}")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _TransportUnavailable("Codex App Server returned malformed protocol data") from exc
        if not isinstance(value, dict):
            raise _TransportUnavailable("Codex App Server returned malformed protocol data")
        return value

    def receive(self) -> Mapping[str, Any]:
        if self.deferred:
            return self.deferred.pop(0)
        return self.receive_raw()

    def _stderr_tail(self) -> str:
        lines: list[str] = []
        while True:
            try:
                line = self.stderr.get_nowait()
            except queue.Empty:
                break
            if line:
                lines.append(line.strip())
        return " ".join(lines[-3:])[:500]

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            if self.process.stdin is not None:
                try:
                    self.process.stdin.close()
                except (OSError, ValueError):
                    pass
            if self.process.poll() is None:
                try:
                    self.process.terminate()
                except OSError:
                    return
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    try:
                        self.process.kill()
                    except OSError:
                        return
                    try:
                        self.process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass


@dataclass
class _AppServerLane:
    runtime: _JsonlProcess
    plan_type: str | None
    busy: bool = False
    lease_count: int = 0
    next_request_id: int = 3

    def request_id(self) -> int:
        request_id = self.next_request_id
        self.next_request_id += 1
        return request_id

    def healthy(self) -> bool:
        return self.runtime.process.poll() is None


@dataclass
class _PoolEntry:
    lanes: list[_AppServerLane]
    creating: int = 0


_POOL_CONDITION = threading.Condition(threading.RLock())
_APP_SERVER_POOL: dict[tuple[str, tuple[str, ...]], _PoolEntry] = {}
_POOL_COUNTERS = {
    "process_starts": 0,
    "reused_leases": 0,
    "evictions": 0,
    "recoveries": 0,
}


def _pool_size() -> int:
    raw = os.environ.get("EDITABLE_PPT_CODEX_APP_SERVER_POOL_SIZE", "2").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("EDITABLE_PPT_CODEX_APP_SERVER_POOL_SIZE must be an integer") from exc
    if value < 1 or value > 8:
        raise ValueError("EDITABLE_PPT_CODEX_APP_SERVER_POOL_SIZE must be between 1 and 8")
    return value


def _pool_key(project: Path, command: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    return str(project.resolve()), tuple(str(part) for part in command)


def _initialize_lane(command: Sequence[str], deadline: float) -> _AppServerLane:
    runtime = _JsonlProcess(command, deadline)
    with _POOL_CONDITION:
        _POOL_COUNTERS["process_starts"] += 1
    try:
        runtime.send(
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "awesome-editable-ppt-workflow", "version": "1.1.0"}},
            }
        )
        _response(runtime, 1)
        runtime.send({"method": "initialized", "params": {}})
        runtime.send({"id": 2, "method": "account/read", "params": {"refreshToken": True}})
        account_result = _response(runtime, 2)
        account = account_result.get("account")
        auth_mode = account.get("type") if isinstance(account, dict) else None
        if auth_mode != "chatgpt":
            raise CodexRuntimeUnavailable(
                "This workflow requires ChatGPT-managed authentication in Codex; API-key authentication is not used"
            )
        plan_type = account.get("planType") if isinstance(account.get("planType"), str) else None
        return _AppServerLane(runtime=runtime, plan_type=plan_type)
    except BaseException:
        runtime.close()
        raise


def _acquire_lane(
    key: tuple[str, tuple[str, ...]], command: Sequence[str], deadline: float
) -> tuple[_AppServerLane, bool]:
    max_lanes = _pool_size()
    while True:
        stale: list[_AppServerLane] = []
        selected: tuple[_AppServerLane, bool] | None = None
        should_create = False
        with _POOL_CONDITION:
            entry = _APP_SERVER_POOL.setdefault(key, _PoolEntry(lanes=[]))
            for lane in list(entry.lanes):
                if not lane.busy and not lane.healthy():
                    entry.lanes.remove(lane)
                    stale.append(lane)
                    _POOL_COUNTERS["evictions"] += 1
            for lane in entry.lanes:
                if not lane.busy and lane.healthy():
                    reused = lane.lease_count > 0
                    lane.busy = True
                    lane.lease_count += 1
                    if reused:
                        _POOL_COUNTERS["reused_leases"] += 1
                    selected = (lane, reused)
                    break
            if selected is not None:
                pass
            elif len(entry.lanes) + entry.creating < max_lanes:
                entry.creating += 1
                should_create = True
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CodexRuntimeUnavailable("Codex App Server pool lease timeout")
                _POOL_CONDITION.wait(timeout=remaining)
        for lane in stale:
            lane.runtime.close()
        if selected is not None:
            return selected
        if not should_create:
            continue
        try:
            lane = _initialize_lane(command, deadline)
        except BaseException:
            with _POOL_CONDITION:
                entry = _APP_SERVER_POOL.get(key)
                if entry is not None:
                    entry.creating -= 1
                    if not entry.lanes and entry.creating == 0:
                        _APP_SERVER_POOL.pop(key, None)
                _POOL_CONDITION.notify_all()
            raise
        with _POOL_CONDITION:
            entry = _APP_SERVER_POOL.setdefault(key, _PoolEntry(lanes=[]))
            entry.creating -= 1
            lane.busy = True
            lane.lease_count = 1
            entry.lanes.append(lane)
            _POOL_CONDITION.notify_all()
        return lane, False


def _release_lane(
    key: tuple[str, tuple[str, ...]], lane: _AppServerLane, *, healthy: bool
) -> None:
    close_lane = False
    with _POOL_CONDITION:
        entry = _APP_SERVER_POOL.get(key)
        if entry is not None and lane in entry.lanes:
            if healthy and lane.healthy():
                lane.busy = False
            else:
                entry.lanes.remove(lane)
                _POOL_COUNTERS["evictions"] += 1
                close_lane = True
            if not entry.lanes and entry.creating == 0:
                _APP_SERVER_POOL.pop(key, None)
        else:
            close_lane = True
        _POOL_CONDITION.notify_all()
    if close_lane:
        lane.runtime.close()


def shutdown_app_server_pool(
    *, project: Path | None = None, command: Sequence[str] | None = None
) -> None:
    """Close reusable App Server lanes globally or for one exact pool key."""
    if (project is None) != (command is None):
        raise ValueError("project and command must be provided together")
    with _POOL_CONDITION:
        if project is None:
            entries = list(_APP_SERVER_POOL.values())
            _APP_SERVER_POOL.clear()
            for name in _POOL_COUNTERS:
                _POOL_COUNTERS[name] = 0
        else:
            entry = _APP_SERVER_POOL.pop(_pool_key(Path(project), command or ()), None)
            entries = [entry] if entry is not None else []
        lanes = [lane for entry in entries for lane in entry.lanes]
        _POOL_CONDITION.notify_all()
    for lane in lanes:
        lane.runtime.close()


def app_server_pool_stats() -> Mapping[str, int]:
    """Return non-sensitive process-pool counters for diagnostics and tests."""
    with _POOL_CONDITION:
        live_lanes = sum(len(entry.lanes) for entry in _APP_SERVER_POOL.values())
        busy_lanes = sum(
            1 for entry in _APP_SERVER_POOL.values() for lane in entry.lanes if lane.busy
        )
        return {
            **_POOL_COUNTERS,
            "pool_keys": len(_APP_SERVER_POOL),
            "live_lanes": live_lanes,
            "busy_lanes": busy_lanes,
            "max_lanes_per_key": _pool_size(),
        }


atexit.register(shutdown_app_server_pool)


def _default_command() -> list[str]:
    executable = os.environ.get("EDITABLE_PPT_CODEX_EXECUTABLE") or shutil.which("codex")
    if not executable:
        raise CodexRuntimeUnavailable(
            "Codex desktop runtime was not found; install or open Codex and sign in with ChatGPT"
        )
    return [executable, "app-server", "--stdio"]


def app_server_web_search_modes(
    command: Sequence[str], *, timeout: float
) -> frozenset[str]:
    """Read the installed App Server v2 schema's supported web-search modes."""
    if timeout <= 0:
        raise CodexRuntimeUnavailable("Codex App Server capability probe timeout")
    if not command:
        raise CodexRuntimeUnavailable("Codex App Server capability command is empty")
    try:
        with tempfile.TemporaryDirectory(prefix="editable-ppt-codex-schema-") as output:
            completed = subprocess.run(
                [
                    str(command[0]),
                    "app-server",
                    "generate-json-schema",
                    "--experimental",
                    "--out",
                    output,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()[:500]
                raise CodexRuntimeUnavailable(
                    f"Codex App Server capability generation failed: {detail or completed.returncode}"
                )
            schema_path = Path(output) / "codex_app_server_protocol.v2.schemas.json"
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired as exc:
        raise CodexRuntimeUnavailable("Codex App Server capability probe timeout") from exc
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CodexRuntimeUnavailable(
            f"Codex App Server capability schema is unavailable: {exc}"
        ) from exc
    definitions = schema.get("definitions") if isinstance(schema, dict) else None
    web_search = definitions.get("WebSearchMode") if isinstance(definitions, dict) else None
    modes = web_search.get("enum") if isinstance(web_search, dict) else None
    if not isinstance(modes, list) or any(not isinstance(item, str) for item in modes):
        raise CodexRuntimeUnavailable(
            "Codex App Server v2 schema does not declare config.web_search"
        )
    return frozenset(modes)


def _response(runtime: _JsonlProcess, request_id: int) -> Mapping[str, Any]:
    while True:
        message = runtime.receive_raw()
        if message.get("id") != request_id:
            runtime.deferred.append(message)
            continue
        if message.get("error") is not None:
            raise CodexRuntimeUnavailable(f"Codex App Server request failed: {message['error']}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise _TransportUnavailable("Codex App Server returned an invalid response")
        return result


def _model_override(role: str) -> str | None:
    normalized_role = re.sub(r"[^A-Z0-9]+", "_", role.upper()).strip("_")
    name = f"EDITABLE_PPT_{normalized_role}_CODEX_MODEL"
    return os.environ.get(name) or os.environ.get("EDITABLE_PPT_CODEX_MODEL") or None


_SUPPORTED_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max", "ultra"})


def _effort_override(role: str) -> str | None:
    normalized_role = re.sub(r"[^A-Z0-9]+", "_", role.upper()).strip("_")
    name = f"EDITABLE_PPT_{normalized_role}_CODEX_EFFORT"
    value = os.environ.get(name) or os.environ.get("EDITABLE_PPT_CODEX_EFFORT")
    if value is None or not value.strip():
        return None
    effort = value.strip().lower()
    if effort not in _SUPPORTED_EFFORTS:
        allowed = ", ".join(sorted(_SUPPORTED_EFFORTS))
        raise ValueError(f"Codex effort must be one of: {allowed}")
    return effort


def _image_input(project: Path, image: Path) -> Mapping[str, Any]:
    resolved_project = project.resolve()
    resolved_image = image.resolve(strict=True)
    try:
        resolved_image.relative_to(resolved_project)
    except ValueError as exc:
        raise CodexRuntimeUnavailable("Codex local image must be contained in the project") from exc
    return {"type": "localImage", "path": str(resolved_image), "detail": "original"}


def invoke_structured(
    project: Path,
    *,
    role: str,
    prompt: str,
    images: Sequence[Path],
    output_schema: Mapping[str, Any],
    timeout: float,
    command: Sequence[str] | None = None,
    web_search: Literal["disabled", "live"] = "disabled",
    capability_probe: Callable[[Sequence[str], float], frozenset[str]] | None = None,
) -> CodexStructuredResult:
    """Run one schema-constrained turn through the user's Codex subscription."""
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if web_search not in {"disabled", "live"}:
        raise ValueError("web_search must be disabled or live")
    if web_search == "live" and role != "visual-material-search":
        raise ValueError("live web search is reserved for visual material search")
    started = time.monotonic()
    deadline = started + timeout
    project = Path(project).resolve(strict=True)
    Draft202012Validator.check_schema(output_schema)
    effort_override = _effort_override(role)
    runtime_command = list(command or _default_command())
    if web_search == "live":
        remaining = deadline - time.monotonic()
        probe = capability_probe or (
            lambda cmd, seconds: app_server_web_search_modes(cmd, timeout=seconds)
        )
        modes = probe(runtime_command, remaining)
        required_modes = {"disabled", "cached", "indexed", "live"}
        if modes != required_modes:
            raise CodexRuntimeUnavailable(
                "installed Codex App Server v2 schema lacks required web_search modes"
            )
    key = _pool_key(project, runtime_command)
    for recovery_attempt in range(2):
        lane, startup_reused = _acquire_lane(key, runtime_command, deadline)
        runtime = lane.runtime
        runtime.deadline = deadline
        runtime.deferred.clear()
        try:
            thread_request_id = lane.request_id()
            turn_request_id = lane.request_id()
            plan_type = lane.plan_type
            thread_params: dict[str, Any] = {
                "ephemeral": True,
                "cwd": str(project),
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "config": {"web_search": web_search},
            }
            model_override = _model_override(role)
            if model_override:
                thread_params["model"] = model_override
            runtime.send(
                {"id": thread_request_id, "method": "thread/start", "params": thread_params}
            )
            thread_result = _response(runtime, thread_request_id)
            thread = thread_result.get("thread")
            thread_id = thread.get("id") if isinstance(thread, dict) else None
            if not isinstance(thread_id, str) or not thread_id:
                raise _TransportUnavailable("Codex App Server did not create a thread")

            turn_input: list[Mapping[str, Any]] = [{"type": "text", "text": prompt}]
            turn_input.extend(_image_input(project, Path(path)) for path in images)
            turn_params: dict[str, Any] = {
                "threadId": thread_id,
                "input": turn_input,
                "outputSchema": output_schema,
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
            }
            if effort_override:
                turn_params["effort"] = effort_override
            runtime.send(
                {
                    "id": turn_request_id,
                    "method": "turn/start",
                    "params": turn_params,
                }
            )
            turn_result = _response(runtime, turn_request_id)
            turn = turn_result.get("turn")
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            if not isinstance(turn_id, str) or not turn_id:
                raise _TransportUnavailable("Codex App Server did not create a turn")

            final_text: str | None = None
            usage: Mapping[str, Any] = {}
            while True:
                event = runtime.receive()
                method = event.get("method")
                params = event.get("params")
                if method == "item/completed" and isinstance(params, dict):
                    item = params.get("item")
                    if isinstance(item, dict) and item.get("type") == "agentMessage":
                        text = item.get("text")
                        if isinstance(text, str):
                            final_text = text
                elif method == "turn/completed" and isinstance(params, dict):
                    completed = params.get("turn")
                    if isinstance(completed, dict) and completed.get("id") == turn_id:
                        error = completed.get("error")
                        if error:
                            raise CodexRuntimeUnavailable(f"Codex model turn failed: {error}")
                        candidate_usage = completed.get("usage")
                        if isinstance(candidate_usage, dict):
                            usage = candidate_usage
                        break

            if final_text is None:
                raise CodexRuntimeUnavailable("Codex did not return structured output")
            try:
                value = json.loads(final_text)
            except json.JSONDecodeError as exc:
                raise CodexRuntimeUnavailable("Codex returned invalid structured output") from exc
            if not isinstance(value, dict):
                raise CodexRuntimeUnavailable("Codex returned invalid structured output")
            errors = sorted(
                Draft202012Validator(output_schema).iter_errors(value),
                key=lambda err: list(err.path),
            )
            if errors:
                raise CodexRuntimeUnavailable(
                    f"Codex structured output failed validation: {errors[0].message}"
                )

            model = thread_result.get("model")
            provider = thread_result.get("modelProvider")
            resolved_effort = turn_result.get("effort")
            if not isinstance(resolved_effort, str):
                resolved_effort = effort_override
            duration_seconds = time.monotonic() - started
            safe_trace = {
                "runtime": "codex-app-server",
                "role": role,
                "thread_id": thread_id,
                "turn_id": turn_id,
                "model": model if isinstance(model, str) else "",
                "model_provider": provider if isinstance(provider, str) else "",
                "auth_mode": "chatgpt",
                "plan_type": plan_type,
                "usage": dict(usage),
                "image_count": len(images),
                "web_search": web_search,
                "effort": resolved_effort,
                "duration_seconds": duration_seconds,
                "startup_reused": startup_reused,
                "recovery_attempt": recovery_attempt,
            }
            result = CodexStructuredResult(
                value=value,
                thread_id=thread_id,
                turn_id=turn_id,
                model=safe_trace["model"],
                model_provider=safe_trace["model_provider"],
                auth_mode="chatgpt",
                plan_type=plan_type,
                usage=usage,
                safe_trace=safe_trace,
                effort=resolved_effort,
                duration_seconds=duration_seconds,
                startup_reused=startup_reused,
            )
        except _TransportUnavailable:
            _release_lane(key, lane, healthy=False)
            if recovery_attempt == 0 and time.monotonic() < deadline:
                with _POOL_CONDITION:
                    _POOL_COUNTERS["recoveries"] += 1
                continue
            raise
        except Exception:
            _release_lane(key, lane, healthy=True)
            raise
        except BaseException:
            _release_lane(key, lane, healthy=False)
            raise
        else:
            _release_lane(key, lane, healthy=True)
            return result
    raise CodexRuntimeUnavailable("Codex App Server recovery exhausted")

"""Bounded, non-secret evidence for the isolated page-1 experiment."""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import re
import stat
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ContextManager, Literal, cast

from jsonschema import Draft202012Validator

from workflow_v6_secure_io import (
    _held_parent,
    _open_relative,
    atomic_write_bytes,
    atomic_write_json,
    read_bytes,
)
from workflow_v6_state import load


StageKind = Literal["local", "codex_wait", "image2_wait", "office_wait", "reconstruction"]
CallKind = Literal[
    "page_director",
    "correction_decision",
    "image2",
    "visual_review",
    "reconstruct_edit",
]

STAGE_NAMES = frozenset(
    {
        "material_preparation",
        "attachment_cache_lookup",
        "page_director",
        "image2_queue",
        "image2_execution",
        "visual_review",
        "correction_decision",
        "reconstruction",
        "fixed_layer_assembly",
        "lock_wait",
        "oauth_wait",
        "office_wait",
        "network_wait",
        "recovery",
    }
)
STAGE_KINDS = frozenset({"local", "codex_wait", "image2_wait", "office_wait", "reconstruction"})
CALL_KINDS: tuple[CallKind, ...] = (
    "page_director",
    "correction_decision",
    "image2",
    "visual_review",
    "reconstruct_edit",
)
CALL_BUDGETS: Mapping[CallKind, int] = {
    "page_director": 1,
    "correction_decision": 2,
    "image2": 3,
    "visual_review": 3,
    "reconstruct_edit": 3,
}
RECOVERY_CALLS: tuple[CallKind, ...] = (
    "page_director",
    "correction_decision",
    "image2",
    "visual_review",
    "reconstruct_edit",
)
TECHNICAL_PREFLIGHT_PROBLEMS = frozenset(
    {
        "Candidate is not an existing readable regular file in the isolated project.",
        "Candidate failed PNG decoding or corruption verification.",
        "Candidate is not native PNG format.",
        "Candidate dimensions must be exactly 1904x896 pixels.",
    }
)
RESOURCE_KEYS = (
    "rss_bytes",
    "handle_count",
    "active_external_calls",
    "temp_file_count",
)
SCHEMA = Path(__file__).resolve().parents[2] / "schemas" / "complex_page_evidence_v1.schema.json"
MAX_EVENTS = 64
MAX_METADATA_BYTES = 4096
MAX_METADATA_DEPTH = 8
MAX_METADATA_ITEMS = 64
MAX_METADATA_STRING = 512

_SECRET_KEY = re.compile(
    r"(?:access|refresh|oauth|bearer|authorization|auth)(?:[_-]?token)?|"
    r"(?:token|secret|password|credential|api[_-]?key|signing[_-]?key)|"
    r"(?:capability.*hmac|hmac.*capability|hmac[_-]?sha256)|"
    r"(?:inline[_-]?image|image[_-]?(?:bytes|b64|base64|data))|"
    r"(?:document|docx|attachment|payload|content|file|raw)[_-]?(?:bytes|b64|base64|data|payload)|"
    r"(?:bytes|data)[_-]?(?:b64|base64)|"
    r"(?:prompt|comment|document[_-]?text|source[_-]?text)|"
    r"(?:request|response)[_-]?payload",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?:bearer\s+[A-Za-z0-9._~+/=-]{12,}|data:image/[^;]+;base64,)",
    re.IGNORECASE,
)
_SAFE_EXPERIMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
)


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _load_schema() -> dict[str, Any]:
    value = json.loads(SCHEMA.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("evidence schema must be a JSON object")
    Draft202012Validator.check_schema(value)
    return value


def _validate_summary(value: Mapping[str, object]) -> None:
    errors = sorted(
        Draft202012Validator(_load_schema()).iter_errors(dict(value)),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        path = "/".join(str(item) for item in errors[0].absolute_path) or "<root>"
        raise ValueError(f"evidence schema rejected {path}: {errors[0].message}")


def _validate_metadata(value: object, *, depth: int = 0) -> None:
    if depth > MAX_METADATA_DEPTH:
        raise ValueError("metadata exceeds nesting limit")
    if value is None or isinstance(value, bool):
        return
    if type(value) is int:
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("metadata contains a non-finite number")
        return
    if isinstance(value, str):
        if len(value) > MAX_METADATA_STRING or _SECRET_VALUE.search(value):
            raise ValueError("metadata contains a secret-like or oversized value")
        return
    if isinstance(value, Mapping):
        if len(value) > MAX_METADATA_ITEMS:
            raise ValueError("metadata contains too many fields")
        for key, item in value.items():
            if not isinstance(key, str) or not key or _SECRET_KEY.search(key):
                raise ValueError("metadata contains a secret-like field")
            _validate_metadata(item, depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_METADATA_ITEMS:
            raise ValueError("metadata contains too many items")
        for item in value:
            _validate_metadata(item, depth=depth + 1)
        return
    raise ValueError("metadata contains a non-JSON value")


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be an object")
    _validate_metadata(metadata)
    safe = json.loads(_canonical_bytes(dict(metadata)))
    if not isinstance(safe, dict):
        raise ValueError("metadata must be an object")
    if len(_canonical_bytes(safe)) > MAX_METADATA_BYTES:
        raise ValueError("metadata exceeds its byte limit")
    return safe


def _nonempty(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def _safe_experiment_id(value: str) -> str:
    result = _nonempty(value, "experiment_id")
    if (
        result != value
        or not _SAFE_EXPERIMENT_ID.fullmatch(result)
        or result.endswith((".", " "))
        or result in {".", ".."}
        or result.split(".", 1)[0].upper() in _WINDOWS_RESERVED
    ):
        raise ValueError("experiment_id must be one safe, non-reserved path component")
    return result


def _duration(value: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("duration_seconds must be a non-negative finite number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("duration_seconds must be a non-negative finite number")
    return result


def _validate_page(page_number: int) -> None:
    if type(page_number) is not int or page_number < 1:
        raise ValueError("complex-page evidence page_number must be a positive integer")


def _validate_stage(name: str, kind: str, page_number: int) -> None:
    if name not in STAGE_NAMES:
        raise ValueError("stage name is not part of the approved experiment")
    if kind not in STAGE_KINDS:
        raise ValueError("stage kind is invalid")
    _validate_page(page_number)


def _current_process_resources() -> tuple[int, int]:
    if os.name == "nt":
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        kernel32.GetProcessHandleCount.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetProcessHandleCount.restype = wintypes.BOOL
        process = kernel32.GetCurrentProcess()
        memory = ProcessMemoryCounters()
        memory.cb = ctypes.sizeof(memory)
        if not psapi.GetProcessMemoryInfo(process, ctypes.byref(memory), memory.cb):
            raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
        handles = wintypes.DWORD()
        if not kernel32.GetProcessHandleCount(process, ctypes.byref(handles)):
            raise OSError(ctypes.get_last_error(), "GetProcessHandleCount failed")
        return int(memory.WorkingSetSize), int(handles.value)

    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss = int(usage) * (1 if os.uname().sysname == "Darwin" else 1024)
    except (ImportError, AttributeError):
        rss = 1
    handles = len(list((Path("/proc") / str(os.getpid()) / "fd").iterdir())) if Path("/proc").is_dir() else 1
    return max(rss, 1), max(handles, 1)


def _open_append_relative(parent: int, name: str) -> int:
    """Open an existing regular evidence file for append under a held parent."""
    if os.name != "nt":
        return os.open(
            name,
            os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )

    import msvcrt
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(UnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", ctypes.c_void_p),
            ("SecurityQualityOfService", ctypes.c_void_p),
        ]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    buffer = ctypes.create_unicode_buffer(name)
    native_name = UnicodeString(
        len(name.encode("utf-16-le")),
        (len(name) + 1) * 2,
        ctypes.cast(buffer, wintypes.LPWSTR),
    )
    attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes),
        msvcrt.get_osfhandle(parent),
        ctypes.pointer(native_name),
        0x40 | 0x1000,
        None,
        None,
    )
    status_block = IoStatusBlock()
    native = wintypes.HANDLE()
    nt_create = ctypes.WinDLL("ntdll").NtCreateFile
    nt_create.argtypes = (
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(ObjectAttributes),
        ctypes.POINTER(IoStatusBlock),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
    )
    nt_create.restype = ctypes.c_long
    status = nt_create(
        ctypes.byref(native),
        0x00000004 | 0x00100000,
        ctypes.byref(attributes),
        ctypes.byref(status_block),
        None,
        0x80,
        0x00000001 | 0x00000002,
        1,
        0x00000040 | 0x00000020 | 0x00200000,
        None,
        0,
    )
    if status < 0:
        raise OSError(status, "secure relative evidence append failed", name)
    try:
        return msvcrt.open_osfhandle(
            native.value,
            os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0),
        )
    except OSError:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(native.value)
        raise


def sample_resources(project_copy: Path) -> Mapping[str, int]:
    """Return process usage and bounded experiment-tree temporary-file count."""
    root = Path(project_copy).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("project_copy must be an existing directory")
    rss, handles = _current_process_resources()
    temp_files = sum(
        1
        for path in root.rglob("*")
        if path.is_file()
        and (
            path.name.endswith(".tmp")
            or path.name.startswith(".") and ".tmp" in path.name
        )
    )
    return {
        "rss_bytes": rss,
        "handle_count": handles,
        "active_external_calls": 0,
        "temp_file_count": temp_files,
    }


class EvidenceRecorder:
    def __init__(
        self,
        experiment_root: Path,
        *,
        project_copy: Path,
        experiment_id: str,
        page_number: int | None = None,
        source_identity: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        resource_sampler: Callable[[Path], Mapping[str, int]] = sample_resources,
    ) -> None:
        self.project_copy = Path(project_copy).resolve(strict=True)
        self.experiment_id = _safe_experiment_id(experiment_id)
        self.experiment_root = Path(experiment_root).resolve(strict=True)
        expected_root = (
            self.project_copy / "04_v6" / "experiments" / self.experiment_id
        ).resolve(strict=False)
        if self.experiment_root != expected_root:
            if self.experiment_root.name != self.experiment_id:
                raise ValueError("experiment_root does not match experiment_id")
            raise ValueError("experiment_root must be project_copy/04_v6/experiments/<experiment_id>")
        if (page_number is None) != (source_identity is None):
            raise ValueError("live evidence requires page_number and source_identity together")
        self._explicit_live_identity = page_number is not None
        if page_number is not None:
            if type(page_number) is not int or page_number < 1:
                raise ValueError("live evidence page_number must be a positive integer")
            if not isinstance(source_identity, str) or not re.fullmatch(
                r"[0-9a-f]{64}", source_identity
            ):
                raise ValueError("live evidence source_identity must be a SHA-256 digest")
            current = load(self.project_copy)
            if page_number > len(current["pages"]):
                raise ValueError("live evidence page_number is out of range")
            if self.experiment_id != f"live-page-{page_number:03d}":
                raise ValueError("live evidence experiment_id does not match page_number")
            if source_identity != current["source_identity"]:
                raise ValueError("live evidence source_identity does not match current workflow")
            self.page_number = page_number
            self.source_snapshot_sha256 = source_identity
        else:
            try:
                snapshot = json.loads((self.project_copy.parent / "source_snapshot.json").read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("workspace source snapshot identity is missing or invalid") from exc
            if (
                not isinstance(snapshot, dict)
                or snapshot.get("experiment_id") != self.experiment_id
                or type(snapshot.get("page_number")) is not int
                or cast(int, snapshot.get("page_number")) < 1
                or cast(int, snapshot.get("page_number")) > 4
                or not isinstance(snapshot.get("source_snapshot_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", cast(str, snapshot.get("source_snapshot_sha256")))
            ):
                raise ValueError("workspace source snapshot identity does not match experiment_id")
            self.page_number = cast(int, snapshot["page_number"])
            self.source_snapshot_sha256 = cast(str, snapshot["source_snapshot_sha256"])
        self.workspace_identity_sha256 = hashlib.sha256(
            _canonical_bytes(
                {
                    "experiment_id": self.experiment_id,
                    "source_snapshot_sha256": self.source_snapshot_sha256,
                }
            )
        ).hexdigest()
        self._clock = clock
        self._resource_sampler = resource_sampler
        self._events: list[dict[str, object]] = []
        self._stages: list[dict[str, object]] = []
        self._calls: list[dict[str, object]] = []
        self._candidate_preflights: list[dict[str, object]] = []
        self._attachment_cache = {"hits": 0, "misses": 0}
        self._recovery_events = 0
        self._recovery_skipped: list[CallKind] = []
        self._resource_peaks = {key: 0 for key in RESOURCE_KEYS}
        self._active_external_calls = 0
        self._session_calls: list[dict[str, object]] = []
        self._session_recovery = False
        self._loaded_session_recovery = False
        self._finalized = False
        self._summary_exists = (self.experiment_root / "summary.json").is_file()
        raw = self._read_existing_jsonl()
        checkpoint_count = self._verify_existing_summary(raw)
        self._load_existing_events(raw, checkpoint_count=checkpoint_count)
        if self._summary_exists:
            expected = self._build_summary(self._events[:checkpoint_count])
            if self._stored_summary != expected:
                raise ValueError("existing evidence summary aggregate does not match its checkpoint events")

    def _read_existing_jsonl(self) -> bytes:
        path = self.experiment_root / "evidence.jsonl"
        if not path.exists():
            return b""
        return read_bytes(self.experiment_root, "evidence.jsonl", max_bytes=64 * 1024)

    def refresh_from_disk(self) -> None:
        """Rehydrate this session after waiting for page ownership."""
        fresh = type(self)(
            self.experiment_root, project_copy=self.project_copy,
            experiment_id=self.experiment_id, clock=self._clock,
            resource_sampler=self._resource_sampler,
            page_number=self.page_number if self._explicit_live_identity else None,
            source_identity=(
                self.source_snapshot_sha256 if self._explicit_live_identity else None
            ),
        )
        fresh._session_calls = []
        fresh._session_recovery = fresh._loaded_session_recovery
        self.__dict__.update(fresh.__dict__)

    def _verify_existing_summary(self, raw: bytes) -> int:
        if not self._summary_exists:
            self._stored_summary = None
            return 0
        try:
            summary = json.loads(
                read_bytes(
                    self.experiment_root,
                    "summary.json",
                    max_bytes=128 * 1024,
                )
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("existing evidence summary is invalid") from exc
        if not isinstance(summary, dict):
            raise ValueError("existing evidence summary is invalid")
        _validate_summary(summary)
        self._stored_summary = summary
        count = summary["event_count"]
        assert isinstance(count, int)
        lines = raw.splitlines(keepends=True)
        if count > len(lines):
            raise ValueError("existing evidence summary checkpoint exceeds JSONL")
        prefix = b"".join(lines[:count])
        expected = hashlib.sha256(prefix).hexdigest()
        if summary.get("evidence_sha256") != expected:
            raise ValueError("existing evidence summary does not match JSONL checkpoint prefix")
        return count

    def _load_existing_events(self, data: bytes, *, checkpoint_count: int) -> None:
        if not data:
            return
        lines = data.splitlines(keepends=True)
        if not lines or len(lines) > MAX_EVENTS or any(not line.endswith(b"\n") for line in lines):
            raise ValueError("existing evidence JSONL is malformed or exceeds its bound")
        for index, line in enumerate(lines):
            try:
                value = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("existing evidence JSONL is malformed") from exc
            if not isinstance(value, dict) or line != _canonical_bytes(value) + b"\n":
                raise ValueError("existing evidence JSONL is not canonical")
            if (
                value.get("experiment_id") != self.experiment_id
                or value.get("workspace_identity_sha256") != self.workspace_identity_sha256
                or value.get("source_snapshot_sha256") != self.source_snapshot_sha256
            ):
                raise ValueError("existing evidence identity does not match this experiment workspace")
            event = value.get("event")
            if value.get("page_number") != self.page_number:
                raise ValueError("existing evidence contains an out-of-scope page")
            if event == "stage":
                if value.get("name") not in STAGE_NAMES or value.get("kind") not in STAGE_KINDS:
                    raise ValueError("existing evidence contains an invalid stage")
                self._stages.append(value)
                for resource_field in ("resource_start", "resource_end"):
                    resources = value.get(resource_field)
                    if resources is not None:
                        if not isinstance(resources, dict):
                            raise ValueError("existing evidence contains invalid resources")
                        for key in RESOURCE_KEYS:
                            observed = resources.get(key)
                            if type(observed) is not int or observed < 0:
                                raise ValueError("existing evidence contains invalid resources")
                            self._resource_peaks[key] = max(self._resource_peaks[key], observed)
            elif event == "call":
                kind = value.get("kind")
                if kind not in CALL_KINDS or not isinstance(value.get("metadata"), dict):
                    raise ValueError("existing evidence contains an invalid call")
                duration = value.get("duration_seconds")
                reason = value.get("unavailable_reason")
                if duration is None:
                    _nonempty(cast(str, reason), "unavailable_reason")
                elif reason is not None:
                    raise ValueError("existing evidence call duration/reason is inconsistent")
                else:
                    _duration(cast(float, duration))
                _validate_metadata(value["metadata"])
                self._validate_call_sequence(
                    cast(CallKind, kind), value.get("attempt"), existing=True
                )
                self._calls.append(value)
                if index >= checkpoint_count:
                    self._session_calls.append(value)
            elif event == "attachment_cache":
                hits, misses = value.get("hits"), value.get("misses")
                if type(hits) is not int or hits < 0 or misses != 0:
                    raise ValueError("existing evidence contains invalid cache evidence")
                self._attachment_cache["hits"] += hits
            elif event == "candidate_preflight":
                self._validate_preflight_event(value)
                self._candidate_preflights.append(value)
            elif event == "recovery":
                skipped = value.get("skipped_calls")
                if skipped != list(RECOVERY_CALLS):
                    raise ValueError("existing evidence contains invalid recovery evidence")
                if index >= checkpoint_count:
                    if self._session_calls:
                        prior = self._events[-1] if self._events else {}
                        metadata = prior.get("metadata") if isinstance(prior, dict) else None
                        terminal_accept = (
                            prior.get("event") == "call"
                            and prior.get("kind") == "visual_review"
                            and prior.get("status") == "ok"
                            and prior.get("operation") == "independent_semantic_review"
                            and isinstance(metadata, dict)
                            and metadata.get("decision") == "accept"
                            and metadata.get("problem_count") == 0
                        )
                        if self._session_recovery or not terminal_accept:
                            raise ValueError("zero-call recovery cannot follow a resumed-session call")
                        self._session_calls = []
                    self._session_recovery = True
                    self._loaded_session_recovery = True
                self._recovery_events += 1
                for item in skipped:
                    if item not in self._recovery_skipped:
                        self._recovery_skipped.append(item)
            else:
                raise ValueError("existing evidence contains an unknown event")
            self._events.append(value)

    @property
    def resource_peaks(self) -> Mapping[str, int]:
        return dict(self._resource_peaks)

    def _sample(self) -> dict[str, int]:
        sample = dict(self._resource_sampler(self.project_copy))
        if set(sample) != set(RESOURCE_KEYS) or any(
            type(sample[key]) is not int or sample[key] < 0 for key in RESOURCE_KEYS
        ):
            raise ValueError("resource sampler returned an invalid sample")
        sample["active_external_calls"] = max(
            sample["active_external_calls"], self._active_external_calls
        )
        for key in RESOURCE_KEYS:
            self._resource_peaks[key] = max(self._resource_peaks[key], sample[key])
        return {key: sample[key] for key in RESOURCE_KEYS}

    def _append(self, event: Mapping[str, object]) -> None:
        if self._finalized:
            raise RuntimeError("evidence recorder is finalized")
        if len(self._events) >= MAX_EVENTS:
            raise ValueError("evidence event stream exceeds its one-page bound")
        value = {
            "experiment_id": self.experiment_id,
            "workspace_identity_sha256": self.workspace_identity_sha256,
            "source_snapshot_sha256": self.source_snapshot_sha256,
            **dict(event),
        }
        payload = _canonical_bytes(value) + b"\n"
        if not self._events:
            atomic_write_bytes(self.experiment_root, "evidence.jsonl", payload)
            self._events.append(value)
            return
        with _held_parent(self.experiment_root, "evidence.jsonl", create=False) as (
            parent,
            name,
        ):
            expected = b"".join(
                _canonical_bytes(item) + b"\n" for item in self._events
            )
            read_descriptor = _open_relative(parent, name)
            try:
                read_info = os.fstat(read_descriptor)
                if not stat.S_ISREG(read_info.st_mode):
                    raise ValueError("append-only evidence is not a regular file")
                with os.fdopen(read_descriptor, "rb", closefd=False) as handle:
                    if handle.read(64 * 1024 + 1) != expected:
                        raise ValueError("append-only evidence JSONL was modified")
                descriptor = _open_append_relative(parent, name)
                try:
                    written = 0
                    while written < len(payload):
                        count = os.write(descriptor, payload[written:])
                        if count <= 0:
                            raise OSError("append-only evidence write did not progress")
                        written += count
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                os.close(read_descriptor)
        self._events.append(value)

    def _commit_event(self, event: Mapping[str, object], collection: list[dict[str, object]]) -> None:
        value = dict(event)
        self._append(value)
        collection.append(value)

    @contextmanager
    def stage(
        self, name: str, kind: StageKind, *, page_number: int | None = None
    ) -> ContextManager[None]:
        """Append one completed stage and classify local versus external wait."""
        page_number = self.page_number if page_number is None else page_number
        if page_number != self.page_number:
            raise ValueError("stage page does not match the experiment workspace")
        _validate_stage(name, kind, page_number)
        external_stage = kind in {"codex_wait", "image2_wait", "office_wait"}
        if external_stage:
            self._active_external_calls += 1
        start_resource = self._sample()
        start = float(self._clock())
        if not math.isfinite(start):
            raise ValueError("stage clock returned a non-finite value")
        status = "ok"
        try:
            yield
        except BaseException:
            status = "error"
            raise
        finally:
            end = float(self._clock())
            if not math.isfinite(end) or end < start:
                raise ValueError("stage clock must be finite and monotonic")
            end_resource = self._sample()
            if external_stage:
                self._active_external_calls -= 1
            elapsed = end - start
            local = elapsed if kind in {"local", "reconstruction"} else 0.0
            external = elapsed if kind in {"codex_wait", "image2_wait", "office_wait"} else 0.0
            stage = {
                "event": "stage",
                "page_number": page_number,
                "name": name,
                "kind": kind,
                "start_seconds": start,
                "end_seconds": end,
                "duration_seconds": elapsed,
                "local_duration_seconds": local,
                "external_wait_seconds": external,
                "unavailable_reason": None,
                "status": status,
                "resource_start": start_resource,
                "resource_end": end_resource,
            }
            self._commit_event(stage, self._stages)

    def record_unavailable_stage(
        self,
        name: str,
        kind: StageKind,
        *,
        unavailable_reason: str,
        page_number: int | None = None,
    ) -> None:
        """Record an unavailable Provider/Office sub-timing without inventing zero."""
        page_number = self.page_number if page_number is None else page_number
        if page_number != self.page_number:
            raise ValueError("stage page does not match the experiment workspace")
        _validate_stage(name, kind, page_number)
        reason = _nonempty(unavailable_reason, "unavailable_reason")
        stage = {
            "event": "stage",
            "page_number": page_number,
            "name": name,
            "kind": kind,
            "start_seconds": None,
            "end_seconds": None,
            "duration_seconds": None,
            "local_duration_seconds": None,
            "external_wait_seconds": None,
            "unavailable_reason": reason,
            "status": "unavailable",
            "resource_start": None,
            "resource_end": None,
        }
        self._commit_event(stage, self._stages)

    def record_call(
        self,
        *,
        kind: CallKind,
        attempt: int | None,
        model: str,
        effort: str | None,
        operation: str | None,
        duration_seconds: float | None,
        unavailable_reason: str | None = None,
        status: str,
        metadata: Mapping[str, object],
    ) -> None:
        """Append one quota/model call after recursive metadata sanitization."""
        if kind not in CALL_KINDS:
            raise ValueError("call kind is invalid")
        if self._session_recovery:
            raise ValueError("model or Provider call cannot be recorded after recovery")
        if attempt is not None and (type(attempt) is not int or attempt < 1 or attempt > 3):
            raise ValueError("call attempt must be between 1 and 3")
        safe_metadata = _safe_metadata(metadata)
        if duration_seconds is None:
            safe_duration = None
            safe_reason = _nonempty(unavailable_reason or "", "unavailable_reason")
        else:
            safe_duration = _duration(duration_seconds)
            if unavailable_reason is not None:
                raise ValueError("available call duration cannot carry unavailable_reason")
            safe_reason = None
        self._validate_call_sequence(kind, attempt, existing=False)
        call = {
            "event": "call",
            "page_number": self.page_number,
            "kind": kind,
            "attempt": attempt,
            "model": _nonempty(model, "model"),
            "effort": None if effort is None else _nonempty(effort, "effort"),
            "operation": None if operation is None else _nonempty(operation, "operation"),
            "duration_seconds": safe_duration,
            "unavailable_reason": safe_reason,
            "status": _nonempty(status, "status"),
            "metadata": safe_metadata,
        }
        self._commit_event(call, self._calls)
        self._session_calls.append(call)

    def _validate_preflight_event(self, value: Mapping[str, object]) -> None:
        attempt = value.get("attempt")
        if type(attempt) is not int or attempt < 1 or attempt > 3:
            raise ValueError("candidate preflight attempt must be between 1 and 3")
        if any(item["attempt"] == attempt for item in self._candidate_preflights):
            raise ValueError("candidate preflight budget exceeded or attempt repeated")
        image_calls = [
            call for call in self._calls
            if call["kind"] == "image2" and call["attempt"] == attempt
        ]
        if len(image_calls) != 1:
            raise ValueError("candidate preflight requires the same Image2 candidate attempt")
        if any(
            call["attempt"] == attempt
            and call["kind"] in {"visual_review", "correction_decision"}
            for call in self._calls
        ):
            raise ValueError("candidate preflight must precede review and correction")
        candidate_sha = value.get("candidate_sha256")
        request_identity = value.get("request_identity")
        if (
            not isinstance(candidate_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", candidate_sha) is None
            or not isinstance(request_identity, str)
            or re.fullmatch(r"[0-9a-f]{64}", request_identity) is None
        ):
            raise ValueError("candidate preflight identity is invalid")
        recorded_identity = image_calls[0]["metadata"].get("request_identity_sha256")
        if recorded_identity is not None and recorded_identity != request_identity:
            raise ValueError("candidate preflight request identity does not match Image2 evidence")
        passed = value.get("passed")
        problems = value.get("problems")
        if type(passed) is not bool or not isinstance(problems, list) or any(
            not isinstance(problem, str) for problem in problems
        ):
            raise ValueError("candidate preflight outcome is invalid")
        if passed and problems:
            raise ValueError("passed candidate preflight requires empty problems")
        if not passed and (
            not problems
            or len(problems) != len(set(problems))
            or any(problem not in TECHNICAL_PREFLIGHT_PROBLEMS for problem in problems)
        ):
            raise ValueError("failed candidate preflight requires exact technical problems")

    def record_candidate_preflight(
        self,
        *,
        attempt: int,
        candidate_sha256: str,
        request_identity: str,
        passed: bool,
        problems: Sequence[str],
    ) -> None:
        """Durably bind one four-check technical outcome to one Image2 attempt."""
        event = {
            "event": "candidate_preflight",
            "page_number": self.page_number,
            "attempt": attempt,
            "candidate_sha256": candidate_sha256,
            "request_identity": request_identity,
            "passed": passed,
            "problems": list(problems),
        }
        self._validate_preflight_event(event)
        self._commit_event(event, self._candidate_preflights)

    def _validate_call_sequence(
        self, kind: CallKind, attempt: object, *, existing: bool
    ) -> None:
        attempts = [call["attempt"] for call in self._calls if call["kind"] == kind]
        if len(attempts) >= CALL_BUDGETS[kind] or attempt in attempts:
            raise ValueError(f"{kind} call budget exceeded or attempt repeated")
        if kind == "page_director":
            if attempt not in {None, 1}:
                raise ValueError("page_director attempt must be 1 when present")
            return
        if type(attempt) is not int:
            raise ValueError(f"{kind} candidate attempt must be an integer")
        if kind in {"image2", "reconstruct_edit"} and attempt != len(attempts) + 1:
            raise ValueError(f"{kind} candidate attempts must begin at 1 and be contiguous")
        if kind in {"visual_review", "correction_decision"} and attempts and attempt <= attempts[-1]:
            raise ValueError(f"{kind} candidate attempts must be strictly increasing")
        image_attempts = {
            call["attempt"] for call in self._calls if call["kind"] == "image2"
        }
        review_attempts = {
            call["attempt"] for call in self._calls if call["kind"] == "visual_review"
        }
        correction_attempts = {
            call["attempt"] for call in self._calls if call["kind"] == "correction_decision"
        }
        preflights = {
            event["attempt"]: event for event in self._candidate_preflights
        }
        candidate = int(attempt)
        if kind == "image2" and candidate > 1:
            prior = preflights.get(candidate - 1)
            semantic_path = (
                prior is not None
                and prior["passed"] is True
                and candidate - 1 in review_attempts
                and candidate - 1 in correction_attempts
            )
            technical_path = (
                prior is not None
                and prior["passed"] is False
                and candidate - 1 not in review_attempts
                and candidate - 1 not in correction_attempts
            )
            if semantic_path == technical_path:
                raise ValueError(
                    "next Image2 candidate requires exactly one prior preflight causal path"
                )
        if kind == "visual_review":
            preflight = preflights.get(candidate)
            if candidate not in image_attempts or preflight is None or preflight["passed"] is not True:
                raise ValueError("visual_review requires the same passed candidate preflight")
        if kind == "correction_decision":
            preflight = preflights.get(candidate)
            if preflight is None or preflight["passed"] is not True:
                raise ValueError("correction_decision is forbidden after failed or missing preflight")
            if candidate not in review_attempts:
                raise ValueError("correction_decision attempt requires the same visual review attempt")

    def record_attachment_cache(self, *, hits: int, misses: int) -> None:
        """Record immutable-render reuse for view-only page material construction."""
        if type(hits) is not int or hits < 0 or type(misses) is not int or misses < 0:
            raise ValueError("cache hits and misses must be non-negative integers")
        if misses != 0:
            raise ValueError("view-only experiment requires attachment cache misses=0")
        event = {"event": "attachment_cache", "page_number": self.page_number, "hits": hits, "misses": misses}
        self._append(event)
        self._attachment_cache["hits"] += hits

    def has_call(self, *, kind: CallKind, attempt: int) -> bool:
        """Return whether durable append-only evidence already accounts for this attempt."""
        return any(call["kind"] == kind and call["attempt"] == attempt for call in self._calls)

    def durable_call_count(self) -> int:
        """Return durable model/Provider calls already bound to this page."""
        return len(self._calls)

    def acceptance_checkpoint(
        self, *, attempt: int, candidate_sha256: str,
        request_identity: str, review_authority_sha256: str,
    ) -> Mapping[str, object]:
        """Project the exact terminal Image2/preflight/review acceptance causality."""
        if self._finalized:
            raise RuntimeError("evidence recorder is finalized")
        if type(attempt) is not int or attempt < 1 or attempt > 3:
            raise ValueError("acceptance checkpoint attempt is invalid")
        for label, value in (("candidate", candidate_sha256), ("request identity", request_identity),
                             ("review authority", review_authority_sha256)):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"acceptance checkpoint {label} is invalid")
        indexed = list(enumerate(self._events))
        image = [(index, event) for index, event in indexed
                 if event.get("event") == "call" and event.get("kind") == "image2" and event.get("attempt") == attempt]
        preflight = [(index, event) for index, event in indexed
                     if event.get("event") == "candidate_preflight" and event.get("attempt") == attempt]
        review = [(index, event) for index, event in indexed
                  if event.get("event") == "call" and event.get("kind") == "visual_review" and event.get("attempt") == attempt]
        if len(image) != 1 or len(preflight) != 1 or len(review) != 1:
            raise ValueError("acceptance checkpoint requires one exact candidate causal sequence")
        image_index, image_event = image[0]
        preflight_index, preflight_event = preflight[0]
        review_index, review_event = review[0]
        image_metadata = cast(Mapping[str, object], image_event["metadata"])
        review_metadata = cast(Mapping[str, object], review_event["metadata"])
        if not image_index < preflight_index < review_index or review_index != len(self._events) - 1:
            raise ValueError("acceptance review must be the terminal causal event with no later event")
        if (
            image_event.get("status") in {"error", "outcome_unknown"}
            or image_metadata.get("request_identity_sha256") != request_identity
            or preflight_event.get("request_identity") != request_identity
            or preflight_event.get("candidate_sha256") != candidate_sha256
            or preflight_event.get("passed") is not True or preflight_event.get("problems") != []
            or review_event.get("status") != "ok"
            or review_event.get("operation") != "independent_semantic_review"
            or review_metadata.get("decision") != "accept"
            or review_metadata.get("problem_count") != 0
            or review_metadata.get("request_identity_sha256") != request_identity
            or review_metadata.get("review_result_sha256") != review_authority_sha256
        ):
            raise ValueError("acceptance checkpoint candidate or review identity is inconsistent")
        raw = self._read_existing_jsonl()
        projection: dict[str, object] = {
            "schema_version": "awesome-complex-page-candidate-acceptance-checkpoint-v1",
            "experiment_id": self.experiment_id,
            "workspace_identity_sha256": self.workspace_identity_sha256,
            "source_snapshot_sha256": self.source_snapshot_sha256,
            "page_number": self.page_number, "selected_attempt": attempt,
            "candidate_sha256": candidate_sha256, "request_identity": request_identity,
            "review_authority_sha256": review_authority_sha256,
            "event_count": len(self._events), "terminal_event_index": review_index,
            "evidence_prefix_sha256": hashlib.sha256(raw).hexdigest(),
            "causal_events": [
                {"event_index": image_index, "value": dict(image_event)},
                {"event_index": preflight_index, "value": dict(preflight_event)},
                {"event_index": review_index, "value": dict(review_event)},
            ],
        }
        projection["checkpoint_sha256"] = hashlib.sha256(_canonical_bytes(projection)).hexdigest()
        self.validate_acceptance_checkpoint(projection)
        return projection

    def validate_acceptance_checkpoint(self, checkpoint: Mapping[str, object]) -> None:
        """Validate an immutable checkpoint against this recorder's exact prefix."""
        value = dict(checkpoint)
        digest = value.pop("checkpoint_sha256", None)
        if not isinstance(digest, str) or digest != hashlib.sha256(_canonical_bytes(value)).hexdigest():
            raise ValueError("acceptance checkpoint digest is invalid")
        expected_keys = {
            "schema_version", "experiment_id", "workspace_identity_sha256", "source_snapshot_sha256",
            "page_number", "selected_attempt", "candidate_sha256", "request_identity",
            "review_authority_sha256", "event_count", "terminal_event_index",
            "evidence_prefix_sha256", "causal_events",
        }
        if set(value) != expected_keys or value["schema_version"] != "awesome-complex-page-candidate-acceptance-checkpoint-v1":
            raise ValueError("acceptance checkpoint shape is invalid")
        if value["experiment_id"] != self.experiment_id or value["workspace_identity_sha256"] != self.workspace_identity_sha256 or value["source_snapshot_sha256"] != self.source_snapshot_sha256:
            raise ValueError("acceptance checkpoint workspace identity is invalid")
        count = value["event_count"]
        if type(count) is not int or count < 3 or count > len(self._events):
            raise ValueError("acceptance checkpoint event count is invalid")
        raw = self._read_existing_jsonl()
        lines = raw.splitlines(keepends=True)
        if hashlib.sha256(b"".join(lines[:count])).hexdigest() != value["evidence_prefix_sha256"]:
            raise ValueError("acceptance checkpoint evidence prefix changed")
        causal = value["causal_events"]
        if not isinstance(causal, list) or len(causal) != 3:
            raise ValueError("acceptance checkpoint causal events are invalid")
        for item in causal:
            if not isinstance(item, Mapping) or set(item) != {"event_index", "value"}:
                raise ValueError("acceptance checkpoint causal event is invalid")
            index = item["event_index"]
            if type(index) is not int or index < 0 or index >= count or item["value"] != self._events[index]:
                raise ValueError("acceptance checkpoint causal event changed")
        for event in self._events[count:]:
            if event.get("event") != "recovery" or event.get("skipped_calls") != list(RECOVERY_CALLS):
                raise ValueError("acceptance checkpoint has a later workflow event")

    def record_recovery(self, *, skipped_calls: Sequence[CallKind]) -> None:
        """Record the exact calls skipped by accepted-page recovery."""
        values = list(skipped_calls)
        if values != list(RECOVERY_CALLS):
            raise ValueError("recovery requires the complete approved skipped-call set in canonical order")
        if self._session_recovery:
            if self._loaded_session_recovery and not self._session_calls:
                return
            raise ValueError("recovery was already recorded in this session")
        recorded = {cast(CallKind, call["kind"]) for call in self._session_calls}
        if recorded.intersection(values):
            raise ValueError("zero-call recovery cannot skip a call already recorded")
        event = {"event": "recovery", "page_number": self.page_number, "skipped_calls": values}
        self._append(event)
        self._recovery_events += 1
        self._session_recovery = True
        for value in values:
            if value not in self._recovery_skipped:
                self._recovery_skipped.append(value)

    def finalize(self) -> Mapping[str, object]:
        """Validate and atomically publish summary.json beside canonical JSONL."""
        if self._finalized:
            raise RuntimeError("evidence recorder is finalized")
        review_attempts = {
            call["attempt"] for call in self._calls if call["kind"] == "visual_review"
        }
        for preflight in self._candidate_preflights:
            reviewed = preflight["attempt"] in review_attempts
            if preflight["passed"] is True and not reviewed:
                raise ValueError("passed candidate preflight requires exactly one visual review")
            if preflight["passed"] is False and reviewed:
                raise ValueError("failed candidate preflight forbids visual review")
        summary = self._build_summary(self._events)
        _validate_summary(summary)
        text = _canonical_bytes(summary).decode("utf-8") + "\n"
        atomic_write_json(
            self.experiment_root,
            "summary.json",
            text,
            replace=self._summary_exists,
        )
        self._finalized = True
        return summary

    def _build_summary(self, events: Sequence[Mapping[str, object]]) -> dict[str, object]:
        stages = [dict(event) for event in events if event.get("event") == "stage"]
        calls = [dict(event) for event in events if event.get("event") == "call"]
        preflights = [dict(event) for event in events if event.get("event") == "candidate_preflight"]
        call_totals = {kind: sum(call["kind"] == kind for call in calls) for kind in CALL_KINDS}
        local_total = sum(
            cast(float, stage["local_duration_seconds"])
            for stage in stages
            if stage["local_duration_seconds"] is not None
        )
        external_total = sum(
            cast(float, stage["external_wait_seconds"])
            for stage in stages
            if stage["external_wait_seconds"] is not None
        )
        reconstruction_total = sum(
            cast(float, stage["duration_seconds"])
            for stage in stages
            if stage["kind"] == "reconstruction" and stage["duration_seconds"] is not None
        )
        resource_peaks = {key: 0 for key in RESOURCE_KEYS}
        for stage in stages:
            for resource_field in ("resource_start", "resource_end"):
                resources = stage.get(resource_field)
                if isinstance(resources, dict):
                    for key in RESOURCE_KEYS:
                        resource_peaks[key] = max(resource_peaks[key], cast(int, resources[key]))
        cache_hits = sum(
            cast(int, event["hits"]) for event in events if event.get("event") == "attachment_cache"
        )
        recovery_events = [event for event in events if event.get("event") == "recovery"]
        recovery_skipped: list[str] = []
        for event in recovery_events:
            for item in cast(Sequence[str], event["skipped_calls"]):
                if item not in recovery_skipped:
                    recovery_skipped.append(item)
        summary: dict[str, object] = {
            "schema_version": "awesome-complex-page-evidence-v1",
            "experiment_id": self.experiment_id,
            "workspace_identity_sha256": self.workspace_identity_sha256,
            "source_snapshot_sha256": self.source_snapshot_sha256,
            "page_number": self.page_number,
            "event_count": len(events),
            "evidence_sha256": hashlib.sha256(
                b"".join(_canonical_bytes(event) + b"\n" for event in events)
            ).hexdigest(),
            "stages": stages,
            "duration_totals": {
                "local_duration_seconds": local_total,
                "external_wait_seconds": external_total,
                "reconstruction_duration_seconds": reconstruction_total,
            },
            "calls": calls,
            "candidate_preflights": preflights,
            "call_totals": call_totals,
            "image2_total_calls": call_totals["image2"],
            "reconstruct_image2_total_calls": call_totals["reconstruct_edit"],
            "attachment_cache": {"hits": cache_hits, "misses": 0},
            "recovery": {
                "events": len(recovery_events),
                "skipped_calls": recovery_skipped,
            },
            "resource_peaks": resource_peaks,
        }
        return summary


__all__ = [
    "CallKind",
    "EvidenceRecorder",
    "StageKind",
    "sample_resources",
    "TECHNICAL_PREFLIGHT_PROBLEMS",
]

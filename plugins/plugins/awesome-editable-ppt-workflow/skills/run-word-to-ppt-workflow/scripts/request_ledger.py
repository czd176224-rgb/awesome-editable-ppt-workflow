"""Project-local idempotency ledger for external workflow invocations.

The ledger deliberately signs request content, never machine-specific absolute
paths.  It is a narrow foundation shared by Image2, search, QA and
reconstruction; individual stage adapters may add their own receipt checks.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping


_VERSION = "request-ledger-v1"
_ACTIVE_TTL_SECONDS = 900.0


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _assert_relative_paths(value: Any, *, field: str = "") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_relative_paths(item, field=str(key))
    elif isinstance(value, list):
        for item in value:
            _assert_relative_paths(item, field=field)
    elif isinstance(value, str) and (field == "path" or field.endswith("_path")):
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("request identity paths must be project-relative")


def request_identity(stage: str, inputs: Mapping[str, Any]) -> str:
    """Return a cross-machine stable SHA-256 identity for one provider request."""
    if not isinstance(stage, str) or not stage.strip():
        raise ValueError("request stage is required")
    if not isinstance(inputs, Mapping):
        raise ValueError("request identity inputs must be an object")
    _assert_relative_paths(inputs)
    return hashlib.sha256(_canonical_bytes({"version": _VERSION, "stage": stage, "inputs": dict(inputs)})).hexdigest()


def _project_root(project: Path) -> Path:
    root = Path(project).resolve()
    if not root.is_dir():
        raise ValueError("request ledger project directory is missing")
    return root


def _entry_path(project: Path, stage: str, identity: str) -> Path:
    if not isinstance(identity, str) or len(identity) != 64:
        raise ValueError("request identity must be a SHA-256 digest")
    if not isinstance(stage, str) or not stage.isidentifier():
        raise ValueError("request ledger stage is invalid")
    root = _project_root(project)
    path = root / "04_v4" / "request_ledger" / stage / f"{identity}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("request ledger entry is unreadable") from exc
    if not isinstance(value, dict):
        raise ValueError("request ledger entry must be an object")
    return value


def _write(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(_canonical_bytes(value) + b"\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def _entry_lock(path: Path):
    """Serialize claims across processes without widening the project state lock."""
    lock = path.with_suffix(path.suffix + ".lock")
    deadline = time.monotonic() + 5.0
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError("request ledger claim lock timed out")
            time.sleep(0.01)
    try:
        yield
    finally:
        os.close(descriptor)
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def _valid_entry(entry: Mapping[str, Any], *, stage: str, identity: str) -> None:
    if (
        entry.get("artifact_version") != _VERSION
        or entry.get("stage") != stage
        or entry.get("request_identity") != identity
        or entry.get("status") not in {"active", "completed", "failed"}
    ):
        raise ValueError("request ledger entry identity is invalid")


def claim_request(project: Path, stage: str, identity: str, *, owner: str, now: float | None = None) -> dict[str, Any]:
    """Claim a request once, or return its active/completed durable outcome."""
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("request owner is required")
    current = time.time() if now is None else float(now)
    path = _entry_path(project, stage, identity)
    with _entry_lock(path):
        entry = _read(path)
        if entry is not None:
            _valid_entry(entry, stage=stage, identity=identity)
            if entry["status"] == "completed":
                return {"status": "completed", "receipt": dict(entry["receipt"])}
            if entry["status"] == "active" and float(entry["lease_expires_at"]) > current:
                return {"status": "active", "owner": entry["owner"]}
            if entry["status"] == "failed" and entry.get("retryable") is False:
                return {"status": "failed", "reason": entry.get("reason", "request failed")}
        replacement = {
            "artifact_version": _VERSION,
            "stage": stage,
            "request_identity": identity,
            "status": "active",
            "owner": owner,
            "lease_expires_at": current + _ACTIVE_TTL_SECONDS,
        }
        _write(path, replacement)
        return {"status": "claimed", "owner": owner}


def complete_request(project: Path, stage: str, identity: str, *, owner: str, receipt: Mapping[str, Any]) -> None:
    """Atomically make a claimed external request reusable after receipt validation."""
    if not isinstance(receipt, Mapping) or not isinstance(receipt.get("path"), str) or not isinstance(receipt.get("sha256"), str):
        raise ValueError("request ledger receipt requires relative path and sha256")
    _assert_relative_paths(receipt)
    path = _entry_path(project, stage, identity)
    with _entry_lock(path):
        entry = _read(path)
        if entry is None:
            raise ValueError("request ledger claim is missing")
        _valid_entry(entry, stage=stage, identity=identity)
        if entry.get("status") != "active" or entry.get("owner") != owner:
            raise ValueError("request ledger claim is not owned by this worker")
        _write(path, {
            "artifact_version": _VERSION,
            "stage": stage,
            "request_identity": identity,
            "status": "completed",
            "receipt": dict(receipt),
        })

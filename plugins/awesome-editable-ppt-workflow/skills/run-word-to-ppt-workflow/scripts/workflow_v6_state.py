"""Atomic persistence for the V6 project contract."""

from __future__ import annotations

import errno
import json
import os
import uuid
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from workflow_v6_contract import validate_material_receipts, validate_project


STATE_FILE = "workflow_v6.json"
LOCK_DIRECTORY = ".workflow_v6.lock"
REPLACE_ATTEMPTS = 10
REPLACE_RETRY_SECONDS = 0.05


def state_path(project: Path) -> Path:
    return Path(project).resolve() / STATE_FILE


def load(project: Path) -> dict[str, Any]:
    path = state_path(project)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("V6 state root must be an object")
    validate_project(value)
    validate_material_receipts(Path(project), value)
    return value


def save(project: Path, value: Mapping[str, Any]) -> Path:
    validate_project(value)
    path = state_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    try:
        temporary.write_text(payload, encoding="utf-8", newline="\n")
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt + 1 >= REPLACE_ATTEMPTS:
                    raise
                time.sleep(REPLACE_RETRY_SECONDS)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def create(project: Path, value: Mapping[str, Any]) -> Path:
    path = state_path(project)
    if path.exists():
        raise FileExistsError(f"V6 state already exists: {path}")
    return save(project, value)


@contextmanager
def mutation_lock(project: Path, timeout: float = 30.0):
    project_root = Path(project).resolve(strict=True)
    lock = project_root / LOCK_DIRECTORY
    if lock.parent.resolve(strict=True) != project_root:
        raise ValueError("V6 state mutation lock must be project-local")
    if os.path.lexists(lock):
        try:
            lock_stat = lock.lstat()
            reparse = bool(
                getattr(lock_stat, "st_file_attributes", 0)
                & getattr(lock_stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
            valid_lock = (
                not lock.is_symlink()
                and not reparse
                and lock.resolve(strict=True) == lock
                and lock_stat.st_nlink == 1
                and lock.is_file()
            )
        except OSError as exc:
            raise ValueError("V6 state mutation lock must be project-local") from exc
        if not valid_lock:
            raise ValueError("V6 state mutation lock must be project-local")

    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock, flags, 0o600)
    handle = os.fdopen(descriptor, "r+b", closefd=True)
    acquired = False
    deadline = time.monotonic() + timeout
    try:
        if not _lock_identity_matches(lock, handle.fileno()):
            raise ValueError("V6 state mutation lock must be project-local")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as exc:
                if not _is_lock_contention(exc):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out acquiring V6 state mutation lock") from exc
                time.sleep(0.01)
        if not _lock_identity_matches(lock, handle.fileno()):
            raise ValueError("V6 state mutation lock must be project-local")
        yield
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _is_lock_contention(error: OSError) -> bool:
    """Classify only advisory-lock conflicts, never pathname access denials."""
    if os.name == "nt":
        return error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or getattr(
            error, "winerror", None
        ) in {32, 33, 36}
    return error.errno in {errno.EACCES, errno.EAGAIN}


def _lock_identity_matches(path: Path, descriptor: int) -> bool:
    """Prove the open lock handle still names the literal project-local file."""
    try:
        before = path.lstat()
        reparse = bool(
            getattr(before, "st_file_attributes", 0)
            & getattr(before, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
        if path.is_symlink() or reparse or not path.is_file() or before.st_nlink != 1:
            return False
        if path.resolve(strict=True) != path:
            return False
        first = os.fstat(descriptor)
        verify_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
        verify_flags |= getattr(os, "O_NOFOLLOW", 0)
        verifier = os.open(path, verify_flags)
        try:
            second = os.fstat(verifier)
            try:
                same_open = os.path.sameopenfile(descriptor, verifier)
            except (AttributeError, OSError):
                same_open = os.path.samestat(first, second)
        finally:
            os.close(verifier)
        after = path.lstat()
        return (
            same_open
            and os.path.samestat(first, before)
            and os.path.samestat(first, after)
            and after.st_nlink == 1
            and not path.is_symlink()
        )
    except OSError:
        return False


def update_page(project: Path, page_number: int, page: Mapping[str, Any]) -> Path:
    """Atomically merge one page result without overwriting concurrent pages."""
    with mutation_lock(project):
        state = load(project)
        if page_number < 1 or page_number > len(state["pages"]):
            raise ValueError("V6 page number is out of range")
        if page.get("page_number") != page_number:
            raise ValueError("V6 page update identity is invalid")
        state["pages"][page_number - 1] = dict(page)
        return save(project, state)

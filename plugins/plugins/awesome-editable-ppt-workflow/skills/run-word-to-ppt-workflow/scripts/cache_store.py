"""Atomic, verified storage for current project-local page cache entries."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, ContextManager, Mapping


_LAYERS = frozenset({"generations", "pages", "renders"})
_INDEX_NAME = "index.json"
_ENTRY_DIRECTORY_KEY_LENGTH = 24


@dataclass(frozen=True)
class CacheHit:
    layer: str
    key: str
    path: Path
    manifest: Mapping[str, Any]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


class CacheStore:
    """A cache that never resolves outside ``<project>/.workflow_cache``."""

    def __init__(self, project: Path):
        self.project = Path(project).resolve()
        self.root = self.project / ".workflow_cache"
        self._require_project_local_directory(self.root)
        self._initialize()

    def _require_project_local_directory(self, path: Path) -> None:
        """Create a directory only when it is its literal project-local path."""
        path.mkdir(parents=True, exist_ok=True)
        try:
            resolved = path.resolve()
        except OSError as exc:
            raise ValueError("workflow cache must be project-local") from exc
        if path.is_symlink() or resolved != path or not self._inside(resolved, self.project):
            raise ValueError("workflow cache must be project-local")

    def _initialize(self) -> None:
        self._require_project_local_directory(self.root / "temporary")
        for layer in _LAYERS:
            self._require_project_local_directory(self.root / layer)
        index = self.root / _INDEX_NAME
        if index.is_symlink() or (index.exists() and not self._inside(index, self.root)):
            raise ValueError("workflow cache index must be project-local")
        with self._mutation_lock():
            if not index.exists():
                self._write_json_atomic(index, {"schema_version": 1, "entries": {}})

    def _validated_mutation_lock_path(self) -> Path:
        """Return the literal cache-owned lock, rejecting links/reparse points."""
        path = self.root / ".mutation.lock"
        if path.parent.resolve(strict=True) != self.root.resolve(strict=True):
            raise ValueError("workflow cache mutation lock must be project-local")
        if os.path.lexists(path):
            try:
                stat = path.lstat()
                resolved = path.resolve(strict=True)
            except OSError as exc:
                raise ValueError("workflow cache mutation lock must be project-local") from exc
            reparse = bool(getattr(stat, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
            if path.is_symlink() or reparse or resolved != path or stat.st_nlink != 1 or not path.is_file():
                raise ValueError("workflow cache mutation lock must be project-local")
        return path

    @staticmethod
    def _lock_identity_matches(path: Path, descriptor: int) -> bool:
        """Prove the open handle still names the literal non-link lock path."""
        try:
            before = path.lstat()
            if path.is_symlink() or not path.is_file() or before.st_nlink != 1:
                return False
            reparse = bool(
                getattr(before, "st_file_attributes", 0)
                & getattr(before, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
            if reparse or path.resolve(strict=True) != path:
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

    @contextmanager
    def _mutation_lock(self, timeout_seconds: float = 30.0) -> ContextManager[None]:
        """Serialize cache index mutations across threads, instances and processes."""
        path = self._validated_mutation_lock_path()
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        handle = os.fdopen(descriptor, "r+b", closefd=True)
        try:
            if not self._lock_identity_matches(path, handle.fileno()):
                raise ValueError("workflow cache mutation lock must be project-local")
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"timed out waiting for workflow cache mutation lock: {path}")
                    time.sleep(0.01)
            if not self._lock_identity_matches(path, handle.fileno()):
                raise ValueError("workflow cache mutation lock must be project-local")
            try:
                yield
            finally:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    @staticmethod
    def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(temporary, path)

    @staticmethod
    def _valid_key(key: str) -> bool:
        return len(key) == 64 and all(character in "0123456789abcdef" for character in key)

    def _entry_path(self, layer: str, key: str) -> Path:
        if layer not in _LAYERS:
            raise ValueError(f"unknown workflow cache layer: {layer}")
        if not self._valid_key(key):
            raise ValueError("workflow cache key must be a lowercase SHA-256 digest")
        # Keep the full SHA-256 identity in the signed manifest and index, but
        # use a compact on-disk directory on Windows.  Nested reconstruction
        # artifacts otherwise exceed MAX_PATH before they can be reopened.
        return self.root / layer / key[:_ENTRY_DIRECTORY_KEY_LENGTH]

    @staticmethod
    def _relative_file_path(value: Any) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        path = Path(value)
        if path.is_absolute() or any(part == ".." for part in path.parts):
            return None
        return path

    @staticmethod
    def _inside(path: Path, container: Path) -> bool:
        try:
            path.resolve().relative_to(container.resolve())
        except (OSError, ValueError):
            return False
        return True

    def _manifest_files(self, manifest: Mapping[str, Any]) -> list[dict[str, str]] | None:
        files = manifest.get("files")
        if not isinstance(files, list) or not files:
            return None
        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        for record in files:
            if not isinstance(record, dict):
                return None
            relative = self._relative_file_path(record.get("path"))
            digest = record.get("sha256")
            if relative is None or not isinstance(digest, str) or not self._valid_key(digest):
                return None
            name = relative.as_posix()
            if name in seen:
                return None
            seen.add(name)
            normalized.append({"path": name, "sha256": digest})
        return normalized

    def _read_index(self) -> dict[str, Any] | None:
        try:
            index = json.loads((self.root / _INDEX_NAME).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(index, dict) or index.get("schema_version") != 1 or not isinstance(index.get("entries"), dict):
            return None
        return index

    @staticmethod
    def _index_entry(layer: str, key: str) -> str:
        return f"{layer}/{key}"

    @contextmanager
    def staging(self, layer: str, key: str) -> ContextManager[Path]:
        self._entry_path(layer, key)
        staged = self.root / "temporary" / f"{layer}-{key[:12]}-{uuid.uuid4().hex[:8]}"
        staged.mkdir()
        try:
            yield staged
        finally:
            if staged.exists():
                shutil.rmtree(staged)

    def seal(self, layer: str, key: str, staged: Path, manifest: dict) -> Path:
        entry = self._entry_path(layer, key)
        staged = Path(staged)
        temporary = self.root / "temporary"
        if not staged.is_dir() or not self._inside(staged, temporary):
            raise ValueError("cache staging directory must be inside this project's temporary cache area")
        if not isinstance(manifest, dict) or "trusted" in manifest:
            raise ValueError("only seal may add the trusted cache marker")
        copied = json.loads(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
        if copied.get("layer", layer) != layer or copied.get("key", key) != key:
            raise ValueError("cache manifest identity does not match its destination")
        files = self._manifest_files(copied)
        if files is None:
            raise ValueError("cache manifest must declare unique relative files and SHA-256 hashes")
        for record in files:
            target = staged / record["path"]
            if target.is_symlink() or not target.is_file() or not self._inside(target, staged) or not self._inside(target, self.root):
                raise ValueError("cache manifest file is outside the project-local cache staging area")
            if _sha256_file(target) != record["sha256"]:
                raise ValueError(f"cache manifest hash mismatch: {record['path']}")
        copied.update({"layer": layer, "key": key, "files": files, "trusted": True})
        self._write_json_atomic(staged / "manifest.json", copied)
        manifest_sha256 = _sha256_file(staged / "manifest.json")
        with self._mutation_lock():
            if entry.exists():
                existing = self._read_index()
                exact = existing is not None and self._index_entry(layer, key) in existing["entries"]
                if exact:
                    raise FileExistsError(f"immutable workflow cache entry already exists: {entry}")
                raise ValueError("workflow cache directory-key collision")
            index = self._read_index()
            if index is None:
                raise ValueError("workflow cache index is invalid")
            index["entries"][self._index_entry(layer, key)] = manifest_sha256
            self._write_json_atomic(self.root / _INDEX_NAME, index)
            try:
                os.replace(staged, entry)
            except OSError:
                recovered = self._read_index()
                if recovered is not None and recovered["entries"].get(self._index_entry(layer, key)) == manifest_sha256:
                    del recovered["entries"][self._index_entry(layer, key)]
                    try:
                        self._write_json_atomic(self.root / _INDEX_NAME, recovered)
                    except OSError:
                        pass
                raise
        return entry

    def lookup(self, layer: str, key: str) -> CacheHit | None:
        try:
            entry = self._entry_path(layer, key)
        except ValueError:
            return None
        if entry.is_symlink() or not entry.is_dir() or not self._inside(entry, self.root):
            return None
        manifest_path = entry / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file() or not self._inside(manifest_path, entry):
            return None
        index = self._read_index()
        try:
            manifest_sha256 = _sha256_file(manifest_path)
        except OSError:
            return None
        if index is None or index["entries"].get(self._index_entry(layer, key)) != manifest_sha256:
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(manifest, dict) or manifest.get("trusted") is not True:
            return None
        if manifest.get("layer") != layer or manifest.get("key") != key:
            return None
        files = self._manifest_files(manifest)
        if files is None:
            return None
        for record in files:
            target = entry / record["path"]
            if target.is_symlink() or not target.is_file() or not self._inside(target, entry) or not self._inside(target, self.root):
                return None
            try:
                if _sha256_file(target) != record["sha256"]:
                    return None
            except OSError:
                return None
        return CacheHit(layer=layer, key=key, path=entry, manifest=_freeze(manifest))

    def verified_hits(self, layer: str) -> tuple[CacheHit, ...]:
        """Return every sealed layer entry, rejecting index/directory divergence."""
        layer_root = self._entry_path(layer, "0" * 64).parent
        index = self._read_index()
        if index is None:
            raise ValueError("workflow cache index is invalid")
        prefix = f"{layer}/"
        indexed = {
            name[len(prefix):]
            for name in index["entries"]
            if isinstance(name, str) and name.startswith(prefix)
        }
        actual = {
            child.name for child in layer_root.iterdir()
            if child.is_dir() and not child.is_symlink()
        }
        expected = {key[:_ENTRY_DIRECTORY_KEY_LENGTH] for key in indexed}
        if (
            any(not self._valid_key(key) for key in indexed)
            or len(expected) != len(indexed)
            or expected != actual
        ):
            raise ValueError("workflow cache sealed layer index is inconsistent")
        hits: list[CacheHit] = []
        for key in sorted(indexed):
            hit = self.lookup(layer, key)
            if hit is None:
                raise ValueError("workflow cache sealed layer entry is invalid")
            hits.append(hit)
        return tuple(hits)

    def discard_verified(self, layer: str, key: str, manifest_sha256: str) -> bool:
        """Remove only the exact verified entry named by a failed publication transaction."""
        if not self._valid_key(manifest_sha256):
            raise ValueError("transaction manifest identity must be a lowercase SHA-256 digest")
        entry = self._entry_path(layer, key)
        quarantine = self.root / "temporary" / f"aborted-{layer}-{key[:12]}-{uuid.uuid4().hex[:8]}"
        with self._mutation_lock():
            index = self._read_index()
            manifest_path = entry / "manifest.json"
            if index is None:
                raise ValueError("workflow cache index is invalid")
            indexed = index["entries"].get(self._index_entry(layer, key))
            if not entry.exists() and indexed is None:
                return False
            if (
                entry.is_symlink() or not entry.is_dir() or not manifest_path.is_file()
                or indexed != manifest_sha256 or _sha256_file(manifest_path) != manifest_sha256
                or self.lookup(layer, key) is None
            ):
                raise ValueError("transaction cache entry is not the exact verified publication")
            os.replace(entry, quarantine)
            del index["entries"][self._index_entry(layer, key)]
            try:
                self._write_json_atomic(self.root / _INDEX_NAME, index)
            except OSError:
                os.replace(quarantine, entry)
                raise
        shutil.rmtree(quarantine)
        return True

    def quarantine_invalid(self, layer: str, key: str) -> Path | None:
        """Atomically move only an existing invalid entry out of the immutable namespace."""
        entry = self._entry_path(layer, key)
        with self._mutation_lock():
            if self.lookup(layer, key) is not None or not os.path.lexists(entry):
                return None
            quarantine = self.root / "temporary" / f"corrupt-{layer}-{key[:12]}-{uuid.uuid4().hex[:8]}"
            os.replace(entry, quarantine)
            return quarantine

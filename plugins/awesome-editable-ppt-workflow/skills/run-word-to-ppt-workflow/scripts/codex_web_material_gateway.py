"""Retrieve bounded visual evidence through the user's Codex subscription."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import multiprocessing
import os
import queue
import re
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import urljoin, urlsplit

from PIL import Image, ImageOps, UnidentifiedImageError

from codex_subscription_runtime import CodexRuntimeUnavailable, CodexStructuredResult, invoke_structured
from natural_comment_resolver import search_material_id


MAX_FILE_BYTES = 12 * 1024 * 1024
OFFICIAL_SOURCE_AUTHORITIES = (
    "first_party_website",
    "official_brand_media",
    "official_newsroom",
    "official_institution",
    "official_event_organizer",
)
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_EDGE = 12_000
MAX_IMAGES_PER_PAGE = 3
MAX_REDIRECTS = 3
MAX_PAGE_NETWORK_BYTES = 24 * 1024 * 1024
MAX_PAGE_DECODED_PIXELS = 60_000_000
MAX_PAGE_DECODED_BYTES = 480 * 1024 * 1024
_ALLOWED_MIME_TO_FORMAT = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}
_FORMAT_TO_EXTENSION = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
_MATERIAL_ID = re.compile(r"^search-request-[0-9a-f]{16}$")
_KEY_RELATIVE = Path(".private") / "web_material_gateway_attestation.key"
_KEY_INIT_LOCK = __import__("threading").Lock()
_SEARCH_CACHE_VERSION = "codex-web-material-cache-v1"
_PROJECT_DISCOVERY_VERSION = "codex-project-material-discovery-v1"
_SEARCH_CAPABILITY = {"web_search": "live", "auth_mode": "chatgpt"}
_SEARCH_INVOCATION_VERSION = "codex-web-material-invocation-v2"
_BATCH_RECEIPT_VERSION = "codex-web-material-batch-receipt-v1"
_MAX_BATCH_REQUESTS = 20
_MAX_PAGE_SEARCH_REQUESTS = 20
_MATERIAL_RETRIEVAL_GRACE_SECONDS = 120.0
_SAFE_TRACE_FIELDS = {
    "runtime", "role", "thread_id", "turn_id", "model", "model_provider",
    "auth_mode", "plan_type", "usage", "image_count", "web_search",
}
_CANDIDATE_HTTP_UNAVAILABLE = "candidate_http_unavailable"
_CANDIDATE_NETWORK_TIMEOUT = "candidate_network_timeout"


class SearchMaterialBlocked(RuntimeError):
    """A search-material failure that must stop required Image2 work."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "required_search_material_unavailable",
        state: str = "material_blocked",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.state = state


def _is_link(path: Path) -> bool:
    return path.is_symlink() or bool(
        getattr(path, "is_junction", lambda: False)()
    )


@dataclass(frozen=True)
class DownloadResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class MaterialTransport(Protocol):
    def resolve(self, hostname: str, port: int) -> Sequence[str]: ...

    def get(
        self,
        url: str,
        *,
        connect_ip: str,
        timeout: float,
        max_bytes: int,
    ) -> DownloadResponse: ...


class ResourceBudget:
    """Thread-safe page-level reservations shared by every search directive."""

    def __init__(
        self,
        *,
        max_images: int = MAX_IMAGES_PER_PAGE,
        max_network_bytes: int = MAX_PAGE_NETWORK_BYTES,
        max_decoded_pixels: int = MAX_PAGE_DECODED_PIXELS,
        max_decoded_bytes: int = MAX_PAGE_DECODED_BYTES,
    ) -> None:
        for name, value in {
            "max_images": max_images,
            "max_network_bytes": max_network_bytes,
            "max_decoded_pixels": max_decoded_pixels,
            "max_decoded_bytes": max_decoded_bytes,
        }.items():
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self.max_images = max_images
        self.max_network_bytes = max_network_bytes
        self.max_decoded_pixels = max_decoded_pixels
        self.max_decoded_bytes = max_decoded_bytes
        self._lock = __import__("threading").Lock()
        self._selected_images = 0
        self._reserved_images = 0
        self._network_used = 0
        self._network_reserved = 0
        self._decoded_pixels_used = 0
        self._decoded_pixels_reserved = 0
        self._decoded_bytes_used = 0
        self._decoded_bytes_reserved = 0

    def reserve_candidate(self) -> "_CandidateReservation":
        with self._lock:
            if self._selected_images + self._reserved_images >= self.max_images:
                raise SearchMaterialBlocked("page image count budget is exhausted")
            pixel_allowance = min(
                MAX_IMAGE_PIXELS,
                self.max_decoded_pixels
                - self._decoded_pixels_used
                - self._decoded_pixels_reserved,
            )
            byte_allowance = min(
                MAX_IMAGE_PIXELS * 8,
                self.max_decoded_bytes
                - self._decoded_bytes_used
                - self._decoded_bytes_reserved,
            )
            if pixel_allowance < 1 or byte_allowance < 8:
                raise SearchMaterialBlocked("page decoded image budget is exhausted")
            self._reserved_images += 1
            self._decoded_pixels_reserved += pixel_allowance
            self._decoded_bytes_reserved += byte_allowance
            return _CandidateReservation(self, pixel_allowance, byte_allowance)

    def reserve_network(self) -> "_NetworkReservation":
        with self._lock:
            allowance = min(
                MAX_FILE_BYTES,
                self.max_network_bytes - self._network_used - self._network_reserved,
            )
            if allowance < 1:
                raise SearchMaterialBlocked("page network byte budget is exhausted")
            self._network_reserved += allowance
            return _NetworkReservation(self, allowance)

    def adopt_cached(self, *, width: int, height: int) -> None:
        """Count verified cached pixels without charging a second network transfer."""
        pixels = width * height
        decoded_bytes = pixels * 8
        with self._lock:
            if self._selected_images + self._reserved_images >= self.max_images:
                raise SearchMaterialBlocked("page image count budget is exhausted")
            if self._decoded_pixels_used + self._decoded_pixels_reserved + pixels > self.max_decoded_pixels:
                raise SearchMaterialBlocked("page decoded pixel budget is exhausted")
            if self._decoded_bytes_used + self._decoded_bytes_reserved + decoded_bytes > self.max_decoded_bytes:
                raise SearchMaterialBlocked("page decoded image byte budget is exhausted")
            self._selected_images += 1
            self._decoded_pixels_used += pixels
            self._decoded_bytes_used += decoded_bytes


class _NetworkReservation:
    def __init__(self, budget: ResourceBudget, allowance: int) -> None:
        self.budget = budget
        self.allowance = allowance
        self._settled = False

    def settle(self, actual: int) -> None:
        if type(actual) is not int or actual < 0 or actual > self.allowance:
            raise SearchMaterialBlocked("page network byte budget was exceeded")
        with self.budget._lock:
            if self._settled:
                raise RuntimeError("network reservation already settled")
            self.budget._network_reserved -= self.allowance
            self.budget._network_used += actual
            self._settled = True

    def cancel(self) -> None:
        with self.budget._lock:
            if not self._settled:
                self.budget._network_reserved -= self.allowance
                self._settled = True


class _CandidateReservation:
    def __init__(self, budget: ResourceBudget, pixel_allowance: int, byte_allowance: int) -> None:
        self.budget = budget
        self.pixel_allowance = pixel_allowance
        self.byte_allowance = byte_allowance
        self._dimensions_settled = False
        self._finished = False

    def settle_dimensions(self, width: int, height: int) -> None:
        pixels = width * height
        decoded_bytes = pixels * 8
        if pixels > self.pixel_allowance or decoded_bytes > self.byte_allowance:
            raise SearchMaterialBlocked("page decoded image budget was exceeded")
        with self.budget._lock:
            if self._dimensions_settled:
                raise RuntimeError("image dimensions already settled")
            self.budget._decoded_pixels_reserved -= self.pixel_allowance
            self.budget._decoded_bytes_reserved -= self.byte_allowance
            self.budget._decoded_pixels_used += pixels
            self.budget._decoded_bytes_used += decoded_bytes
            self._dimensions_settled = True

    def finish(self, *, selected: bool) -> None:
        with self.budget._lock:
            if self._finished:
                return
            self.budget._reserved_images -= 1
            if not self._dimensions_settled:
                self.budget._decoded_pixels_reserved -= self.pixel_allowance
                self.budget._decoded_bytes_reserved -= self.byte_allowance
            if selected:
                self.budget._selected_images += 1
            self._finished = True


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, port: int, connect_ip: str, timeout: float) -> None:
        super().__init__(hostname, port=port, timeout=timeout, context=ssl.create_default_context())
        self._connect_ip = connect_ip

    def connect(self) -> None:
        raw = socket.create_connection((self._connect_ip, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


class _NetworkTransport:
    def resolve(self, hostname: str, port: int) -> Sequence[str]:
        try:
            return sorted({item[4][0] for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)})
        except socket.gaierror as exc:
            raise SearchMaterialBlocked(f"image host DNS resolution failed: {hostname}") from exc

    def get(
        self,
        url: str,
        *,
        connect_ip: str,
        timeout: float,
        max_bytes: int,
    ) -> DownloadResponse:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        if hostname is None:
            raise SearchMaterialBlocked("image URL is not a safe HTTPS URL")
        port = parsed.port or 443
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        connection = _PinnedHTTPSConnection(hostname, port, connect_ip, timeout)
        try:
            connection.request(
                "GET",
                target,
                headers={
                    "Accept": "image/avif,image/webp,image/png,image/jpeg;q=0.9",
                    "User-Agent": "awesome-editable-ppt-workflow/1.0.1",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise SearchMaterialBlocked("image exceeds the 12 MiB download limit")
            return DownloadResponse(response.status, dict(response.getheaders()), body)
        except (OSError, http.client.HTTPException) as exc:
            code = (
                _CANDIDATE_NETWORK_TIMEOUT
                if isinstance(exc, TimeoutError)
                else "required_search_material_unavailable"
            )
            raise SearchMaterialBlocked(
                f"image HTTPS download failed: {exc}", code=code,
            ) from exc
        finally:
            connection.close()


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _check_deadline(
    deadline: float, label: str, *, code: str = "required_search_material_unavailable",
) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SearchMaterialBlocked(f"{label} timed out", code=code)
    return remaining


def _raise_if_cancelled(cancel_deadline: float | None) -> None:
    if cancel_deadline is not None and time.monotonic() >= cancel_deadline:
        raise SearchMaterialBlocked(
            "material search was cancelled by the caller deadline",
            code="material_search_cancelled",
            state="cancelled",
        )


def _operation_deadline(timeout: float, cancel_deadline: float | None) -> float:
    """Return a fresh operation watchdog capped only by explicit caller cancellation."""
    _raise_if_cancelled(cancel_deadline)
    deadline = time.monotonic() + timeout
    return min(deadline, cancel_deadline) if cancel_deadline is not None else deadline


def _operation_timeout(
    timeout: float, cancel_deadline: float | None, label: str,
) -> float:
    return _check_deadline(_operation_deadline(timeout, cancel_deadline), label)


def _is_search_cancelled(exc: BaseException) -> bool:
    return isinstance(exc, SearchMaterialBlocked) and exc.code == "material_search_cancelled"


def _cancel_aware(cancel_deadline: float | None, operation: Callable[[], Any]) -> Any:
    """Map any locally bounded stage crossing caller cancellation to one result."""
    _raise_if_cancelled(cancel_deadline)
    try:
        result = operation()
    except Exception:
        _raise_if_cancelled(cancel_deadline)
        raise
    _raise_if_cancelled(cancel_deadline)
    return result


def _isolated_entry(connection: Any, operation: Callable[..., Any], arguments: tuple[Any, ...]) -> None:
    try:
        connection.send((True, operation(*arguments)))
    except BaseException as exc:
        connection.send((False, f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


def _run_isolated(
    operation: Callable[..., Any],
    arguments: tuple[Any, ...],
    *,
    deadline: float,
    label: str,
) -> Any:
    """Run CPU/blocking work in a terminable process under one absolute deadline."""
    _check_deadline(deadline, label)
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_isolated_entry,
        args=(sender, operation, arguments),
        daemon=True,
    )
    process.start()
    sender.close()
    try:
        if not receiver.poll(_check_deadline(deadline, label)):
            process.terminate()
            process.join(timeout=1)
            raise SearchMaterialBlocked(f"{label} timed out")
        ok, value = receiver.recv()
        _check_deadline(deadline, label)
        process.join(timeout=min(1.0, _check_deadline(deadline, label)))
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)
            raise SearchMaterialBlocked(f"{label} timed out")
        if not ok:
            raise SearchMaterialBlocked(f"{label} failed: {value}")
        return value
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)


def _run_thread_bounded(
    operation: Callable[..., Any],
    arguments: tuple[Any, ...],
    *,
    deadline: float,
    label: str,
) -> Any:
    """Bound non-pickleable DNS transports without blocking the main workflow."""
    output: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            output.put((True, operation(*arguments)))
        except BaseException as exc:
            output.put((False, exc))

    thread = __import__("threading").Thread(target=worker, daemon=True)
    thread.start()
    try:
        ok, value = output.get(timeout=_check_deadline(
            deadline, label, code=_CANDIDATE_NETWORK_TIMEOUT,
        ))
    except queue.Empty as exc:
        raise SearchMaterialBlocked(
            f"{label} timed out", code=_CANDIDATE_NETWORK_TIMEOUT,
        ) from exc
    _check_deadline(deadline, label, code=_CANDIDATE_NETWORK_TIMEOUT)
    if not ok:
        if isinstance(value, SearchMaterialBlocked):
            raise value
        if isinstance(value, TimeoutError):
            raise SearchMaterialBlocked(
                f"{label} timed out", code=_CANDIDATE_NETWORK_TIMEOUT,
            ) from value
        raise SearchMaterialBlocked(f"{label} failed: {value}") from value
    return value


_WRITE_PROCESS_PROGRAM = r"""
import os
import stat
import sys

path_text = sys.argv[1]
expected_dev = int(sys.argv[2])
expected_ino = int(sys.argv[3])
payload = sys.stdin.buffer.read()
flags = os.O_WRONLY | getattr(os, "O_BINARY", 0)
descriptor = os.open(path_text, flags, 0o600)
try:
    opened = os.fstat(descriptor)
    named = os.lstat(path_text)
    expected = (expected_dev, expected_ino)
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or (opened.st_dev, opened.st_ino) != expected
        or (named.st_dev, named.st_ino) != expected
    ):
        raise OSError("reserved evidence file identity changed")
    os.ftruncate(descriptor, 0)
    written = 0
    view = memoryview(payload)
    while written < len(payload):
        count = os.write(descriptor, view[written:])
        if count <= 0:
            raise OSError("exclusive evidence write made no progress")
        written += count
    os.fsync(descriptor)
finally:
    os.close(descriptor)
"""


def _run_write_process(
    path: Path,
    payload: bytes,
    *,
    deadline: float,
    expected_dev: int,
    expected_ino: int,
) -> None:
    """Write and fsync in a minimal process that can be killed at the absolute deadline."""
    _check_deadline(deadline, "evidence write")
    process = subprocess.Popen(
        [
            sys.executable, "-c", _WRITE_PROCESS_PROGRAM, str(path),
            str(expected_dev), str(expected_ino),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _stdout, stderr = process.communicate(
            input=payload,
            timeout=_check_deadline(deadline, "evidence write"),
        )
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise SearchMaterialBlocked("evidence write timed out") from exc
    _check_deadline(deadline, "evidence write")
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[:500]
        raise SearchMaterialBlocked(f"evidence write failed: {detail or process.returncode}")


def _safe_parent(path: Path) -> None:
    parent = path.parent
    if not parent.is_dir() or _is_link(parent) or parent.resolve() != parent:
        raise SearchMaterialBlocked("evidence parent directory is not a literal directory")


def _atomic_publish(
    path: Path,
    payload: bytes,
    *,
    deadline: float,
    accept_existing: bool,
) -> None:
    """Publish a fully written same-directory temp through atomic no-replace linking."""
    _check_deadline(deadline, "evidence write")
    _safe_parent(path)
    if path.exists() or _is_link(path):
        if _is_link(path) or not path.is_file():
            raise SearchMaterialBlocked("immutable evidence target is not a regular file")
        if accept_existing or path.read_bytes() == payload:
            return
        raise SearchMaterialBlocked(f"immutable search evidence already differs: {path.name}")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(16)}.tmp")
    try:
        _check_deadline(deadline, "evidence write")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            reserved = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        _run_write_process(
            temporary,
            payload,
            deadline=deadline,
            expected_dev=reserved.st_dev,
            expected_ino=reserved.st_ino,
        )
        _check_deadline(deadline, "evidence write")
        _check_deadline(deadline, "evidence publish")
        _safe_parent(path)
        temporary_is_link = _is_link(temporary)
        temporary_is_file = temporary.is_file()
        temporary_payload = temporary.read_bytes() if temporary_is_file else b""
        if temporary_is_link or not temporary_is_file or temporary_payload != payload:
            raise SearchMaterialBlocked(
                "temporary evidence payload failed verification "
                f"({path.name}: link={temporary_is_link}, file={temporary_is_file}, "
                f"expected={len(payload)}, actual={len(temporary_payload)})"
            )
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _is_link(path) or not path.is_file():
                raise SearchMaterialBlocked("immutable evidence target is not a regular file")
            if not accept_existing and path.read_bytes() != payload:
                raise SearchMaterialBlocked(
                    f"immutable search evidence already differs: {path.name}"
                )
        except OSError as exc:
            raise SearchMaterialBlocked(f"atomic evidence publish failed: {exc}") from exc
        _check_deadline(deadline, "evidence publish")
        _safe_parent(path)
        if _is_link(path) or not path.is_file():
            raise SearchMaterialBlocked("published evidence target changed type")
        if not accept_existing and path.read_bytes() != payload:
            raise SearchMaterialBlocked("published evidence payload changed")
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _attestation_key(project: Path, *, deadline: float | None = None) -> bytes:
    project = Path(project).resolve(strict=True)
    deadline = deadline if deadline is not None else time.monotonic() + 30.0
    _check_deadline(deadline, "attestation key")
    parent = project / _KEY_RELATIVE.parent
    if parent.exists() and (_is_link(parent) or not parent.is_dir() or parent.resolve() != parent):
        raise SearchMaterialBlocked("web material attestation key path is not project-local")
    parent.mkdir(parents=True, exist_ok=True)
    if not _KEY_INIT_LOCK.acquire(timeout=_check_deadline(deadline, "attestation key")):
        raise SearchMaterialBlocked("attestation key timed out")
    try:
        path = project / _KEY_RELATIVE
        if _is_link(path) or (path.exists() and not path.is_file()):
            raise SearchMaterialBlocked("web material attestation key path is invalid")
        if not path.exists():
            _atomic_publish(
                path,
                secrets.token_bytes(32),
                deadline=deadline,
                accept_existing=True,
            )
        _check_deadline(deadline, "attestation key")
        if _is_link(path) or not path.is_file():
            raise SearchMaterialBlocked("web material attestation key path is invalid")
        value = path.read_bytes()
        if len(value) != 32:
            raise SearchMaterialBlocked("web material attestation key is invalid")
        return value
    finally:
        _KEY_INIT_LOCK.release()


def sign_project_payload(
    project: Path, payload: Mapping[str, Any], *, purpose: str,
) -> str:
    """Sign a canonical project-local payload for a domain-separated purpose."""
    if not isinstance(purpose, str) or not purpose.strip():
        raise ValueError("attestation purpose is required")
    message = purpose.strip().encode("utf-8") + b"\0" + json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(_attestation_key(Path(project)), message, hashlib.sha256).hexdigest()


def verify_project_payload_signature(
    project: Path,
    payload: Mapping[str, Any],
    *,
    purpose: str,
    signature: Any,
) -> bool:
    """Verify a domain-separated payload without exposing the project key."""
    if not isinstance(signature, str):
        return False
    try:
        expected = sign_project_payload(project, payload, purpose=purpose)
    except (OSError, ValueError, SearchMaterialBlocked):
        return False
    return hmac.compare_digest(signature, expected)


def _project_regular_file(project: Path, value: str | Path, label: str) -> Path:
    project = project.resolve(strict=True)
    raw = Path(value)
    unresolved = raw if raw.is_absolute() else project / raw
    current = unresolved
    while current != project:
        if _is_link(current) or project not in current.parents:
            raise SearchMaterialBlocked(f"{label} must be a regular project-local file")
        current = current.parent
    path = unresolved.resolve()
    try:
        path.relative_to(project)
    except ValueError as exc:
        raise SearchMaterialBlocked(f"{label} must be project-local") from exc
    if not path.is_file() or _is_link(path):
        raise SearchMaterialBlocked(f"{label} must be a regular project-local file")
    return path


def _validate_search_trace(trace: Any, outer: Mapping[str, Any]) -> None:
    if not isinstance(trace, Mapping) or set(trace) != _SAFE_TRACE_FIELDS:
        raise SearchMaterialBlocked("App Server safe trace shape is incomplete")
    expected = {
        "runtime": "codex-app-server",
        "role": "visual-material-search",
        "thread_id": outer.get("thread_id"),
        "turn_id": outer.get("turn_id"),
        "model": outer.get("model"),
        "model_provider": outer.get("model_provider"),
        "auth_mode": "chatgpt",
        "plan_type": outer.get("plan_type"),
        "usage": outer.get("usage"),
        "image_count": 0,
        "web_search": "live",
    }
    for field, expected_value in expected.items():
        actual = trace.get(field)
        if _canonical_bytes(actual) != _canonical_bytes(expected_value):
            raise SearchMaterialBlocked(f"App Server safe trace {field} mismatch")
    for field in ("thread_id", "turn_id", "model", "model_provider"):
        if not isinstance(outer.get(field), str) or not outer[field]:
            raise SearchMaterialBlocked(f"App Server invocation {field} is incomplete")
    if outer.get("auth_mode") != "chatgpt" or not isinstance(outer.get("usage"), Mapping):
        raise SearchMaterialBlocked("App Server invocation OAuth or usage closure is invalid")


def _read_closed_json_file(
    project: Path, record: Any, *, label: str, deadline: float,
) -> dict[str, Any]:
    if not isinstance(record, Mapping) or set(record) != {"path", "sha256"}:
        raise SearchMaterialBlocked(f"{label} record is invalid")
    path = _project_regular_file(project, str(record.get("path")), label)
    if _sha256_file(path) != record.get("sha256"):
        raise SearchMaterialBlocked(f"{label} file SHA-256 mismatch")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SearchMaterialBlocked(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise SearchMaterialBlocked(f"{label} is invalid")
    _check_deadline(deadline, f"{label} verification")
    return value


def _validate_invocation_closure(
    project: Path, value: Mapping[str, Any], *, deadline: float,
) -> None:
    expected_fields = {
        "artifact_version", "status", "page_number", "material_id", "directive_id",
        "query", "role", "web_search", "thread_id", "turn_id", "model",
        "model_provider", "auth_mode", "plan_type", "usage", "safe_trace",
        "model_response", "request", "raw_response",
    }
    if set(value) != expected_fields or value.get("artifact_version") != _SEARCH_INVOCATION_VERSION:
        raise SearchMaterialBlocked("App Server invocation schema is not closed")
    if (
        value.get("status") != "completed"
        or type(value.get("page_number")) is not int
        or value["page_number"] < 1
        or value.get("role") != "visual-material-search"
        or value.get("web_search") != "live"
        or not isinstance(value.get("model_response"), Mapping)
    ):
        raise SearchMaterialBlocked("App Server invocation status or capability is invalid")
    _validate_search_trace(value.get("safe_trace"), value)
    request = _read_closed_json_file(
        project, value.get("request"), label="App Server search request", deadline=deadline,
    )
    response = _read_closed_json_file(
        project, value.get("raw_response"), label="App Server raw response", deadline=deadline,
    )
    try:
        result_limit = request["output_schema"]["properties"]["candidates"]["maxItems"]
    except (KeyError, TypeError) as exc:
        raise SearchMaterialBlocked("App Server search result limit is invalid") from exc
    if type(result_limit) is not int or not 1 <= result_limit <= MAX_IMAGES_PER_PAGE:
        raise SearchMaterialBlocked("App Server search result limit is invalid")
    request_fields = {
        "artifact_version", "page_number", "page_authority_sha256", "material_id",
        "directive_id", "query", "role", "web_search", "prompt", "images",
        "output_schema", "page_context",
    }
    if (
        set(request) != request_fields
        or request.get("artifact_version") != "codex-web-material-request-v1"
        or request.get("page_number") != value["page_number"]
        or request.get("material_id") != value["material_id"]
        or request.get("directive_id") != value["directive_id"]
        or request.get("query") != value["query"]
        or request.get("role") != value["role"]
        or request.get("web_search") != value["web_search"]
        or request.get("images") != []
        or not isinstance(request.get("page_context"), Mapping)
        or hashlib.sha256(_canonical_bytes(request["page_context"])).hexdigest()
        != request.get("page_authority_sha256")
        or request.get("prompt") != _prompt(
            query=str(request.get("query")),
            material_id=str(request.get("material_id")),
            directive_id=str(request.get("directive_id")),
            page_context=request["page_context"],
            max_results=result_limit,
        )
        or request.get("output_schema") != _schema(result_limit)
        or not isinstance(request.get("prompt"), str)
        or not isinstance(request.get("output_schema"), Mapping)
    ):
        raise SearchMaterialBlocked("App Server search request closure mismatch")
    if (
        set(response) != {"artifact_version", "status", "value", "safe_trace"}
        or response.get("artifact_version") != "codex-web-material-response-v1"
        or response.get("status") != "completed"
        or response.get("value") != value["model_response"]
        or response.get("safe_trace") != value["safe_trace"]
    ):
        raise SearchMaterialBlocked("App Server raw response closure mismatch")


def _read_signed_invocation(
    project: Path,
    bundle_path: str | Path,
    *,
    deadline: float,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    _check_deadline(deadline, "invocation verification")
    project = Path(project).resolve(strict=True)
    path = _project_regular_file(project, bundle_path, "invocation bundle")
    actual_file_sha = _sha256_file(path)
    if expected_sha256 is not None and actual_file_sha != expected_sha256:
        raise SearchMaterialBlocked("invocation bundle file SHA-256 mismatch")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SearchMaterialBlocked("invocation bundle is unreadable") from exc
    if not isinstance(value, dict):
        raise SearchMaterialBlocked("invocation bundle is invalid")
    signature = value.pop("signature", None)
    if not isinstance(signature, str) or not hmac.compare_digest(
        signature,
        hmac.new(
            _attestation_key(project, deadline=deadline),
            _canonical_bytes(value),
            hashlib.sha256,
        ).hexdigest(),
    ):
        raise SearchMaterialBlocked("invocation bundle signature mismatch")
    recorded_seal = value.pop("sealed_sha256", None)
    if not isinstance(recorded_seal, str) or not hmac.compare_digest(
        recorded_seal, hashlib.sha256(_canonical_bytes(value)).hexdigest()
    ):
        raise SearchMaterialBlocked("invocation bundle seal mismatch")
    _validate_invocation_closure(project, value, deadline=deadline)
    _check_deadline(deadline, "invocation verification")
    return {
        **value,
        "signature": signature,
        "sealed_sha256": recorded_seal,
        "file_sha256": actual_file_sha,
        "path": path.relative_to(project).as_posix(),
    }


def verify_signed_invocation_bundle(project: Path, bundle_path: Path) -> bool:
    """Verify a project-local Codex material-search invocation signature and seal."""
    try:
        _read_signed_invocation(
            project,
            bundle_path,
            deadline=time.monotonic() + 30.0,
        )
        return True
    except (OSError, ValueError, json.JSONDecodeError, SearchMaterialBlocked):
        return False


def _binding(item: Mapping[str, Any]) -> tuple[str, str, str | None]:
    return (str(item.get("material_id")), str(item.get("directive_id")), item.get("entity"))


def _binding_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "material_id": item.get("material_id"),
        "directive_id": item.get("directive_id"),
        "entity": item.get("entity"),
    }


def _validate_batch_bijection(
    requests: Sequence[Mapping[str, Any]], value: Any,
) -> dict[tuple[str, str, str | None], dict[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != {"results"}:
        raise SearchMaterialBlocked("batch search response binding is invalid")
    results = value.get("results")
    if not isinstance(results, list):
        raise SearchMaterialBlocked("batch search response binding is invalid")
    expected = {_binding(item): item for item in requests}
    if len(expected) != len(requests):
        raise ValueError("batch search request bindings must be unique")
    received: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    for raw in results:
        if not isinstance(raw, Mapping) or set(raw) != {
            "material_id", "directive_id", "entity", "candidates",
        }:
            raise SearchMaterialBlocked("batch search response binding is invalid")
        key = _binding(raw)
        if key in received:
            raise SearchMaterialBlocked("batch search response is not a strict bijection: duplicate binding")
        request = expected.get(key)
        candidates = raw.get("candidates")
        if request is None:
            raise SearchMaterialBlocked("batch search response is not a strict bijection: extra binding")
        if not isinstance(candidates, list) or len(candidates) > int(request["max_results"]):
            raise SearchMaterialBlocked("batch search response binding has invalid candidates")
        if any(not isinstance(candidate, Mapping) for candidate in candidates):
            raise SearchMaterialBlocked("batch search response binding has invalid candidates")
        received[key] = dict(raw)
    if set(received) != set(expected):
        raise SearchMaterialBlocked("batch search response is not a strict bijection: missing binding")
    return received


def _write_batch_receipt(
    project: Path,
    *,
    page_context: Mapping[str, Any],
    requests: Sequence[Mapping[str, Any]],
    prompt: str,
    output_schema: Mapping[str, Any],
    result: CodexStructuredResult,
    deadline: float,
) -> dict[str, Any]:
    page_number = int(page_context["page_number"])
    request = {
        "page_number": page_number,
        "page_authority_sha256": hashlib.sha256(_canonical_bytes(dict(page_context))).hexdigest(),
        "page_context": dict(page_context),
        "requests": [dict(item) for item in requests],
        "role": "visual-material-search",
        "web_search": "live",
        "prompt": prompt,
        "images": [],
        "output_schema": dict(output_schema),
    }
    receipt: dict[str, Any] = {
        "artifact_version": _BATCH_RECEIPT_VERSION,
        "status": "completed",
        "role": "visual-material-search",
        "web_search": "live",
        "page_number": page_number,
        "batch_id": "material-batch-" + hashlib.sha256(_canonical_bytes(request)).hexdigest()[:16],
        "thread_id": result.thread_id,
        "turn_id": result.turn_id,
        "model": result.model,
        "model_provider": result.model_provider,
        "auth_mode": result.auth_mode,
        "plan_type": result.plan_type,
        "usage": dict(result.usage),
        "request": request,
        "response": {"value": dict(result.value), "safe_trace": dict(result.safe_trace)},
    }
    _validate_search_trace(result.safe_trace, receipt)
    _validate_batch_bijection(requests, result.value)
    receipt["sealed_sha256"] = hashlib.sha256(_canonical_bytes(receipt)).hexdigest()
    receipt["signature"] = hmac.new(
        _attestation_key(project, deadline=deadline), _canonical_bytes(receipt), hashlib.sha256,
    ).hexdigest()
    directory = _safe_evidence_dir(project, page_number, deadline=deadline)
    target = directory / f"{receipt['batch_id']}-receipt-{receipt['sealed_sha256'][:12]}.json"
    _write_immutable(
        target,
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n",
        deadline=deadline,
    )
    return {
        "path": target.relative_to(Path(project).resolve(strict=True)).as_posix(),
        "sha256": _sha256_file(target),
        "sealed_sha256": receipt["sealed_sha256"],
        "signature": receipt["signature"],
        "batch_id": receipt["batch_id"],
    }


def _read_signed_batch_receipt(
    project: Path, receipt_path: str | Path, *, deadline: float, expected_sha256: str | None = None,
) -> dict[str, Any]:
    project = Path(project).resolve(strict=True)
    path = _project_regular_file(project, receipt_path, "batch search receipt")
    if expected_sha256 is not None and _sha256_file(path) != expected_sha256:
        raise SearchMaterialBlocked("batch search receipt file SHA-256 mismatch")
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SearchMaterialBlocked("batch search receipt is unreadable") from exc
    if not isinstance(stored, dict):
        raise SearchMaterialBlocked("batch search receipt is invalid")
    value = dict(stored)
    signature = value.pop("signature", None)
    sealed = value.pop("sealed_sha256", None)
    if (
        not isinstance(signature, str)
        or not isinstance(sealed, str)
        or hashlib.sha256(_canonical_bytes(value)).hexdigest() != sealed
    ):
        raise SearchMaterialBlocked("batch search receipt seal is invalid")
    signed = {**value, "sealed_sha256": sealed}
    expected_signature = hmac.new(
        _attestation_key(project, deadline=deadline), _canonical_bytes(signed), hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise SearchMaterialBlocked("batch search receipt signature mismatch")
    request = value.get("request")
    response = value.get("response")
    if (
        value.get("artifact_version") != _BATCH_RECEIPT_VERSION
        or value.get("status") != "completed"
        or not isinstance(request, Mapping)
        or not isinstance(response, Mapping)
        or set(response) != {"value", "safe_trace"}
        or request.get("role") != "visual-material-search"
        or request.get("web_search") != "live"
        or request.get("images") != []
        or not isinstance(request.get("page_context"), Mapping)
        or hashlib.sha256(_canonical_bytes(dict(request["page_context"]))).hexdigest()
        != request.get("page_authority_sha256")
        or request.get("prompt") != _batch_prompt(
            requests=request.get("requests", []), page_context=request["page_context"],
        )
        or request.get("output_schema") != _batch_schema(request.get("requests", []))
    ):
        raise SearchMaterialBlocked("batch search receipt closure mismatch")
    _validate_search_trace(response.get("safe_trace"), value)
    bindings = _validate_batch_bijection(request["requests"], response.get("value"))
    _check_deadline(deadline, "batch receipt verification")
    return {
        **stored,
        "path": path.relative_to(project).as_posix(),
        "file_sha256": _sha256_file(path),
        "bindings": bindings,
    }


def verify_signed_batch_receipt(project: Path, receipt_path: Path) -> bool:
    try:
        _read_signed_batch_receipt(
            project, receipt_path, deadline=time.monotonic() + 30.0,
        )
        return True
    except (OSError, ValueError, json.JSONDecodeError, SearchMaterialBlocked):
        return False


def _directive_value(directive: Any, key: str) -> Any:
    if isinstance(directive, Mapping):
        return directive.get(key)
    return getattr(directive, key, None)


def _request_identity(directive: Any) -> tuple[str, str, bool, str]:
    directive_id = _directive_value(directive, "directive_id")
    query = _directive_value(directive, "search_query")
    required = _directive_value(directive, "required")
    decisions = _directive_value(directive, "decisions")
    if not isinstance(directive_id, str) or not directive_id:
        raise ValueError("search directive_id is required")
    if _directive_value(directive, "search_required") is not True:
        raise ValueError("directive must require visual material search")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("search directive query is required")
    if type(required) is not bool:
        raise ValueError("search directive required must be boolean")
    if not isinstance(decisions, Sequence):
        raise ValueError("search directive decisions are required")
    material_ids = {
        item.get("material_id")
        for item in decisions
        if isinstance(item, Mapping) and item.get("target") == "material.search_evidence"
    }
    expected = search_material_id(query)
    if material_ids != {expected}:
        raise ValueError("search directive material_id is inconsistent with its query")
    return directive_id, query, bool(required), expected


def _batch_request_identity(directive: Any) -> dict[str, Any]:
    directive_id, query, required, material_id = _request_identity(directive)
    entity = _directive_value(directive, "entity")
    if entity is None:
        entity = ""
    if not isinstance(entity, str) or (entity and not entity.strip()):
        raise ValueError("search directive entity must be a string")
    material_role = _directive_value(directive, "material_role")
    if material_role is not None and (not isinstance(material_role, str) or not material_role):
        raise ValueError("search directive material_role is invalid")
    max_results = _directive_value(directive, "max_results")
    if max_results is None:
        max_results = MAX_IMAGES_PER_PAGE
    if type(max_results) is not int or not 1 <= max_results <= MAX_IMAGES_PER_PAGE:
        raise ValueError("search directive max_results is invalid")
    return {
        "material_id": material_id,
        "directive_id": directive_id,
        "entity": entity,
        "query": query,
        "required": required,
        "material_role": material_role,
        "max_results": max_results,
    }


def _schema(max_results: int = MAX_IMAGES_PER_PAGE) -> dict[str, Any]:
    if type(max_results) is not int or not 1 <= max_results <= MAX_IMAGES_PER_PAGE:
        raise ValueError("search schema result limit is invalid")
    candidate = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "source_page_url",
            "direct_image_url",
            "title",
            "publisher",
            "caption",
            "matched_entities",
            "retrieved_at",
        ],
        "properties": {
            "source_page_url": {"type": "string", "minLength": 1},
            "direct_image_url": {"type": "string", "minLength": 1},
            "title": {"type": "string", "minLength": 1, "maxLength": 300},
            "publisher": {"type": "string", "minLength": 1, "maxLength": 200},
            "source_authority": {
                "type": "string", "enum": list(OFFICIAL_SOURCE_AUTHORITIES),
            },
            "authority_basis": {"type": "string", "minLength": 10, "maxLength": 500},
            "caption": {"type": "string", "maxLength": 1000},
            "matched_entities": {
                "type": "array",
                "maxItems": 20,
                "items": {"type": "string", "minLength": 1, "maxLength": 200},
            },
            "retrieved_at": {"type": "string"},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["candidates"],
        "properties": {
            "candidates": {
                "type": "array",
                "maxItems": max_results,
                "items": candidate,
            }
        },
    }


def _batch_schema(requests: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not 1 <= len(requests) <= _MAX_BATCH_REQUESTS:
        raise ValueError("batch search shard must contain between one and twenty requests")
    base_candidate = _schema(MAX_IMAGES_PER_PAGE)["properties"]["candidates"]["items"]
    candidate = {
        **base_candidate,
        "required": [
            *base_candidate["required"], "source_authority", "authority_basis",
        ],
    }
    result_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["material_id", "directive_id", "entity", "candidates"],
        "properties": {
            "material_id": {"type": "string", "minLength": 1},
            "directive_id": {"type": "string", "minLength": 1},
            "entity": {"type": "string"},
            "candidates": {
                "type": "array", "maxItems": MAX_IMAGES_PER_PAGE, "items": candidate,
            },
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["results"],
        "properties": {
            "results": {
                "type": "array", "minItems": len(requests), "maxItems": len(requests),
                "items": result_item,
            },
        },
    }


def _batch_prompt(*, requests: Sequence[Mapping[str, Any]], page_context: Mapping[str, Any]) -> str:
    locked = {
        "page_number": page_context.get("page_number"),
        "page_title": page_context.get("page_title", ""),
        "body_text": page_context.get("body_text", ""),
        "key_facts": page_context.get("key_facts", []),
        "detected_dates": page_context.get("detected_dates", []),
    }
    public_requests = [{
        "material_id": item["material_id"], "directive_id": item["directive_id"],
        "entity": item["entity"], "query": item["query"],
        "max_results": item["max_results"],
    } for item in requests]
    return (
        "Use one live web-search turn to resolve every visual-material request from allowed first-party "
        "official sources: for enterprise logos, the enterprise website or official brand media page; "
        "for people, the person, their employer or official event organizer; for products or projects, "
        "the enterprise or project site or official news. Echo material_id, "
        "directive_id, and entity exactly; do not merge or substitute requests. Return real raster "
        "candidates with the official source page, direct HTTPS image URL, title, official publisher, "
        "source_authority (one allowed enum value), and authority_basis naming the exact publisher "
        "and source-page host that prove the first-party or official relationship, "
        "caption, only the matched locked entities actually represented by that candidate, and retrieval "
        "time. Do not use aggregators, fan sites, social reposts, search-result thumbnails, generated "
        "images, SVG, data, file, localhost, or private-network URLs. "
        "Return JSON only.\n"
        f"SEARCH_REQUESTS: {json.dumps(public_requests, ensure_ascii=False, sort_keys=True)}\n"
        f"LOCKED_WORD_CONTEXT: {json.dumps(locked, ensure_ascii=False, sort_keys=True)}"
    )
def _prompt(
    *,
    query: str,
    material_id: str,
    directive_id: str,
    page_context: Mapping[str, Any],
    max_results: int = MAX_IMAGES_PER_PAGE,
) -> str:
    result_phrase = "three" if max_results == MAX_IMAGES_PER_PAGE else str(max_results)
    locked = {
        "page_number": page_context.get("page_number"),
        "page_title": page_context.get("page_title", ""),
        "body_text": page_context.get("body_text", ""),
        "key_facts": page_context.get("key_facts", []),
        "detected_dates": page_context.get("detected_dates", []),
    }
    request = {"query": query, "material_id": material_id, "directive_id": directive_id}
    return (
        f"Use live web search to locate up to {result_phrase} real raster photographs that directly satisfy the "
        "visual-material request. Return a source page and a direct HTTPS image URL for each candidate, "
        "plus title, publisher, caption, matched locked entities, and retrieval time. Do not add facts "
        "to the locked Word content; search results are evidence/material only. Do not return SVG, data, "
        "file, localhost, private-network, or generated-image URLs. Return JSON only.\n"
        f"SEARCH_REQUEST: {json.dumps(request, ensure_ascii=False, sort_keys=True)}\n"
        f"LOCKED_WORD_CONTEXT: {json.dumps(locked, ensure_ascii=False, sort_keys=True)}"
    )


def _safe_https_url(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise SearchMaterialBlocked(f"{label} is not a safe HTTPS URL")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise SearchMaterialBlocked(f"{label} is not a safe HTTPS URL") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise SearchMaterialBlocked(f"{label} is not a safe HTTPS URL")
    if port == 0:
        raise SearchMaterialBlocked(f"{label} has an invalid explicit port")
    return value


def _public_addresses(
    transport: MaterialTransport,
    hostname: str,
    port: int,
    *,
    deadline: float,
) -> tuple[str, ...]:
    normalized_hostname = hostname.rstrip(".").casefold()
    if normalized_hostname == "localhost" or normalized_hostname.endswith(".localhost"):
        raise SearchMaterialBlocked("image host must resolve only to public Internet addresses")
    try:
        literal = ipaddress.ip_address(hostname.strip("[]"))
        addresses = (str(literal),)
    except ValueError:
        addresses = tuple(
            _run_thread_bounded(
                transport.resolve,
                (hostname, port),
                deadline=deadline,
                label="image DNS resolution",
            )
        )
    if not addresses:
        raise SearchMaterialBlocked("image host DNS returned no public Internet address")
    normalized: list[str] = []
    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise SearchMaterialBlocked("image host DNS returned an invalid address") from exc
        if (
            not address.is_global
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
            or address.is_loopback
            or address.is_link_local
            or address.is_private
        ):
            raise SearchMaterialBlocked("image host must resolve only to public Internet addresses")
        normalized.append(str(address))
    return tuple(sorted(set(normalized)))


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    return next((str(value) for key, value in headers.items() if str(key).lower() == lowered), None)


def _fetch(
    url: str,
    *,
    transport: MaterialTransport,
    deadline: float,
    budget: ResourceBudget,
) -> dict[str, Any]:
    current = _safe_https_url(url, "direct image URL")
    for redirect_count in range(MAX_REDIRECTS + 1):
        parsed = urlsplit(current)
        hostname = parsed.hostname or ""
        port = 443 if parsed.port is None else parsed.port
        before = _public_addresses(transport, hostname, port, deadline=deadline)
        reservation = budget.reserve_network()
        try:
            response = transport.get(
                current,
                connect_ip=before[0],
                timeout=_check_deadline(
                    deadline, "image retrieval", code=_CANDIDATE_NETWORK_TIMEOUT,
                ),
                max_bytes=reservation.allowance,
            )
            reservation.settle(len(response.body))
        except TimeoutError as exc:
            reservation.cancel()
            raise SearchMaterialBlocked(
                "image retrieval timed out", code=_CANDIDATE_NETWORK_TIMEOUT,
            ) from exc
        except BaseException:
            reservation.cancel()
            raise
        try:
            after = _public_addresses(transport, hostname, port, deadline=deadline)
        except SearchMaterialBlocked as exc:
            raise SearchMaterialBlocked(
                "image host DNS changed during retrieval; possible DNS rebinding"
            ) from exc
        if after != before:
            raise SearchMaterialBlocked("image host DNS changed during retrieval; possible DNS rebinding")
        _check_deadline(deadline, "image retrieval", code=_CANDIDATE_NETWORK_TIMEOUT)
        if response.status in {301, 302, 303, 307, 308}:
            if redirect_count >= MAX_REDIRECTS:
                raise SearchMaterialBlocked("image redirect limit exceeded")
            location = _header(response.headers, "location")
            if not location:
                raise SearchMaterialBlocked("image redirect has no safe HTTPS location")
            current = _safe_https_url(urljoin(current, location), "image redirect URL")
            continue
        if response.status != 200:
            code = (
                _CANDIDATE_HTTP_UNAVAILABLE
                if response.status in {403, 404}
                else "required_search_material_unavailable"
            )
            raise SearchMaterialBlocked(
                f"image server returned HTTP {response.status}", code=code,
            )
        length = _header(response.headers, "content-length")
        if length is not None:
            try:
                declared = int(length)
            except ValueError as exc:
                raise SearchMaterialBlocked("image content length is invalid") from exc
            if declared < 0 or declared > MAX_FILE_BYTES or declared > reservation.allowance:
                raise SearchMaterialBlocked("image exceeds the 12 MiB download limit")
        if len(response.body) > MAX_FILE_BYTES or len(response.body) > reservation.allowance:
            raise SearchMaterialBlocked("image exceeds the 12 MiB download limit")
        mime = (_header(response.headers, "content-type") or "").split(";", 1)[0].strip().lower()
        if mime not in _ALLOWED_MIME_TO_FORMAT:
            raise SearchMaterialBlocked("response is not an allowed raster image MIME type")
        return {
            "final_url": current,
            "mime_type": mime,
            "content_length": int(length) if length is not None else None,
            "body": bytes(response.body),
            "body_bytes": len(response.body),
        }
    raise SearchMaterialBlocked("image redirect limit exceeded")


def _decode_image_worker(
    payload: bytes,
    declared_mime: str,
    max_pixels: int,
    max_decoded_bytes: int,
) -> tuple[bytes, str, int, int, str]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(__import__("io").BytesIO(payload)) as opened:
                image_format = opened.format
                if image_format not in _FORMAT_TO_EXTENSION:
                    raise SearchMaterialBlocked("payload is not an allowed raster image")
                if _ALLOWED_MIME_TO_FORMAT[declared_mime] != image_format:
                    raise SearchMaterialBlocked("image MIME type does not match decoded payload")
                width, height = opened.size
                if width < 1 or height < 1 or width > MAX_IMAGE_EDGE or height > MAX_IMAGE_EDGE:
                    raise SearchMaterialBlocked("image dimension exceeds the single-edge limit")
                if width * height > MAX_IMAGE_PIXELS or width * height > max_pixels:
                    raise SearchMaterialBlocked("image exceeds the 40 megapixels limit")
                if width * height * 8 > max_decoded_bytes:
                    raise SearchMaterialBlocked("image exceeds the decoded memory budget")
                orientation = opened.getexif().get(274, 1)
                opened.load()
                if orientation in {None, 1}:
                    return payload, image_format, width, height, declared_mime
                normalized = ImageOps.exif_transpose(opened)
                output = __import__("io").BytesIO()
                save_kwargs = {"quality": 95} if image_format == "JPEG" else {}
                normalized.save(output, format=image_format, **save_kwargs)
                width, height = normalized.size
                return output.getvalue(), image_format, width, height, declared_mime
    except SearchMaterialBlocked:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError, ValueError) as exc:
        raise SearchMaterialBlocked("payload could not be decoded as a safe raster image") from exc


_DECODE_PROCESS_PROGRAM = r"""
import io
import json
import sys
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError

payload = sys.stdin.buffer.read()
declared_mime = sys.argv[1]
max_pixels = int(sys.argv[2])
max_decoded_bytes = int(sys.argv[3])
max_edge = int(sys.argv[4])
global_max_pixels = int(sys.argv[5])
allowed_mime_to_format = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
allowed_formats = {"JPEG", "PNG", "WEBP"}
try:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(payload)) as opened:
            image_format = opened.format
            if image_format not in allowed_formats:
                raise ValueError("payload is not an allowed raster image")
            if allowed_mime_to_format[declared_mime] != image_format:
                raise ValueError("image MIME type does not match decoded payload")
            width, height = opened.size
            if width < 1 or height < 1 or width > max_edge or height > max_edge:
                raise ValueError("image dimension exceeds the single-edge limit")
            if width * height > global_max_pixels or width * height > max_pixels:
                raise ValueError("image exceeds the 40 megapixels limit")
            if width * height * 8 > max_decoded_bytes:
                raise ValueError("image exceeds the decoded memory budget")
            orientation = opened.getexif().get(274, 1)
            opened.load()
            stored = payload
            if orientation not in {None, 1}:
                normalized = ImageOps.exif_transpose(opened)
                output = io.BytesIO()
                save_kwargs = {"quality": 95} if image_format == "JPEG" else {}
                normalized.save(output, format=image_format, **save_kwargs)
                stored = output.getvalue()
                width, height = normalized.size
    metadata = json.dumps([image_format, width, height, declared_mime]).encode("utf-8")
    sys.stdout.buffer.write(len(metadata).to_bytes(4, "big"))
    sys.stdout.buffer.write(metadata)
    sys.stdout.buffer.write(stored)
except BaseException as exc:
    sys.stderr.write(str(exc))
    raise SystemExit(1)
"""


def _run_decode_process(
    payload: bytes,
    declared_mime: str,
    *,
    max_pixels: int,
    max_decoded_bytes: int,
    deadline: float,
) -> tuple[bytes, str, int, int, str]:
    _check_deadline(deadline, "image decode")
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _DECODE_PROCESS_PROGRAM,
            declared_mime,
            str(max_pixels),
            str(max_decoded_bytes),
            str(MAX_IMAGE_EDGE),
            str(MAX_IMAGE_PIXELS),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = process.communicate(
            input=payload,
            timeout=_check_deadline(deadline, "image decode"),
        )
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise SearchMaterialBlocked("image decode timed out") from exc
    _check_deadline(deadline, "image decode")
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[:500]
        raise SearchMaterialBlocked(detail or "payload could not be decoded as a safe raster image")
    if len(stdout) < 4:
        raise SearchMaterialBlocked("image decode returned an invalid result")
    metadata_length = int.from_bytes(stdout[:4], "big")
    metadata_end = 4 + metadata_length
    try:
        image_format, width, height, media_type = json.loads(
            stdout[4:metadata_end].decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise SearchMaterialBlocked("image decode returned invalid metadata") from exc
    stored = stdout[metadata_end:]
    if (
        image_format not in _FORMAT_TO_EXTENSION
        or type(width) is not int
        or type(height) is not int
        or media_type != declared_mime
        or not stored
    ):
        raise SearchMaterialBlocked("image decode returned invalid metadata")
    return stored, image_format, width, height, media_type


def _decode_image(
    payload: bytes,
    declared_mime: str,
    *,
    reservation: _CandidateReservation,
    deadline: float,
) -> tuple[bytes, str, int, int, str]:
    return _run_decode_process(
        payload,
        declared_mime,
        max_pixels=reservation.pixel_allowance,
        max_decoded_bytes=reservation.byte_allowance,
        deadline=deadline,
    )


def _safe_evidence_dir(
    project: Path,
    page_number: int,
    *,
    deadline: float | None = None,
) -> Path:
    project = Path(project).resolve(strict=True)
    deadline = deadline if deadline is not None else time.monotonic() + 30.0
    if type(page_number) is not int or page_number < 1:
        raise ValueError("page_number must be positive")
    relative = Path("03_evidence") / f"page_{page_number:03d}" / "search"
    current = project
    for part in relative.parts:
        _check_deadline(deadline, "evidence directory creation")
        current = current / part
        if current.exists() and (_is_link(current) or not current.is_dir()):
            raise SearchMaterialBlocked("search evidence path must be a regular project directory")
        current.mkdir(exist_ok=True)
        resolved = current.resolve()
        if project not in resolved.parents or resolved != current:
            raise SearchMaterialBlocked("search evidence path escaped the project")
    _check_deadline(deadline, "evidence directory creation")
    return current


def _write_immutable(
    path: Path,
    payload: bytes,
    *,
    deadline: float | None = None,
) -> None:
    _atomic_publish(
        path,
        payload,
        deadline=deadline if deadline is not None else time.monotonic() + 30.0,
        accept_existing=False,
    )


def _validated_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    if any(
        not isinstance(candidate.get(key), str) or not str(candidate[key]).strip()
        for key in ("title", "publisher", "retrieved_at")
    ) or not isinstance(candidate.get("caption"), str):
        raise SearchMaterialBlocked("search candidate has incomplete source provenance")
    _safe_https_url(candidate.get("source_page_url"), "source page URL")
    _safe_https_url(candidate.get("direct_image_url"), "direct image URL")
    authority = candidate.get("source_authority")
    basis = candidate.get("authority_basis")
    if authority is not None or basis is not None:
        if (
            authority not in OFFICIAL_SOURCE_AUTHORITIES
            or not isinstance(basis, str)
            or len(basis.strip()) < 10
        ):
            raise SearchMaterialBlocked("search candidate official source authority is invalid")
    matched = candidate.get("matched_entities")
    if not isinstance(matched, list) or any(not isinstance(item, str) or not item for item in matched):
        raise SearchMaterialBlocked("search candidate matched_entities are invalid")
    try:
        retrieved_at = datetime.fromisoformat(str(candidate["retrieved_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise SearchMaterialBlocked("search candidate retrieved_at is invalid") from exc
    if retrieved_at.tzinfo is None:
        raise SearchMaterialBlocked("search candidate retrieved_at is invalid")
    return dict(candidate)


def _is_retryable_candidate_availability_failure(exc: BaseException) -> bool:
    return (
        isinstance(exc, SearchMaterialBlocked)
        and exc.code in {
            _CANDIDATE_HTTP_UNAVAILABLE,
            _CANDIDATE_NETWORK_TIMEOUT,
        }
    )


def download_visual_material(
    project: Path,
    *,
    page_number: int,
    material_id: str,
    directive_id: str,
    query: str | None = None,
    candidates: Sequence[Mapping[str, Any]],
    timeout: float,
    deadline: float | None = None,
    budget: ResourceBudget | None = None,
    transport: MaterialTransport | None = None,
    invocation_bundle_path: str | None = None,
    invocation_bundle_sha256: str | None = None,
    invocation_bundle_signature: str | None = None,
    invocation_bundle_sealed_sha256: str | None = None,
    batch_receipt_reference: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Download at most three HTTPS raster candidates into project-local evidence."""
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        raise ValueError("search candidates must be an array")
    if len(candidates) > MAX_IMAGES_PER_PAGE:
        raise SearchMaterialBlocked("no more than three selected images are allowed per page")
    if not isinstance(material_id, str) or not _MATERIAL_ID.fullmatch(material_id):
        raise ValueError("stable search material_id is required")
    if not isinstance(directive_id, str) or not directive_id:
        raise ValueError("directive_id is required")
    project_root = Path(project).resolve(strict=True)
    # The legacy keyword is now explicitly a caller cancellation deadline,
    # never an internally-created cumulative retrieval deadline.
    cancel_deadline = deadline
    setup_deadline = _operation_deadline(timeout, cancel_deadline)
    network = transport or _NetworkTransport()
    output_dir = _cancel_aware(
        cancel_deadline,
        lambda: _safe_evidence_dir(
            project_root, page_number, deadline=setup_deadline,
        ),
    )
    page_budget = budget or ResourceBudget()
    materials: list[dict[str, Any]] = []
    failures: list[str] = []
    for candidate_number, raw in enumerate(candidates, start=1):
        candidate_deadline = _operation_deadline(timeout, cancel_deadline)
        reservation: _CandidateReservation | None = None
        candidate_stage = "validation"
        try:
            reservation = page_budget.reserve_candidate()
            if not isinstance(raw, Mapping):
                raise SearchMaterialBlocked("search candidate must be an object")
            candidate = _validated_candidate(raw)
            candidate_stage = "retrieval"
            response = _fetch(
                candidate["direct_image_url"],
                transport=network,
                deadline=candidate_deadline,
                budget=page_budget,
            )
            candidate_stage = "decode"
            stored, image_format, width, height, media_type = _decode_image(
                response["body"],
                response["mime_type"],
                reservation=reservation,
                deadline=candidate_deadline,
            )
            reservation.settle_dimensions(width, height)
            candidate_stage = "integrity"
            digest = hashlib.sha256(stored).hexdigest()
            extension = _FORMAT_TO_EXTENSION[image_format]
            filename = f"{material_id}-{candidate_number:02d}-{digest[:12]}{extension}"
            target = output_dir / filename
            _write_immutable(
                target, stored,
                deadline=_operation_deadline(timeout, cancel_deadline),
            )
            local_path = target.relative_to(project_root).as_posix()
            identity = {
                "material_id": material_id,
                "directive_id": directive_id,
                "source_page_url": candidate["source_page_url"],
                "original_image_url": candidate["direct_image_url"],
                "final_image_url": response["final_url"],
                "local_path": local_path,
                "sha256": digest,
            }
            evidence_id = "search-image-" + hashlib.sha256(_canonical_bytes(identity)).hexdigest()[:16]
            material = {
                "evidence_id": evidence_id,
                "asset_id": material_id,
                "material_id": material_id,
                "directive_id": directive_id,
                "query": query or "",
                "source_page_url": candidate["source_page_url"],
                "direct_image_url": candidate["direct_image_url"],
                "final_image_url": response["final_url"],
                "title": candidate["title"],
                "publisher": candidate["publisher"],
                **(
                    {
                        "source_authority": candidate["source_authority"],
                        "authority_basis": candidate["authority_basis"],
                    }
                    if "source_authority" in candidate else {}
                ),
                "caption": candidate["caption"],
                "matched_entities": list(candidate["matched_entities"]),
                "retrieved_at": candidate["retrieved_at"],
                "local_path": local_path,
                "media_type": media_type,
                "image_format": image_format,
                "width": width,
                "height": height,
                "sha256": digest,
                "http_mime_type": response["mime_type"],
                "http_content_length": response["content_length"],
                "http_body_bytes": response["body_bytes"],
                "record_version": "search-material-attestation-v1",
                "record_timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            if invocation_bundle_path is not None:
                material["invocation_bundle_path"] = invocation_bundle_path
            if invocation_bundle_sha256 is not None:
                material["invocation_bundle_sha256"] = invocation_bundle_sha256
            if invocation_bundle_signature is not None:
                material["invocation_bundle_signature"] = invocation_bundle_signature
            if invocation_bundle_sealed_sha256 is not None:
                material["invocation_bundle_sealed_sha256"] = invocation_bundle_sealed_sha256
            if batch_receipt_reference is not None:
                material.update({
                    "batch_receipt_path": batch_receipt_reference.get("path"),
                    "batch_receipt_sha256": batch_receipt_reference.get("sha256"),
                    "batch_receipt_signature": batch_receipt_reference.get("signature"),
                    "batch_receipt_sealed_sha256": batch_receipt_reference.get("sealed_sha256"),
                    "batch_binding_sha256": batch_receipt_reference.get("binding_sha256"),
                })
            attestation = {
                "record_version": material["record_version"],
                "record_timestamp": material["record_timestamp"],
                "evidence_id": evidence_id,
                "material_id": material_id,
                "directive_id": directive_id,
                "query": query or "",
                "invocation": {
                    "path": invocation_bundle_path,
                    "sha256": invocation_bundle_sha256,
                    "sealed_sha256": invocation_bundle_sealed_sha256,
                    "signature": invocation_bundle_signature,
                },
                "source": {
                    "page_url": candidate["source_page_url"],
                    "original_image_url": candidate["direct_image_url"],
                    "final_image_url": response["final_url"],
                    "title": candidate["title"],
                    "publisher": candidate["publisher"],
                    **(
                        {
                            "source_authority": candidate["source_authority"],
                            "authority_basis": candidate["authority_basis"],
                        }
                        if "source_authority" in candidate else {}
                    ),
                    "caption": candidate["caption"],
                    "matched_entities": list(candidate["matched_entities"]),
                    "retrieved_at": candidate["retrieved_at"],
                },
                "http": {
                    "mime_type": response["mime_type"],
                    "content_length": response["content_length"],
                    "body_bytes": response["body_bytes"],
                },
                "image": {
                    "format": image_format,
                    "media_type": media_type,
                    "width": width,
                    "height": height,
                    "local_path": local_path,
                    "sha256": digest,
                },
            }
            if batch_receipt_reference is not None:
                attestation.pop("invocation", None)
                attestation["batch_receipt"] = {
                    "path": batch_receipt_reference.get("path"),
                    "sha256": batch_receipt_reference.get("sha256"),
                    "sealed_sha256": batch_receipt_reference.get("sealed_sha256"),
                    "signature": batch_receipt_reference.get("signature"),
                    "batch_id": batch_receipt_reference.get("batch_id"),
                    "binding": dict(batch_receipt_reference.get("binding", {})),
                    "binding_sha256": batch_receipt_reference.get("binding_sha256"),
                    "result_sha256": batch_receipt_reference.get("result_sha256"),
                    "request_identity": dict(
                        batch_receipt_reference.get("request_identity", {})
                    ),
                }
            attestation_sha = hashlib.sha256(_canonical_bytes(attestation)).hexdigest()
            signed = {"attestation_sha256": attestation_sha, "attestation": attestation}
            signature = hmac.new(
                _attestation_key(
                    project_root,
                    deadline=_operation_deadline(timeout, cancel_deadline),
                ),
                _canonical_bytes(signed),
                hashlib.sha256,
            ).hexdigest()
            record: dict[str, Any] = {
                "artifact_version": "search-material-signed-attestation-v1",
                **signed,
                "signature": signature,
            }
            record_payload = (
                json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
                + b"\n"
            )
            record_path = output_dir / f"{evidence_id}-{attestation_sha[:12]}.attestation.json"
            _write_immutable(
                record_path, record_payload,
                deadline=_operation_deadline(timeout, cancel_deadline),
            )
            material["material_attestation_path"] = record_path.relative_to(project_root).as_posix()
            material["material_attestation_sha256"] = _sha256_file(record_path)
            material["material_attestation_digest"] = attestation_sha
            material["material_attestation_signature"] = signature
            _raise_if_cancelled(cancel_deadline)
            reservation.finish(selected=True)
            materials.append(material)
        except (SearchMaterialBlocked, OSError, TypeError, ValueError) as exc:
            if reservation is not None:
                reservation.finish(selected=False)
            _raise_if_cancelled(cancel_deadline)
            if (
                candidate_stage == "validation"
                and materials
                and isinstance(exc, SearchMaterialBlocked)
                and str(exc) == "page image count budget is exhausted"
            ):
                break
            if (
                candidate_stage == "retrieval"
                and _is_retryable_candidate_availability_failure(exc)
            ):
                failures.append(str(exc))
                continue
            raise
        if len(materials) >= page_budget.max_images:
            break
    if candidates and not materials:
        detail = failures[0] if failures else "no valid image candidate"
        raise SearchMaterialBlocked(detail)
    return materials


def verify_search_material(
    project: Path,
    material: Mapping[str, Any],
    *,
    expected_material_id: str,
    expected_directive_id: str,
    expected_query: str,
    deadline: float,
) -> dict[str, Any]:
    """Re-read and verify the keyed attestation, invocation, and current raster file."""
    _check_deadline(deadline, "material verification")
    project = Path(project).resolve(strict=True)
    if not isinstance(material, Mapping):
        raise SearchMaterialBlocked("search material reference must be an object")
    record_relative = material.get("material_attestation_path")
    if not isinstance(record_relative, str):
        raise SearchMaterialBlocked("search material has no keyed attestation path")
    record_path = _project_regular_file(project, record_relative, "material attestation")
    record_file_sha = _sha256_file(record_path)
    if record_file_sha != material.get("material_attestation_sha256"):
        raise SearchMaterialBlocked("material attestation file digest mismatch")
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SearchMaterialBlocked("material attestation is unreadable") from exc
    if not isinstance(record, dict) or record.get("artifact_version") != "search-material-signed-attestation-v1":
        raise SearchMaterialBlocked("material attestation record version is invalid")
    attestation = record.get("attestation")
    digest = record.get("attestation_sha256")
    signature = record.get("signature")
    if not isinstance(attestation, dict) or not isinstance(digest, str) or not isinstance(signature, str):
        raise SearchMaterialBlocked("material attestation is incomplete")
    if hashlib.sha256(_canonical_bytes(attestation)).hexdigest() != digest:
        raise SearchMaterialBlocked("material attestation digest mismatch")
    signed = {"attestation_sha256": digest, "attestation": attestation}
    expected_signature = hmac.new(
        _attestation_key(project, deadline=deadline),
        _canonical_bytes(signed),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise SearchMaterialBlocked("material attestation signature mismatch")
    if (
        material.get("material_attestation_digest") != digest
        or material.get("material_attestation_signature") != signature
    ):
        raise SearchMaterialBlocked("material attestation reference mismatch")
    if attestation.get("record_version") != "search-material-attestation-v1":
        raise SearchMaterialBlocked("material attestation version is invalid")
    try:
        recorded_at = datetime.fromisoformat(
            str(attestation.get("record_timestamp", "")).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise SearchMaterialBlocked("material attestation timestamp is invalid") from exc
    if recorded_at.tzinfo is None:
        raise SearchMaterialBlocked("material attestation timestamp is invalid")
    if (
        attestation.get("material_id") != expected_material_id
        or attestation.get("directive_id") != expected_directive_id
        or attestation.get("query") != expected_query
        or not isinstance(attestation.get("evidence_id"), str)
    ):
        raise SearchMaterialBlocked("material attestation authority identity mismatch")
    batch_reference = attestation.get("batch_receipt")
    batch_flattened: dict[str, Any] = {}
    invocation_flattened: dict[str, Any] = {}
    if batch_reference is None:
        invocation_reference = attestation.get("invocation")
        if not isinstance(invocation_reference, dict):
            raise SearchMaterialBlocked("material attestation invocation reference is missing")
        invocation_path = invocation_reference.get("path")
        invocation_sha = invocation_reference.get("sha256")
        if not isinstance(invocation_path, str) or not isinstance(invocation_sha, str):
            raise SearchMaterialBlocked("material attestation invocation reference is incomplete")
        invocation = _read_signed_invocation(
            project, invocation_path, deadline=deadline, expected_sha256=invocation_sha,
        )
        if (
            invocation.get("material_id") != expected_material_id
            or invocation.get("directive_id") != expected_directive_id
            or invocation.get("query") != expected_query
            or invocation.get("signature") != invocation_reference.get("signature")
            or invocation.get("sealed_sha256") != invocation_reference.get("sealed_sha256")
        ):
            raise SearchMaterialBlocked("material attestation invocation identity mismatch")
        invocation_flattened = {
            "invocation_bundle_path": invocation["path"],
            "invocation_bundle_sha256": invocation["file_sha256"],
            "invocation_bundle_signature": invocation["signature"],
            "invocation_bundle_sealed_sha256": invocation["sealed_sha256"],
        }
    else:
        if attestation.get("invocation") is not None:
            raise SearchMaterialBlocked("batch material must not claim a synthetic single invocation")
        if not isinstance(batch_reference, Mapping):
            raise SearchMaterialBlocked("material batch receipt reference is invalid")
        binding = batch_reference.get("binding")
        request_identity = batch_reference.get("request_identity")
        expected_binding = {
            "material_id": expected_material_id,
            "directive_id": expected_directive_id,
            "entity": binding.get("entity") if isinstance(binding, Mapping) else None,
        }
        if not isinstance(binding, Mapping) or dict(binding) != expected_binding:
            raise SearchMaterialBlocked("material batch receipt binding mismatch")
        binding_sha = hashlib.sha256(_canonical_bytes(expected_binding)).hexdigest()
        if batch_reference.get("binding_sha256") != binding_sha:
            raise SearchMaterialBlocked("material batch receipt binding digest mismatch")
        if (
            not isinstance(request_identity, Mapping)
            or set(request_identity) != {
                "material_id", "directive_id", "entity", "query", "required",
                "material_role", "max_results",
            }
            or request_identity.get("material_id") != expected_material_id
            or request_identity.get("directive_id") != expected_directive_id
            or request_identity.get("entity") != expected_binding["entity"]
            or request_identity.get("query") != expected_query
        ):
            raise SearchMaterialBlocked("material batch request identity mismatch")
        batch = _read_signed_batch_receipt(
            project,
            str(batch_reference.get("path")),
            deadline=deadline,
            expected_sha256=str(batch_reference.get("sha256")),
        )
        key = _binding(expected_binding)
        result_item = batch["bindings"].get(key)
        signed_request = next(
            (item for item in batch["request"]["requests"] if _binding(item) == key), None,
        )
        if (
            result_item is None
            or signed_request is None
            or dict(signed_request) != dict(request_identity)
            or hashlib.sha256(_canonical_bytes(result_item)).hexdigest()
            != batch_reference.get("result_sha256")
            or batch.get("batch_id") != batch_reference.get("batch_id")
            or batch.get("signature") != batch_reference.get("signature")
            or batch.get("sealed_sha256") != batch_reference.get("sealed_sha256")
        ):
            raise SearchMaterialBlocked("material batch receipt result binding mismatch")
        batch_flattened = {
            "batch_receipt_path": batch["path"],
            "batch_receipt_sha256": batch["file_sha256"],
            "batch_receipt_signature": batch["signature"],
            "batch_receipt_sealed_sha256": batch["sealed_sha256"],
            "batch_binding_sha256": binding_sha,
            "batch_page_authority_sha256": batch["request"]["page_authority_sha256"],
            "batch_request_identity": dict(request_identity),
        }
    source = attestation.get("source")
    http = attestation.get("http")
    image = attestation.get("image")
    if not isinstance(source, dict) or not isinstance(http, dict) or not isinstance(image, dict):
        raise SearchMaterialBlocked("material attestation provenance is incomplete")
    local_relative = image.get("local_path")
    if not isinstance(local_relative, str):
        raise SearchMaterialBlocked("material attestation local path is missing")
    image_path = _project_regular_file(project, local_relative, "search material image")
    file_sha = _sha256_file(image_path)
    if file_sha != image.get("sha256"):
        raise SearchMaterialBlocked("search material file SHA-256 mismatch")
    payload = image_path.read_bytes()
    if len(payload) > MAX_FILE_BYTES:
        raise SearchMaterialBlocked("search material file exceeds the size limit")
    decoded = _run_isolated(
        _decode_image_worker,
        (payload, image.get("media_type"), MAX_IMAGE_PIXELS, MAX_IMAGE_PIXELS * 8),
        deadline=deadline,
        label="material image verification",
    )
    stored, image_format, width, height, media_type = decoded
    if (
        stored != payload
        or image_format != image.get("format")
        or media_type != image.get("media_type")
        or width != image.get("width")
        or height != image.get("height")
    ):
        raise SearchMaterialBlocked("search material decoded dimensions or format changed")
    body_bytes = http.get("body_bytes")
    content_length = http.get("content_length")
    if (
        http.get("mime_type") != media_type
        or not isinstance(body_bytes, int)
        or body_bytes < 1
        or (content_length is not None and content_length != body_bytes)
    ):
        raise SearchMaterialBlocked("material HTTP metadata is inconsistent")
    flattened = {
        "evidence_id": attestation["evidence_id"],
        "asset_id": expected_material_id,
        "material_id": expected_material_id,
        "directive_id": expected_directive_id,
        "query": expected_query,
        "source_page_url": source.get("page_url"),
        "direct_image_url": source.get("original_image_url"),
        "final_image_url": source.get("final_image_url"),
        "title": source.get("title"),
        "publisher": source.get("publisher"),
        "source_authority": source.get("source_authority"),
        "authority_basis": source.get("authority_basis"),
        "caption": source.get("caption"),
        "matched_entities": source.get("matched_entities"),
        "retrieved_at": source.get("retrieved_at"),
        "local_path": local_relative,
        "media_type": media_type,
        "image_format": image_format,
        "width": width,
        "height": height,
        "sha256": file_sha,
        "record_version": attestation["record_version"],
        "record_timestamp": attestation["record_timestamp"],
        "material_attestation_path": record_relative,
        "material_attestation_sha256": record_file_sha,
        "material_attestation_digest": digest,
        "material_attestation_signature": signature,
        **invocation_flattened,
        **batch_flattened,
    }
    for key, value in flattened.items():
        if key in material and material.get(key) != value:
            raise SearchMaterialBlocked(f"search material field changed after attestation: {key}")
    _check_deadline(deadline, "material verification")
    return flattened


def _write_invocation_bundle(
    project: Path,
    *,
    page_number: int,
    material_id: str,
    directive_id: str,
    query: str,
    request: Mapping[str, Any],
    result: CodexStructuredResult,
    timeout: float,
    cancel_deadline: float | None,
) -> str:
    _raise_if_cancelled(cancel_deadline)
    directory = _safe_evidence_dir(
        project, page_number, deadline=_operation_deadline(timeout, cancel_deadline),
    )
    request_payload = {
        "artifact_version": "codex-web-material-request-v1",
        **dict(request),
    }
    response_payload = {
        "artifact_version": "codex-web-material-response-v1",
        "status": "completed",
        "value": dict(result.value),
        "safe_trace": dict(result.safe_trace),
    }
    request_path = directory / f"{material_id}-request-{hashlib.sha256(_canonical_bytes(request_payload)).hexdigest()[:12]}.json"
    response_path = directory / f"{material_id}-response-{hashlib.sha256(_canonical_bytes(response_payload)).hexdigest()[:12]}.json"
    _write_immutable(
        request_path,
        json.dumps(request_payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n",
        deadline=_operation_deadline(timeout, cancel_deadline),
    )
    _write_immutable(
        response_path,
        json.dumps(response_payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n",
        deadline=_operation_deadline(timeout, cancel_deadline),
    )
    bundle: dict[str, Any] = {
        "artifact_version": _SEARCH_INVOCATION_VERSION,
        "status": "completed",
        "page_number": page_number,
        "material_id": material_id,
        "directive_id": directive_id,
        "query": query,
        "role": "visual-material-search",
        "web_search": "live",
        "thread_id": result.thread_id,
        "turn_id": result.turn_id,
        "model": result.model,
        "model_provider": result.model_provider,
        "auth_mode": result.auth_mode,
        "plan_type": result.plan_type,
        "usage": dict(result.usage),
        "safe_trace": dict(result.safe_trace),
        "model_response": dict(result.value),
        "request": {
            "path": request_path.relative_to(Path(project).resolve(strict=True)).as_posix(),
            "sha256": _sha256_file(request_path),
        },
        "raw_response": {
            "path": response_path.relative_to(Path(project).resolve(strict=True)).as_posix(),
            "sha256": _sha256_file(response_path),
        },
    }
    bundle["sealed_sha256"] = hashlib.sha256(_canonical_bytes(bundle)).hexdigest()
    bundle["signature"] = hmac.new(
        _attestation_key(
            project, deadline=_operation_deadline(timeout, cancel_deadline),
        ),
        _canonical_bytes(bundle), hashlib.sha256,
    ).hexdigest()
    payload = json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    target = directory / f"{material_id}-invocation-{bundle['sealed_sha256'][:12]}.json"
    _write_immutable(
        target, payload, deadline=_operation_deadline(timeout, cancel_deadline),
    )
    return target.relative_to(Path(project).resolve(strict=True)).as_posix()


def _search_cache_identity(
    *, page_context: Mapping[str, Any], directive_id: str, material_id: str,
    query: str, required: bool, budget: ResourceBudget,
) -> dict[str, Any]:
    return {
        "artifact_version": _SEARCH_CACHE_VERSION,
        "page_number": int(page_context["page_number"]),
        "page_authority_sha256": hashlib.sha256(
            json.dumps(
                dict(page_context), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "directive_id": directive_id,
        "material_id": material_id,
        "query": query,
        "required": required,
        "resource_limits": {
            "max_images": budget.max_images,
            "max_network_bytes": budget.max_network_bytes,
            "max_decoded_pixels": budget.max_decoded_pixels,
            "max_decoded_bytes": budget.max_decoded_bytes,
        },
        "provider_capability": dict(_SEARCH_CAPABILITY),
    }


def _project_discovery_identity(
    *, directive_id: str, material_id: str, query: str, required: bool,
    max_results: int,
) -> dict[str, Any]:
    """Identity for a live discovery that can be safely reused between pages.

    Page text and page number deliberately do not appear here.  They belong to
    the page-local cache and authority record; the discovery itself is only the
    user-approved material request.
    """
    return {
        "artifact_version": _PROJECT_DISCOVERY_VERSION,
        "directive_id": directive_id,
        "material_id": material_id,
        "query": query,
        "required": required,
        "max_results": max_results,
        "provider_capability": dict(_SEARCH_CAPABILITY),
    }


def _project_discovery_path(
    project: Path, identity: Mapping[str, Any], *, deadline: float,
) -> Path:
    project = Path(project).resolve(strict=True)
    directory = project / "03_evidence" / "project_material_registry"
    for parent in (project / "03_evidence", directory):
        if parent.exists() and (_is_link(parent) or not parent.is_dir()):
            raise SearchMaterialBlocked("project material registry path must be a regular directory")
        parent.mkdir(exist_ok=True)
        resolved = parent.resolve()
        if project not in resolved.parents or resolved != parent:
            raise SearchMaterialBlocked("project material registry path escaped the project")
    _check_deadline(deadline, "project material registry")
    return directory / (hashlib.sha256(_canonical_bytes(identity)).hexdigest() + ".json")


def _read_project_discovery(
    project: Path, path: Path, *, identity: Mapping[str, Any], deadline: float,
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SearchMaterialBlocked("project material registry is unreadable") from exc
    if not isinstance(record, dict) or record.get("identity") != dict(identity):
        raise SearchMaterialBlocked("project material registry identity mismatch")
    materials = record.get("materials")
    if not isinstance(materials, list) or not all(isinstance(item, dict) for item in materials):
        raise SearchMaterialBlocked("project material registry materials are invalid")
    if type(record.get("origin_page_number")) is not int or not isinstance(
        record.get("origin_page_authority_sha256"), str,
    ):
        raise SearchMaterialBlocked("project material registry origin is invalid")
    return record


def _write_project_discovery(
    project: Path, path: Path, *, identity: Mapping[str, Any],
    materials: Sequence[Mapping[str, Any]], page_context: Mapping[str, Any], deadline: float,
) -> None:
    record = {"artifact_version": _PROJECT_DISCOVERY_VERSION, "identity": dict(identity),
              "materials": [dict(item) for item in materials],
              "origin_page_number": int(page_context["page_number"]),
              "origin_page_authority_sha256": hashlib.sha256(_canonical_bytes(dict(page_context))).hexdigest()}
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    _write_immutable(path, payload, deadline=deadline)


def _page_local_discovery_reference(
    project: Path, material: Mapping[str, Any], *, page_number: int,
    deadline: float,
) -> dict[str, Any]:
    """Copy a verified evidence record into the consuming page's evidence area."""
    project = Path(project).resolve(strict=True)
    source_path = _project_regular_file(project, str(material["material_attestation_path"]), "material attestation")
    source_payload = source_path.read_bytes()
    target_dir = _safe_evidence_dir(project, page_number, deadline=deadline)
    token = hashlib.sha256(source_payload + str(page_number).encode("ascii")).hexdigest()[:16]
    target = target_dir / f"reused-{token}.attestation.json"
    if not target.exists():
        _write_immutable(target, source_payload, deadline=deadline)
    referenced = dict(material)
    referenced["material_attestation_path"] = target.relative_to(project).as_posix()
    referenced["material_attestation_sha256"] = _sha256_file(target)
    return referenced


def _batch_search_cache_identity(
    *, page_context: Mapping[str, Any], request: Mapping[str, Any], budget: ResourceBudget,
) -> dict[str, Any]:
    identity = _search_cache_identity(
        page_context=page_context,
        directive_id=str(request["directive_id"]),
        material_id=str(request["material_id"]),
        query=str(request["query"]),
        required=bool(request["required"]),
        budget=budget,
    )
    identity.update({
        "artifact_version": "codex-web-material-cache-v2",
        "entity": request["entity"],
        "material_role": request["material_role"],
        "max_results": request["max_results"],
        "discovery_protocol": _BATCH_RECEIPT_VERSION,
    })
    return identity


def _single_search_lease_ttl_seconds(search_timeout: float) -> float:
    """Cover search, evidence, three candidates, and the final cache commit."""
    bounded_operations = 1 + 1 + MAX_IMAGES_PER_PAGE + 1
    return max(900.0, bounded_operations * search_timeout + 300.0)


def _batch_lease_ttl_seconds(
    *, request_count: int, shard_size: int, max_search_concurrency: int,
    max_download_concurrency: int, search_timeout: float,
) -> float:
    """Cover every bounded operation without turning lease age into a page timeout."""
    shard_count = (request_count + shard_size - 1) // shard_size
    search_waves = (shard_count + max_search_concurrency - 1) // max_search_concurrency
    largest_shard = min(request_count, shard_size)
    download_waves = (
        largest_shard + max_download_concurrency - 1
    ) // max_download_concurrency
    per_search_wave = (
        search_timeout
        + _MATERIAL_RETRIEVAL_GRACE_SECONDS  # signed receipt
        + download_waves * MAX_IMAGES_PER_PAGE * _MATERIAL_RETRIEVAL_GRACE_SECONDS
    )
    serial_cache_commits = request_count * _MATERIAL_RETRIEVAL_GRACE_SECONDS
    return max(900.0, search_waves * per_search_wave + serial_cache_commits + 300.0)


def _search_cache_path(project: Path, identity: Mapping[str, Any], *, deadline: float) -> Path:
    directory = _safe_evidence_dir(project, int(identity["page_number"]), deadline=deadline)
    key = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    return directory / f"search-cache-{key}.json"


def _search_lease_path(cache_path: Path) -> Path:
    return cache_path.with_name(f"{cache_path.name}.lease")


def _acquire_search_lease(cache_path: Path, *, ttl_seconds: float = 900.0) -> str:
    if ttl_seconds <= 0:
        raise ValueError("search lease ttl must be positive")
    token = secrets.token_hex(32)
    lease_path = _search_lease_path(cache_path)
    takeover_path = lease_path.with_name(f"{lease_path.name}.takeover")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)

    def write_new_lease() -> str:
        descriptor = os.open(lease_path, flags, 0o600)
        payload = json.dumps(
            {"token": token, "expires_at": time.time() + ttl_seconds},
            sort_keys=True, separators=(",", ":"),
        ).encode("ascii")
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return token

    if takeover_path.exists():
        raise FileExistsError(str(lease_path))
    try:
        return write_new_lease()
    except FileExistsError:
        if _is_link(lease_path) or not lease_path.is_file():
            raise
        now = time.time()
        stale = now - lease_path.stat().st_mtime >= ttl_seconds
        try:
            payload = json.loads(lease_path.read_text(encoding="ascii"))
            stale = isinstance(payload, Mapping) and float(payload.get("expires_at", 0)) <= now
        except (ValueError, TypeError, json.JSONDecodeError):
            pass
        if not stale:
            raise

    guard = os.open(takeover_path, flags, 0o600)
    os.close(guard)
    tombstone = lease_path.with_name(f"{lease_path.name}.stale-{secrets.token_hex(8)}")
    try:
        if _is_link(lease_path) or not lease_path.is_file():
            raise FileExistsError(str(lease_path))
        now = time.time()
        contents = lease_path.read_text(encoding="ascii")
        stale = now - lease_path.stat().st_mtime >= ttl_seconds
        try:
            stale = float(json.loads(contents).get("expires_at", 0)) <= now
        except (AttributeError, ValueError, TypeError, json.JSONDecodeError):
            pass
        if not stale:
            raise FileExistsError(str(lease_path))
        os.replace(lease_path, tombstone)
        return write_new_lease()
    finally:
        try:
            tombstone.unlink()
        except FileNotFoundError:
            pass
        try:
            takeover_path.unlink()
        except FileNotFoundError:
            pass


def _release_search_lease(cache_path: Path, token: str) -> None:
    lease_path = _search_lease_path(cache_path)
    try:
        if _is_link(lease_path) or not lease_path.is_file():
            return
        contents = lease_path.read_text(encoding="ascii")
        try:
            stored_token = json.loads(contents).get("token")
        except (AttributeError, json.JSONDecodeError):
            stored_token = contents
        if stored_token != token:
            return
        lease_path.unlink()
    except FileNotFoundError:
        return


def _assert_search_lease(cache_path: Path, token: str) -> None:
    lease_path = _search_lease_path(cache_path)
    if _is_link(lease_path) or not lease_path.is_file():
        raise SearchMaterialBlocked("search lease was lost before cache commit")
    contents = lease_path.read_text(encoding="ascii")
    try:
        stored_token = json.loads(contents).get("token")
    except (AttributeError, json.JSONDecodeError):
        stored_token = contents
    if stored_token != token:
        raise SearchMaterialBlocked("search lease ownership changed before cache commit")


def _search_cache_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in {"sealed_sha256", "signature"}}


def _verify_cached_search(
    project: Path,
    path: Path,
    *,
    identity: Mapping[str, Any],
    deadline: float,
    budget: ResourceBudget,
) -> list[dict[str, Any]]:
    try:
        value = json.loads(_project_regular_file(project, path, "search cache").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SearchMaterialBlocked("committed search cache is unreadable") from exc
    if not isinstance(value, dict) or set(value) != {
        "artifact_version", "identity", "materials", "sealed_sha256", "signature",
    }:
        raise SearchMaterialBlocked("committed search cache shape is invalid")
    payload = _search_cache_payload(value)
    if (
        value.get("artifact_version") != _SEARCH_CACHE_VERSION
        or value.get("identity") != dict(identity)
        or value.get("sealed_sha256") != hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        or not isinstance(value.get("signature"), str)
        or not hmac.compare_digest(
            value["signature"],
            hmac.new(_attestation_key(project, deadline=deadline), _canonical_bytes({
                **payload, "sealed_sha256": value["sealed_sha256"],
            }), hashlib.sha256).hexdigest(),
        )
    ):
        raise SearchMaterialBlocked("committed search cache signature or identity mismatch")
    materials = value.get("materials")
    if not isinstance(materials, list) or any(not isinstance(item, Mapping) for item in materials):
        raise SearchMaterialBlocked("committed search cache materials are invalid")
    verified: list[dict[str, Any]] = []
    for item in materials:
        if item.get("batch_receipt_path") is None:
            invocation = _read_signed_invocation(
                project, str(item.get("invocation_bundle_path")), deadline=deadline,
                expected_sha256=str(item.get("invocation_bundle_sha256")),
            )
            verified_request = _read_closed_json_file(
                project, invocation.get("request"), label="App Server search request",
                deadline=deadline,
            )
            if (
                invocation.get("material_id") != identity["material_id"]
                or invocation.get("directive_id") != identity["directive_id"]
                or invocation.get("query") != identity["query"]
                or invocation.get("auth_mode") != "chatgpt"
                or invocation.get("role") != "visual-material-search"
                or invocation.get("web_search") != "live"
                or invocation.get("page_number") != identity["page_number"]
                or verified_request.get("page_authority_sha256")
                != identity["page_authority_sha256"]
                or invocation.get("signature") != item.get("invocation_bundle_signature")
                or invocation.get("sealed_sha256") != item.get("invocation_bundle_sealed_sha256")
            ):
                raise SearchMaterialBlocked("committed search invocation authority mismatch")
        material = verify_search_material(
            project, item,
            expected_material_id=str(identity["material_id"]),
            expected_directive_id=str(identity["directive_id"]),
            expected_query=str(identity["query"]),
            deadline=deadline,
        )
        if (
            item.get("batch_receipt_path") is not None
            and (
                material.get("batch_page_authority_sha256") != identity["page_authority_sha256"]
                or material.get("batch_request_identity") != {
                    "material_id": identity["material_id"],
                    "directive_id": identity["directive_id"],
                    "entity": identity["entity"],
                    "query": identity["query"],
                    "required": identity["required"],
                    "material_role": identity["material_role"],
                    "max_results": identity["max_results"],
                }
            )
        ):
            raise SearchMaterialBlocked("committed batch search request authority mismatch")
        budget.adopt_cached(width=int(material["width"]), height=int(material["height"]))
        verified.append(material)
    if identity["required"] and not verified:
        raise SearchMaterialBlocked("committed required search cache has no material")
    return verified


def _write_search_cache(
    project: Path, path: Path, *, identity: Mapping[str, Any],
    materials: Sequence[Mapping[str, Any]], deadline: float,
    lease_token: str | None = None,
) -> None:
    if lease_token is not None:
        _assert_search_lease(path, lease_token)
    value: dict[str, Any] = {
        "artifact_version": _SEARCH_CACHE_VERSION,
        "identity": dict(identity),
        "materials": [dict(item) for item in materials],
    }
    value["sealed_sha256"] = hashlib.sha256(_canonical_bytes(value)).hexdigest()
    value["signature"] = hmac.new(
        _attestation_key(project, deadline=deadline), _canonical_bytes(value), hashlib.sha256,
    ).hexdigest()
    _write_immutable(
        path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n",
        deadline=deadline,
    )


def search_visual_material(
    project: Path,
    *,
    directive: Mapping[str, Any],
    page_context: Mapping[str, Any],
    timeout: float,
    deadline: float | None = None,
    budget: ResourceBudget | None = None,
    invoke: Callable[..., CodexStructuredResult] | None = None,
    transport: MaterialTransport | None = None,
    _lease_token: str | None = None,
    max_results: int | None = None,
) -> list[dict[str, Any]]:
    """Use Codex App Server live search and return bounded project-local images."""
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    project = Path(project).resolve(strict=True)
    if not isinstance(page_context, Mapping):
        raise ValueError("page_context must be an object")
    page_number = page_context.get("page_number")
    if type(page_number) is not int or page_number < 1:
        raise ValueError("page_context page_number must be positive")
    directive_id, query, required, material_id = _request_identity(directive)
    directive_limit = _directive_value(directive, "max_results")
    result_limit = max_results if max_results is not None else (
        directive_limit if directive_limit is not None else MAX_IMAGES_PER_PAGE
    )
    if type(result_limit) is not int or not 1 <= result_limit <= MAX_IMAGES_PER_PAGE:
        raise ValueError("max_results must be between 1 and the gateway per-page limit")
    invoke_model = invoke or invoke_structured
    # A supplied deadline is caller cancellation authority.  None means no
    # total pipeline deadline; every stage still has its own watchdog.
    cancel_deadline = deadline
    page_budget = budget or ResourceBudget()
    cache_identity = _search_cache_identity(
        page_context=page_context, directive_id=directive_id, material_id=material_id,
        query=query, required=required, budget=page_budget,
    )
    discovery_identity = _project_discovery_identity(
        directive_id=directive_id, material_id=material_id, query=query,
        required=required, max_results=result_limit,
    )
    discovery_path = _cancel_aware(
        cancel_deadline,
        lambda: _project_discovery_path(
            project, discovery_identity,
            deadline=_operation_deadline(timeout, cancel_deadline),
        ),
    )
    cache_path = _cancel_aware(
        cancel_deadline,
        lambda: _search_cache_path(
            project, cache_identity,
            deadline=_operation_deadline(timeout, cancel_deadline),
        ),
    )
    if cache_path.exists():
        return _cancel_aware(
            cancel_deadline,
            lambda: _verify_cached_search(
                project, cache_path, identity=cache_identity,
                deadline=_operation_deadline(timeout, cancel_deadline), budget=page_budget,
            ),
        )
    discovered = _cancel_aware(
        cancel_deadline,
        lambda: _read_project_discovery(
            project, discovery_path, identity=discovery_identity,
            deadline=_operation_deadline(timeout, cancel_deadline),
        ),
    )
    page_authority_sha = hashlib.sha256(_canonical_bytes(dict(page_context))).hexdigest()
    if discovered is not None and not (
        discovered["origin_page_number"] == page_number
        and discovered["origin_page_authority_sha256"] != page_authority_sha
    ):
        references = [
            _page_local_discovery_reference(
                project, item, page_number=page_number,
                deadline=_operation_deadline(timeout, cancel_deadline),
            )
            for item in discovered["materials"]
        ]
        _cancel_aware(
            cancel_deadline,
            lambda: _write_search_cache(
                project, cache_path, identity=cache_identity, materials=references,
                deadline=_operation_deadline(timeout, cancel_deadline),
                lease_token=None,
            ),
        )
        return references
    if _lease_token is None:
        try:
            lease_token = _cancel_aware(
                cancel_deadline,
                lambda: _acquire_search_lease(
                    cache_path, ttl_seconds=_single_search_lease_ttl_seconds(timeout),
                ),
            )
        except FileExistsError as exc:
            if cache_path.exists():
                return _cancel_aware(
                    cancel_deadline,
                    lambda: _verify_cached_search(
                        project, cache_path, identity=cache_identity,
                        deadline=_operation_deadline(timeout, cancel_deadline), budget=page_budget,
                    ),
                )
            raise SearchMaterialBlocked(
                "visual material search is already in progress",
                code="visual_material_resolution_pending",
                state="material_resolution_pending",
            ) from exc
        try:
            return search_visual_material(
                project,
                directive=directive,
                page_context=page_context,
                timeout=timeout,
                deadline=cancel_deadline,
                budget=page_budget,
                invoke=invoke_model,
                transport=transport,
                _lease_token=lease_token,
                max_results=result_limit,
            )
        finally:
            _release_search_lease(cache_path, lease_token)

    def advisory_warning(detail: str) -> list[dict[str, Any]]:
        warnings.warn(
            f"advisory visual material search was skipped: {detail}",
            RuntimeWarning,
            stacklevel=2,
        )
        return []

    request_prompt = _prompt(
        query=query,
        material_id=material_id,
        directive_id=directive_id,
        page_context=page_context,
        max_results=result_limit,
    )
    request_schema = _schema(result_limit)
    try:
        result = invoke_model(
            project,
            role="visual-material-search",
            prompt=request_prompt,
            images=[],
            output_schema=request_schema,
            timeout=_operation_timeout(timeout, cancel_deadline, "visual material search"),
            web_search="live",
        )
    except CodexRuntimeUnavailable as exc:
        _raise_if_cancelled(cancel_deadline)
        if not required:
            return advisory_warning(str(exc))
        code = (
            "codex_app_server_timeout"
            if str(exc) == "Codex App Server timeout"
            else "required_search_material_unavailable"
        )
        raise SearchMaterialBlocked(
            f"required visual material search failed: {exc}", code=code,
        ) from exc
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        if not required:
            return advisory_warning(str(exc))
        raise SearchMaterialBlocked(f"required visual material search failed: {exc}") from exc
    _raise_if_cancelled(cancel_deadline)
    if result.auth_mode != "chatgpt":
        if not required:
            return advisory_warning("the result did not use ChatGPT OAuth")
        raise SearchMaterialBlocked("required visual material search did not use ChatGPT OAuth")
    invocation_path = _cancel_aware(
        cancel_deadline,
        lambda: _write_invocation_bundle(
            project,
            page_number=page_number,
            material_id=material_id,
            directive_id=directive_id,
            query=query,
            request={
                "page_number": page_number,
                "page_authority_sha256": cache_identity["page_authority_sha256"],
                "page_context": dict(page_context),
                "material_id": material_id,
                "directive_id": directive_id,
                "query": query,
                "role": "visual-material-search",
                "web_search": "live",
                "prompt": request_prompt,
                "images": [],
                "output_schema": request_schema,
            },
            result=result,
            timeout=timeout,
            cancel_deadline=cancel_deadline,
        ),
    )
    invocation_sha256 = _cancel_aware(
        cancel_deadline, lambda: _sha256_file(project / invocation_path),
    )
    invocation = _cancel_aware(
        cancel_deadline,
        lambda: _read_signed_invocation(
            project,
            invocation_path,
            deadline=_operation_deadline(timeout, cancel_deadline),
            expected_sha256=invocation_sha256,
        ),
    )
    candidates = result.value.get("candidates")
    if not isinstance(candidates, list):
        if not required:
            return advisory_warning("the result had no candidate array")
        raise SearchMaterialBlocked("required visual material search returned no candidate array")
    try:
        materials = download_visual_material(
            project,
            page_number=page_number,
            material_id=material_id,
            directive_id=directive_id,
            query=query,
            candidates=candidates,
            timeout=timeout,
            deadline=cancel_deadline,
            budget=page_budget,
            transport=transport,
            invocation_bundle_path=invocation_path,
            invocation_bundle_sha256=invocation_sha256,
            invocation_bundle_signature=invocation["signature"],
            invocation_bundle_sealed_sha256=invocation["sealed_sha256"],
        )
    except SearchMaterialBlocked:
        _raise_if_cancelled(cancel_deadline)
        if not required:
            return advisory_warning("no candidate passed safe image validation")
        raise
    if required and not materials:
        raise SearchMaterialBlocked("required search material is unavailable")
    if not required and not materials:
        return advisory_warning("no qualifying candidate was returned")
    _cancel_aware(
        cancel_deadline,
        lambda: _write_search_cache(
            project, cache_path, identity=cache_identity, materials=materials,
            deadline=_operation_deadline(timeout, cancel_deadline),
            lease_token=_lease_token,
        ),
    )
    if discovered is None:
        _cancel_aware(
            cancel_deadline,
            lambda: _write_project_discovery(
                project, discovery_path, identity=discovery_identity, materials=materials,
                page_context=page_context,
                deadline=_operation_deadline(timeout, cancel_deadline),
            ),
        )
    return materials


def search_visual_materials(
    project: Path,
    *,
    directives: Sequence[Any],
    page_context: Mapping[str, Any],
    timeout: float,
    deadline: float | None = None,
    budget: ResourceBudget | None = None,
    invoke: Callable[..., CodexStructuredResult] | None = None,
    transport: MaterialTransport | None = None,
    shard_size: int = _MAX_BATCH_REQUESTS,
    max_search_concurrency: int = 2,
    max_download_concurrency: int = 3,
) -> list[list[dict[str, Any]]]:
    """Resolve ordered search directives through signed shards and per-item caches."""
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if not isinstance(directives, Sequence) or isinstance(directives, (str, bytes)):
        raise ValueError("search directives must be an array")
    if not 1 <= shard_size <= _MAX_BATCH_REQUESTS:
        raise ValueError("search shard_size must be between one and twenty")
    if type(max_search_concurrency) is not int or not 1 <= max_search_concurrency <= 2:
        raise ValueError("max_search_concurrency must be between one and two")
    if type(max_download_concurrency) is not int or not 1 <= max_download_concurrency <= 3:
        raise ValueError("max_download_concurrency must be between one and three")
    if not directives:
        return []
    if len(directives) > _MAX_PAGE_SEARCH_REQUESTS:
        raise ValueError("batch search cannot exceed 20 requests per page")
    project = Path(project).resolve(strict=True)
    if not isinstance(page_context, Mapping):
        raise ValueError("page_context must be an object")
    page_number = page_context.get("page_number")
    if type(page_number) is not int or page_number < 1:
        raise ValueError("page_context page_number must be positive")
    # A supplied deadline is caller cancellation authority.  None means no
    # page-wide timeout; every operation below still gets a fresh watchdog.
    cancel_deadline = deadline
    _raise_if_cancelled(cancel_deadline)
    lease_ttl_seconds = _batch_lease_ttl_seconds(
        request_count=len(directives), shard_size=shard_size,
        max_search_concurrency=max_search_concurrency,
        max_download_concurrency=max_download_concurrency,
        search_timeout=timeout,
    )
    requests = [_batch_request_identity(item) for item in directives]
    bindings = [_binding(item) for item in requests]
    if len(set(bindings)) != len(bindings):
        raise ValueError("batch search request bindings must be unique")
    page_budget = budget or ResourceBudget(
        max_images=max(MAX_IMAGES_PER_PAGE, len(requests)),
        max_network_bytes=MAX_FILE_BYTES * max(MAX_IMAGES_PER_PAGE, len(requests)),
        max_decoded_pixels=MAX_IMAGE_PIXELS * max(MAX_IMAGES_PER_PAGE, len(requests)),
        max_decoded_bytes=MAX_IMAGE_PIXELS * 8 * max(MAX_IMAGES_PER_PAGE, len(requests)),
    )
    total_slots = max(MAX_IMAGES_PER_PAGE, len(requests))
    selection_quotas = [1] * len(requests)
    for index, request in enumerate(requests):
        if total_slots <= len(requests):
            break
        extra = min(request["max_results"] - 1, total_slots - len(requests))
        selection_quotas[index] += extra
        total_slots -= extra
    invoke_model = invoke or invoke_structured
    outcomes: list[list[dict[str, Any]] | None] = [None] * len(requests)
    owned: list[tuple[int, dict[str, Any], dict[str, Any], Path, str]] = []
    leases: list[tuple[Path, str]] = []
    discoveries: dict[int, tuple[dict[str, Any], Path, dict[str, Any] | None]] = {}
    try:
        for index, request in enumerate(requests):
            identity = _batch_search_cache_identity(
                page_context=page_context, request=request, budget=page_budget,
            )
            discovery_identity = _project_discovery_identity(
                directive_id=request["directive_id"], material_id=request["material_id"],
                query=request["query"], required=bool(request["required"]),
                max_results=int(request["max_results"]),
            )
            discovery_path = _cancel_aware(
                cancel_deadline,
                lambda: _project_discovery_path(
                    project, discovery_identity,
                    deadline=_operation_deadline(timeout, cancel_deadline),
                ),
            )
            discovery = _cancel_aware(
                cancel_deadline,
                lambda: _read_project_discovery(
                    project, discovery_path, identity=discovery_identity,
                    deadline=_operation_deadline(timeout, cancel_deadline),
                ),
            )
            discoveries[index] = (discovery_identity, discovery_path, discovery)
            cache_deadline = _operation_deadline(timeout, cancel_deadline)
            cache_path = _cancel_aware(
                cancel_deadline,
                lambda: _search_cache_path(project, identity, deadline=cache_deadline),
            )
            if cache_path.exists():
                outcomes[index] = _cancel_aware(
                    cancel_deadline,
                    lambda: _verify_cached_search(
                        project, cache_path, identity=identity,
                        deadline=_operation_deadline(timeout, cancel_deadline), budget=page_budget,
                    ),
                )
                continue
            page_authority_sha = hashlib.sha256(_canonical_bytes(dict(page_context))).hexdigest()
            if discovery is not None and not (
                discovery["origin_page_number"] == page_number
                and discovery["origin_page_authority_sha256"] != page_authority_sha
            ):
                references = [
                    _page_local_discovery_reference(
                        project, item, page_number=page_number,
                        deadline=_operation_deadline(timeout, cancel_deadline),
                    )
                    for item in discovery["materials"]
                ]
                _cancel_aware(
                    cancel_deadline,
                    lambda: _write_search_cache(
                        project, cache_path, identity=identity, materials=references,
                        deadline=_operation_deadline(timeout, cancel_deadline),
                    ),
                )
                outcomes[index] = references
                continue
            try:
                token = _cancel_aware(
                    cancel_deadline,
                    lambda: _acquire_search_lease(
                        cache_path, ttl_seconds=lease_ttl_seconds,
                    ),
                )
            except FileExistsError as exc:
                if cache_path.exists():
                    outcomes[index] = _cancel_aware(
                        cancel_deadline,
                        lambda: _verify_cached_search(
                            project, cache_path, identity=identity,
                            deadline=_operation_deadline(timeout, cancel_deadline), budget=page_budget,
                        ),
                    )
                    continue
                raise SearchMaterialBlocked(
                    "visual material search is already in progress",
                    code="visual_material_resolution_pending",
                    state="material_resolution_pending",
                ) from exc
            leases.append((cache_path, token))
            owned.append((index, request, identity, cache_path, token))

        download_slots = threading.Semaphore(max_download_concurrency)
        digest_claims: dict[str, tuple[str, str]] = {}
        for index, materials in enumerate(outcomes):
            for material in materials or []:
                digest = material.get("sha256")
                owner = (requests[index]["material_id"], requests[index]["directive_id"])
                if isinstance(digest, str) and digest in digest_claims and digest_claims[digest] != owner:
                    raise SearchMaterialBlocked(
                        "cached image is bound to two different search materials",
                        code="required_search_material_duplicate",
                    )
                if isinstance(digest, str):
                    digest_claims[digest] = owner

        def process_shard(
            shard: list[tuple[int, dict[str, Any], dict[str, Any], Path, str]],
        ) -> tuple[
            list[tuple[int, dict[str, Any], Path, str, list[dict[str, Any]]]],
            list[tuple[int, Exception]],
        ]:
            shard_successes: list[
                tuple[int, dict[str, Any], Path, str, list[dict[str, Any]]]
            ] = []
            shard_failures: list[tuple[int, Exception]] = []
            shard_requests = [item[1] for item in shard]
            prompt = _batch_prompt(requests=shard_requests, page_context=page_context)
            output_schema = _batch_schema(shard_requests)
            try:
                result = invoke_model(
                    project,
                    role="visual-material-search",
                    prompt=prompt,
                    images=[],
                    output_schema=output_schema,
                    timeout=_operation_timeout(
                        timeout, cancel_deadline, "batch visual material search",
                    ),
                    web_search="live",
                )
            except CodexRuntimeUnavailable as exc:
                _raise_if_cancelled(cancel_deadline)
                code = (
                    "codex_app_server_timeout"
                    if str(exc) == "Codex App Server timeout"
                    else "required_search_material_unavailable"
                )
                raise SearchMaterialBlocked(
                    f"required visual material batch search failed: {exc}", code=code,
                ) from exc
            _raise_if_cancelled(cancel_deadline)
            if result.auth_mode != "chatgpt":
                raise SearchMaterialBlocked("required visual material batch search did not use ChatGPT OAuth")
            result_by_binding = _validate_batch_bijection(shard_requests, result.value)
            receipt = _cancel_aware(
                cancel_deadline,
                lambda: _write_batch_receipt(
                    project,
                    page_context=page_context,
                    requests=shard_requests,
                    prompt=prompt,
                    output_schema=output_schema,
                    result=result,
                    deadline=_operation_deadline(
                        _MATERIAL_RETRIEVAL_GRACE_SECONDS, cancel_deadline,
                    ),
                ),
            )

            def retrieve(item: tuple[int, dict[str, Any], dict[str, Any], Path, str]):
                index, request, identity, cache_path, _token = item
                result_item = result_by_binding[_binding(request)]
                if request["material_role"] == "enterprise_logo":
                    for candidate in result_item["candidates"]:
                        if candidate.get("matched_entities") != [request["entity"]]:
                            raise SearchMaterialBlocked("batch search candidate is bound to the wrong entity")
                binding_payload = _binding_payload(request)
                batch_reference = {
                    **receipt,
                    "binding": binding_payload,
                    "binding_sha256": hashlib.sha256(_canonical_bytes(binding_payload)).hexdigest(),
                    "result_sha256": hashlib.sha256(_canonical_bytes(result_item)).hexdigest(),
                    "request_identity": dict(request),
                }
                with download_slots:
                    item_budget = ResourceBudget(
                        max_images=selection_quotas[index],
                        max_network_bytes=MAX_FILE_BYTES * selection_quotas[index],
                        max_decoded_pixels=MAX_IMAGE_PIXELS * selection_quotas[index],
                        max_decoded_bytes=MAX_IMAGE_PIXELS * 8 * selection_quotas[index],
                    )
                    materials = download_visual_material(
                        project,
                        page_number=page_number,
                        material_id=request["material_id"],
                        directive_id=request["directive_id"],
                        query=request["query"],
                        candidates=result_item["candidates"],
                        timeout=_MATERIAL_RETRIEVAL_GRACE_SECONDS,
                        deadline=cancel_deadline,
                        budget=item_budget,
                        transport=transport,
                        batch_receipt_reference=batch_reference,
                    )
                if request["required"] and not materials:
                    raise SearchMaterialBlocked(
                        f"required batch search material is unavailable: {request['material_id']}",
                        code="required_search_material_empty",
                    )
                return index, identity, cache_path, _token, materials

            worker_count = min(max_download_concurrency, len(shard))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                remaining_items = iter(shard)
                future_map = {}
                for _worker in range(worker_count):
                    item = next(remaining_items, None)
                    if item is not None:
                        future_map[executor.submit(retrieve, item)] = item
                while future_map:
                    future = next(as_completed(tuple(future_map)))
                    completed_item = future_map.pop(future)
                    try:
                        shard_successes.append(future.result())
                    except Exception as exc:
                        if _is_search_cancelled(exc):
                            for pending in future_map:
                                pending.cancel()
                            raise
                        shard_failures.append((completed_item[0], exc))
                    item = next(remaining_items, None)
                    if item is not None:
                        future_map[executor.submit(retrieve, item)] = item
            return shard_successes, shard_failures

        shards = [
            owned[offset:offset + shard_size]
            for offset in range(0, len(owned), shard_size)
        ]
        failures: list[tuple[int, Exception]] = []
        successes: list[tuple[int, dict[str, Any], Path, str, list[dict[str, Any]]]] = []
        if shards:
            worker_count = min(max_search_concurrency, len(shards))
            with ThreadPoolExecutor(max_workers=worker_count) as search_executor:
                remaining_shards = iter(shards)
                futures = {}
                for _worker in range(worker_count):
                    shard = next(remaining_shards, None)
                    if shard is not None:
                        futures[search_executor.submit(process_shard, shard)] = shard[0][0]
                while futures:
                    future = next(as_completed(tuple(futures)))
                    first_index = futures.pop(future)
                    try:
                        shard_successes, shard_failures = future.result()
                        successes.extend(shard_successes)
                        failures.extend(shard_failures)
                    except Exception as exc:
                        if _is_search_cancelled(exc):
                            for pending in futures:
                                pending.cancel()
                            raise
                        failures.append((first_index, exc))
                    shard = next(remaining_shards, None)
                    if shard is not None:
                        futures[search_executor.submit(process_shard, shard)] = shard[0][0]
        for index, identity, cache_path, lease_token, materials in sorted(
            successes, key=lambda item: item[0],
        ):
            request = requests[index]
            owner = (request["material_id"], request["directive_id"])
            duplicate = any(
                isinstance(material.get("sha256"), str)
                and material["sha256"] in digest_claims
                and digest_claims[material["sha256"]] != owner
                for material in materials
            )
            if duplicate:
                failures.append((index, SearchMaterialBlocked(
                    "one image cannot satisfy two different required search materials",
                    code="required_search_material_duplicate",
                )))
                continue
            for material in materials:
                digest = material.get("sha256")
                if isinstance(digest, str):
                    digest_claims[digest] = owner
            _cancel_aware(
                cancel_deadline,
                lambda: _write_search_cache(
                    project, cache_path, identity=identity,
                    materials=materials,
                    deadline=_operation_deadline(
                        _MATERIAL_RETRIEVAL_GRACE_SECONDS, cancel_deadline,
                    ),
                    lease_token=lease_token,
                ),
            )
            discovery_identity, discovery_path, prior_discovery = discoveries[index]
            if prior_discovery is None:
                _cancel_aware(
                    cancel_deadline,
                    lambda: _write_project_discovery(
                        project, discovery_path, identity=discovery_identity,
                        materials=materials, page_context=page_context,
                        deadline=_operation_deadline(
                            _MATERIAL_RETRIEVAL_GRACE_SECONDS, cancel_deadline,
                        ),
                    ),
                )
            outcomes[index] = materials
        if failures:
            _index, first = min(failures, key=lambda item: item[0])
            if isinstance(first, SearchMaterialBlocked):
                raise first
            raise SearchMaterialBlocked(f"batch visual material retrieval failed: {first}") from first
        if any(item is None for item in outcomes):
            raise SearchMaterialBlocked("batch visual material search did not close every request")
        return [list(item or []) for item in outcomes]
    finally:
        for cache_path, token in reversed(leases):
            _release_search_lease(cache_path, token)


def emit_reference_work_items(project: Path) -> list[dict[str, Any]]:
    """Write bounded V6 work for the outer orchestrator, without fetching result URLs."""
    project = Path(project).resolve()
    items: list[dict[str, Any]] = []
    material_dir = project / "02_v6" / "reference_materials"
    for receipt_path in sorted(material_dir.glob("page_*.json")):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(receipt, Mapping) or not isinstance(receipt.get("page_number"), int):
            continue
        for request in receipt.get("reference_acquisitions", []):
            if not isinstance(request, Mapping) or request.get("status") != "pending":
                continue
            request_id = request.get("request_id")
            purpose = request.get("purpose")
            need = request.get("identity_evidence_need")
            if not all(isinstance(value, str) and value for value in (request_id, purpose, need)):
                continue
            page_number = receipt["page_number"]
            items.append({
                "page_number": page_number,
                "request_id": request_id,
                "purpose": purpose,
                "identity_evidence_need": need,
                "status": "pending",
                "max_results": 1,
                "commands": {
                    "import_reference": (
                        "workflow_v6_cli.py import-reference --project <project> "
                        f"--page {page_number} --request-id {request_id} --image <local-file> "
                        "[--source-url <metadata-url>]"
                    ),
                    "fail_reference": (
                        "workflow_v6_cli.py fail-reference --project <project> "
                        f"--page {page_number} --request-id {request_id} --reason <reason>"
                    ),
                    "reject_reference": (
                        "workflow_v6_cli.py reject-reference --project <project> "
                        f"--page {page_number} --request-id {request_id} --reason <reason>"
                    ),
                    "confirm_reference": (
                        "workflow_v6_cli.py confirm-reference --project <project> "
                        f"--page {page_number} --request-id {request_id}"
                    ),
                },
            })
    items = sorted(items, key=lambda item: (item["page_number"], item["request_id"]))
    output = project / "02_v6" / "orchestrator" / "reference_work_items.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"artifact_version": "reference-work-items-v6", "items": items}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return items

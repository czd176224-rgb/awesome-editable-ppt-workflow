"""Render project-owned attachments into image-only page candidates."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import stat
import platform
import re
import time
import uuid
import weakref
import zipfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from functools import lru_cache
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Final

from PIL import Image, ImageOps

from workflow_v6_contract import transition_page
from workflow_v6_media import (
    MAX_DECODED_PIXELS, MAX_EDGE, MAX_ENCODED_BYTES, _is_link_or_reparse,
    _open_project_root_handle, _verify_handle_within,
    validated_raster_bytes,
)
from workflow_v6_state import load, mutation_lock, save


ROOT: Final = Path("02_v6/attachment_renders")
MAX_ATTACHMENT_BYTES: Final = 250 * 1024 * 1024
MAX_RENDERED_PAGES: Final = 200
MAX_TOTAL_PIXELS: Final = 400_000_000
MAX_TOTAL_OUTPUT_BYTES: Final = 200 * 1024 * 1024
MAX_RECEIPT_BYTES: Final = 1024 * 1024
MAX_OOXML_ENTRIES: Final = 10_000
MAX_OOXML_TOTAL_BYTES: Final = 250 * 1024 * 1024
MAX_OOXML_ENTRY_BYTES: Final = 50 * 1024 * 1024
MAX_OOXML_RATIO: Final = 100
RENDER_TIMEOUT_SECONDS: Final = 180
SUPPORTED_IMAGES: Final = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})
SUPPORTED_DOCUMENTS: Final = frozenset({".pdf", ".docx", ".pptx", ".xlsx"})


class AttachmentRenderError(RuntimeError):
    """A file-local rendering error that is safe to expose in project state."""


class _PageLease:
    __slots__ = ("project", "page_number", "handle", "active", "__weakref__")

    def __init__(self, project: Path, page_number: int, handle: Any):
        self.project = project
        self.page_number = page_number
        self.handle = handle
        self.active = True


_ACTIVE_PAGE_LEASES: weakref.WeakSet[_PageLease] = weakref.WeakSet()


@dataclass(frozen=True)
class RenderedImage:
    page_number: int
    path: str
    width: int
    height: int
    byte_size: int
    sha256: str


@dataclass(frozen=True)
class AttachmentRenderReceipt:
    schema_version: str
    original_path: str
    original_sha256: str
    original_byte_size: int
    renderer_identity: str
    pages: tuple[RenderedImage, ...]
    contact_sheet: RenderedImage

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        # dataclasses preserves tuples; the durable JSON authority has one
        # representation only, so expose arrays before schema validation too.
        value["pages"] = list(value["pages"])
        return value


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _contained_original(project: Path, attachment: Path) -> tuple[Path, bytes]:
    project = project.resolve(strict=True)
    candidate = Path(os.path.abspath(attachment))
    source_root = (project / "01_source_assets").resolve(strict=True)
    if candidate != source_root and source_root not in candidate.parents:
        raise AttachmentRenderError(f"attachment is outside project source assets: {attachment.name}")
    if _is_link_or_reparse(candidate) or candidate.stat().st_size > MAX_ATTACHMENT_BYTES:
        raise AttachmentRenderError(f"attachment is unsafe or too large: {attachment.name}")
    resolved = candidate.resolve(strict=True)
    if resolved != source_root and source_root not in resolved.parents:
        raise AttachmentRenderError(f"attachment escapes project source assets: {attachment.name}")
    root_handle = _open_project_root_handle(project)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(candidate, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise AttachmentRenderError(f"attachment is not a regular file: {attachment.name}")
        _verify_handle_within(root_handle, descriptor)
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            data = stream.read(MAX_ATTACHMENT_BYTES + 1)
        after = os.fstat(descriptor)
        if not os.path.samestat(metadata, after) or metadata.st_size != after.st_size or metadata.st_mtime_ns != after.st_mtime_ns:
            raise AttachmentRenderError(f"attachment changed during stable read: {attachment.name}")
    finally:
        os.close(descriptor)
        os.close(root_handle)
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentRenderError(f"attachment exceeds byte limit: {attachment.name}")
    return candidate, data


def _renderer_identity(suffix: str) -> str:
    if suffix in SUPPORTED_IMAGES:
        return f"pillow/{Image.__version__}:original-image;png-lossless"
    if suffix == ".pdf":
        import pypdfium2 as pdfium

        build = getattr(pdfium, "PDFIUM_INFO", None) or getattr(pdfium, "__version__", "bundled")
        return f"pypdfium2/{build}:pdf-raster-v1;scale=2;png"
    version, build = _office_application_build(suffix)
    product = {".docx": "word", ".pptx": "powerpoint", ".xlsx": "excel"}[suffix]
    return (
        f"windows-office-com/{platform.version()}:{product}/version={version}/build={build};"
        f"automation-security=3;links=disabled;alerts=disabled;pdfium-scale=2;{suffix[1:]}"
    )


@lru_cache(maxsize=3)
def _office_application_build(suffix: str) -> tuple[str, str]:
    """Read the installed renderer build in an isolated bounded COM child."""
    progid = {".docx": "Word.Application", ".pptx": "PowerPoint.Application", ".xlsx": "Excel.Application"}[suffix]
    script = (
        "import json,sys,win32com.client; app=None\n"
        "try:\n"
        " app=win32com.client.DispatchEx(sys.argv[1]); print(json.dumps([str(app.Version),str(getattr(app,'Build','unknown'))]))\n"
        "finally:\n"
        " if app is not None: app.Quit()\n"
    )
    returncode, stdout, stderr = _run_owned_process(
        [os.fspath(Path(os.sys.executable)), "-c", script, progid], 30, "Office renderer identity probe",
    )
    if returncode != 0:
        raise AttachmentRenderError(f"could not identify Office renderer: {(stderr or stdout)[-300:]}")
    try:
        version, build = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError, TypeError) as exc:
        raise AttachmentRenderError("Office renderer returned an invalid build identity") from exc
    return str(version), str(build)


_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def validate_attachment_receipt(
    value: dict[str, Any], *, expected_identity: str | None = None,
    expected_source_path: str | None = None, expected_source_size: int | None = None,
) -> None:
    required = {"schema_version", "original_path", "original_sha256", "original_byte_size", "renderer_identity", "pages", "contact_sheet"}
    if set(value) != required:
        raise ValueError("attachment receipt fields are invalid")
    if value["schema_version"] != "awesome-attachment-render-v1":
        raise ValueError("attachment receipt schema version is invalid")
    if not isinstance(value["original_path"], str) or not value["original_path"].startswith("01_source_assets/"):
        raise ValueError("attachment receipt original path is invalid")
    if not isinstance(value["original_sha256"], str) or not _LOWER_SHA256.fullmatch(value["original_sha256"]):
        raise ValueError("attachment receipt original digest is invalid")
    if not isinstance(value["original_byte_size"], int) or value["original_byte_size"] < 0:
        raise ValueError("attachment receipt original byte size is invalid")
    pages = value["pages"]
    if not isinstance(pages, (list, tuple)) or not 1 <= len(pages) <= MAX_RENDERED_PAGES:
        raise ValueError("attachment receipt page count is invalid")
    if [item.get("page_number") for item in pages] != list(range(1, len(pages) + 1)):
        raise ValueError("attachment receipt page numbers must be consecutive")
    image_fields = {"page_number", "path", "width", "height", "byte_size", "sha256"}
    for item in [*pages, value["contact_sheet"]]:
        if not isinstance(item, dict) or set(item) != image_fields:
            raise ValueError("attachment receipt image fields are invalid")
        if not all(isinstance(item[key], int) and item[key] >= (0 if key == "page_number" else 1) for key in ("page_number", "width", "height", "byte_size")):
            raise ValueError("attachment receipt image dimensions are invalid")
        if not isinstance(item["sha256"], str) or not _LOWER_SHA256.fullmatch(item["sha256"]):
            raise ValueError("attachment receipt image digest is invalid")
    if expected_source_path is not None and value["original_path"] != expected_source_path:
        raise ValueError("attachment receipt source path does not match expected authority")
    if expected_source_size is not None and value["original_byte_size"] != expected_source_size:
        raise ValueError("attachment receipt source size does not match expected authority")
    if expected_identity is not None:
        if not _LOWER_SHA256.fullmatch(expected_identity):
            raise ValueError("attachment receipt identity is invalid")
        base = f"02_v6/attachment_renders/{expected_identity}"
        for index, item in enumerate(pages, start=1):
            if item["path"] != f"{base}/page_{index:04d}.png":
                raise ValueError("attachment receipt page path is not canonical")
        if value["contact_sheet"]["page_number"] != 0 or value["contact_sheet"]["path"] != f"{base}/contact_sheet.png":
            raise ValueError("attachment receipt contact path is not canonical")


def _reject_external_ooxml_relationships(snapshot: Path) -> None:
    try:
        with zipfile.ZipFile(snapshot) as package:
            infos = package.infolist()
            if len(infos) > MAX_OOXML_ENTRIES:
                raise AttachmentRenderError("OOXML package has too many entries")
            names: set[str] = set()
            total_size = 0
            for info in infos:
                normalized = info.filename.replace("\\", "/").casefold()
                if normalized in names:
                    raise AttachmentRenderError(f"OOXML package contains duplicate entry: {info.filename}")
                names.add(normalized)
                total_size += info.file_size
                if info.file_size > MAX_OOXML_ENTRY_BYTES or total_size > MAX_OOXML_TOTAL_BYTES:
                    raise AttachmentRenderError(f"OOXML entry is too large: {info.filename}")
                if info.file_size and (not info.compress_size or info.file_size / info.compress_size > MAX_OOXML_RATIO):
                    raise AttachmentRenderError(f"OOXML compression ratio is unsafe: {info.filename}")
            relationship_names = [info.filename for info in infos if info.filename.lower().endswith(".rels")]
            for name in relationship_names:
                with package.open(name) as stream:
                    data = stream.read(MAX_RECEIPT_BYTES + 1)
                if len(data) > MAX_RECEIPT_BYTES:
                    raise AttachmentRenderError(f"OOXML relationship part is too large: {name}")
                root = ET.fromstring(data)
                for relationship in root.iter():
                    if relationship.tag.rsplit("}", 1)[-1] == "Relationship" and relationship.attrib.get("TargetMode", "").lower() == "external":
                        raise AttachmentRenderError(f"OOXML external relationship is forbidden: {name}")
    except AttachmentRenderError:
        raise
    except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise AttachmentRenderError(f"OOXML package validation failed: {snapshot.name}: {exc}") from exc


def _render_pdf(pdf_path: Path, output: Path) -> list[Path]:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_path)
    try:
        if len(document) < 1 or len(document) > MAX_RENDERED_PAGES:
            raise AttachmentRenderError("PDF page count is outside the supported boundary")
        paths: list[Path] = []
        projected_pixels = 0
        for index in range(len(document)):
            page = document[index]
            try:
                width, height = page.get_size()
                projected_width, projected_height = math.ceil(width * 2), math.ceil(height * 2)
                if projected_width > MAX_EDGE or projected_height > MAX_EDGE or projected_width * projected_height > MAX_DECODED_PIXELS:
                    raise AttachmentRenderError("PDF page dimensions exceed the image boundary")
                projected_pixels += projected_width * projected_height
                if projected_pixels > MAX_TOTAL_PIXELS:
                    raise AttachmentRenderError("PDF pages exceed the total pixel boundary")
                image = page.render(scale=2).to_pil().convert("RGB")
            finally:
                page.close()
            path = output / f"page_{index + 1:04d}.png"
            image.save(path, "PNG", optimize=False, compress_level=9)
            paths.append(path)
        return paths
    finally:
        document.close()


def _office_to_pdf(attachment: Path, suffix: str, output_pdf: Path) -> None:
    # Keep COM in a child process so a renderer hang can be bounded and killed.
    script = r'''
import sys
import win32com.client
source, suffix, target = sys.argv[1:]
app = doc = None
try:
    if suffix == ".docx":
        app = win32com.client.DispatchEx("Word.Application"); app.Visible = False; app.DisplayAlerts = 0
        app.AutomationSecurity = 3
        app.Options.UpdateLinksAtOpen = False
        doc = app.Documents.Open(source, ConfirmConversions=False, ReadOnly=True, AddToRecentFiles=False, Revert=False, NoEncodingDialog=True, OpenAndRepair=False)
        doc.ExportAsFixedFormat(target, 17)
    elif suffix == ".pptx":
        app = win32com.client.DispatchEx("PowerPoint.Application"); app.AutomationSecurity = 3; app.DisplayAlerts = 1
        doc = app.Presentations.Open(source, ReadOnly=True, Untitled=False, WithWindow=False)
        doc.SaveAs(target, 32)
    elif suffix == ".xlsx":
        app = win32com.client.DispatchEx("Excel.Application"); app.Visible = False; app.DisplayAlerts = False
        app.AutomationSecurity = 3; app.AskToUpdateLinks = False
        doc = app.Workbooks.Open(source, UpdateLinks=0, ReadOnly=True, IgnoreReadOnlyRecommended=True, AddToMru=False, CorruptLoad=0)
        doc.ExportAsFixedFormat(0, target)
    else:
        raise RuntimeError("unsupported Office attachment")
finally:
    if doc is not None:
        try: doc.Close(False)
        except Exception: pass
    if app is not None:
        try: app.Quit()
        except Exception: pass
'''
    command = [os.fspath(Path(os.sys.executable)), "-c", script, str(attachment), suffix, str(output_pdf)]
    returncode, stdout, stderr = _run_owned_process(
        command,
        RENDER_TIMEOUT_SECONDS,
        f"Office renderer for {attachment.name}",
    )
    if returncode != 0 or not output_pdf.is_file() or not output_pdf.stat().st_size:
        detail = (stderr or stdout).strip()[-500:]
        raise AttachmentRenderError(f"Office renderer failed: {detail or 'no PDF output'}")


def _run_owned_process(command: list[str], timeout: int, label: str) -> tuple[int, str, str]:
    """Run one suspended child only after its whole future process tree is owned."""
    child = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
        creationflags=getattr(subprocess, "CREATE_SUSPENDED", 0x00000004),
    )
    job_handle = None
    try:
        job_handle = _assign_kill_on_close_job(child)
        _resume_suspended_process(child)
        stdout, stderr = child.communicate(timeout=timeout)
        return int(child.returncode or 0), stdout, stderr
    except subprocess.TimeoutExpired as exc:
        if job_handle is not None:
            _close_native_handle(job_handle)
            job_handle = None
        else:
            child.terminate()
        try:
            child.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=5)
        raise AttachmentRenderError(f"{label} timed out") from exc
    except BaseException:
        if job_handle is not None:
            _close_native_handle(job_handle)
            job_handle = None
        else:
            child.terminate()
        try:
            child.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=5)
        raise
    finally:
        if job_handle is not None:
            _close_native_handle(job_handle)


def _assign_kill_on_close_job(child: subprocess.Popen[str]) -> int | None:
    """Own the exact renderer process tree; closing the job kills descendants."""
    if os.name != "nt" or not hasattr(child, "_handle"):
        return None
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
        ]

    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise AttachmentRenderError("could not create Office renderer job object")
    limits = EXTENDED_LIMIT()
    limits.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        kernel.CloseHandle(job)
        raise AttachmentRenderError("could not configure Office renderer job object")
    if not kernel.AssignProcessToJobObject(job, wintypes.HANDLE(child._handle)):
        kernel.CloseHandle(job)
        raise AttachmentRenderError("could not assign Office renderer to job object")
    return int(job)


def _resume_suspended_process(child: subprocess.Popen[str]) -> None:
    import ctypes
    from ctypes import wintypes

    resume = ctypes.WinDLL("ntdll").NtResumeProcess
    resume.argtypes = (wintypes.HANDLE,)
    resume.restype = ctypes.c_long
    status = resume(wintypes.HANDLE(child._handle))
    if status < 0:
        raise AttachmentRenderError("could not resume owned Office renderer")


def _close_native_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    close = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    if not close(wintypes.HANDLE(handle)):
        raise OSError(ctypes.get_last_error(), "could not close renderer Job Object")


def _validated_png(path: Path) -> tuple[bytes, int, int]:
    before = path.stat()
    if before.st_size > MAX_ENCODED_BYTES:
        raise AttachmentRenderError("renderer PNG exceeds the encoded byte boundary")
    with path.open("rb") as stream:
        data = stream.read(MAX_ENCODED_BYTES + 1)
    after = path.stat()
    if len(data) > MAX_ENCODED_BYTES or not os.path.samestat(before, after) or before.st_size != after.st_size:
        raise AttachmentRenderError("renderer PNG changed or exceeded the encoded byte boundary")
    image, mime = validated_raster_bytes(data)
    if mime != "image/png":
        data_buffer = BytesIO()
        image.save(data_buffer, "PNG", optimize=False, compress_level=9)
        data = data_buffer.getvalue()
    width, height = image.size
    return data, width, height


def _contact_sheet(images: list[Image.Image]) -> Image.Image:
    count = len(images)
    columns = min(4, count)
    rows = math.ceil(count / columns)
    tile_w, tile_h = 512, 384
    sheet = Image.new("RGB", (columns * tile_w, rows * tile_h), "white")
    for index, image in enumerate(images):
        tile = ImageOps.contain(image.convert("RGB"), (tile_w, tile_h), Image.Resampling.LANCZOS)
        x = (index % columns) * tile_w + (tile_w - tile.width) // 2
        y = (index // columns) * tile_h + (tile_h - tile.height) // 2
        sheet.paste(tile, (x, y))
    return sheet


def _canonical(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _bounded_stable_read(project: Path, path: Path, limit: int) -> bytes:
    if _is_link_or_reparse(path):
        raise ValueError("render cache artifact must not be a link or reparse point")
    root_descriptor = _open_project_root_handle(project)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        _verify_handle_within(root_descriptor, descriptor)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("render cache artifact is invalid or too large")
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            data = stream.read(limit + 1)
        after = os.fstat(descriptor)
        if len(data) > limit or not os.path.samestat(before, after) or before.st_size != after.st_size:
            raise ValueError("render cache artifact changed during stable read")
        return data
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(root_descriptor)


def _read_relative_handle(directory_descriptor: int, filename: str, limit: int) -> tuple[int, bytes]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if os.name != "nt":
        descriptor = os.open(filename, flags, dir_fd=directory_descriptor)
    else:
        from awesome_page_materials import _open_relative_material
        descriptor = _open_relative_material(directory_descriptor, filename)
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        os.close(descriptor)
        raise ValueError("render cache artifact is invalid or too large")
    with os.fdopen(os.dup(descriptor), "rb") as stream:
        data = stream.read(limit + 1)
    after = os.fstat(descriptor)
    if len(data) > limit or not os.path.samestat(before, after) or before.st_size != after.st_size:
        os.close(descriptor)
        raise ValueError("render cache artifact changed during held read")
    return descriptor, data


def _validate_open_cache(
    final_descriptor: int, identity: str, expected_sha: str, renderer: str,
    expected_source_path: str, expected_source_size: int,
    *, receipt_data: bytes | None = None, artifact_buffers: list[tuple[str, bytes]] | None = None,
    held_handles: dict[str, int] | None = None,
) -> AttachmentRenderReceipt:
    handles: list[int] = []
    try:
        def read_one(filename: str, limit: int) -> bytes:
            if held_handles is None:
                descriptor, data = _read_relative_handle(final_descriptor, filename, limit)
                handles.append(descriptor)
                return data
            descriptor = held_handles[filename]
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError("held render cache artifact is invalid or too large")
            os.lseek(descriptor, 0, os.SEEK_SET)
            data = os.read(descriptor, limit + 1)
            after = os.fstat(descriptor)
            if len(data) > limit or not os.path.samestat(before, after) or before.st_size != after.st_size:
                raise ValueError("held render cache artifact changed during verification")
            return data

        raw_receipt = read_one("receipt.json", MAX_RECEIPT_BYTES)
        if receipt_data is not None and raw_receipt != receipt_data:
            raise ValueError("published attachment receipt differs from committed bytes")
        value = json.loads(raw_receipt.decode("utf-8"))
        validate_attachment_receipt(value, expected_identity=identity, expected_source_path=expected_source_path, expected_source_size=expected_source_size)
        if value["original_sha256"] != expected_sha or value["renderer_identity"] != renderer:
            raise ValueError("attachment cache authority differs from source")
        expected_buffers = dict(artifact_buffers or [])
        pages = tuple(RenderedImage(**item) for item in value["pages"])
        contact = RenderedImage(**value["contact_sheet"])
        for item in (*pages, contact):
            filename = Path(item.path).name
            data = read_one(filename, MAX_ENCODED_BYTES)
            if expected_buffers and data != expected_buffers[filename]:
                raise ValueError("published attachment artifact differs from committed bytes")
            if len(data) != item.byte_size or _digest(data) != item.sha256:
                raise ValueError("published attachment artifact digest differs")
            image, mime = validated_raster_bytes(data)
            if mime != "image/png" or image.size != (item.width, item.height):
                raise ValueError("published attachment artifact dimensions differ")
        return AttachmentRenderReceipt(value["schema_version"], value["original_path"], expected_sha, value["original_byte_size"], renderer, pages, contact)
    finally:
        for descriptor in handles:
            os.close(descriptor)


def _validate_reuse(
    project: Path, receipt_path: Path, expected_sha: str, renderer: str,
    expected_source_path: str, expected_source_size: int,
) -> AttachmentRenderReceipt | None:
    if not receipt_path.is_file() or receipt_path.is_symlink():
        return None
    try:
        value = json.loads(_bounded_stable_read(project, receipt_path, MAX_RECEIPT_BYTES).decode("utf-8"))
        identity = receipt_path.parent.name
        validate_attachment_receipt(
            value, expected_identity=identity, expected_source_path=expected_source_path,
            expected_source_size=expected_source_size,
        )
        if value["original_sha256"] != expected_sha or value["renderer_identity"] != renderer:
            return None
        pages = tuple(RenderedImage(**item) for item in value["pages"])
        contact = RenderedImage(**value["contact_sheet"])
        for item in (*pages, contact):
            raw_target = project / item.path
            target = raw_target.resolve(strict=True)
            if project not in target.parents or target.is_symlink():
                return None
            data = _bounded_stable_read(project, raw_target, MAX_ENCODED_BYTES)
            if len(data) != item.byte_size or _digest(data) != item.sha256:
                return None
            image, mime = validated_raster_bytes(data)
            if mime != "image/png":
                return None
            width, height = image.size
            if (width, height) != (item.width, item.height):
                return None
        return AttachmentRenderReceipt(value["schema_version"], value["original_path"], expected_sha, value["original_byte_size"], renderer, pages, contact)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _after_pending_write(_pending: Path, _filename: str) -> None:
    """Test seam proving recovery at every pending-cache durability point."""


def _after_pending_receipt_close(_pending: Path, _identity: str) -> None:
    """Test seam proving replacement is denied while commit handles remain held."""


def _sha_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _directory_relative(parent_descriptor: int, name: str, *, create: bool, rename_access: bool = True) -> int:
    """Create or open one directory relative to a held parent without path resolution."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", wintypes.LPWSTR)]
    class ObjectAttributes(ctypes.Structure):
        _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE), ("ObjectName", ctypes.POINTER(UnicodeString)), ("Attributes", wintypes.ULONG), ("SecurityDescriptor", ctypes.c_void_p), ("SecurityQualityOfService", ctypes.c_void_p)]
    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    buffer = ctypes.create_unicode_buffer(name)
    unicode_name = UnicodeString(len(name.encode("utf-16-le")), (len(name) + 1) * 2, ctypes.cast(buffer, wintypes.LPWSTR))
    attributes = ObjectAttributes(ctypes.sizeof(ObjectAttributes), msvcrt.get_osfhandle(parent_descriptor), ctypes.pointer(unicode_name), 0x40 | 0x1000, None, None)
    status_block = IoStatusBlock()
    native = wintypes.HANDLE()
    nt_create = ctypes.WinDLL("ntdll").NtCreateFile
    desired = 0x80000000 | 0x40000000 | 0x00100000 | (0x00010000 if rename_access else 0)
    status = nt_create(
        ctypes.byref(native), desired,
        ctypes.byref(attributes), ctypes.byref(status_block), None, 0x80,
        0x00000001 | 0x00000002, 2 if create else 1,  # CREATE/OPEN; DELETE share forbidden
        0x00000001 | 0x00000020 | 0x00200000, None, 0,  # DIRECTORY + synchronous + no reparse
    )
    if status < 0:
        if create and status in {-1073741771, -1073741772}:
            raise FileExistsError(status, "attachment cache identity already exists", name)
        raise OSError(status, "NtCreateFile failed for attachment cache directory", name)
    return msvcrt.open_osfhandle(native.value, os.O_RDONLY)


def _create_directory_relative(parent_descriptor: int, name: str, *, rename_access: bool = True) -> int:
    return _directory_relative(parent_descriptor, name, create=True, rename_access=rename_access)


def _open_directory_relative(parent_descriptor: int, name: str, *, rename_access: bool = True) -> int:
    return _directory_relative(parent_descriptor, name, create=False, rename_access=rename_access)


def _open_lock_relative(directory_descriptor: int, filename: str) -> int:
    """Open-or-create one page lock relative to its held lease directory."""
    import ctypes
    import msvcrt
    from ctypes import wintypes
    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", wintypes.LPWSTR)]
    class ObjectAttributes(ctypes.Structure):
        _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE), ("ObjectName", ctypes.POINTER(UnicodeString)), ("Attributes", wintypes.ULONG), ("SecurityDescriptor", ctypes.c_void_p), ("SecurityQualityOfService", ctypes.c_void_p)]
    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]
    buffer = ctypes.create_unicode_buffer(filename)
    name = UnicodeString(len(filename.encode("utf-16-le")), (len(filename) + 1) * 2, ctypes.cast(buffer, wintypes.LPWSTR))
    attributes = ObjectAttributes(ctypes.sizeof(ObjectAttributes), msvcrt.get_osfhandle(directory_descriptor), ctypes.pointer(name), 0x40 | 0x1000, None, None)
    status_block = IoStatusBlock(); native = wintypes.HANDLE()
    nt_create = ctypes.WinDLL("ntdll").NtCreateFile
    nt_create.argtypes = (ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, ctypes.POINTER(ObjectAttributes), ctypes.POINTER(IoStatusBlock), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG)
    nt_create.restype = ctypes.c_long
    status = nt_create(ctypes.byref(native), 0x80000000 | 0x40000000 | 0x00100000, ctypes.byref(attributes), ctypes.byref(status_block), None, 0x80, 0x1 | 0x2, 3, 0x40 | 0x20 | 0x00200000, None, 0)
    if status < 0:
        raise OSError(status, "NtCreateFile failed for page lease", filename)
    return msvcrt.open_osfhandle(native.value, os.O_RDWR | getattr(os, "O_BINARY", 0))


def _rename_relative_handle(descriptor: int, parent_descriptor: int, name: str) -> None:
    """Atomically rename an owned pending directory relative to the held cache root."""
    if os.name != "nt":
        raise AttachmentRenderError("secure attachment cache rename is Windows-only")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class RenameInfo(ctypes.Structure):
        _fields_ = [("ReplaceIfExists", wintypes.BOOL), ("RootDirectory", wintypes.HANDLE), ("FileNameLength", wintypes.DWORD), ("FileName", wintypes.WCHAR * 1)]
    encoded = name.encode("utf-16-le")
    buffer = ctypes.create_string_buffer(RenameInfo.FileName.offset + len(encoded))
    info = ctypes.cast(buffer, ctypes.POINTER(RenameInfo)).contents
    info.ReplaceIfExists = False
    info.RootDirectory = msvcrt.get_osfhandle(parent_descriptor)
    info.FileNameLength = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.FileName.offset, encoded, len(encoded))
    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]
    status_block = IoStatusBlock()
    nt_set = ctypes.WinDLL("ntdll").NtSetInformationFile
    nt_set.argtypes = (wintypes.HANDLE, ctypes.POINTER(IoStatusBlock), ctypes.c_void_p, wintypes.ULONG, ctypes.c_int)
    nt_set.restype = ctypes.c_long
    status = nt_set(msvcrt.get_osfhandle(descriptor), ctypes.byref(status_block), buffer, len(buffer), 10)
    if status < 0:
        raise OSError(status, "failed to commit attachment cache directory", name)


def _remove_pending(directory: Path) -> None:
    """Remove only a recognized uncommitted directory below the held/validated cache root."""
    if directory.name.startswith((".pending-", ".orphan-")) and directory.is_dir() and not _is_link_or_reparse(directory):
        shutil.rmtree(directory)


def _after_quarantine_rename(_quarantine: Path) -> None:
    """Test seam while the exact renamed quarantine descriptor is held."""


def _recover_pending(cache_root: Path, cache_descriptor: int, root_descriptor: int, identity: str) -> None:
    prefix = f".pending-{identity}-"
    for candidate in cache_root.iterdir():
        if candidate.name.startswith(prefix):
            if _is_link_or_reparse(candidate) or not candidate.is_dir():
                raise AttachmentRenderError("attachment render pending cache is unsafe")
            pending_descriptor = _open_directory_relative(cache_descriptor, candidate.name)
            quarantine_name = f".orphan-{identity}-{uuid.uuid4().hex}"
            try:
                _verify_handle_within(root_descriptor, pending_descriptor)
                _rename_relative_handle(pending_descriptor, cache_descriptor, quarantine_name)
                quarantine = cache_root / quarantine_name
                _after_quarantine_rename(quarantine)
                # Leave the exact quarantined directory inert. Deleting it by
                # pathname after releasing this descriptor would reintroduce a
                # replacement race; it is never considered cache authority.
            finally:
                os.close(pending_descriptor)


def _write_exclusive_relative(directory_descriptor: int, filename: str, data: bytes) -> int:
    from awesome_page_materials import _open_exclusive_material

    descriptor = _open_exclusive_material(directory_descriptor, filename)
    try:
        with os.fdopen(os.dup(descriptor), "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def render_attachment(project_root: Path, attachment: Path) -> AttachmentRenderReceipt:
    if os.name != "nt":
        raise AttachmentRenderError("attachment rendering is Windows-only")
    project = Path(project_root).resolve(strict=True)
    original, source_data = _contained_original(project, Path(attachment))
    suffix = original.suffix.lower()
    if suffix not in SUPPORTED_IMAGES | SUPPORTED_DOCUMENTS:
        raise AttachmentRenderError(f"unsupported attachment renderer for {original.name}")
    original_sha = _digest(source_data)
    renderer = _renderer_identity(suffix)
    original_relative = original.relative_to(project).as_posix()
    identity = hashlib.sha256(f"{original_relative}\0{original_sha}\0{renderer}".encode()).hexdigest()
    final_dir = project / ROOT / identity
    receipt_path = final_dir / "receipt.json"
    cache_root = project / ROOT
    work_parent = project / "02_v6"
    if os.path.lexists(work_parent) and _is_link_or_reparse(work_parent):
        raise AttachmentRenderError("attachment render staging root is a reparse point")
    # Cache inspection is a short serialized phase. Rendering never owns the global mutation lock.
    with mutation_lock(project):
        cache_root.mkdir(parents=True, exist_ok=True)
        if _is_link_or_reparse(cache_root):
            raise AttachmentRenderError("attachment render cache root is a reparse point")
        from awesome_page_materials import _open_material_directory
        cache_descriptor = _open_material_directory(cache_root)
        root_descriptor = _open_project_root_handle(project)
        try:
            _verify_handle_within(root_descriptor, cache_descriptor)
            reused = _validate_reuse(project, receipt_path, original_sha, renderer, original_relative, len(source_data))
            if reused is not None:
                return reused
            if final_dir.exists():
                raise AttachmentRenderError(f"attachment render cache is incomplete or changed: {original.name}")
            _recover_pending(cache_root, cache_descriptor, root_descriptor, identity)
        finally:
            os.close(cache_descriptor)
            os.close(root_descriptor)
    work_parent.mkdir(exist_ok=True)
    if _is_link_or_reparse(work_parent):
        raise AttachmentRenderError("attachment render staging root is a reparse point")
    from awesome_page_materials import _open_material_directory
    project_descriptor = _open_project_root_handle(project)
    work_descriptor = _open_material_directory(work_parent)
    staging_descriptor = -1
    try:
        _verify_handle_within(project_descriptor, work_descriptor)
        temporary = Path(tempfile.mkdtemp(prefix="attachment-render-", dir=work_parent))
        staging_descriptor = _open_material_directory(temporary)
        _verify_handle_within(project_descriptor, staging_descriptor)
    except Exception:
        if staging_descriptor >= 0:
            os.close(staging_descriptor)
        os.close(work_descriptor)
        os.close(project_descriptor)
        raise
    try:
        snapshot = temporary / f"source{suffix}"
        snapshot.write_bytes(source_data)
        if suffix in {".docx", ".pptx", ".xlsx"}:
            _reject_external_ooxml_relationships(snapshot)
        produced: list[Path]
        if suffix in SUPPORTED_IMAGES:
            image, _mime = validated_raster_bytes(source_data)
            produced = [temporary / "page_0001.png"]
            image.save(produced[0], "PNG", optimize=False, compress_level=9)
        else:
            pdf = snapshot if suffix == ".pdf" else temporary / "converted.pdf"
            if suffix != ".pdf":
                _office_to_pdf(snapshot, suffix, pdf)
            produced = _render_pdf(pdf, temporary)
        if not produced or len(produced) > MAX_RENDERED_PAGES:
            raise AttachmentRenderError("renderer produced an invalid page count")
        page_records: list[RenderedImage] = []
        artifact_buffers: list[tuple[str, bytes]] = []
        sheet_sources: list[Image.Image] = []
        total_pixels = 0
        total_output_bytes = 0
        for page_number, generated in enumerate(produced, start=1):
            data, width, height = _validated_png(generated)
            if width > MAX_EDGE or height > MAX_EDGE or width * height > MAX_DECODED_PIXELS:
                raise AttachmentRenderError("renderer output exceeds the image boundary")
            total_pixels += width * height
            if total_pixels > MAX_TOTAL_PIXELS:
                raise AttachmentRenderError("renderer output exceeds the total pixel boundary")
            total_output_bytes += len(data)
            if total_output_bytes > MAX_TOTAL_OUTPUT_BYTES:
                raise AttachmentRenderError("renderer output exceeds the total byte boundary")
            filename = f"page_{page_number:04d}.png"
            (temporary / filename).write_bytes(data)
            artifact_buffers.append((filename, data))
            sheet_sources.append(validated_raster_bytes(data)[0])
            page_records.append(RenderedImage(page_number, "", width, height, len(data), _digest(data)))
        contact = _contact_sheet(sheet_sources)
        contact_path = temporary / "contact_sheet.png"
        contact.save(contact_path, "PNG", optimize=False, compress_level=9)
        contact_data, contact_w, contact_h = _validated_png(contact_path)
        if total_output_bytes + len(contact_data) > MAX_TOTAL_OUTPUT_BYTES:
            raise AttachmentRenderError("renderer output exceeds the total byte boundary")
        contact_path.write_bytes(contact_data)
        artifact_buffers.append(("contact_sheet.png", contact_data))
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        relative_base = (ROOT / identity).as_posix()
        pages = tuple(
            RenderedImage(item.page_number, f"{relative_base}/page_{item.page_number:04d}.png", item.width, item.height, item.byte_size, item.sha256)
            for item in page_records
        )
        receipt = AttachmentRenderReceipt(
            "awesome-attachment-render-v1", original_relative, original_sha, len(source_data), renderer, pages,
            RenderedImage(0, f"{relative_base}/contact_sheet.png", contact_w, contact_h, len(contact_data), _digest(contact_data)),
        )
        validate_attachment_receipt(
            receipt.to_dict(), expected_identity=identity, expected_source_path=original_relative,
            expected_source_size=len(source_data),
        )
        receipt_data = _canonical(receipt.to_dict())
        (temporary / "receipt.json").write_bytes(receipt_data)
        snapshot.unlink(missing_ok=True)
        if suffix != ".pdf":
            (temporary / "converted.pdf").unlink(missing_ok=True)
        with mutation_lock(project):
            if _is_link_or_reparse(cache_root):
                raise AttachmentRenderError("attachment render cache root is a reparse point")
            from awesome_page_materials import _open_material_directory
            cache_descriptor = _open_material_directory(cache_root)
            root_descriptor = _open_project_root_handle(project)
            try:
                _verify_handle_within(root_descriptor, cache_descriptor)
                reused = _validate_reuse(project, receipt_path, original_sha, renderer, original_relative, len(source_data))
                if reused is not None:
                    return reused
                if os.path.lexists(final_dir):
                    raise AttachmentRenderError(f"attachment render cache is incomplete or changed: {original.name}")
                pending_name = f".pending-{identity}-{uuid.uuid4().hex}"
                pending_dir = cache_root / pending_name
                final_descriptor = _create_directory_relative(cache_descriptor, pending_name)
                published_handles: list[int] = []
                published_by_name: dict[str, int] = {}
                try:
                    _verify_handle_within(root_descriptor, final_descriptor)
                    for filename, data in artifact_buffers:
                        descriptor = _write_exclusive_relative(final_descriptor, filename, data)
                        published_handles.append(descriptor)
                        published_by_name[filename] = descriptor
                        _after_pending_write(pending_dir, filename)
                    # Receipt is the commit marker and is always created last.
                    descriptor = _write_exclusive_relative(final_descriptor, "receipt.json", receipt_data)
                    published_handles.append(descriptor)
                    published_by_name["receipt.json"] = descriptor
                    _after_pending_write(pending_dir, "receipt.json")
                    for descriptor in published_handles:
                        _verify_handle_within(root_descriptor, descriptor)
                    _after_pending_receipt_close(pending_dir, identity)
                    verified = _validate_open_cache(
                        final_descriptor, identity, original_sha, renderer, original_relative, len(source_data),
                        receipt_data=receipt_data, artifact_buffers=artifact_buffers, held_handles=published_by_name,
                    )
                    # Verification is complete. Windows will not rename a directory
                    # while children deny delete sharing, so release only the child
                    # handles; retain both the directory and cache-root handles.
                    for descriptor in published_handles:
                        os.close(descriptor)
                    published_handles.clear()
                    published_by_name.clear()
                    _rename_relative_handle(final_descriptor, cache_descriptor, identity)
                    return verified
                finally:
                    for descriptor in published_handles:
                        os.close(descriptor)
                    os.close(final_descriptor)
            finally:
                os.close(cache_descriptor)
                os.close(root_descriptor)
    except AttachmentRenderError:
        raise
    except Exception as exc:
        raise AttachmentRenderError(f"renderer failed for {original.name}: {exc}") from exc
    finally:
        if staging_descriptor >= 0:
            os.close(staging_descriptor)
        os.close(work_descriptor)
        os.close(project_descriptor)
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _after_lease_root_open(_descriptor: int) -> None:
    """Test seam while lease-root custody is held."""


@contextmanager
def _page_render_lease(project_root: Path, page_number: int, timeout: float = 30.0):
    """Own one page across rendering and its eventual material-state commit."""
    project = Path(project_root).resolve(strict=True)
    if type(page_number) is not int or page_number < 1:
        raise AttachmentRenderError(f"invalid owning page {page_number}")
    parent = project / "02_v6"
    lease_root = parent / "page_render_leases"
    parent_descriptor = lease_descriptor = -1
    with mutation_lock(project):
        parent.mkdir(parents=True, exist_ok=True)
        from awesome_page_materials import _open_material_directory
        root_descriptor = _open_project_root_handle(project)
        parent_descriptor = _open_material_directory(parent)
        try:
            _verify_handle_within(root_descriptor, parent_descriptor)
            try:
                lease_descriptor = _create_directory_relative(parent_descriptor, "page_render_leases", rename_access=False)
            except FileExistsError:
                lease_descriptor = _open_directory_relative(parent_descriptor, "page_render_leases", rename_access=False)
            _verify_handle_within(root_descriptor, lease_descriptor)
            _after_lease_root_open(lease_descriptor)
            descriptor = _open_lock_relative(lease_descriptor, f"page_{page_number:03d}.lock")
            _verify_handle_within(root_descriptor, descriptor)
        finally:
            os.close(root_descriptor)
    handle = os.fdopen(descriptor, "r+b", closefd=True)
    acquired = False
    deadline = time.monotonic() + timeout
    lease = _PageLease(project, page_number, handle)
    try:
        root_descriptor = _open_project_root_handle(project)
        try:
            _verify_handle_within(root_descriptor, handle.fileno())
        finally:
            os.close(root_descriptor)
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0"); handle.flush(); os.fsync(handle.fileno())
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise AttachmentRenderError(f"timed out acquiring attachment render ownership for page {page_number}")
                time.sleep(0.01)
        _ACTIVE_PAGE_LEASES.add(lease)
        yield lease
    finally:
        lease.active = False
        _ACTIVE_PAGE_LEASES.discard(lease)
        if acquired:
            if os.name == "nt":
                import msvcrt
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        if lease_descriptor >= 0:
            os.close(lease_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _validate_page_lease(lease: object, project: Path, page_number: int) -> _PageLease:
    if not isinstance(lease, _PageLease) or lease not in _ACTIVE_PAGE_LEASES or not lease.active:
        raise AttachmentRenderError("page render lease is invalid or stale")
    if lease.project != project.resolve(strict=True) or lease.page_number != page_number or lease.handle.closed:
        raise AttachmentRenderError("page render lease does not own this project/page")
    os.fstat(lease.handle.fileno())
    return lease


def render_page_attachments(
    project_root: Path, page_number: int, attachments: list[Path],
) -> list[AttachmentRenderReceipt]:
    with _page_render_lease(project_root, page_number) as lease:
        return _render_page_attachments_owned(project_root, page_number, attachments, lease)


def _render_page_attachments_owned(
    project_root: Path, page_number: int, attachments: list[Path], lease: object,
) -> list[AttachmentRenderReceipt]:
    project = Path(project_root).resolve(strict=True)
    _validate_page_lease(lease, project, page_number)
    with mutation_lock(project):
        state = load(project)
        if type(page_number) is not int or not 1 <= page_number <= len(state["pages"]):
            raise AttachmentRenderError(f"invalid owning page {page_number}")
        if state["pages"][page_number - 1]["state"] != "prepared":
            raise AttachmentRenderError(
                f"attachment rendering requires prepared page state; page {page_number} is {state['pages'][page_number - 1]['state']}"
            )
    try:
        return [render_attachment(project, item) for item in attachments]
    except AttachmentRenderError as exc:
        with mutation_lock(project):
            state = load(project)
            if type(page_number) is not int or not 1 <= page_number <= len(state["pages"]):
                raise AttachmentRenderError(f"invalid owning page {page_number}: {exc}") from exc
            page = state["pages"][page_number - 1]
            try:
                if page["state"] != "technical_failed":
                    page = transition_page(page, "technical_failed")
            except ValueError as transition_error:
                raise AttachmentRenderError(
                    f"attachment render failed for page {page_number}, but page state {page['state']} cannot enter technical_failed: {exc}"
                ) from transition_error
            page["technical_failure"] = {
                "stage": "attachment_render",
                "detail": str(exc),
                "retryable": False,
            }
            state["pages"][page_number - 1] = page
            save(project, state)
        raise

"""Collect one page's complete Word-owned inputs without semantic rewriting."""

from __future__ import annotations

import copy
import json
import os
import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from fixed_region_contract import BODY_BOX_CM, CONTRACT_VERSION
from workflow_v6_state import load, mutation_lock, save
from workflow_v6_contract import validate_material_receipts
from workflow_v6_media import _open_project_root_handle, _verify_handle_within


SOURCE_MANIFEST = Path("02_v6/paginated_word_source.json")
ASSET_MANIFEST = Path("02_v6/source_assets.json")
SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "awesome_page_materials_v1.schema.json"


def _load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _page(manifest: Mapping[str, Any], page_number: int) -> Mapping[str, Any]:
    pages = manifest.get("pages")
    if not isinstance(pages, list):
        raise ValueError("paginated Word source manifest has no page list")
    matches = [item for item in pages if isinstance(item, Mapping) and item.get("page_number") == page_number]
    if len(matches) != 1:
        raise ValueError(f"paginated Word source has no unique page {page_number}")
    return matches[0]


def _complete_body(page: Mapping[str, Any]) -> list[dict[str, Any]]:
    blocks = page.get("blocks")
    if not isinstance(blocks, list):
        raise ValueError("paginated Word page blocks must be a list")
    title_id = page.get("fixed_page_title_source_block_id")
    return [
        copy.deepcopy(dict(block)) for block in blocks
        if isinstance(block, Mapping) and block.get("source_block_id") != title_id
    ]


def _comments(page: Mapping[str, Any]) -> list[dict[str, Any]]:
    comments = page.get("page_comments", [])
    if not isinstance(comments, list):
        raise ValueError("paginated Word page comments must be a list")
    result: list[dict[str, Any]] = []
    for order, comment in enumerate(comments, start=1):
        if not isinstance(comment, Mapping):
            raise ValueError("paginated Word page comment is invalid")
        comment_id = comment.get("comment_id")
        text = comment.get("text")
        if not isinstance(comment_id, str) or not isinstance(text, str) or not text:
            raise ValueError("paginated Word page comment identity or text is invalid")
        result.append({"comment_id": comment_id, "source_order": order, "text": text})
    return result


def _asset_inputs(
    project: Path, manifest: Mapping[str, Any], page_number: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assets = manifest.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("source asset manifest assets must be a list")
    word_images: list[dict[str, Any]] = []
    attachments: list[dict[str, Any]] = []
    for source_order, asset in enumerate(assets, start=1):
        if not isinstance(asset, Mapping) or page_number not in asset.get("page_numbers", []):
            continue
        relative_path = asset.get("relative_path")
        if not isinstance(relative_path, str):
            raise ValueError("page-owned source asset has no original relative path")
        record = {
            "asset_id": asset.get("asset_id"),
            "source_order": source_order,
            "original_filename": asset.get("original_filename"),
            "media_type": asset.get("media_type"),
            "path": (Path("01_source_assets") / relative_path).as_posix(),
            "sha256": asset.get("sha256"),
            "byte_size": asset.get("byte_size"),
        }
        if str(asset.get("media_type", "")).startswith("image/"):
            word_images.append(record)
        else:
            attachments.append(record)
    return word_images, attachments


def collect_page_materials(project_root: Path, page_number: int) -> dict[str, Any]:
    """Return the lossless page authority from immutable project manifests."""
    if type(page_number) is not int or page_number < 1:
        raise ValueError("page_number must be a positive integer")
    project = Path(project_root).resolve(strict=True)
    state = load(project)
    if state["style_confirmation"]["status"] != "confirmed" or state["page_materials_status"] not in {
        "pending",
        "confirmed",
    }:
        raise ValueError("prepare-page-materials requires a confirmed visual contract and pending materials")
    if page_number > len(state["pages"]):
        raise ValueError("page_number is out of range")
    source = _load_object(project / SOURCE_MANIFEST, "paginated Word source manifest")
    assets = _load_object(project / ASSET_MANIFEST, "source asset manifest")
    page = _page(source, page_number)
    identified_title = page.get("fixed_page_title", state["pages"][page_number - 1]["title"])
    if not isinstance(identified_title, str) or not identified_title:
        raise ValueError("paginated Word page has no fixed title identification")
    fixed_title = identified_title
    word_images, attachment_inputs = _asset_inputs(project, assets, page_number)
    result = {
        "page_number": page_number,
        "fixed_page_title": fixed_title,
        "complete_word_content": _complete_body(page),
        "original_comments": _comments(page),
        "word_images": word_images,
        "attachment_inputs": attachment_inputs,
        "visual_contract": copy.deepcopy(dict(state["style_confirmation"]["contract"])),
        "body_frame": {
            "geometry_version": CONTRACT_VERSION,
            "body_bounds_cm": dict(BODY_BOX_CM),
            "body_pixels": {"width": 1904, "height": 896},
            "fixed_layers": ["title", "logo", "footer", "page_number"],
        },
    }
    validate_page_materials(result)
    return result


def _render_attachment_inputs_owned(project_root: Path, page_number: int, lease: object) -> list[dict[str, Any]]:
    """Render this page's attachment candidates before prompt compilation."""
    from awesome_attachment_render import (
        AttachmentRenderError,
        SUPPORTED_DOCUMENTS,
        SUPPORTED_IMAGES,
        _render_page_attachments_owned,
    )

    project = Path(project_root).resolve(strict=True)
    materials = collect_page_materials(project, page_number)
    inputs = list(materials["attachment_inputs"])
    if not inputs:
        return []
    supported = SUPPORTED_IMAGES | SUPPORTED_DOCUMENTS
    renderable = [
        (index, item)
        for index, item in enumerate(inputs)
        if Path(str(item["path"])).suffix.lower() in supported
    ]
    if not renderable:
        return inputs
    paths = [project / item["path"] for _index, item in renderable]
    try:
        receipts = _render_page_attachments_owned(project, page_number, paths, lease)
    except AttachmentRenderError:
        raise
    rendered = list(inputs)
    for (index, item), receipt in zip(renderable, receipts, strict=True):
        rendered[index] = {**item, "render_receipt": receipt.to_dict()}
    return rendered


def validate_page_materials(value: Mapping[str, Any]) -> None:
    schema = _load_object(SCHEMA, "awesome page materials schema")
    Draft202012Validator(schema).validate(dict(value))
    forbidden = {"unsupported_comment", "classification", "search", "search_requests", "summary", "degradation", "degradations"}

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            overlap = forbidden.intersection(item)
            if overlap:
                raise ValueError(f"page materials contain forbidden semantic fields: {sorted(overlap)}")
            for nested in item.values():
                walk(nested)
        elif isinstance(item, list):
            for nested in item:
                walk(nested)

    walk(value)


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(metadata, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _secure_material_directory(project: Path) -> Path:
    directory = project
    for component in ("02_v6", "awesome_page_materials"):
        directory = directory / component
        if os.path.lexists(directory):
            if not directory.is_dir() or _is_link_or_reparse(directory):
                raise ValueError("page-material output path must not contain a reparse point")
        else:
            directory.mkdir()
            if _is_link_or_reparse(directory):
                raise ValueError("page-material output path must not contain a reparse point")
    return directory


def _read_contained(project: Path, path: Path) -> bytes:
    if _is_link_or_reparse(path):
        raise ValueError("page-material artifact must not be a link or reparse point")
    root_descriptor = _open_project_root_handle(project)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            _verify_handle_within(root_descriptor, handle.fileno())
            before = os.fstat(handle.fileno())
            data = handle.read()
            if _is_link_or_reparse(path) or not os.path.samestat(before, path.stat()):
                raise ValueError("page-material artifact identity changed during verification")
            return data
    finally:
        os.close(root_descriptor)


def _decode_materials(data: bytes) -> dict[str, Any]:
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("published page materials must be a JSON object")
    validate_page_materials(value)
    return value


def _open_material_directory(path: Path) -> int:
    """Open the already-validated directory itself; callers create relative to this handle."""
    if os.name != "nt":
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        return os.open(path, flags)
    import ctypes
    import msvcrt
    from ctypes import wintypes

    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    native = create_file(
        str(path), 0x80000000, 0x00000001 | 0x00000002, None,
        3, 0x02000000 | 0x00200000, None,
    )
    if native == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), "CreateFileW failed for page-material directory", str(path))
    try:
        return msvcrt.open_osfhandle(native, os.O_RDONLY)
    except OSError:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(native)
        raise


def _open_exclusive_material(directory_descriptor: int, filename: str) -> int:
    """Create a file relative to a held safe directory, never by re-resolving its pathname."""
    if os.name != "nt":
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        return os.open(filename, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory_descriptor)
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", wintypes.LPWSTR)]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(UnicodeString)), ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", ctypes.c_void_p), ("SecurityQualityOfService", ctypes.c_void_p),
        ]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    name_buffer = ctypes.create_unicode_buffer(filename)
    name = UnicodeString(
        len(filename.encode("utf-16-le")), (len(filename) + 1) * 2,
        ctypes.cast(name_buffer, wintypes.LPWSTR),
    )
    attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes), msvcrt.get_osfhandle(directory_descriptor),
        ctypes.pointer(name), 0x40 | 0x1000, None, None,
    )
    status_block = IoStatusBlock()
    native = wintypes.HANDLE()
    nt_create = ctypes.WinDLL("ntdll").NtCreateFile
    nt_create.argtypes = (
        ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, ctypes.POINTER(ObjectAttributes),
        ctypes.POINTER(IoStatusBlock), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG,
        wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
    )
    nt_create.restype = ctypes.c_long
    status = nt_create(
        ctypes.byref(native), 0x80000000 | 0x40000000 | 0x00010000 | 0x00100000,
        ctypes.byref(attributes), ctypes.byref(status_block), None, 0x80,
        0x00000001 | 0x00000002 | 0x00000004, 2,
        0x00000040 | 0x00000020 | 0x00200000, None, 0,
    )
    if status < 0:
        if status in {-1073741772, -1073741771}:  # STATUS_OBJECT_NAME_NOT_FOUND / STATUS_OBJECT_NAME_COLLISION
            raise FileExistsError(status, "page materials already exist", filename)
        if status == -1073741184:  # STATUS_REPARSE_POINT_ENCOUNTERED
            raise ValueError("page-material output path contains a reparse point")
        raise OSError(status, "NtCreateFile failed for page materials", filename)
    try:
        return msvcrt.open_osfhandle(native.value, os.O_RDWR | getattr(os, "O_BINARY", 0))
    except OSError:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(native.value)
        raise


def _open_relative_material(directory_descriptor: int, filename: str) -> int:
    """Open an existing regular file relative to a held directory without following reparses."""
    if os.name != "nt":
        return os.open(filename, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_descriptor)
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
    status_block = IoStatusBlock()
    native = wintypes.HANDLE()
    nt_create = ctypes.WinDLL("ntdll").NtCreateFile
    nt_create.argtypes = (ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, ctypes.POINTER(ObjectAttributes), ctypes.POINTER(IoStatusBlock), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG)
    nt_create.restype = ctypes.c_long
    status = nt_create(ctypes.byref(native), 0x80000000 | 0x00100000, ctypes.byref(attributes), ctypes.byref(status_block), None, 0x80, 0x00000001 | 0x00000002, 1, 0x00000040 | 0x00000020 | 0x00200000, None, 0)
    if status < 0:
        raise OSError(status, "NtCreateFile failed for relative material", filename)
    try:
        return msvcrt.open_osfhandle(native.value, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except OSError:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(native.value)
        raise


def _delete_open_material_handle(descriptor: int, path: Path) -> None:
    """Delete the exact newly-created object, even if its pathname parent was swapped."""
    if os.name != "nt":
        path.unlink(missing_ok=True)
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class FileDispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOL)]

    disposition = FileDispositionInfo(True)
    set_information = ctypes.WinDLL("kernel32", use_last_error=True).SetFileInformationByHandle
    set_information.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    set_information.restype = wintypes.BOOL
    if not set_information(
        msvcrt.get_osfhandle(descriptor), 4,
        ctypes.byref(disposition), ctypes.sizeof(disposition),
    ):
        raise OSError(ctypes.get_last_error(), "failed to delete rejected page-material handle")


def publish_page_materials(project_root: Path, page_number: int, output: Path) -> dict[str, Any]:
    """Validate, atomically publish canonical JSON, and record truthful readiness."""
    if os.name != "nt":
        raise RuntimeError("secure page-material publication is unsupported on this platform")
    project = Path(project_root).resolve(strict=True)
    expected = project / "02_v6" / "awesome_page_materials" / f"page_{page_number:03d}.json"
    destination = Path(os.path.abspath(output))
    if destination != expected:
        raise ValueError("--out must be the canonical project page-material path")
    from awesome_attachment_render import _page_render_lease
    with _page_render_lease(project, page_number) as lease:
        rendered_attachments = _render_attachment_inputs_owned(project, page_number, lease)
        with mutation_lock(project):
            state = load(project)
            value = collect_page_materials(project, page_number)
            value["attachment_inputs"] = rendered_attachments
            validate_page_materials(value)
            payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            digest = hashlib.sha256(payload).hexdigest()
            material_directory = _secure_material_directory(project)
            root_descriptor = _open_project_root_handle(project)
            directory_descriptor = -1
            try:
                directory_descriptor = _open_material_directory(material_directory)
                _verify_handle_within(root_descriptor, directory_descriptor)
                return _publish_with_held_directory(
                    project, page_number, destination, state, value, payload, digest, directory_descriptor, root_descriptor
                )
            finally:
                if directory_descriptor >= 0:
                    os.close(directory_descriptor)
                os.close(root_descriptor)


def _publish_with_held_directory(
    project: Path,
    page_number: int,
    destination: Path,
    state: dict[str, Any],
    value: dict[str, Any],
    payload: bytes,
    digest: str,
    directory_descriptor: int,
    root_descriptor: int,
) -> dict[str, Any]:
    """Complete artifact, receipt, and state transaction while its parent cannot be moved."""
    existing_receipt = state["pages"][page_number - 1]["material_receipt"]
    if existing_receipt is not None:
        data = _read_contained(project, destination)
        published = _decode_materials(data)
        if hashlib.sha256(data).hexdigest() != existing_receipt["digest"]:
            raise ValueError("published page materials diverge from the durable receipt")
        if digest != existing_receipt["digest"]:
            raise ValueError("requested source materials diverge from the durable receipt")
        return published
    recovered_or_created = False
    if os.path.lexists(destination):
        data = _read_contained(project, destination)
        _decode_materials(data)
        if data != payload:
            raise ValueError("page materials are already published without a matching receipt")
        recovered_or_created = True
    else:
        descriptor = _open_exclusive_material(directory_descriptor, destination.name)
        with os.fdopen(descriptor, "wb") as handle:
            _verify_handle_within(root_descriptor, handle.fileno())
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if _read_contained(project, destination) != payload:
            destination.unlink(missing_ok=True)
            raise ValueError("published page materials failed stable verification")
        recovered_or_created = True
    if state["page_materials_status"] not in {"pending", "confirmed"}:
        raise ValueError("page materials state changed before publication")
    page = copy.deepcopy(state["pages"][page_number - 1])
    page["material_state"] = "available"
    page["material_receipt"] = {
        "schema_version": "awesome-page-materials-v1",
        "page_number": page_number,
        "path": destination.relative_to(project).as_posix(),
        "digest": digest,
    }
    state["pages"][page_number - 1] = page
    state["page_materials_status"] = (
        "confirmed" if all(item["material_state"] == "available" for item in state["pages"]) else "pending"
    )
    try:
        validate_material_receipts(project, state)
        save(project, state)
    except Exception:
        if recovered_or_created:
            destination.unlink(missing_ok=True)
        raise
    return value

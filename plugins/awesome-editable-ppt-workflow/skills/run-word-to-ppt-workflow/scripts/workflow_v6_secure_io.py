"""Handle-relative, fail-closed project I/O for sealed V6 artifacts.

The Windows implementation deliberately holds the project root and every
ancestor directory without FILE_SHARE_DELETE from validation through publish.
No caller supplied pathname is accepted: every target is a canonical relative
path chosen by the workflow.
"""

from __future__ import annotations

import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator


def _secure_io_boundary(_root: Path, _relative: Path, _stage: str) -> None:
    """Fault-injection seam for real directory-replacement tests."""


def _parts(relative: Path | PurePosixPath | str) -> tuple[str, ...]:
    raw = str(relative).replace("\\", "/")
    value = PurePosixPath(raw)
    parts = value.parts
    if value.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("secure project path must be canonical and relative")
    if ":" in parts[0] or str(value) != raw:
        raise ValueError("secure project path must be canonical and relative")
    return tuple(parts)


def reject_reparse_chain(path: Path) -> None:
    """Reject a reparse point in any existing literal ancestor before resolve."""
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            raise ValueError("secure project root does not exist")
        if current.is_symlink() or bool(
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise ValueError("secure project root or ancestor is a reparse point")


def _open_root(root: Path) -> int:
    if os.name != "nt":
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        return os.open(root, flags)
    from awesome_page_materials import _open_material_directory
    return _open_material_directory(root)


def _directory_relative(parent: int, name: str, *, create: bool) -> int:
    if os.name != "nt":
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        if create:
            os.mkdir(name, mode=0o700, dir_fd=parent)
        return os.open(name, flags, dir_fd=parent)
    from awesome_attachment_render import _create_directory_relative, _open_directory_relative
    # Denying FILE_SHARE_DELETE is sufficient to prevent replacement.  Do not
    # request DELETE access ourselves: concurrent page writers must be able to
    # hold the same ancestors at the same time.
    return (_create_directory_relative if create else _open_directory_relative)(
        parent, name, rename_access=False,
    )


@contextmanager
def _held_parent(root: Path, relative: Path | PurePosixPath | str, *, create: bool) -> Iterator[tuple[int, str]]:
    parts = _parts(relative)
    literal_root = Path(os.path.abspath(root))
    reject_reparse_chain(literal_root)
    root = literal_root.resolve(strict=True)
    handles = [_open_root(root)]
    try:
        for part in parts[:-1]:
            try:
                child = _directory_relative(handles[-1], part, create=False)
            except (FileNotFoundError, OSError) as exc:
                if not create:
                    raise
                try:
                    child = _directory_relative(handles[-1], part, create=True)
                except FileExistsError:
                    child = _directory_relative(handles[-1], part, create=False)
            handles.append(child)
        yield handles[-1], parts[-1]
    finally:
        for handle in reversed(handles):
            os.close(handle)


def _open_relative(parent: int, name: str, *, write_new: bool = False) -> int:
    if not write_new:
        if os.name != "nt":
            return os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
        from awesome_page_materials import _open_relative_material
        return _open_relative_material(parent, name)
    if os.name != "nt":
        return os.open(
            name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent,
        )

    import ctypes
    import msvcrt
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", wintypes.LPWSTR)]
    class ObjectAttributes(ctypes.Structure):
        _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
                    ("ObjectName", ctypes.POINTER(UnicodeString)), ("Attributes", wintypes.ULONG),
                    ("SecurityDescriptor", ctypes.c_void_p), ("SecurityQualityOfService", ctypes.c_void_p)]
    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    buffer = ctypes.create_unicode_buffer(name)
    unicode_name = UnicodeString(len(name.encode("utf-16-le")), (len(name) + 1) * 2,
                                 ctypes.cast(buffer, wintypes.LPWSTR))
    attributes = ObjectAttributes(ctypes.sizeof(ObjectAttributes), msvcrt.get_osfhandle(parent),
                                  ctypes.pointer(unicode_name), 0x40 | 0x1000, None, None)
    status_block = IoStatusBlock(); native = wintypes.HANDLE()
    nt_create = ctypes.WinDLL("ntdll").NtCreateFile
    nt_create.restype = ctypes.c_long
    status = nt_create(
        ctypes.byref(native), 0x80000000 | 0x40000000 | 0x00010000 | 0x00100000,
        ctypes.byref(attributes), ctypes.byref(status_block), None, 0x80,
        0x00000001 | 0x00000002, 2, 0x00000040 | 0x00000020 | 0x00200000, None, 0,
    )
    if status < 0:
        if status in {-1073741771, -1073741772}:
            raise FileExistsError(status, "secure artifact already exists", name)
        raise OSError(status, "secure relative file create failed", name)
    return msvcrt.open_osfhandle(native.value, os.O_RDWR | getattr(os, "O_BINARY", 0))


def _rename(descriptor: int, parent: int, name: str, *, replace: bool) -> None:
    if os.name != "nt":
        raise RuntimeError("secure atomic project publication is Windows-only")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class RenameInfo(ctypes.Structure):
        _fields_ = [("ReplaceIfExists", wintypes.BOOL), ("RootDirectory", wintypes.HANDLE),
                    ("FileNameLength", wintypes.DWORD), ("FileName", wintypes.WCHAR * 1)]
    encoded = name.encode("utf-16-le")
    buffer = ctypes.create_string_buffer(RenameInfo.FileName.offset + len(encoded))
    info = ctypes.cast(buffer, ctypes.POINTER(RenameInfo)).contents
    info.ReplaceIfExists = replace
    info.RootDirectory = msvcrt.get_osfhandle(parent)
    info.FileNameLength = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.FileName.offset, encoded, len(encoded))
    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]
    status_block = IoStatusBlock()
    nt_set = ctypes.WinDLL("ntdll").NtSetInformationFile
    nt_set.restype = ctypes.c_long
    status = nt_set(msvcrt.get_osfhandle(descriptor), ctypes.byref(status_block), buffer, len(buffer), 10)
    if status < 0:
        if status == -1073741771:
            raise FileExistsError(status, "secure artifact already exists", name)
        raise OSError(status, "secure relative rename failed", name)


def _delete(descriptor: int) -> None:
    if os.name != "nt":
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes
    class FileDispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOL)]
    value = FileDispositionInfo(True)
    setter = ctypes.WinDLL("kernel32", use_last_error=True).SetFileInformationByHandle
    if not setter(msvcrt.get_osfhandle(descriptor), 4, ctypes.byref(value), ctypes.sizeof(value)):
        raise OSError(ctypes.get_last_error(), "secure temporary cleanup failed")


def read_bytes(root: Path, relative: Path | PurePosixPath | str, *, max_bytes: int = 96 * 1024 * 1024) -> bytes:
    """Read one stable regular-file handle while all ancestors remain held."""
    with _held_parent(root, relative, create=False) as (parent, name):
        descriptor = _open_relative(parent, name)
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
                raise ValueError("secure project artifact is not a bounded regular file")
            data = handle.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError("secure project artifact exceeds its byte limit")
            return data


def atomic_write_bytes(
    root: Path,
    relative: Path | PurePosixPath | str,
    data: bytes,
    *,
    replace: bool = False,
) -> Path:
    """Publish bytes by handle-relative create and atomic rename."""
    if os.name != "nt":
        raise RuntimeError("secure atomic project publication is Windows-only")
    parts = _parts(relative)
    relative_path = Path(*parts)
    with _held_parent(root, relative_path, create=True) as (parent, name):
        temporary_name = f".{name}.{secrets.token_hex(12)}.tmp"
        descriptor = _open_relative(parent, temporary_name, write_new=True)
        published = False
        try:
            with os.fdopen(descriptor, "w+b", closefd=False) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
                _secure_io_boundary(Path(root), relative_path, "before_publish")
                _rename(handle.fileno(), parent, name, replace=replace)
                published = True
                handle.seek(0)
                if handle.read() != data:
                    raise ValueError("secure artifact verification failed")
        finally:
            if not published:
                _delete(descriptor)
            os.close(descriptor)
        return Path(root) / relative_path


def atomic_write_json(root: Path, relative: Path | PurePosixPath | str, text: str, *, replace: bool = False) -> Path:
    return atomic_write_bytes(root, relative, text.encode("utf-8"), replace=replace)


@contextmanager
def hold_parent(root: Path, relative: Path | PurePosixPath | str, *, create: bool = False) -> Iterator[None]:
    """Hold the full ancestor chain when a library must publish to a pathname."""
    with _held_parent(root, relative, create=create):
        yield

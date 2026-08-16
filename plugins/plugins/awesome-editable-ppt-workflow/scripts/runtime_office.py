"""Shared, lazy Microsoft Office and LibreOffice runtime discovery."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable


class NoRenderBackendError(RuntimeError):
    """No local presentation renderer is installed or configured."""


def _windows_registry_candidates() -> Iterable[Path]:
    if os.name != "nt":
        return ()
    try:
        import winreg
    except ImportError:
        return ()
    values: list[Path] = []
    locations = (
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\soffice.exe"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\soffice.exe"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\soffice.exe"),
    )
    for hive, key_name in locations:
        try:
            with winreg.OpenKey(hive, key_name) as key:
                value, _kind = winreg.QueryValueEx(key, None)
        except OSError:
            continue
        if isinstance(value, str) and value.strip():
            values.append(Path(value.strip().strip('"')))
    return tuple(values)


def resolve_soffice(explicit: str | Path | None = None) -> str | None:
    """Resolve LibreOffice only when a caller actually needs the fallback."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    configured = os.getenv("SOFFICE_EXE")
    if configured:
        candidates.append(Path(configured))
    home = os.getenv("LIBREOFFICE_HOME")
    if home:
        candidates.append(Path(home) / "program" / "soffice.exe")
        candidates.append(Path(home) / "soffice")
    for command in ("soffice", "soffice.exe", "libreoffice"):
        discovered = shutil.which(command)
        if discovered:
            candidates.append(Path(discovered))
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.getenv(variable)
        if base:
            candidates.append(Path(base) / "LibreOffice" / "program" / "soffice.exe")
    candidates.extend(_windows_registry_candidates())
    seen: set[str] = set()
    for candidate in candidates:
        normalized = os.path.normcase(os.path.abspath(os.path.expandvars(str(candidate))))
        if normalized in seen:
            continue
        seen.add(normalized)
        path = Path(normalized)
        if path.is_file():
            return str(path.resolve())
    return None

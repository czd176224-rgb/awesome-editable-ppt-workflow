"""Office smoke-test fallback and backend detection regression tests."""

from __future__ import annotations

import sys
import subprocess
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import doctor  # noqa: E402
import extract_docx_pages  # noqa: E402
import render_pptx  # noqa: E402


def test_editable_python_prefers_installer_executable_over_cmd_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    executable = tmp_path / "runtime" / "editable-ppt" / "Scripts" / "editppt.exe"
    python = executable.parent / "python.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    python.touch()
    wrapper = tmp_path / "bin" / "editppt.CMD"
    wrapper.parent.mkdir()
    wrapper.write_text(f'@echo off\n"{executable}" %*\n', encoding="ascii")
    monkeypatch.setenv("EDITPPT_EXE", str(executable))

    assert doctor._editable_python_path(str(wrapper)) == python


def test_core_office_smoke_never_uses_detected_officecli(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        doctor,
        "_create_officecli_smoke",
        lambda *_args: pytest.fail("optional OfficeCLI must never create the core smoke deck"),
    )
    monkeypatch.setattr(
        doctor,
        "_powerpoint_open_smoke",
        lambda _pptx: {"available": True, "passed": True, "detail": "PowerPoint opened 1 slide"},
    )
    monkeypatch.setattr(doctor, "resolve_soffice", lambda: None)

    result = doctor.office_smoke_test("broken-officecli.exe")

    assert result["passed"] is True
    assert result["backend"] == "powerpoint"
    assert "python-pptx" in result["detail"]


def test_broken_optional_officecli_is_warning_only_for_ready_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    editppt = tmp_path / "editable-ppt" / "Scripts" / "editppt.exe"
    editppt.parent.mkdir(parents=True)
    editppt.write_text("stub", encoding="utf-8")
    (editppt.parent / "python.exe").write_text("stub", encoding="utf-8")
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(
        doctor,
        "external_skill_status",
        lambda: {
            "codex_gpt_image_script": "image.py", "codex_gpt_image_available": True,
            "codex_oauth_available": True, "codex_auth_file": "auth.json",
            "image_to_editable_ppt_available": True, "officecli_available": True,
        },
    )
    monkeypatch.setattr(
        doctor,
        "command_version",
        lambda command, _args: (
            {"available": True, "path": str(editppt), "detail": "ok"}
            if command == "editppt"
            else {"available": command == "officecli", "path": "broken-officecli.exe", "detail": "detected"}
        ),
    )
    monkeypatch.setattr(doctor, "cjk_font_status", lambda: {"ready": True})
    monkeypatch.setattr(doctor, "background_text_detection_status", lambda: {"available": True})
    monkeypatch.setattr(
        doctor,
        "office_smoke_test",
        lambda *_args, **_kwargs: {
            "passed": True, "backend": "powerpoint", "detail": "core rendered",
            "powerpoint": {"available": True, "detail": "PowerPoint opened"},
        },
    )
    monkeypatch.setattr(
        doctor,
        "_create_officecli_smoke",
        lambda *_args: {"passed": False, "detail": "optional OfficeCLI is broken"},
    )
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="0.3.0\n", stderr=""),
    )

    result = doctor.diagnose(check_powerpoint=True, smoke_test=True)

    assert result["officecli_optional"]["diagnostic"]["passed"] is False
    assert result["officecli_optional"]["warning"] is True
    assert result["workflow_ready"] is True
    assert result["render_backend_available"] is True
    assert result["high_quality_ready"] is True


def test_office_smoke_prefers_powerpoint_and_does_not_call_soffice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    officecli = tmp_path / "officecli.exe"
    officecli.touch()
    calls: list[str] = []

    def fake_create(_officecli: str, pptx: Path) -> dict:
        pptx.write_bytes(b"pptx")
        return {"passed": True, "detail": "created"}

    monkeypatch.setattr(doctor, "_create_officecli_smoke", fake_create)
    monkeypatch.setattr(
        doctor,
        "_powerpoint_open_smoke",
        lambda _pptx: {"available": True, "passed": True, "detail": "PowerPoint opened 1 slide"},
    )
    monkeypatch.setattr(doctor, "resolve_soffice", lambda: calls.append("resolved") or "soffice.exe")

    result = doctor.office_smoke_test(str(officecli))

    assert result["passed"] is True
    assert result["backend"] == "powerpoint"
    assert calls == []


def test_office_smoke_falls_back_to_soffice_when_powerpoint_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    officecli = tmp_path / "officecli.exe"
    officecli.touch()

    def fake_create(_officecli: str, pptx: Path) -> dict:
        pptx.write_bytes(b"pptx")
        return {"passed": True, "detail": "created"}

    monkeypatch.setattr(doctor, "_create_officecli_smoke", fake_create)
    monkeypatch.setattr(
        doctor,
        "_powerpoint_open_smoke",
        lambda _pptx: {"available": False, "passed": False, "detail": "COM unavailable"},
    )
    monkeypatch.setattr(doctor, "resolve_soffice", lambda: "C:/LibreOffice/program/soffice.exe")
    monkeypatch.setattr(
        doctor,
        "_libreoffice_render_smoke",
        lambda path, _pptx, _temporary: {
            "available": True,
            "passed": True,
            "detail": f"rendered with {path}",
        },
    )

    result = doctor.office_smoke_test(str(officecli))

    assert result["passed"] is True
    assert result["backend"] == "libreoffice"
    assert "PowerPoint unavailable" in result["detail"]


def test_powerpoint_status_probe_timeout_is_bounded_and_never_runs_com_in_parent(
    monkeypatch: pytest.MonkeyPatch,
):
    def timed_out(command: list[str], **kwargs):
        assert kwargs["timeout"] == doctor.POWERPOINT_COM_LIFECYCLE_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(command, doctor.POWERPOINT_COM_LIFECYCLE_TIMEOUT_SECONDS)

    parent_package = types.ModuleType("win32com")
    parent_package.__path__ = []  # type: ignore[attr-defined]
    parent_com = types.ModuleType("win32com.client")
    parent_com.DispatchEx = lambda _name: pytest.fail(  # type: ignore[attr-defined]
        "PowerPoint COM must not run in the doctor parent process"
    )
    parent_package.client = parent_com  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "win32com", parent_package)
    monkeypatch.setitem(sys.modules, "win32com.client", parent_com)
    monkeypatch.setattr(doctor.platform, "system", lambda: "Windows")
    monkeypatch.setattr(doctor.subprocess, "run", timed_out)

    result = doctor.powerpoint_status()

    assert result["available"] is False
    assert f"timed out after {doctor.POWERPOINT_COM_LIFECYCLE_TIMEOUT_SECONDS} seconds" in result["detail"]


def test_powerpoint_open_timeout_falls_back_to_fake_soffice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    officecli = tmp_path / "officecli.exe"
    officecli.touch()

    def fake_create(_officecli: str, pptx: Path) -> dict:
        pptx.write_bytes(b"pptx")
        return {"passed": True, "detail": "created"}

    def timed_out(command: list[str], **kwargs):
        assert kwargs["timeout"] == doctor.POWERPOINT_COM_LIFECYCLE_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(command, doctor.POWERPOINT_COM_LIFECYCLE_TIMEOUT_SECONDS)

    monkeypatch.setattr(doctor, "_create_officecli_smoke", fake_create)
    monkeypatch.setattr(doctor.platform, "system", lambda: "Windows")
    monkeypatch.setattr(doctor.subprocess, "run", timed_out)
    monkeypatch.setattr(doctor, "resolve_soffice", lambda: "fake-soffice.exe")
    monkeypatch.setattr(
        doctor,
        "_libreoffice_render_smoke",
        lambda path, _pptx, _temporary: {
            "available": True,
            "passed": True,
            "detail": f"fake render with {path}",
        },
    )

    result = doctor.office_smoke_test(str(officecli))

    assert result["passed"] is True
    assert result["backend"] == "libreoffice"
    assert f"timed out after {doctor.POWERPOINT_COM_LIFECYCLE_TIMEOUT_SECONDS} seconds" in result["detail"]


def test_failed_bounded_status_probe_skips_a_second_com_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    officecli = tmp_path / "officecli.exe"
    officecli.touch()

    def fake_create(_officecli: str, pptx: Path) -> dict:
        pptx.write_bytes(b"pptx")
        return {"passed": True, "detail": "created"}

    monkeypatch.setattr(doctor, "_create_officecli_smoke", fake_create)
    monkeypatch.setattr(
        doctor,
        "_powerpoint_open_smoke",
        lambda _pptx: pytest.fail("a timed-out status probe must not trigger a second COM attempt"),
    )
    monkeypatch.setattr(doctor, "resolve_soffice", lambda: "fake-soffice.exe")
    monkeypatch.setattr(
        doctor,
        "_libreoffice_render_smoke",
        lambda _path, _pptx, _temporary: {
            "available": True,
            "passed": True,
            "detail": "fake LibreOffice render",
        },
    )

    result = doctor.office_smoke_test(
        str(officecli),
        {
            "available": False,
            "detail": (
                "PowerPoint COM lifecycle timed out after "
                f"{doctor.POWERPOINT_COM_LIFECYCLE_TIMEOUT_SECONDS} seconds"
            ),
        },
    )

    assert result["passed"] is True
    assert result["backend"] == "libreoffice"
    assert f"timed out after {doctor.POWERPOINT_COM_LIFECYCLE_TIMEOUT_SECONDS} seconds" in result["detail"]


def test_office_smoke_fails_when_neither_real_office_backend_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A CI worker without Office must not pass merely because officecli created a file."""
    officecli = tmp_path / "officecli.exe"
    officecli.touch()

    def fake_create(_officecli: str, pptx: Path) -> dict:
        pptx.write_bytes(b"pptx")
        return {"passed": True, "detail": "created"}

    monkeypatch.setattr(doctor, "_create_officecli_smoke", fake_create)
    monkeypatch.setattr(
        doctor,
        "_powerpoint_open_smoke",
        lambda _pptx: {"available": False, "passed": False, "detail": "COM unavailable"},
    )
    monkeypatch.setattr(doctor, "resolve_soffice", lambda: None)

    result = doctor.office_smoke_test(str(officecli))

    assert result["passed"] is False
    assert result["backend"] is None
    assert "neither PowerPoint COM nor LibreOffice" in result["detail"]


def test_resolve_soffice_checks_common_windows_install_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    executable = tmp_path / "LibreOffice" / "program" / "soffice.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.delenv("SOFFICE_EXE", raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.setattr(doctor.shutil, "which", lambda _command: None)

    assert doctor.resolve_soffice() == str(executable.resolve())


def test_real_libreoffice_consumers_honor_explicit_soffice_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Detection and the actual render/pagination paths must use one resolver."""
    from pypdf import PdfWriter

    executable = tmp_path / "portable-lo" / "program" / "soffice.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setenv("SOFFICE_EXE", str(executable))
    calls: list[Path] = []

    def fake_run(command: list[str], **_kwargs) -> subprocess.CompletedProcess:
        calls.append(Path(command[0]))
        output_dir = Path(command[command.index("--outdir") + 1])
        source = Path(command[-1])
        writer = PdfWriter(); writer.add_blank_page(width=612, height=792)
        with (output_dir / f"{source.stem}.pdf").open("wb") as handle: writer.write(handle)
        return subprocess.CompletedProcess(command, 0, stdout="converted", stderr="")

    monkeypatch.setattr(render_pptx.subprocess, "run", fake_run)
    monkeypatch.setattr(extract_docx_pages.subprocess, "run", fake_run)
    pptx = tmp_path / "deck.pptx"
    pptx.write_bytes(b"pptx")
    rendered = tmp_path / "rendered"
    rendered.mkdir()
    assert render_pptx.render_libreoffice(pptx, rendered) == 1
    docx = tmp_path / "source.docx"
    docx.write_bytes(b"docx")
    assert extract_docx_pages._render_pdf_with_libreoffice(docx, tmp_path / "source.pdf") is True
    assert calls == [executable.resolve(), executable.resolve()]


def test_successful_office_smoke_is_authoritative_when_version_probe_times_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    editppt = tmp_path / "editable-ppt" / "Scripts" / "editppt.exe"
    editppt.parent.mkdir(parents=True)
    editppt.write_text("stub", encoding="utf-8")
    (editppt.parent / "python.exe").write_text("stub", encoding="utf-8")

    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(
        doctor,
        "external_skill_status",
        lambda: {
            "codex_gpt_image_script": "image.py",
            "codex_gpt_image_available": True,
            "codex_oauth_available": True,
            "codex_auth_file": "auth.json",
            "image_to_editable_ppt_available": True,
            "officecli_available": True,
        },
    )
    monkeypatch.setattr(
        doctor,
        "command_version",
        lambda command, _args: (
            {"available": True, "path": str(editppt), "detail": "ok"}
            if command == "editppt"
            else {"available": command == "officecli", "path": "officecli.exe", "detail": "probe timed out"}
        ),
    )
    monkeypatch.setattr(doctor, "cjk_font_status", lambda: {"ready": True})
    monkeypatch.setattr(doctor, "background_text_detection_status", lambda: {"available": True})
    monkeypatch.setattr(
        doctor,
        "powerpoint_status",
        lambda: pytest.fail("check-powerpoint plus smoke-test must use one real PPTX COM lifecycle"),
    )
    monkeypatch.setattr(
        doctor,
        "office_smoke_test",
        lambda *_args, **_kwargs: {
            "passed": True,
            "backend": "powerpoint",
            "detail": "rendered",
            "powerpoint": {"available": True, "detail": "PowerPoint 16.0 opened 1 slide"},
        },
    )
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="0.3.0\n", stderr=""),
    )

    result = doctor.diagnose(check_powerpoint=True, smoke_test=True)

    assert result["commands"]["soffice"]["available"] is False
    assert result["office_smoke_test"]["passed"] is True
    assert result["powerpoint"] == {
        "available": True,
        "detail": "PowerPoint 16.0 opened 1 slide",
    }
    assert result["render_backend_available"] is True
    assert result["high_quality_ready"] is True


def test_libreoffice_smoke_requires_zero_exit_and_real_rendered_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from pypdf import PdfWriter

    def fake_run(command: list[str], **_kwargs) -> subprocess.CompletedProcess:
        output_dir = Path(command[command.index("--outdir") + 1])
        rendered = output_dir / "smoke.pdf"
        writer = PdfWriter(); writer.add_blank_page(width=612, height=792)
        with rendered.open("wb") as handle: writer.write(handle)
        return subprocess.CompletedProcess(command, 0, stdout="converted", stderr="")

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    pptx = tmp_path / "smoke.pptx"
    pptx.write_bytes(b"pptx")

    result = doctor._libreoffice_render_smoke("soffice.exe", pptx, tmp_path)

    assert result == {
        "available": True,
        "passed": True,
        "detail": "LibreOffice rendered a non-empty one-page PDF",
    }


def test_libreoffice_smoke_rejects_timeout_and_missing_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pptx = tmp_path / "smoke.pptx"
    pptx.write_bytes(b"pptx")

    def timeout(command: list[str], **_kwargs):
        raise subprocess.TimeoutExpired(command, 60)

    monkeypatch.setattr(doctor.subprocess, "run", timeout)
    timeout_root = tmp_path / "timeout"
    timeout_root.mkdir()
    timed_out = doctor._libreoffice_render_smoke("soffice.exe", pptx, timeout_root)
    assert timed_out["passed"] is False
    assert "timed out after 60 seconds" in timed_out["detail"]

    def no_output(command: list[str], **_kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(command, 0, stdout="converted", stderr="")

    monkeypatch.setattr(doctor.subprocess, "run", no_output)
    missing_root = tmp_path / "missing"
    missing_root.mkdir()
    missing = doctor._libreoffice_render_smoke("soffice.exe", pptx, missing_root)
    assert missing["passed"] is False
    assert "returned success without smoke.pdf" in missing["detail"]

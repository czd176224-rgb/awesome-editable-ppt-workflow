"""Execute the PowerShell runtime ownership/deletion safety smoke test."""

from __future__ import annotations

import platform
import re
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins/awesome-editable-ppt-workflow"
SMOKE = PLUGIN_ROOT / "scripts/test_install_runtime_safety.ps1"
INSTALLER = PLUGIN_ROOT / "scripts/install_runtime.ps1"
PROCESS_ENVIRONMENT = PLUGIN_ROOT / "scripts/runtime_process_environment.ps1"
PORTABLE_E2E = PLUGIN_ROOT / "scripts/portable_e2e_smoke.py"
VERIFY = REPO_ROOT / "verify.ps1"
EDITABLE_PYPROJECT = (
    REPO_ROOT
    / "plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/pyproject.toml"
)
EDITABLE_CLI_ROOT = (
    REPO_ROOT
    / "plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli"
)
BACKGROUND_TEXT_DETECTOR = (
    REPO_ROOT
    / "plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/background_text_detector.py"
)
WORD_WORKFLOW_SCRIPTS = BACKGROUND_TEXT_DETECTOR.parent


def _dependency_python() -> Path:
    candidates = [Path(sys.executable)]
    configured = os.getenv("EDITPPT_PYTHON")
    if configured:
        candidates.append(Path(configured))
    candidates.extend((
        Path.home() / ".codex/plugin-runtimes/editable-ppt-workflow-fixed-canvas-cm-v2/workflow/Scripts/python.exe",
        Path.home() / ".codex/plugin-runtimes/awesome-editable-ppt-workflow-fixed-canvas-cm-v2/editable-ppt/Scripts/python.exe",
        Path.home() / ".codex/plugin-runtimes/editable-ppt-workflow-fixed-canvas-cm-v2/editable-ppt/Scripts/python.exe",
    ))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        completed = subprocess.run(
            [str(candidate), "-c", "import numpy, PIL, docx, pptx"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if completed.returncode == 0:
            return candidate.resolve()
    raise RuntimeError("No Python interpreter with the declared detector/workflow dependencies is available")


DEPENDENCY_PYTHON = _dependency_python()
DEPENDENCY_SITE = Path(subprocess.run(
    [str(DEPENDENCY_PYTHON), "-c", "from pathlib import Path; import numpy; print(Path(numpy.__file__).resolve().parent.parent)"],
    capture_output=True, text=True, timeout=30, check=True,
).stdout.strip())


def _isolated_python(root: Path) -> tuple[Path, Path]:
    subprocess.run(
        [str(DEPENDENCY_PYTHON), "-m", "venv", "--system-site-packages", str(root)],
        capture_output=True, text=True, timeout=60, check=True,
    )
    python = root / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [str(python), "-c", "import site; print(site.getsitepackages()[0])"],
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
        check=True,
    )
    site_packages = Path(completed.stdout.strip())
    (site_packages / "test_dependency_runtime.pth").write_text(
        str(DEPENDENCY_SITE) + "\n", encoding="utf-8",
    )
    return python, site_packages


def _write_runtime_paths(site_packages: Path, *roots: Path) -> None:
    (site_packages / "awesome_runtime_test.pth").write_text(
        "".join(f"{root}\n" for root in roots), encoding="utf-8",
    )


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows PowerShell verifier contract")
def test_verify_metadata_preflight_accepts_current_single_global_confirmation_contract():
    """A real install must not reject the package's current one-confirmation metadata."""
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    assert powershell, "PowerShell is required on Windows"
    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(VERIFY),
            "-MetadataOnly",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "verify-metadata-preflight=ok" in completed.stdout


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows PowerShell installer contract")
def test_runtime_install_safety_smoke():
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    assert powershell, "PowerShell is required on Windows"
    completed = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SMOKE)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "runtime-root-safety-smoke=ok" in completed.stdout


def test_portable_installer_injects_current_workflow_and_runs_real_v6_object_build_smoke():
    """A --help-only smoke cannot prove that the installed CLI builds V6 objects."""
    source = INSTALLER.read_text(encoding="utf-8")

    assert '$WorkflowPackageName = "workflow-"' in source
    assert "$WorkflowStage" in source
    assert ".pth" in source
    assert "portable_e2e_smoke.py" in source
    assert "& $EditExe --help" not in source
    e2e = PORTABLE_E2E.read_text(encoding="utf-8")
    assert re.search(r'"page",\s*"build"', e2e)
    assert re.search(r'"page",\s*"validate"', e2e)
    assert "from workflow_v6_source import initialize_v6_project" in e2e
    assert '"awesome-word-ppt-workflow-v1"' in e2e
    assert '"reconstruction_contract_version": "editable-image-v3"' in e2e
    assert '"revision": recommendations["revision"]' in e2e
    assert '"submission_id": "portable-smoke-0001"' in e2e


def test_runtime_installer_does_not_require_retired_project_template():
    """The installed V6 runtime contains executable assets, not the retired starter tree."""
    source = INSTALLER.read_text(encoding="utf-8")

    assert 'foreach ($name in @("scripts", "schemas"))' in source
    assert 'foreach ($name in @("scripts", "schemas", "template"))' not in source


def test_workflow_runtime_installs_its_declared_editppt_package_dependency():
    """Word orchestration imports editppt state/finalize modules and must own that package."""
    source = INSTALLER.read_text(encoding="utf-8")

    assert '& $WorkflowPython -m pip install --disable-pip-version-check (Join-Path $EditableSkill "cli")' in source
    assert '& $WorkflowPython -c "import flask, jsonschema, PIL, pypdf, pypdfium2, docx, pptx, editppt"' in source


def test_workflow_and_editable_smokes_use_distinct_interpreters_and_bounded_paths(tmp_path: Path):
    """Workflow owns V6 orchestration; editable owns only the real detector worker."""
    source = INSTALLER.read_text(encoding="utf-8")
    workflow_python, workflow_site = _isolated_python(tmp_path / "workflow-python")
    editable_python, editable_site = _isolated_python(tmp_path / "editable-python")
    staged_scripts = tmp_path / "runtime" / "workflow-current" / "scripts"
    staged_image_skill = tmp_path / "runtime" / "generate-slide-body-image"
    detector_runtime = tmp_path / "runtime" / "background-text-detector"
    shutil.copytree(WORD_WORKFLOW_SCRIPTS, staged_scripts)
    shutil.copytree(PLUGIN_ROOT / "skills" / "generate-slide-body-image", staged_image_skill)
    shutil.copy2(PLUGIN_ROOT / "scripts" / "runtime_office.py", staged_scripts / "runtime_office.py")
    shutil.copytree(WORD_WORKFLOW_SCRIPTS.parent / "schemas", staged_scripts.parent / "schemas")
    detector_runtime.mkdir(parents=True)
    shutil.copy2(BACKGROUND_TEXT_DETECTOR, detector_runtime / BACKGROUND_TEXT_DETECTOR.name)
    _write_runtime_paths(workflow_site, staged_scripts, EDITABLE_CLI_ROOT)
    _write_runtime_paths(editable_site, detector_runtime, EDITABLE_CLI_ROOT)

    workflow_probe = "\n".join((
        "import editppt, workflow_v6_contract, workflow_v6_source, workflow_v6_image, workflow_v6_reconstruction",
        "print('awesome-v6-workflow-boundary=ok')",
    ))
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    workflow = subprocess.run(
        [str(workflow_python), "-c", workflow_probe],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    blank = tmp_path / "blank.png"
    Image.new("RGB", (320, 180), "white").save(blank)
    editable_probe = "\n".join((
        "import importlib.util",
        "from pathlib import Path",
        "from background_text_detector import capability_status, detect_background_text",
        "assert importlib.util.find_spec('workflow_v6_contract') is None",
        "status = capability_status()",
        "assert status['available'], status",
        f"result = detect_background_text(Path({str(blank)!r}))",
        "assert result['background_text_detected'] is False, result",
        "print('background-text-detector-worker=ok')",
    ))
    editable = subprocess.run(
        [str(editable_python), "-c", editable_probe],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert workflow_python != editable_python
    assert workflow_site != editable_site
    assert '$EditableSitePackages = (& $EditablePython -c "import site; print(site.getsitepackages()[0])").Trim()' in source
    assert '$DetectorModules = @("background_text_detector.py")' in source
    assert 'Set-Content -LiteralPath $EditableDetectorPthStage -Value $CurrentDetectorRuntime' in source
    assert 'Set-Content -LiteralPath $EditableWorkflowPthStage -Value $CurrentWorkflowScripts' not in source
    assert workflow.returncode == 0, workflow.stdout + workflow.stderr
    assert workflow.stdout.strip() == "awesome-v6-workflow-boundary=ok"
    assert editable.returncode == 0, editable.stdout + editable.stderr
    assert editable.stdout.strip() == "background-text-detector-worker=ok"


@pytest.mark.skipif(platform.system() != "Windows", reason="PowerShell environment contract")
def test_production_import_smokes_clear_and_restore_poisoned_pythonpath(tmp_path: Path):
    """A checkout on inherited PYTHONPATH must not satisfy installed-runtime probes."""
    source = INSTALLER.read_text(encoding="utf-8")
    verifier = VERIFY.read_text(encoding="utf-8")
    poison = tmp_path / "poison"
    poison.mkdir()
    (poison / "workflow_v6_contract.py").write_text("POISONED = True\n", encoding="utf-8")
    workflow_python, _workflow_site = _isolated_python(tmp_path / "workflow-python")
    editable_python, _editable_site = _isolated_python(tmp_path / "editable-python")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(poison)
    contaminated = subprocess.run(
        [str(workflow_python), "-c", "import workflow_v6_contract; assert workflow_v6_contract.POISONED"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert contaminated.returncode == 0, contaminated.stdout + contaminated.stderr
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    assert powershell
    escaped_helper = str(PROCESS_ENVIRONMENT).replace("'", "''")
    escaped_poison = str(poison).replace("'", "''")
    probe_parts = [
        f". '{escaped_helper}'",
        f"$env:PYTHONPATH = '{escaped_poison}'",
    ]
    for interpreter in (workflow_python, editable_python):
        escaped_python = str(interpreter).replace("'", "''")
        probe_parts.extend((
            "Invoke-WithClearedPythonPath {",
            f"& '{escaped_python}' -c \"import workflow_v6_contract\"",
            "if ($LASTEXITCODE -eq 0) { throw 'poison leaked into smoke' }",
            "}",
            f"if ($env:PYTHONPATH -ne '{escaped_poison}') {{ throw 'PYTHONPATH was not restored' }}",
        ))
    probe_parts.append("Write-Output 'pythonpath-isolation=ok'")
    probe = "; ".join(probe_parts)
    isolated = subprocess.run(
        [powershell, "-NoProfile", "-Command", probe],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert isolated.returncode == 0, isolated.stdout + isolated.stderr
    assert "pythonpath-isolation=ok" in isolated.stdout
    installer_smokes = source[source.index("Invoke-WithClearedPythonPath {"):]
    verifier_smokes = verifier[verifier.index("Invoke-WithClearedPythonPath {"):]
    for smokes in (installer_smokes, verifier_smokes):
        assert "& $WorkflowPython -c" in smokes
        assert "& $EditablePython -c" in smokes


def test_verify_imports_only_the_installed_current_workflow_copy():
    """Verification must not hide a broken package by importing repository scripts."""
    source = VERIFY.read_text(encoding="utf-8")

    assert '$CurrentWorkflowRoot = Join-Path $RuntimeRoot $WorkflowPackageName' in source
    assert '$WorkflowScripts = Join-Path $CurrentWorkflowRoot "scripts"' in source
    assert '$WorkflowScripts = Join-Path $WorkflowSkill "scripts"' not in source
    assert '& $WorkflowPython (Join-Path $WorkflowScripts "doctor.py")' in source
    assert r'& $WorkflowPython (Join-Path $WorkflowSkill "scripts\doctor.py")' not in source
    assert '$env:CODEX_GPT_IMAGE_SKILL = Join-Path $RuntimeRoot "generate-slide-body-image"' in source
    assert '& $EditablePython -c "from pathlib import Path; import importlib.util, tempfile;' in source
    assert 'import confirm_ui.server, workflow_state, final_mechanical_qa' not in source


def test_portable_verify_reuses_attested_v6_cli_result_after_both_runtime_smokes():
    source = VERIFY.read_text(encoding="utf-8")
    portable = source.split("if ($PortableSmokeTest) {", 1)[1].split("} else {", 1)[0]
    smokes = source[:source.index("if ($PortableSmokeTest) {")]
    assert '& $WorkflowPython -c "import flask, jsonschema' in smokes
    assert '& $EditablePython -c "from pathlib import Path; import importlib.util, tempfile;' in smokes
    assert '$Report.editppt_cli -ne "v6-build-validate-ok"' in source


def test_editable_runtime_declares_windows_only_powerpoint_com_dependency():
    metadata = tomllib.loads(EDITABLE_PYPROJECT.read_text(encoding="utf-8"))

    assert "pywin32>=306; sys_platform == 'win32'" in metadata["project"]["dependencies"]


def test_windows_nonportable_runtime_probes_real_win32com_and_portable_records_skip():
    installer = INSTALLER.read_text(encoding="utf-8")
    verifier = VERIFY.read_text(encoding="utf-8")
    probe = "import win32com.client; print('editppt-win32com=ok')"

    assert probe in installer
    assert probe in verifier
    assert "$RunningOnWindows -and -not $PortableSmokeTest" in installer
    assert "$RunningOnWindows -and -not $PortableSmokeTest" in verifier
    assert 'win32com_import = "skipped-portable"' in installer
    assert '$Report.win32com_import -ne "skipped-portable"' in verifier


def test_optional_local_renderer_never_blocks_runtime_installation():
    installer = INSTALLER.read_text(encoding="utf-8")

    assert 'throw "Install Microsoft PowerPoint or LibreOffice, then retry."' not in installer
    assert "Write-Warning" in installer


def test_installed_current_workflow_contains_the_shared_office_resolver():
    installer = INSTALLER.read_text(encoding="utf-8")

    assert 'Join-Path $PluginRoot "scripts\\runtime_office.py"' in installer
    assert 'Join-Path $WorkflowStage "scripts\\runtime_office.py"' in installer


def test_runtime_packages_exact_image_generator_for_repair_attempts():
    installer = INSTALLER.read_text(encoding="utf-8")

    assert '$ImageSkill = Join-Path $PluginRoot "skills\\generate-slide-body-image"' in installer
    assert '$ImageSkillStage = Join-Path $RuntimeRoot ".generate-slide-body-image.$PID.tmp"' in installer
    assert 'Move-Item -LiteralPath $ImageSkillStage -Destination $CurrentImageSkillRoot' in installer
    assert '$env:CODEX_GPT_IMAGE_SKILL = $CurrentImageSkillRoot' in installer


def test_background_detector_uses_packaged_editppt_runtime_not_checkout_siblings(tmp_path: Path):
    """The copied Word workflow must find detector modules through the installed editppt package."""
    workflow_scripts = tmp_path / "workflow-current" / "scripts"
    shutil.copytree(WORD_WORKFLOW_SCRIPTS, workflow_scripts)
    blank = tmp_path / "blank.png"
    Image.new("RGB", (320, 180), "white").save(blank)

    editable_python, editable_site = _isolated_python(tmp_path / "editable-python")
    detector_runtime = tmp_path / "background-text-detector"
    detector_runtime.mkdir()
    shutil.copy2(BACKGROUND_TEXT_DETECTOR, detector_runtime / BACKGROUND_TEXT_DETECTOR.name)
    _write_runtime_paths(editable_site, detector_runtime, EDITABLE_CLI_ROOT)
    probe = "\n".join([
        "import json",
        "from background_text_detector import capability_status, detect_background_text",
        "status = capability_status()",
        f"detection = detect_background_text(__import__('pathlib').Path({str(blank)!r})) if status['available'] else None",
        "print(json.dumps({'status': status, 'detection': detection}, ensure_ascii=False))",
    ])
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [str(editable_python), "-c", probe],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=environment,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = __import__("json").loads(completed.stdout)
    assert result["status"]["available"] is True
    assert result["detection"]["background_text_detected"] is False


def test_v6_assembly_uses_packaged_editppt_runtime_without_checkout_siblings(tmp_path: Path):
    workflow_root = tmp_path / "workflow-current"
    workflow_scripts = workflow_root / "scripts"
    shutil.copytree(WORD_WORKFLOW_SCRIPTS, workflow_scripts)
    shutil.copytree(WORD_WORKFLOW_SCRIPTS.parent / "schemas", workflow_root / "schemas")
    completed = subprocess.run(
        [sys.executable, "-c", "import workflow_v6_reconstruction; print('assembly-import=ok')"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": f"{workflow_scripts}{os.pathsep}{EDITABLE_CLI_ROOT}"},
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "assembly-import=ok" in completed.stdout


def test_background_detector_runs_in_the_declared_editable_runtime(tmp_path: Path):
    """The Word runtime must use the one editable runtime that owns detector dependencies."""
    workflow_scripts = tmp_path / "workflow-current" / "scripts"
    shutil.copytree(WORD_WORKFLOW_SCRIPTS, workflow_scripts)
    blank = tmp_path / "blank.png"
    Image.new("RGB", (320, 180), "white").save(blank)

    editable_python, site_packages = _isolated_python(tmp_path / "editable-runtime")
    (site_packages / "editppt_source.pth").write_text(str(EDITABLE_CLI_ROOT) + "\n", encoding="utf-8")
    detector_runtime = tmp_path / "background-text-detector"
    detector_runtime.mkdir()
    shutil.copy2(BACKGROUND_TEXT_DETECTOR, detector_runtime / BACKGROUND_TEXT_DETECTOR.name)
    (site_packages / "background_text_detector_runtime.pth").write_text(
        str(detector_runtime) + "\n", encoding="utf-8",
    )

    probe = "\n".join([
        "import json, sys",
        f"sys.path.insert(0, {str(workflow_scripts)!r})",
        "from background_text_detector import capability_status, detect_background_text",
        "status = capability_status()",
        f"detection = detect_background_text(__import__('pathlib').Path({str(blank)!r})) if status['available'] else None",
        "print(json.dumps({'status': status, 'detection': detection}, ensure_ascii=False))",
    ])
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["HOME"] = str(tmp_path / "isolated-home")
    environment["USERPROFILE"] = str(tmp_path / "isolated-home")
    environment.pop("EDITPPT_EXE", None)
    environment["EDITPPT_PYTHON"] = str(editable_python)
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=environment,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = __import__("json").loads(completed.stdout)
    assert result["status"]["available"] is True
    assert result["detection"]["background_text_detected"] is False

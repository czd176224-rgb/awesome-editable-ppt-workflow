from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def installer_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    release = tmp_path / "release"
    (release / "plugins/awesome-editable-ppt-workflow/.codex-plugin").mkdir(parents=True)
    (release / "plugins/awesome-editable-ppt-workflow/scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "install.ps1", release / "install.ps1")
    shutil.copy2(ROOT / "update.ps1", release / "update.ps1")
    shutil.copy2(ROOT / "uninstall.ps1", release / "uninstall.ps1")
    shutil.copy2(ROOT / "package-info.json", release / "package-info.json")
    shutil.copy2(
        ROOT / "plugins/awesome-editable-ppt-workflow/.codex-plugin/plugin.json",
        release / "plugins/awesome-editable-ppt-workflow/.codex-plugin/plugin.json",
    )
    (release / "plugins/awesome-editable-ppt-workflow/scripts/install_runtime.ps1").write_text(
        "param([switch]$Force,[string]$RuntimeRoot,[string]$BinDir)\nexit 0\n",
        encoding="utf-8",
    )
    (release / "verify.ps1").write_text("param([string]$RuntimeRoot)\nexit 0\n", encoding="utf-8")
    shutil.copy2(
        ROOT / "plugins/awesome-editable-ppt-workflow/scripts/runtime_root_safety.ps1",
        release / "plugins/awesome-editable-ppt-workflow/scripts/runtime_root_safety.ps1",
    )
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    (mock_bin / "codex.cmd").write_text(
        "@echo off\n"
        "echo %*>>\"%CODEX_MOCK_LOG%\"\n"
        "if \"%1 %2 %3\"==\"plugin marketplace list\" (\n"
        "  if defined CODEX_MOCK_EXISTING echo %CODEX_MOCK_EXISTING%\n"
        "  exit /b 0\n"
        ")\n"
        "if defined CODEX_MOCK_FAIL_ON (\n"
        "  echo %*| %SystemRoot%\\System32\\findstr.exe /c:\"%CODEX_MOCK_FAIL_ON%\" >nul\n"
        "  if not errorlevel 1 exit /b 9\n"
        ")\n"
        "if defined CODEX_MOCK_FAIL_ONCE (\n"
        "  echo %*| %SystemRoot%\\System32\\findstr.exe /c:\"%CODEX_MOCK_FAIL_ONCE%\" >nul\n"
        "  if not errorlevel 1 if not exist \"%CODEX_MOCK_FAIL_MARK%\" (\n"
        "    type nul >\"%CODEX_MOCK_FAIL_MARK%\"\n"
        "    exit /b 8\n"
        "  )\n"
        ")\n"
        "exit /b 0\n",
        encoding="ascii",
    )
    receipt = tmp_path / "state/install-receipt.json"
    log = tmp_path / "codex.log"
    environment = {
        **os.environ,
        "PATH": f"{mock_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "CODEX_HOME": str(tmp_path / "codex-home"),
        "USERPROFILE": str(tmp_path / "user"),
        "LOCALAPPDATA": str(tmp_path / "local"),
        "CODEX_MOCK_LOG": str(log),
        "CODEX_MOCK_FAIL_MARK": str(tmp_path / "fail-once.mark"),
    }
    return release, receipt, environment


def run_installer(release: Path, receipt: Path, environment: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(release / "install.ps1"), "-ReceiptPath", str(receipt), *args],
        cwd=release, capture_output=True, text=True, timeout=30, env=environment,
    )


def run_update(release: Path, receipt: Path, environment: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(release / "update.ps1"), "-ReceiptPath", str(receipt)],
        cwd=release, capture_output=True, text=True, timeout=30, env=environment,
    )


def set_version(release: Path, version: str) -> None:
    package_path = release / "package-info.json"
    package = json.loads(package_path.read_text(encoding="utf-8-sig"))
    package["pluginVersion"] = version
    package["releaseTag"] = f"v{version}"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    plugin_path = release / "plugins/awesome-editable-ppt-workflow/.codex-plugin/plugin.json"
    plugin = json.loads(plugin_path.read_text(encoding="utf-8-sig"))
    plugin["version"] = version
    plugin_path.write_text(json.dumps(plugin), encoding="utf-8")


def log_lines(environment: dict[str, str]) -> list[str]:
    path = Path(environment["CODEX_MOCK_LOG"])
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def install_identity(release: Path) -> tuple[str, str]:
    package = json.loads((release / "package-info.json").read_text(encoding="utf-8-sig"))
    return str(package["plugin"]), str(package["marketplace"])


def test_first_install_writes_receipt_and_same_version_is_rejected_without_cli_mutation(installer_fixture):
    release, receipt, environment = installer_fixture
    first = run_installer(release, receipt, environment)
    assert first.returncode == 0, first.stdout + first.stderr
    state = json.loads(receipt.read_text(encoding="utf-8-sig"))
    assert state["schemaVersion"] == "editable-ppt-install-receipt-v1"
    assert state["releaseTag"] == "v1.2.3"
    assert any("marketplace add" in line and "--ref v1.2.3" in line for line in log_lines(environment))
    before = log_lines(environment)

    repeated = run_installer(release, receipt, environment)
    assert repeated.returncode != 0
    assert "already installed" in repeated.stdout + repeated.stderr
    assert log_lines(environment) == before
    same_update = run_update(release, receipt, environment)
    assert same_update.returncode != 0
    assert "strictly higher" in same_update.stdout + same_update.stderr
    assert log_lines(environment) == before


def test_update_accepts_only_higher_immutable_tag_and_repair_is_explicit(installer_fixture):
    release, receipt, environment = installer_fixture
    assert run_installer(release, receipt, environment).returncode == 0
    before_repair = len(log_lines(environment))
    repaired = run_installer(release, receipt, environment, "-Repair")
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    assert any("--ref v1.2.3" in line for line in log_lines(environment)[before_repair:])

    set_version(release, "2.6.2")
    updated = run_update(release, receipt, environment)
    assert updated.returncode == 0, updated.stdout + updated.stderr
    assert json.loads(receipt.read_text(encoding="utf-8-sig"))["releaseTag"] == "v2.6.2"
    assert any("marketplace add" in line and "--ref v2.6.2" in line for line in log_lines(environment))

    before_downgrade = log_lines(environment)
    set_version(release, "2.2.0")
    downgraded = run_update(release, receipt, environment)
    assert downgraded.returncode != 0
    assert "strictly higher" in downgraded.stdout + downgraded.stderr
    assert log_lines(environment) == before_downgrade


def test_failed_target_registration_restores_previous_ref_and_receipt(installer_fixture):
    release, receipt, environment = installer_fixture
    assert run_installer(release, receipt, environment).returncode == 0
    old_receipt = receipt.read_bytes()
    set_version(release, "2.6.2")
    environment["CODEX_MOCK_FAIL_ON"] = "v2.6.2"
    failed = run_update(release, receipt, environment)
    assert failed.returncode != 0
    assert receipt.read_bytes() == old_receipt
    tail = log_lines(environment)
    assert any("marketplace add" in line and "--ref v2.6.2" in line for line in tail)
    assert any("marketplace add" in line and "--ref v1.2.3" in line for line in tail)


@pytest.mark.parametrize(
    "failed_step",
    ["plugin", "marketplace"],
)
def test_failed_old_teardown_restores_exact_previous_ref_and_preserves_receipt(
    installer_fixture, failed_step: str
):
    release, receipt, environment = installer_fixture
    plugin, marketplace = install_identity(release)
    failed_command = (
        f"plugin remove {plugin}@{marketplace}"
        if failed_step == "plugin"
        else f"plugin marketplace remove {marketplace}"
    )
    assert run_installer(release, receipt, environment).returncode == 0
    old_receipt = receipt.read_bytes()
    before = len(log_lines(environment))
    set_version(release, "2.6.2")
    environment["CODEX_MOCK_FAIL_ONCE"] = failed_command

    failed = run_update(release, receipt, environment)

    assert failed.returncode != 0
    assert receipt.read_bytes() == old_receipt
    lines = log_lines(environment)[before:]
    assert any("marketplace add" in line and "--ref v1.2.3" in line for line in lines)
    assert any(line == f"plugin add {plugin}@{marketplace}" for line in lines)


def test_rollback_failure_leaves_recovery_required_transaction(installer_fixture):
    release, receipt, environment = installer_fixture
    assert run_installer(release, receipt, environment).returncode == 0
    old_receipt = receipt.read_bytes()
    set_version(release, "2.6.2")
    environment["CODEX_MOCK_FAIL_ON"] = "plugin marketplace add"

    failed = run_update(release, receipt, environment)

    assert failed.returncode != 0
    assert "recovery-required" in (failed.stdout + failed.stderr).lower()
    assert receipt.read_bytes() == old_receipt
    transaction = Path(f"{receipt}.transaction.json")
    recovery = json.loads(transaction.read_text(encoding="utf-8-sig"))
    assert recovery["status"] == "recovery-required"
    assert recovery["previous"]["releaseTag"] == "v1.2.3"


def test_failed_v123_update_from_v122_restores_the_published_v122_ref(installer_fixture):
    release, receipt, environment = installer_fixture
    set_version(release, "1.2.2")
    assert run_installer(release, receipt, environment).returncode == 0
    set_version(release, "1.2.3")
    environment["CODEX_MOCK_FAIL_ON"] = "v1.2.3"

    failed = run_update(release, receipt, environment)

    assert failed.returncode != 0
    assert json.loads(receipt.read_text(encoding="utf-8-sig"))["releaseTag"] == "v1.2.2"
    assert any("marketplace add" in line and "--ref v1.2.2" in line for line in log_lines(environment))


def run_uninstall(release: Path, receipt: Path, environment: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(release / "uninstall.ps1"), "-RemoveMarketplace", "-ReceiptPath", str(receipt)],
        cwd=release, capture_output=True, text=True, timeout=30, env=environment,
    )


def test_complete_uninstall_removes_receipt_and_allows_fresh_install(installer_fixture):
    release, receipt, environment = installer_fixture
    assert run_installer(release, receipt, environment).returncode == 0

    removed = run_uninstall(release, receipt, environment)

    assert removed.returncode == 0, removed.stdout + removed.stderr
    assert not receipt.exists()
    fresh = run_installer(release, receipt, environment)
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr


@pytest.mark.parametrize(
    "failed_step",
    ["plugin", "marketplace"],
)
def test_uninstall_failure_preserves_verified_receipt(installer_fixture, failed_step: str):
    release, receipt, environment = installer_fixture
    plugin, marketplace = install_identity(release)
    failed_command = (
        f"plugin remove {plugin}@{marketplace}"
        if failed_step == "plugin"
        else f"plugin marketplace remove {marketplace}"
    )
    assert run_installer(release, receipt, environment).returncode == 0
    original = receipt.read_bytes()
    environment["CODEX_MOCK_FAIL_ONCE"] = failed_command

    failed = run_uninstall(release, receipt, environment)

    assert failed.returncode != 0
    assert receipt.read_bytes() == original


def test_uninstall_refuses_to_delete_mismatched_receipt_without_cli_calls(installer_fixture):
    release, receipt, environment = installer_fixture
    assert run_installer(release, receipt, environment).returncode == 0
    state = json.loads(receipt.read_text(encoding="utf-8-sig"))
    state["marketplace"] = "someone-elses-marketplace"
    receipt.write_text(json.dumps(state), encoding="utf-8")
    original = receipt.read_bytes()
    before = log_lines(environment)

    failed = run_uninstall(release, receipt, environment)

    assert failed.returncode != 0
    assert receipt.read_bytes() == original
    assert log_lines(environment) == before


def test_requested_runtime_removal_failure_preserves_receipt(installer_fixture):
    release, receipt, environment = installer_fixture
    assert run_installer(release, receipt, environment).returncode == 0
    original = receipt.read_bytes()
    unsafe_runtime = release.parent / "not-an-owned-runtime"
    unsafe_runtime.mkdir()

    failed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(release / "uninstall.ps1"), "-RemoveMarketplace", "-RemoveRuntime",
         "-RuntimeRoot", str(unsafe_runtime), "-ReceiptPath", str(receipt)],
        cwd=release, capture_output=True, text=True, timeout=30, env=environment,
    )

    assert failed.returncode != 0
    assert receipt.read_bytes() == original

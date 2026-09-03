import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDIT_RUNTIME = ROOT / "plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime"
WORD_RUNTIME = ROOT / "plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts"
sys.path.insert(0, str(EDIT_RUNTIME))
sys.path.insert(0, str(WORD_RUNTIME))


def text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def require_export_checkout() -> None:
    if not (ROOT / ".git").exists():
        pytest.skip("public export requires the private reviewed Git checkout")


def test_committed_public_manifest_hashes_match_head_bytes():
    require_export_checkout()

    def head_bytes(relative: str) -> bytes:
        return subprocess.run(
            ["git", "show", f"HEAD:{relative}"], cwd=ROOT, check=True, capture_output=True,
        ).stdout

    manifest_bytes = head_bytes("public-source-manifest.json")
    manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
    for relative, expected in manifest["files"].items():
        assert hashlib.sha256(head_bytes(relative)).hexdigest() == expected, relative
    audit = json.loads(head_bytes("public-release-audit.json").decode("utf-8-sig"))
    assert audit["sourceManifestSha256"] == hashlib.sha256(manifest_bytes).hexdigest()


def test_release_identity_is_immutable_v123_tag():
    package = json.loads(text("package-info.json"))
    assert package["pluginVersion"] == "1.2.3"
    assert package["releaseTag"] == "v1.2.3"
    assert package["workflowContractVersion"] == "awesome-word-ppt-workflow-v1"
    assert package["promptContractVersion"] == "consulting-page-director-v3-compact-page-plan"
    assert package["pageImagePolicy"] == "generate-without-refs-edit-with-confirmed-refs"
    installer = text("install.ps1")
    assert "--ref $ReleaseTag" in installer
    assert "--ref main" not in installer


def test_metadata_verifier_accepts_adaptive_image_endpoint_contract():
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "verify.ps1"), "-MetadataOnly"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_no_pymupdf_or_fitz_in_active_runtime_contract():
    active = [
        ROOT / "verify.ps1",
        ROOT / "plugins/awesome-editable-ppt-workflow/scripts/install_runtime.ps1",
        ROOT / "plugins/awesome-editable-ppt-workflow/scripts/check_current_runtime.py",
        ROOT / "plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/requirements.txt",
        ROOT / "plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/pyproject.toml",
    ]
    active += list((ROOT / "plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts").glob("*.py"))
    active += list((ROOT / "plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime").glob("*.py"))
    lowered = "\n".join(path.read_text(encoding="utf-8-sig").lower() for path in active)
    assert "pymupdf" not in lowered
    assert "import fitz" not in lowered
    assert '"fitz"' not in lowered


def test_officecli_is_optional_preinstalled_and_never_downloaded():
    installer = text("plugins/awesome-editable-ppt-workflow/scripts/install_runtime.ps1")
    assert "Invoke-RestMethod" not in installer
    assert "d.officecli.ai" not in installer
    assert "officecli_optional" in installer
    doctor = text("plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/doctor.py")
    assert "officecli_optional" in doctor
    assert "workflow_ready" in doctor


def test_uninstall_runtime_path_matches_installer_and_preserves_projects():
    installer = text("plugins/awesome-editable-ppt-workflow/scripts/install_runtime.ps1")
    uninstaller = text("uninstall.ps1")
    expected = "plugin-runtimes\\awesome-editable-ppt-workflow-fixed-canvas-cm-v2"
    assert expected in installer
    assert expected in uninstaller
    assert "User-created Word and PPT project folders were not searched or modified" in uninstaller


def test_notice_and_release_runbook_are_public_assets():
    allowlist = json.loads(text("public-release-files.json"))["files"]
    assert "tests" in allowlist
    assert "NOTICE" in allowlist
    assert "docs/RELEASE.md" in allowlist
    assert "docs/SECURITY_AND_PROVENANCE.md" in allowlist


def test_export_rejects_untracked_allowlisted_descendants(tmp_path: Path):
    require_export_checkout()
    poison = ROOT / "plugins/awesome-editable-ppt-workflow/UNTRACKED_RELEASE_POISON.txt"
    poison.write_text("must not ship", encoding="utf-8")
    output = tmp_path / "public"
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(ROOT / "scripts/export_public_release.ps1"), "-OutputPath", str(output)],
            cwd=ROOT, capture_output=True, text=True, timeout=90,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert not (output / poison.relative_to(ROOT)).exists()
        source_manifest = json.loads((output / "public-source-manifest.json").read_text(encoding="utf-8-sig"))
        assert poison.relative_to(ROOT).as_posix() not in source_manifest["files"]
        assert "sourceCommit" not in source_manifest
        assert source_manifest["authority"] == "tracked-public-source"
        assert source_manifest["releaseTag"] == "v1.2.3"
        assert len(source_manifest["indexTreeSha256"]) == 64
    finally:
        poison.unlink(missing_ok=True)


def test_package_rejects_untracked_nested_files_in_exported_snapshot(tmp_path: Path):
    require_export_checkout()
    output = tmp_path / "public"
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "scripts/export_public_release.ps1"), "-OutputPath", str(output)],
        cwd=ROOT, capture_output=True, text=True, timeout=90,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    poison = output / "plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/UNTRACKED_NESTED.py"
    poison.write_text("raise RuntimeError('must not ship')\n", encoding="utf-8")
    dist = tmp_path / "dist"
    packaged = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(output / "scripts/package_release.ps1"), "-SourceRoot", str(output),
         "-OutputDirectory", str(dist)],
        cwd=output, capture_output=True, text=True, timeout=90,
    )
    assert packaged.returncode != 0
    assert "source manifest file set mismatch" in packaged.stdout + packaged.stderr


def test_checker_rejects_manifest_unlisted_files_in_a_git_checkout(tmp_path: Path):
    require_export_checkout()
    output = tmp_path / "public"
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "scripts/export_public_release.ps1"), "-OutputPath", str(output)],
        cwd=ROOT, capture_output=True, text=True, timeout=90,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    for args in (
        ["init", "-b", "release"],
        ["config", "user.email", "release@example.invalid"],
        ["config", "user.name", "Release Test"],
        ["add", "-A"],
        ["commit", "-m", "public"],
    ):
        subprocess.run(["git", *args], cwd=output, check=True, capture_output=True)
    poison = output / "plugins" / "UNLISTED_RELEASE_FILE.txt"
    poison.write_text("must not ship\n", encoding="utf-8")
    subprocess.run(["git", "add", str(poison)], cwd=output, check=True, capture_output=True)
    checked = subprocess.run(
        [sys.executable, "scripts/check_public_release.py", "."],
        cwd=output, capture_output=True, text=True, timeout=90,
    )
    assert checked.returncode != 0
    assert "source manifest file set mismatch" in checked.stdout + checked.stderr


def test_export_scan_report_package_chain_has_non_circular_authorities(tmp_path: Path):
    require_export_checkout()
    output = tmp_path / "public"
    exported = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "scripts/export_public_release.ps1"), "-OutputPath", str(output)],
        cwd=ROOT, capture_output=True, text=True, timeout=90,
    )
    assert exported.returncode == 0, exported.stdout + exported.stderr
    source_manifest = json.loads((output / "public-source-manifest.json").read_text(encoding="utf-8-sig"))
    assert source_manifest["schemaVersion"] == "public-source-manifest-v1"
    assert "public-source-manifest.json" not in source_manifest["files"]
    assert "public-release-audit.json" not in source_manifest["files"]
    audited = subprocess.run(
        [sys.executable, "scripts/check_public_release.py", ".", "--write-report", "public-release-audit.json"],
        cwd=output, capture_output=True, text=True, timeout=90,
    )
    assert audited.returncode == 0, audited.stdout + audited.stderr
    audit = json.loads((output / "public-release-audit.json").read_text(encoding="utf-8-sig"))
    assert audit["schemaVersion"] == "public-release-audit-v1"
    assert audit["releaseTag"] == "v1.2.3"
    packaged = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(output / "scripts/package_release.ps1"), "-SourceRoot", str(output),
         "-OutputDirectory", str(tmp_path / "dist")],
        cwd=output, capture_output=True, text=True, timeout=90,
    )
    assert packaged.returncode == 0, packaged.stdout + packaged.stderr


def test_git_archive_uses_head_bytes_not_dirty_manifest_or_untracked_files(tmp_path: Path):
    require_export_checkout()
    output = tmp_path / "public"
    exported = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "scripts/export_public_release.ps1"), "-OutputPath", str(output)],
        cwd=ROOT, capture_output=True, text=True, timeout=90,
    )
    assert exported.returncode == 0, exported.stdout + exported.stderr
    for args in (["init", "-b", "release"], ["config", "user.email", "release@example.invalid"],
                 ["config", "user.name", "Release Test"], ["add", "-A"], ["commit", "-m", "public"]):
        subprocess.run(["git", *args], cwd=output, check=True, capture_output=True)
    committed_manifest = subprocess.run(
        ["git", "show", "HEAD:public-source-manifest.json"], cwd=output,
        check=True, capture_output=True,
    ).stdout
    manifest_path = output / "public-source-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    manifest["files"]["INJECTED.txt"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (output / "INJECTED.txt").write_text("must not ship", encoding="utf-8")
    dist = tmp_path / "dist"
    packaged = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(output / "scripts/package_release.ps1"), "-SourceRoot", str(output),
         "-OutputDirectory", str(dist)], cwd=output, capture_output=True, text=True, timeout=90,
    )
    assert packaged.returncode == 0, packaged.stdout + packaged.stderr
    with zipfile.ZipFile(dist / "awesome-editable-ppt-workflow-1.2.3-windows.zip") as bundle:
        assert "INJECTED.txt" not in bundle.namelist()
        assert bundle.read("public-source-manifest.json") == committed_manifest


def test_exported_snapshot_runs_public_release_gate(tmp_path: Path):
    if os.getenv("EDITABLE_PPT_NESTED_PUBLIC_GATE") == "1":
        pytest.skip("avoid recursive release-gate invocation")
    require_export_checkout()
    output = tmp_path / "public"
    exported = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "scripts/export_public_release.ps1"), "-OutputPath", str(output)],
        cwd=ROOT, capture_output=True, text=True, timeout=90,
    )
    assert exported.returncode == 0, exported.stdout + exported.stderr
    environment = {
        **os.environ,
        "EDITABLE_PPT_NESTED_PUBLIC_GATE": "1",
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    gated = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(output / "scripts/release_gate.ps1"), "-PublicSnapshotOnly", "-SkipPortableSmoke"],
        cwd=output, capture_output=True, text=True, timeout=3600, env=environment,
    )
    assert gated.returncode == 0, gated.stdout + gated.stderr


def test_ci_and_release_use_the_complete_release_gate():
    ci = text(".github/workflows/ci.yml")
    release = text(".github/workflows/release.yml")
    for required in ("release_gate.ps1", "package_release.ps1", "Portable clean-install smoke"):
        assert required in ci + release
    assert "GITHUB_REF_NAME" in release
    assert "releaseTag" in release


def test_public_scanner_rejects_private_provenance_fields(tmp_path: Path):
    require_export_checkout()
    output = tmp_path / "public"
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "scripts/export_public_release.ps1"), "-OutputPath", str(output)],
        cwd=ROOT, capture_output=True, text=True, timeout=90,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    manifest = json.loads((output / "public-source-manifest.json").read_text(encoding="utf-8-sig"))
    manifest["sourceCommit"] = "a" * 40
    (output / "public-source-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    checked = subprocess.run(
        ["python", "scripts/check_public_release.py", "."], cwd=output,
        capture_output=True, text=True, timeout=90,
    )
    assert checked.returncode != 0
    assert "private-development provenance" in checked.stdout


def test_editppt_runtime_supports_installed_package_import_boundary():
    cli_root = ROOT / "plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli"
    probe = subprocess.run(
        [sys.executable, "-c", "from editppt.runtime.build_pptx_from_manifest import normalize_manifest; print('ok')"],
        cwd=ROOT,
        env={**__import__("os").environ, "PYTHONPATH": str(cli_root)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    reconstruction = text(
        "plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/"
        "workflow_v6_reconstruction.py"
    )
    assert "from editppt.runtime.validate_pptx import" in reconstruction
    assert "except ImportError" not in reconstruction
    assert "from validate_pptx import" not in reconstruction


def test_readme_and_quickstart_install_verified_exact_release_zip():
    docs = text("README.md") + text("docs/QUICKSTART.zh-CN.md")
    assert "releases/download/v1.2.3/awesome-editable-ppt-workflow-1.2.3-windows.zip" in docs
    assert "SHA256SUMS.txt" in docs
    assert "Get-FileHash" in docs
    assert "raw.githubusercontent.com" not in docs
def test_generated_release_json_writers_force_lf():
    exporter = (ROOT / "scripts/export_public_release.ps1").read_text(encoding="utf-8")
    checker = (ROOT / "scripts/check_public_release.py").read_text(encoding="utf-8")
    assert '-replace "`r`n", "`n"' in exporter
    assert 'newline="\\n"' in checker


def test_public_verifier_reports_the_adaptive_image_policy():
    verifier = (ROOT / "verify.ps1").read_text(encoding="utf-8")
    assert "generate-only" not in verifier.lower()
    assert "adaptive generate/edit Image2 bodies" in verifier


def test_release_gate_excludes_ignored_development_workspace_from_json_validation():
    gate = (ROOT / "scripts/release_gate.ps1").read_text(encoding="utf-8")
    assert "\\.superpowers" in gate
    assert "-notmatch" in gate


def test_hosted_workflows_prepare_secure_root_and_skip_only_office_bound_tests():
    gate = text("scripts/release_gate.ps1")
    ci = text(".github/workflows/ci.yml")
    release = text(".github/workflows/release.yml")

    assert "[switch]$SkipOfficeTests" in gate
    assert 'Name -ne "test_awesome_attachment_render.py"' in gate
    for workflow in (ci, release):
        assert 'Join-Path $HOME ".codex"' in workflow
        assert "-SkipOfficeTests" in workflow


def test_release_gate_validates_export_from_clean_public_development_checkout(tmp_path: Path):
    require_export_checkout()
    checkout = tmp_path / "checkout"
    archived = subprocess.run(
        ["git", "archive", "--format=zip", "--output", str(tmp_path / "checkout.zip"), "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert archived.returncode == 0, archived.stdout + archived.stderr
    with zipfile.ZipFile(tmp_path / "checkout.zip") as bundle:
        bundle.extractall(checkout)
    shutil.copy2(ROOT / "scripts/release_gate.ps1", checkout / "scripts/release_gate.ps1")
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "ci@example.invalid"],
        ["config", "user.name", "CI"],
        ["add", "-A"],
        ["commit", "-m", "clean checkout"],
    ):
        subprocess.run(["git", *args], cwd=checkout, check=True, capture_output=True)
    # A private development checkout may contain excluded design notes; the
    # reviewed public source checkout intentionally never contains them.
    assert json.loads((checkout / "package-info.json").read_text(encoding="utf-8"))["repositoryVisibility"] == "public"

    gated = subprocess.run(
        [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(checkout / "scripts/release_gate.ps1"), "-PublicSnapshotOnly",
        ],
        cwd=checkout,
        capture_output=True,
        text=True,
        timeout=600,
        env={**os.environ, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}"},
    )
    assert gated.returncode == 0, gated.stdout + gated.stderr
    assert "Public release snapshot created:" in gated.stdout
    assert '"passed": true' in gated.stdout


def test_package_release_exports_clean_public_source_from_git_checkout(tmp_path: Path):
    require_export_checkout()
    checkout = tmp_path / "checkout"
    archive = tmp_path / "checkout.zip"
    archived = subprocess.run(
        ["git", "archive", "--format=zip", "--output", str(archive), "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert archived.returncode == 0, archived.stdout + archived.stderr
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(checkout)
    shutil.copy2(ROOT / "scripts/package_release.ps1", checkout / "scripts/package_release.ps1")
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "ci@example.invalid"],
        ["config", "user.name", "CI"],
        ["add", "-A"],
        ["commit", "-m", "clean checkout"],
    ):
        subprocess.run(["git", *args], cwd=checkout, check=True, capture_output=True)
    # Both the private reviewed tree and the already-public source tree are
    # valid package inputs; the archive assertion below enforces exclusion.

    dist_a = tmp_path / "dist-a"
    dist_b = tmp_path / "dist-b"
    results = []
    for destination in (dist_a, dist_b):
        results.append(subprocess.run(
            [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(checkout / "scripts/package_release.ps1"),
                "-SourceRoot", str(checkout),
                "-OutputDirectory", str(destination),
            ],
            cwd=checkout,
            capture_output=True,
            text=True,
            timeout=180,
            env={**os.environ, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}"},
        ))
    assert all(result.returncode == 0 for result in results), "\n".join(
        result.stdout + result.stderr for result in results
    )
    zip_name = "awesome-editable-ppt-workflow-1.2.3-windows.zip"
    assert (dist_a / zip_name).read_bytes() == (dist_b / zip_name).read_bytes()
    with zipfile.ZipFile(dist_a / zip_name) as bundle:
        names = set(bundle.namelist())
        assert "docs/superpowers/specs/2026-08-11-v6-adaptive-image-materials-design.md" not in names
        assert ".superpowers/sdd/2026-08-11-v6-adaptive-image-materials-implementation/task-4-report.md" not in names

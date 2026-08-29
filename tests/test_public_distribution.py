import json
from pathlib import Path
import subprocess
import zipfile
import pytest


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8-sig")


def test_installer_reads_distribution_metadata_without_github_cli():
    installer = read("install.ps1")
    assert "$PackageInfo.repository" in installer
    assert "$PackageInfo.marketplace" in installer
    assert "Get-Command gh" not in installer
    assert "gh auth" not in installer


def test_double_click_and_lifecycle_entrypoints_exist():
    for relative in (
        "setup.cmd",
        "update.cmd",
        "update.ps1",
        "uninstall.cmd",
        "uninstall.ps1",
    ):
        assert (ROOT / relative).is_file(), relative


def test_uninstaller_only_deletes_an_owned_runtime_when_requested():
    uninstaller = read("uninstall.ps1")
    assert "RemoveRuntime" in uninstaller
    assert "Test-RuntimeOwnershipSentinel" in uninstaller
    assert "Assert-RuntimeRootLocation" in uninstaller
    assert "plugin remove" in uninstaller
    assert "Get-ChildItem -Recurse" not in uninstaller


def test_runtime_installer_resumes_completed_dependency_stages():
    installer = read("plugins/awesome-editable-ppt-workflow/scripts/install_runtime.ps1")
    assert "runtime_install_state.json" in installer
    assert "workflow_dependencies_ready" in installer
    assert "editable_cli_ready" in installer
    assert "--force-reinstall" not in installer


def test_source_metadata_matches_distribution_visibility():
    marketplace = json.loads(read(".agents/plugins/marketplace.json"))
    package = json.loads(read("package-info.json"))
    if package["repositoryVisibility"] == "private":
        assert marketplace["name"] == package["marketplacePreviewIdentity"]
        assert marketplace["name"].startswith("editable-ppt-local-preview-v")
        assert package["marketplace"] == "editable-ppt-private"
    else:
        assert package["repositoryVisibility"] == "public"
        assert marketplace["name"] == "editable-ppt-public"
        assert package["marketplace"] == "editable-ppt-public"


def test_public_distribution_assets_exist():
    for relative in (
        "LICENSE",
        "SECURITY.md",
        "docs/QUICKSTART.zh-CN.md",
        "docs/USER_GUIDE.zh-CN.md",
        "docs/TROUBLESHOOTING.zh-CN.md",
        "scripts/export_public_release.ps1",
        "scripts/check_public_release.py",
        "scripts/package_release.ps1",
        "scripts/export_public_release.ps1",
        "public-release-files.json",
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
    ):
        assert (ROOT / relative).is_file(), relative


def test_current_release_metadata_and_user_contract_are_consistent():
    package = json.loads(read("package-info.json"))
    plugin = json.loads(read("plugins/awesome-editable-ppt-workflow/.codex-plugin/plugin.json"))
    marketplace = json.loads(read(".agents/plugins/marketplace.json"))
    assert package["pluginVersion"] == plugin["version"] == "1.2.2"
    assert package["releaseTag"] == "v1.2.2"
    assert marketplace["interface"]["displayName"] == "Awesome Editable PPT Workflow 1.2.2"
    assert package["promptContractVersion"] == "consulting-page-director-v2-source-text-custody"
    assert package["pageImagePolicy"] == "generate-without-refs-edit-with-confirmed-refs"
    assert package["designAcceptancePolicy"] == "single-independent-review-with-at-most-two-corrections"
    assert package["qaPolicyVersion"] == "sole-independent-consulting-visual-review-v2"
    assert package["reconstructionVersion"] == "accepted-image-codex-worker-v2-sealed-text-repairs"
    docs = "\n".join(read(path) for path in (
        "README.md", "docs/RELEASE.md", "docs/USER_GUIDE.zh-CN.md", "docs/TROUBLESHOOTING.zh-CN.md"
    ))
    for phrase in (
        "sole material/reference authority", "explicit keep/remove", "1–16 confirmed refs",
        "high-fidelity best effort", "pixel-perfect", "single independent visual review", "token_expired",
        "dynamic centered 17:8 crop", "at most two corrections", "Codex page worker",
    ):
        assert phrase in docs
    for retired in ("candidate1 fallback", "marked unvalidated", "rejected rather than stretched or cropped"):
        assert retired not in docs


def test_export_creates_public_metadata_and_removes_private_material(tmp_path):
    if not (ROOT / ".git").exists():
        pytest.skip("public export requires the private reviewed Git checkout")
    output = tmp_path / "public"
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts/export_public_release.ps1"),
            "-OutputPath",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    marketplace = json.loads(
        (output / ".agents/plugins/marketplace.json").read_text(encoding="utf-8-sig")
    )
    package = json.loads((output / "package-info.json").read_text(encoding="utf-8-sig"))
    assert marketplace["name"] == "editable-ppt-public"
    assert package["marketplace"] == "editable-ppt-public"
    assert package["repository"] == "czd176224-rgb/awesome-editable-ppt-workflow"
    assert package["repositoryVisibility"] == "public"
    assert package["releaseStatus"] == "published-public-marketplace"
    audit = json.loads((output / "public-release-audit.json").read_text(encoding="utf-8-sig"))
    source_manifest = json.loads((output / "public-source-manifest.json").read_text(encoding="utf-8-sig"))
    assert audit["schemaVersion"] == "public-release-audit-v1"
    assert audit["promptContractVersion"] == "consulting-page-director-v2-source-text-custody"
    assert audit["pageImagePolicy"] == "generate-without-refs-edit-with-confirmed-refs"
    assert audit["sourceManifestSha256"]
    assert source_manifest["schemaVersion"] == "public-source-manifest-v1"
    assert source_manifest["promptContractVersion"] == "consulting-page-director-v2-source-text-custody"
    assert source_manifest["pageImagePolicy"] == "generate-without-refs-edit-with-confirmed-refs"
    assert audit["root"] == "."
    assert not (output / "docs/superpowers").exists()
    assert not (output / ".git").exists()


def test_release_zip_contains_dot_directories_and_double_click_installer(tmp_path):
    if not (ROOT / ".git").exists():
        pytest.skip("public export requires the private reviewed Git checkout")
    output = tmp_path / "public"
    export = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts/export_public_release.ps1"),
            "-OutputPath",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert export.returncode == 0, export.stdout + export.stderr
    dist = tmp_path / "dist"
    package = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(output / "scripts/package_release.ps1"),
            "-SourceRoot",
            str(output),
            "-OutputDirectory",
            str(dist),
        ],
        cwd=output,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert package.returncode == 0, package.stdout + package.stderr
    archive = next(dist.glob("*.zip"))
    with zipfile.ZipFile(archive) as bundle:
        names = {name.replace("\\", "/") for name in bundle.namelist()}
    assert ".agents/plugins/marketplace.json" in names
    assert ".github/workflows/ci.yml" in names
    assert ".gitignore" in names
    assert "setup.cmd" in names
    lowered = {name.lower() for name in names}
    assert not any("tests" in name.split("/") for name in lowered)
    assert not any(segment in {"__pycache__", ".pytest_cache", ".cache", ".private", "raw_transport", "fixtures"} for name in lowered for segment in name.split("/"))
    assert not any(name.endswith("sitecustomize.py") or name.endswith((".pyc", ".pyo", ".tmp", ".log")) for name in lowered)
    assert not any(any(part in name.split("/")[-1] for part in ("secret", "token", "credential")) for name in lowered)
    required = {
        "plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/SKILL.md",
        "plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_pipeline.py",
        "plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_reconstruction_worker.py",
        "plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/loop.py",
        "plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/schemas/complex_page_acceptance_v1.schema.json",
        "plugins/awesome-editable-ppt-workflow/.codex-plugin/plugin.json",
        "package-info.json",
    }
    assert required <= names
    assert not any(
        name.startswith(
            "plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/template/"
        )
        for name in names
    )


def test_checker_ignores_untracked_git_and_test_cache_files(tmp_path):
    if not (ROOT / ".git").exists():
        pytest.skip("public export requires the private reviewed Git checkout")
    output = tmp_path / "public"
    export = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts/export_public_release.ps1"),
            "-OutputPath",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert export.returncode == 0, export.stdout + export.stderr
    subprocess.run(["git", "init", "-b", "main"], cwd=output, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=output, check=True, capture_output=True)
    cache = output / ".pytest_cache/v/cache"
    cache.mkdir(parents=True)
    (cache / "nodeids").write_text("[]", encoding="utf-8")
    completed = subprocess.run(
        ["python", "scripts/check_public_release.py", "."],
        cwd=output,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

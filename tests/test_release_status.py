"""Offline release-status checks; run directly with Python or through pytest."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which("pwsh") or shutil.which("powershell")


@unittest.skipUnless(SHELL, "PowerShell is required")
class ReleaseStatusTests(unittest.TestCase):
    def test_real_metadata_verification_accepts_development_and_release(self):
        for status in ("development-not-release-ready", "release-ready", "unknown", None):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for relative in (
                    "verify.ps1", ".agents/plugins/marketplace.json",
                    "plugins/awesome-editable-ppt-workflow/.codex-plugin/plugin.json",
                    "plugins/awesome-editable-ppt-workflow/scripts/check_current_runtime.py",
                    "plugins/awesome-editable-ppt-workflow/scripts/runtime_process_environment.ps1",
                ):
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(ROOT / relative, target)
                package = json.loads((ROOT / "package-info.json").read_text(encoding="utf-8-sig"))
                package["releaseStatus"] = status
                (root / "package-info.json").write_text(json.dumps(package), encoding="utf-8")
                result = subprocess.run([
                    SHELL, "-NoProfile", "-File", str(root / "verify.ps1"), "-MetadataOnly",
                ], cwd=root, capture_output=True)
                expected = status in {"development-not-release-ready", "release-ready"}
                self.assertEqual(result.returncode == 0, expected, result.stderr)
                if expected:
                    self.assertIn(b"verify-metadata-preflight=ok", result.stdout)

    def test_tag_gate_requires_ready_and_matching_version(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        step = workflow.split("- name: Validate immutable tag and metadata", 1)[1]
        script = textwrap.dedent(step.split("run: |", 1)[1].split("      - name:", 1)[0])
        for status, tag, expected in (
            ("development-not-release-ready", "v1.2.3", False),
            ("release-ready", "v1.2.3", True),
            ("release-ready", "v1.2.4", False),
            ("", "v1.2.3", False),
        ):
            with self.subTest(status=status, tag=tag), tempfile.TemporaryDirectory() as directory:
                Path(directory, "package-info.json").write_text(json.dumps({
                    "releaseStatus": status, "releaseTag": "v1.2.3", "pluginVersion": "1.2.3",
                }), encoding="utf-8")
                result = subprocess.run([SHELL, "-NoProfile", "-Command", script], cwd=directory,
                                        env={**os.environ, "GITHUB_REF_NAME": tag}, capture_output=True)
                self.assertEqual(result.returncode == 0, expected, result.stderr)

    def test_export_accepts_both_documented_statuses_and_rejects_unknown(self):
        # Disposable tracked source isolates the export guard; full source validation
        # is covered separately by test_public_distribution.py.
        for status in ("development-not-release-ready", "release-ready", "unknown"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "scripts").mkdir()
                shutil.copy2(ROOT / "scripts/export_public_release.ps1", root / "scripts")
                (root / "scripts/check_public_release.py").write_text("raise SystemExit(0)\n")
                package = json.loads((ROOT / "package-info.json").read_text(encoding="utf-8-sig"))
                package["releaseStatus"] = status
                (root / "package-info.json").write_text(json.dumps(package), encoding="utf-8")
                marketplace = root / ".agents/plugins/marketplace.json"
                marketplace.parent.mkdir(parents=True)
                shutil.copy2(ROOT / ".agents/plugins/marketplace.json", marketplace)
                (root / "public-release-files.json").write_text(json.dumps({"files": [
                    "package-info.json", ".agents/plugins/marketplace.json", "scripts",
                ]}))
                for args in (["init"], ["add", "."],
                             ["-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                              "-c", "commit.gpgsign=false", "commit", "-m", "fixture"]):
                    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
                result = subprocess.run([
                    SHELL, "-NoProfile", "-File", str(root / "scripts/export_public_release.ps1"),
                    "-OutputPath", str(root / "output"),
                ], cwd=root, capture_output=True)
                self.assertEqual(result.returncode == 0, status != "unknown", result.stderr)


if __name__ == "__main__":
    unittest.main()

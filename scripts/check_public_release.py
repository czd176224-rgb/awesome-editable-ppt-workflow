#!/usr/bin/env python3
"""Validate a public awesome-editable-ppt-workflow repository snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


TEXT_SUFFIXES = {
    ".cmd",
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
FORBIDDEN_SUFFIXES = {".docx", ".pptx", ".pdf", ".env"}
FORBIDDEN_DIRECTORY_PARTS = {".git", "__pycache__", ".pytest_cache"}
SKIP_CONTENT_SCAN = {"scripts/check_public_release.py"}
GENERATED_RELEASE_FILES = {"public-source-manifest.json", "public-release-audit.json"}


def normalized(relative: Path) -> str:
    return relative.as_posix()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(files: dict[str, str]) -> str:
    payload = "".join(f"{path} {files[path]}\n" for path in sorted(files))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def release_files(root: Path) -> list[Path]:
    if (root / ".git").exists():
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True,
            capture_output=True,
        )
        return [
            root / item.decode("utf-8")
            for item in completed.stdout.split(b"\0")
            if item
            and item.decode("utf-8") != "public-release-audit.json"
            and (root / item.decode("utf-8")).is_file()
        ]
    return [
        path for path in root.rglob("*")
        if path.is_file() and normalized(path.relative_to(root)) != "public-release-audit.json"
    ]


def validate(root: Path) -> dict:
    errors: list[str] = []
    root = root.resolve()
    manifest_path = root / "plugins/awesome-editable-ppt-workflow/.codex-plugin/plugin.json"
    package_path = root / "package-info.json"
    marketplace_path = root / ".agents/plugins/marketplace.json"
    required = [
        manifest_path,
        package_path,
        marketplace_path,
        root / "LICENSE",
        root / "NOTICE",
        root / "docs/RELEASE.md",
        root / "docs/SECURITY_AND_PROVENANCE.md",
    ]
    for path in required:
        if not path.is_file():
            errors.append(f"missing required file: {path.relative_to(root)}")
    source_manifest_path = root / "public-source-manifest.json"
    source_manifest = None
    if source_manifest_path.is_file():
        source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8-sig"))
        forbidden_provenance = {"sourceCommit", "commit", "branch", "remote", "repositoryPath", "sourcePath"}
        if forbidden_provenance.intersection(source_manifest):
            errors.append("public source manifest contains private-development provenance fields")
        if source_manifest.get("schemaVersion") != "public-source-manifest-v1":
            errors.append("public source manifest schemaVersion is invalid")
        if source_manifest.get("authority") != "tracked-public-source":
            errors.append("public source manifest authority must be tracked-public-source")

    if not errors:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        package = json.loads(package_path.read_text(encoding="utf-8-sig"))
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8-sig"))
        if manifest.get("version") != package.get("pluginVersion"):
            errors.append("plugin manifest and package-info versions differ")
        if package.get("releaseTag") != f"v{package.get('pluginVersion')}":
            errors.append("releaseTag must exactly match v<pluginVersion>")
        if package.get("marketplace") != "editable-ppt-public":
            errors.append("package-info marketplace is not editable-ppt-public")
        if package.get("repository") != "czd176224-rgb/awesome-editable-ppt-workflow":
            errors.append("package-info repository is not the public repository")
        if package.get("repositoryVisibility") != "public":
            errors.append("package-info repositoryVisibility is not public")
        if marketplace.get("name") != "editable-ppt-public":
            errors.append("Marketplace name is not editable-ppt-public")
        entries = marketplace.get("plugins", [])
        if len(entries) != 1 or entries[0].get("name") != "awesome-editable-ppt-workflow":
            errors.append("Marketplace must expose exactly awesome-editable-ppt-workflow")
        if source_manifest is not None:
            for field in (
                "releaseTag", "pluginVersion", "workflowContractVersion",
                "promptContractVersion", "pageImagePolicy",
            ):
                if source_manifest.get(field) != package.get(field):
                    errors.append(f"public source manifest {field} does not match package-info")
            declared = source_manifest.get("files")
            if not isinstance(declared, dict):
                errors.append("public source manifest files must be an object")
            else:
                actual_paths = {
                    normalized(path.relative_to(root))
                    for path in root.rglob("*")
                    if (
                        path.is_file()
                        and not FORBIDDEN_DIRECTORY_PARTS.intersection(path.relative_to(root).parts)
                        and normalized(path.relative_to(root)) not in GENERATED_RELEASE_FILES
                    )
                }
                declared_paths = set(declared)
                if actual_paths != declared_paths:
                    extra = sorted(actual_paths - declared_paths)
                    missing = sorted(declared_paths - actual_paths)
                    errors.append(
                        "source manifest file set mismatch: "
                        f"extra={extra[:5]} missing={missing[:5]}"
                    )
                actual_hashes = {
                    relative: sha256(root / Path(relative))
                    for relative in sorted(actual_paths.intersection(declared_paths))
                }
                for relative, actual_hash in actual_hashes.items():
                    if declared.get(relative) != actual_hash:
                        errors.append(f"public source manifest hash mismatch: {relative}")
                if source_manifest.get("indexTreeSha256") != tree_sha256(
                    {str(path): str(value) for path, value in declared.items()}
                ):
                    errors.append("public source manifest tree digest mismatch")

    token_pattern = re.compile(r"(?:gho_|github_pat_|sk-)[A-Za-z0-9_-]{12,}")
    user_path_pattern = re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+", re.IGNORECASE)
    files: list[dict] = []
    for path in sorted(release_files(root)):
        relative = path.relative_to(root)
        rel = normalized(relative)
        lowered = rel.lower()
        lowered_parts = {part.lower() for part in relative.parts}
        if lowered_parts.intersection(FORBIDDEN_DIRECTORY_PARTS) or lowered.startswith("docs/superpowers/"):
            errors.append(f"forbidden path: {rel}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name.lower() in {
            "auth.json",
            "config.yaml",
        }:
            errors.append(f"forbidden artifact: {rel}")
        is_test_source = "tests" in lowered_parts or "test" in lowered_parts
        if path.suffix.lower() in TEXT_SUFFIXES and rel not in SKIP_CONTENT_SCAN:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            if "editable-ppt-private" in text and not is_test_source:
                errors.append(f"private Marketplace identifier in: {rel}")
            if token_pattern.search(text):
                errors.append(f"credential-like token in: {rel}")
            if user_path_pattern.search(text):
                errors.append(f"machine-specific user path in: {rel}")
            lowered_text = text.lower()
            if "--ref main" in lowered_text and not is_test_source:
                errors.append(f"mutable Marketplace ref in: {rel}")
            if "/tests/" not in f"/{lowered}/" and ("import fitz" in lowered_text or "pymupdf" in lowered_text):
                errors.append(f"prohibited PyMuPDF runtime dependency in: {rel}")
            if (not is_test_source and "d.officecli.ai" in lowered_text and
                    ("invoke-restmethod" in lowered_text or "| iex" in lowered_text or "| bash" in lowered_text)):
                errors.append(f"mutable OfficeCLI installer in: {rel}")
        files.append({"path": rel, "bytes": path.stat().st_size, "sha256": sha256(path)})

    package_identity = {}
    if package_path.is_file():
        package_identity = json.loads(package_path.read_text(encoding="utf-8-sig"))
    return {
        "schemaVersion": "public-release-audit-v1",
        "reportType": "public-release-audit",
        "releaseTag": package_identity.get("releaseTag"),
        "pluginVersion": package_identity.get("pluginVersion"),
        "workflowContractVersion": package_identity.get("workflowContractVersion"),
        "promptContractVersion": package_identity.get("promptContractVersion"),
        "pageImagePolicy": package_identity.get("pageImagePolicy"),
        "sourceManifestSha256": sha256(source_manifest_path) if source_manifest_path.is_file() else None,
        "root": ".",
        "passed": not errors,
        "errors": errors,
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--write-report", type=Path)
    args = parser.parse_args()
    report = validate(args.root)
    if args.write_report:
        args.write_report.parent.mkdir(parents=True, exist_ok=True)
        with args.write_report.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "errors": report["errors"], "fileCount": len(report["files"])}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

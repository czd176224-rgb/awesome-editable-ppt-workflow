#!/usr/bin/env python3
"""Build a deterministic production archive from reviewed public source bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path


BLOCKED_SEGMENTS = {"tests", "test", "__pycache__", ".pytest_cache", ".cache", ".private", "raw_transport", "fixtures", "fixture", ".superpowers", "dist", "tmp", "temp"}
CONTROL_FILES = {"public-source-manifest.json", "public-release-audit.json"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree_sha256(files: dict[str, str]) -> str:
    payload = "".join(f"{path} {files[path]}\n" for path in sorted(files))
    return sha256_bytes(payload.encode("utf-8"))


def allowed(relative: str) -> bool:
    parts = relative.replace("\\", "/").strip("/").split("/")
    lower = [part.lower() for part in parts]
    if any(part in BLOCKED_SEGMENTS for part in lower):
        return False
    leaf = lower[-1]
    if leaf == "sitecustomize.py" or leaf.endswith((".pyc", ".pyo", ".tmp", ".log")):
        return False
    return not any(marker in leaf for marker in ("secret", "token", "credential"))


def git_head_files(root: Path) -> dict[str, bytes]:
    listing = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-r", "-z", "--full-tree", "HEAD"],
        check=True,
        capture_output=True,
    ).stdout
    result: dict[str, bytes] = {}
    for record in listing.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_type, object_id = metadata.decode("ascii").split()
        if mode != "100644" and mode != "100755":
            raise ValueError(f"unsupported tracked release entry mode {mode}: {raw_path!r}")
        if object_type != "blob":
            raise ValueError(f"unsupported tracked release entry type {object_type}: {raw_path!r}")
        relative = raw_path.decode("utf-8")
        result[relative] = subprocess.run(
            ["git", "-C", str(root), "cat-file", "blob", object_id],
            check=True,
            capture_output=True,
        ).stdout
    if not result:
        raise ValueError("Git HEAD contains no tracked release files")
    return result


def filesystem_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).as_posix() not in CONTROL_FILES
    }


def validate_source_manifest(manifest_bytes: bytes, payload: dict[str, bytes]) -> dict:
    manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
    if manifest.get("schemaVersion") != "public-source-manifest-v1":
        raise ValueError("public source manifest schemaVersion is invalid")
    if manifest.get("authority") != "tracked-public-source":
        raise ValueError("public source manifest authority is invalid")
    declared = manifest.get("files")
    if not isinstance(declared, dict) or not all(
        isinstance(path, str) and isinstance(value, str) and len(value) == 64
        for path, value in declared.items()
    ):
        raise ValueError("public source manifest files are invalid")
    if set(payload) != set(declared):
        extra = sorted(set(payload) - set(declared))
        missing = sorted(set(declared) - set(payload))
        raise ValueError(f"source manifest file set mismatch: extra={extra[:5]} missing={missing[:5]}")
    actual = {path: sha256_bytes(data) for path, data in payload.items()}
    for path, digest in actual.items():
        if declared[path] != digest:
            raise ValueError(f"public source manifest hash mismatch: {path}")
    if manifest.get("indexTreeSha256") != tree_sha256({str(k): str(v) for k, v in declared.items()}):
        raise ValueError("public source manifest tree digest mismatch")
    package = json.loads(payload["package-info.json"].decode("utf-8-sig"))
    for field in ("releaseTag", "pluginVersion", "workflowContractVersion"):
        if manifest.get(field) != package.get(field):
            raise ValueError(f"public source manifest {field} does not match package-info")
    if package.get("releaseTag") != f"v{package.get('pluginVersion')}":
        raise ValueError("releaseTag must exactly match v<pluginVersion>")
    return manifest


def validate_audit(audit_bytes: bytes, manifest_bytes: bytes, manifest: dict) -> dict:
    audit = json.loads(audit_bytes.decode("utf-8-sig"))
    if audit.get("schemaVersion") != "public-release-audit-v1" or audit.get("reportType") != "public-release-audit":
        raise ValueError("public release audit schema/identity is invalid")
    if audit.get("passed") is not True or audit.get("errors"):
        raise ValueError("public release audit did not pass")
    if audit.get("sourceManifestSha256") != sha256_bytes(manifest_bytes):
        raise ValueError("public release audit is not bound to the current source manifest")
    for field in ("releaseTag", "pluginVersion", "workflowContractVersion"):
        if audit.get(field) != manifest.get(field):
            raise ValueError(f"public release audit {field} does not match source manifest")
    return audit


def authority(root: Path) -> tuple[dict[str, bytes], dict[str, bytes]]:
    if (root / ".git").exists():
        tracked = git_head_files(root)
        if "public-source-manifest.json" not in tracked:
            raise ValueError("Git HEAD does not contain public-source-manifest.json")
        manifest_bytes = tracked.pop("public-source-manifest.json")
        tracked.pop("public-release-audit.json", None)
        manifest = validate_source_manifest(manifest_bytes, tracked)
        controls = {"public-source-manifest.json": manifest_bytes}
        audit_path = root / "public-release-audit.json"
        if audit_path.is_file():
            audit_bytes = audit_path.read_bytes()
            validate_audit(audit_bytes, manifest_bytes, manifest)
            controls["public-release-audit.json"] = audit_bytes
        return tracked, controls

    manifest_path = root / "public-source-manifest.json"
    if not manifest_path.is_file():
        raise ValueError("exported snapshot is missing public-source-manifest.json")
    manifest_bytes = manifest_path.read_bytes()
    payload = filesystem_files(root)
    manifest = validate_source_manifest(manifest_bytes, payload)
    controls = {"public-source-manifest.json": manifest_bytes}
    audit_path = root / "public-release-audit.json"
    if audit_path.is_file():
        audit_bytes = audit_path.read_bytes()
        validate_audit(audit_bytes, manifest_bytes, manifest)
        controls["public-release-audit.json"] = audit_bytes
    return payload, controls


def build(root: Path, output: Path) -> dict[str, str]:
    payload, controls = authority(root)
    source = {**payload, **controls}
    included = {relative: data for relative, data in source.items() if allowed(relative)}
    included_hashes = {relative: sha256_bytes(data) for relative, data in included.items()}
    archive_manifest = json.dumps(
        {"schemaVersion": "release-archive-manifest-v1", "files": included_hashes},
        sort_keys=True,
        separators=(",", ":"),
    ).encode() + b"\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for relative in sorted(included):
            info = zipfile.ZipInfo(relative, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, included[relative])
        info = zipfile.ZipInfo("ARCHIVE-MANIFEST.json", (1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        bundle.writestr(info, archive_manifest)
    return included_hashes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.source.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Canonical page-local attachment evidence records."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


EVIDENCE_KINDS = frozenset({"text_fact", "table_data", "evidence_image", "chart_preview", "page_preview"})
CHUNK_SIZE = 1200


def normalize_evidence_chunk(
    *,
    asset_id: str,
    kind: str,
    text: str,
    source: Mapping[str, Any],
    ordinal: int,
) -> dict[str, Any]:
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise ValueError("evidence asset_id is required")
    if kind not in EVIDENCE_KINDS:
        raise ValueError("unsupported evidence kind")
    if not isinstance(text, str):
        raise ValueError("evidence text must be a string")
    normalized_text = "\n".join(line.rstrip() for line in text.replace("\r", "").split("\n")).strip()
    source_copy = dict(source)
    for field in ("file", "locator", "sha256"):
        if not isinstance(source_copy.get(field), str) or not source_copy[field]:
            raise ValueError(f"evidence source {field} is required")
    if type(ordinal) is not int or ordinal < 1:
        raise ValueError("evidence ordinal must be positive")
    identity = {
        "asset_id": asset_id.strip(),
        "kind": kind,
        "text": normalized_text,
        "source": source_copy,
        "ordinal": ordinal,
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "evidence_id": f"{asset_id.strip()}_{ordinal:04d}_{digest[:12]}",
        "asset_id": asset_id.strip(),
        "kind": kind,
        "text": normalized_text,
        "source": source_copy,
        "sha256": digest,
        "untrusted_data": True,
    }


def _project_file(project: Path, relative: str) -> Path:
    project = Path(project).resolve()
    path = (project / relative).resolve()
    try:
        path.relative_to(project)
    except ValueError as exc:
        raise ValueError("evidence input must remain inside the project") from exc
    if not path.is_file() or path.is_symlink():
        raise ValueError("evidence input must be a regular project file")
    return path


def _text_chunks(text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    for paragraph in paragraphs:
        chunks.extend(paragraph[index:index + CHUNK_SIZE] for index in range(0, len(paragraph), CHUNK_SIZE))
    return chunks


def build_evidence_index(project: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Build independent indexes per Word page; attachment content is data, never instructions."""
    pages: dict[str, dict[str, Any]] = {}
    for asset in manifest.get("assets", []):
        if not isinstance(asset, Mapping):
            continue
        page_numbers = [value for value in asset.get("page_numbers", []) if type(value) is int and value > 0]
        if not page_numbers:
            continue
        generation = asset.get("generation_input") if isinstance(asset.get("generation_input"), Mapping) else {}
        relative = generation.get("relative_path")
        asset_id = str(asset.get("asset_id", ""))
        filename = str(asset.get("original_filename", asset_id))
        source_sha = str(asset.get("sha256", ""))
        records: list[dict[str, Any]] = []
        if generation.get("media_type") == "text/plain" and isinstance(relative, str):
            text = _project_file(Path(project), relative).read_text(encoding="utf-8-sig")
            for ordinal, value in enumerate(_text_chunks(text), start=1):
                records.append(normalize_evidence_chunk(
                    asset_id=asset_id, kind="table_data" if "|" in value else "text_fact", text=value,
                    source={"file": filename, "locator": f"chunk:{ordinal}", "sha256": source_sha}, ordinal=ordinal,
                ))
        elif isinstance(relative, str) and str(generation.get("media_type", "")).startswith("image/"):
            records.append(normalize_evidence_chunk(
                asset_id=asset_id, kind="evidence_image", text="",
                source={"file": filename, "locator": "embedded-image:1", "sha256": source_sha, "relative_path": relative},
                ordinal=1,
            ))
        for page_number in page_numbers:
            page = pages.setdefault(str(page_number), {"schema_version": "1.0", "page_number": page_number, "chunks": []})
            page["chunks"].extend(dict(record) for record in records)
    return {"schema_version": "1.0", "pages": pages}

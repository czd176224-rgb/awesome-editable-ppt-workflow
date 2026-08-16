"""Project locked Word-page contracts into safe browser-preview data."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

from page_requirement_summary import load_verified_project_page_contracts


PAGE_MARKER = re.compile(r"^\s*第\s*\d+\s*页\s*$")


def _title(text: str, page_number: int) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and PAGE_MARKER.fullmatch(lines[0]):
        lines = lines[1:]
    return lines[0] if lines else f"第{page_number}页内容"


def _assets(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    projected: list[dict[str, str]] = []
    for binding in value:
        if not isinstance(binding, dict):
            continue
        asset_id = binding.get("asset_id")
        media_type = binding.get("media_type")
        relative = binding.get("relative_path")
        if not all(isinstance(item, str) and item for item in (asset_id, media_type, relative)):
            continue
        projected.append(
            {
                "asset_id": asset_id,
                "media_type": media_type,
                "name": PurePosixPath(relative.replace("\\", "/")).name,
            }
        )
    return projected


def project_pages(project: Path, page_count: int) -> list[dict[str, Any]]:
    if type(page_count) is not int or page_count < 1:
        raise ValueError("project page count identity is invalid")
    _authority, contracts = load_verified_project_page_contracts(project)
    if page_count != len(contracts):
        raise ValueError("project page count does not match verified contracts")
    pages: list[dict[str, Any]] = []
    for page_number, contract in enumerate(contracts, start=1):
        text = contract.get("source_text")
        if (
            type(contract.get("page_number")) is not int
            or contract["page_number"] != page_number
            or not isinstance(text, str)
            or not text.strip()
        ):
            raise ValueError(f"locked page contract {page_number} is invalid")
        pages.append(
            {
                "page_number": page_number,
                "title": _title(text, page_number),
                "text": text,
                "assets": _assets(contract.get("asset_bindings")),
                "table_count": len(contract.get("source_tables") or []),
            }
        )
    return pages

"""Classify Word-owned assets for one-page generation without blocking the deck."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Mapping


IMAGE_MEDIA = frozenset({"image/png", "image/jpeg", "image/webp", "image/bmp"})
SPREADSHEET_MEDIA = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "text/csv",
    }
)
DOCUMENT_MEDIA = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-powerpoint",
        "text/plain",
    }
)
REFERENCE_CUES = ("附件", "附表", "附图", "下图", "上图", "下表", "上表", "根据图", "根据表")


def classify_page_asset(
    media_type: str,
    *,
    binding_status: str,
    has_generation_input: bool,
) -> dict[str, Any]:
    """Return an honest, non-blocking processing classification."""
    if binding_status != "bound":
        return {
            "asset_role": "unsupported",
            "processing": "unavailable",
            "blocking": False,
            "advisories": ["附件无法从 Word 中解析；该页继续生成，并在页面 QA 中提示。"],
        }
    if media_type in IMAGE_MEDIA and has_generation_input:
        return {"asset_role": "mandatory_inline_image", "processing": "direct_image", "blocking": False, "advisories": []}
    if media_type in SPREADSHEET_MEDIA:
        return {"asset_role": "data_source", "processing": "extract_content", "blocking": False, "advisories": []}
    if media_type in DOCUMENT_MEDIA:
        return {"asset_role": "document_source", "processing": "extract_content", "blocking": False, "advisories": []}
    return {
        "asset_role": "unsupported",
        "processing": "unavailable",
        "blocking": False,
        "advisories": ["附件格式暂不能可靠读取；该页继续生成，并在页面 QA 中提示。"],
    }


def referenced_by_page(page_text: str, asset: Mapping[str, Any]) -> bool:
    """Detect an explicit page-local instruction without inventing semantics."""
    normalized = "".join(str(page_text).split()).casefold()
    filename = asset.get("original_filename")
    if isinstance(filename, str) and filename:
        name = PurePosixPath(filename).name.casefold()
        stem = PurePosixPath(filename).stem.casefold()
        if name in normalized or (len(stem) >= 2 and stem in normalized):
            return True
    return any(cue.casefold() in normalized for cue in REFERENCE_CUES)


def binding_metadata(page_text: str, asset: Mapping[str, Any]) -> dict[str, Any]:
    classification = classify_page_asset(
        str(asset.get("media_type", "application/octet-stream")),
        binding_status=str(asset.get("binding_status", "unresolved")),
        has_generation_input=isinstance(asset.get("generation_input"), Mapping),
    )
    classification["use_policy"] = (
        "required" if classification["asset_role"] == "mandatory_inline_image" or referenced_by_page(page_text, asset)
        else "contextual"
    )
    return classification

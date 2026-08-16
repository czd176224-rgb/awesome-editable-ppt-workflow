"""Auditable V4 policy for page-local source images."""

from __future__ import annotations

import copy
import re
from typing import Any, Iterable, Mapping


PAGE_IMAGE_POLICY_VERSION = "page-image-policy-v1"
PAGE_COMMENT_DIRECTIVE_PREFIX = "[require-page-image:"
_PAGE_DIRECTIVE = re.compile(r"^\[require-page-image:([A-Za-z0-9][A-Za-z0-9._-]{0,127})\]$")


def _asset_index(assets: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    images: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for raw in assets:
        if not isinstance(raw, Mapping):
            raise ValueError("page image asset must be an object")
        asset_id = raw.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            raise ValueError("page image asset_id must be a non-empty string")
        if asset_id in positions:
            raise ValueError(f"duplicate page image asset_id: {asset_id}")
        required = ("path", "sha256", "media_type")
        missing = [field for field in required if not isinstance(raw.get(field), str) or not raw[field]]
        if missing:
            raise ValueError(f"page image {asset_id} is missing: {', '.join(missing)}")
        positions[asset_id] = len(images)
        image = copy.deepcopy(dict(raw))
        image["presence_policy"] = "reference_only"
        image["promotion"] = None
        images.append(image)
    return images, positions


def _page_promotions(comments: Iterable[str]) -> list[tuple[str, dict[str, Any]]]:
    promotions: list[tuple[str, dict[str, Any]]] = []
    for comment in comments:
        if not isinstance(comment, str):
            raise ValueError("page comments must be strings")
        match = _PAGE_DIRECTIVE.fullmatch(comment)
        if match:
            asset_id = match.group(1)
            promotions.append(
                (
                    asset_id,
                    {
                        "source": "page_comment",
                        "directive_type": "require_page_image",
                        "asset_id": asset_id,
                        "raw": comment,
                    },
                )
            )
    return promotions


def _global_promotions(directives: Iterable[Mapping[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    promotions: list[tuple[str, dict[str, Any]]] = []
    for directive in directives:
        if not isinstance(directive, Mapping):
            raise ValueError("global style directives must be objects")
        if directive.get("directive") != "require_page_image":
            continue
        if set(directive) != {"directive", "asset_id"}:
            raise ValueError("require_page_image global style directive has an unsupported shape")
        asset_id = directive.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            raise ValueError("require_page_image global style directive needs an asset_id")
        promotions.append(
            (
                asset_id,
                {
                    "source": "global_style",
                    "directive_type": "require_page_image",
                    "asset_id": asset_id,
                    "raw": copy.deepcopy(dict(directive)),
                },
            )
        )
    return promotions


def apply_page_image_policy(
    assets: Iterable[Mapping[str, Any]],
    *,
    page_comments: Iterable[str] = (),
    global_style_directives: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Default images to reference-only and promote only exact asset-ID directives."""
    images, positions = _asset_index(assets)
    promotions = _page_promotions(page_comments) + _global_promotions(global_style_directives)
    required: list[str] = []
    for asset_id, audit in promotions:
        if asset_id not in positions:
            raise ValueError(f"unknown page image asset_id in required-presence directive: {asset_id}")
        image = images[positions[asset_id]]
        if image["presence_policy"] == "required_presence":
            raise ValueError(f"duplicate required-presence directive for page image: {asset_id}")
        image["presence_policy"] = "required_presence"
        image["promotion"] = audit
        required.append(asset_id)
    return {
        "policy_version": PAGE_IMAGE_POLICY_VERSION,
        "images": images,
        "required_presence_asset_ids": required,
    }

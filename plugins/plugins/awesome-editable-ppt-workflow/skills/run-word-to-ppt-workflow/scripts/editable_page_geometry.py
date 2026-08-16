"""Reusable geometry contract for the future editable-page backend."""

from __future__ import annotations

from typing import Any, Mapping

from body_image_profile import body_image_profile
from fixed_region_contract import CONTRACT_VERSION


def editable_page_target(style_execution: Mapping[str, Any]) -> dict[str, Any]:
    frame = style_execution.get("fixed_frame")
    profile = style_execution.get("canvas_profile")
    if not isinstance(frame, Mapping) or frame.get("geometry_version") != CONTRACT_VERSION:
        raise ValueError("editable page target requires confirmed body bounds")
    if not isinstance(profile, Mapping) or not isinstance(profile.get("aspect_ratio"), str):
        raise ValueError("editable page target requires a slide aspect ratio")
    body_profile = style_execution.get("body_image_profile")
    if not isinstance(body_profile, Mapping):
        body_profile = body_image_profile("balanced")
    return {
        "coordinate_mode": "dynamic_source_normalized",
        "slide_aspect_ratio": profile["aspect_ratio"],
        "content_box_cm": dict(frame["body_bounds_cm"]),
        "content_box": dict(frame["body_bounds"]),
        "body_image_profile": dict(body_profile),
    }

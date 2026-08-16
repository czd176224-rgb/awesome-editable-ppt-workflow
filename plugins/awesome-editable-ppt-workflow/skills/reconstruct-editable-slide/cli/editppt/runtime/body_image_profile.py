"""Installed reconstruction boundary for the accepted 17:8 body image."""

from __future__ import annotations

from typing import Any

try:
    from .fixed_region_runtime import CONTENT_BOX
except ImportError:  # direct runtime script execution through the editppt launcher
    from fixed_region_runtime import CONTENT_BOX


BODY_IMAGE_PROFILE_VERSION = "body-image-profile-v2"
TARGET_ASPECT_RATIO = 17 / 8
DIRECT_ASPECT_TOLERANCE = 0.01


def mapping_for_source(width: int, height: int) -> dict[str, Any]:
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError("source image dimensions must be positive integers")
    source_ratio = width / height
    error = abs(source_ratio / TARGET_ASPECT_RATIO - 1)
    repair_required = error > DIRECT_ASPECT_TOLERANCE
    box_cm = {
        "x": CONTENT_BOX["left"] * 2.54,
        "y": CONTENT_BOX["top"] * 2.54,
        "w": CONTENT_BOX["width"] * 2.54,
        "h": CONTENT_BOX["height"] * 2.54,
    }
    return {
        "version": BODY_IMAGE_PROFILE_VERSION,
        "mode": "repair_required" if repair_required else "direct",
        "source_size": {"width": width, "height": height},
        "aspect_error": error,
        "effective_box_cm": box_cm,
        "semantic_qa_required": False,
        "image_repair_required": repair_required,
    }

"""17:8 body-image generation profile and V4 repair gate."""

from __future__ import annotations

from typing import Any

from fixed_region_contract import BODY_BOX_CM


BODY_IMAGE_PROFILE_VERSION = "body-image-profile-v2"
# V5 keeps one exact canvas across every production profile. Profiles change
# model quality, candidate/repair policy, and concurrency, never slide geometry.
PROFILE_SIZES = {"speed": "1904x896", "balanced": "1904x896", "quality": "1904x896"}
TARGET_ASPECT_RATIO = 17 / 8
DIRECT_ASPECT_TOLERANCE = 0.01


def body_image_profile(profile: str) -> dict[str, Any]:
    if profile not in PROFILE_SIZES:
        raise ValueError("production profile must be speed, balanced, or quality")
    return {
        "version": BODY_IMAGE_PROFILE_VERSION, "production_profile": profile,
        "size": PROFILE_SIZES[profile], "ratio": "17:8", "mapping": "direct_then_repair",
        "direct_aspect_tolerance": DIRECT_ASPECT_TOLERANCE,
    }


def mapping_for_source(width: int, height: int) -> dict[str, Any]:
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError("source image dimensions must be positive integers")
    source_ratio = width / height
    error = abs(source_ratio / TARGET_ASPECT_RATIO - 1)
    box = dict(BODY_BOX_CM)
    mode = "direct"
    repair_required = False
    if error > DIRECT_ASPECT_TOLERANCE:
        mode = "repair_required"
        repair_required = True
    return {
        "version": BODY_IMAGE_PROFILE_VERSION, "mode": mode,
        "source_size": {"width": width, "height": height}, "aspect_error": error,
        "effective_box_cm": box, "semantic_qa_required": False,
        "image_repair_required": repair_required,
    }

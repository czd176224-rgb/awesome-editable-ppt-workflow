"""Runtime boundary for the sole supported editable-PPT geometry contract."""

from __future__ import annotations

from typing import Any, Mapping

CM_PER_INCH = 2.54
CONTRACT_VERSION = "fixed-canvas-cm-v2"
GEOMETRY_TOLERANCE_RATIO = 0.001
SLIDE_SIZE_CM = {"w": 25.4, "h": 14.288}
BODY_BOX_CM = {"x": 0.81, "y": 2.3, "w": 23.78, "h": 11.18}
SLIDE = {
    "width": float(SLIDE_SIZE_CM["w"]) / CM_PER_INCH,
    "height": float(SLIDE_SIZE_CM["h"]) / CM_PER_INCH,
    "size_mode": CONTRACT_VERSION,
    "background": "#FFFFFF",
}
CONTENT_BOX = {
    "left": float(BODY_BOX_CM["x"]) / CM_PER_INCH,
    "top": float(BODY_BOX_CM["y"]) / CM_PER_INCH,
    "width": float(BODY_BOX_CM["w"]) / CM_PER_INCH,
    "height": float(BODY_BOX_CM["h"]) / CM_PER_INCH,
}
def _same_number(actual: Any, expected: float) -> bool:
    if not isinstance(actual, (int, float)):
        return False
    tolerance = abs(float(expected)) * GEOMETRY_TOLERANCE_RATIO
    return abs(float(actual) - float(expected)) <= max(tolerance, 1e-9)


def require_contract(value: Any) -> None:
    if value != CONTRACT_VERSION:
        raise ValueError(f"editable reconstruction accepts only workflow_contract_version {CONTRACT_VERSION}")


def require_source_size(width: Any, height: Any) -> None:
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError("editable reconstruction source dimensions must be positive integers")


def require_slide(value: Any) -> None:
    if not isinstance(value, Mapping) or any(
        not _same_number(value.get(key), expected)
        for key, expected in SLIDE.items()
        if key not in {"size_mode", "background"}
    ):
        raise ValueError(f"manifest slide does not match {CONTRACT_VERSION}")
    if value.get("size_mode") != CONTRACT_VERSION:
        raise ValueError(f"manifest slide.size_mode must be {CONTRACT_VERSION}")
    if str(value.get("background", "")).upper() != "#FFFFFF":
        raise ValueError("manifest slide.background must be the fixed neutral #FFFFFF")


def require_content_box(value: Any) -> None:
    if not isinstance(value, Mapping) or any(
        not _same_number(value.get(key), expected) for key, expected in CONTENT_BOX.items()
    ):
        raise ValueError(f"manifest content_box does not match {CONTRACT_VERSION}")


def validate_manifest_geometry(manifest: Mapping[str, Any]) -> None:
    require_contract(manifest.get("workflow_contract_version"))
    require_slide(manifest.get("slide"))
    require_content_box(manifest.get("content_box"))
    source = manifest.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("manifest source is missing")
    require_source_size(source.get("width_px"), source.get("height_px"))

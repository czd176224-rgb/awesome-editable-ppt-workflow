"""Single immutable centimetre geometry authority for fixed-canvas-cm-v2."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping


CONTRACT_VERSION = "fixed-canvas-cm-v2"
SLIDE_SIZE_CM: Mapping[str, float] = MappingProxyType({"w": 25.4, "h": 14.288})
SLIDE_SIZE_IN: Mapping[str, float] = MappingProxyType({"w": 10.0, "h": 14.288 / 2.54})
GEOMETRY_TOLERANCE_RATIO = 0.001

BODY_BOX_CM: Mapping[str, float] = MappingProxyType({"x": 0.81, "y": 2.3, "w": 23.78, "h": 11.18})
BODY_REMAINDER_CM: Mapping[str, float] = MappingProxyType(
    {"left": 0.81, "top": 2.3, "right": 0.81, "bottom": 0.808}
)
TITLE_BOX_CM: Mapping[str, float] = MappingProxyType({"x": 0.9, "y": 0.5, "w": 20.066, "h": 1.4288})
LOGO_BOX_CM: Mapping[str, float] = MappingProxyType({"x": 21.844, "y": 0.57152, "w": 2.667, "h": 1.0716})
FOOTER_LINE: Mapping[str, Any] = MappingProxyType(
    {"x": 0.9, "y": 13.64504, "w": 23.6, "h": 0.028576, "color": "#B8C0CC"}
)
PAGE_NUMBER_BOX_CM: Mapping[str, float] = MappingProxyType(
    {"x": 23.368, "y": 13.687904, "w": 1.143, "h": 0.3572}
)
PAGE_NUMBER_STYLE: Mapping[str, Any] = MappingProxyType(
    {"font": "Microsoft YaHei", "size_pt": 9, "color": "#6B7280"}
)


def _copy(value: Mapping[str, Any]) -> dict[str, Any]:
    return dict(value)


def normalized_box(box: Mapping[str, float]) -> dict[str, float]:
    return {
        "x": float(box["x"]) / SLIDE_SIZE_CM["w"],
        "y": float(box["y"]) / SLIDE_SIZE_CM["h"],
        "w": float(box["w"]) / SLIDE_SIZE_CM["w"],
        "h": float(box["h"]) / SLIDE_SIZE_CM["h"],
    }


def fixed_frame_execution() -> dict[str, Any]:
    """Return the complete executable frame; callers cannot supply alternatives."""
    return {
        "geometry_version": CONTRACT_VERSION,
        "body_bounds_cm": _copy(BODY_BOX_CM),
        "body_bounds": normalized_box(BODY_BOX_CM),
        "title_bounds_cm": _copy(TITLE_BOX_CM),
        "title_bounds": normalized_box(TITLE_BOX_CM),
        "logo_bounds_cm": _copy(LOGO_BOX_CM),
        "logo_bounds": normalized_box(LOGO_BOX_CM),
        "footer_line": _copy(FOOTER_LINE),
        "page_number_bounds_cm": _copy(PAGE_NUMBER_BOX_CM),
        "page_number_bounds": normalized_box(PAGE_NUMBER_BOX_CM),
        "page_number_style": _copy(PAGE_NUMBER_STYLE),
    }

"""Deterministic image/native/hybrid route selection."""

from __future__ import annotations

from typing import Any, Mapping

from page_complexity import measure_contract


def choose_page_route(contract: Mapping[str, Any]) -> dict[str, Any]:
    features = measure_contract(contract)
    tables = features["table_cells"]
    chars = features["characters"]
    images = sum(
        1 for item in contract.get("asset_bindings", [])
        if isinstance(item, Mapping) and item.get("asset_role") == "mandatory_inline_image"
    )
    relations = features["relations"]
    if (tables and images) or (chars > 900 and images):
        route, reason = "hybrid", "authoritative_structured_content_with_required_visuals"
    elif tables >= 6 or chars > 800 or features["numbers"] >= 10:
        route, reason = "native", "dense_or_data_heavy_content"
    elif relations >= 2 or (chars < 500 and tables == 0):
        route, reason = "image", "visual_relationship_or_concise_narrative"
    else:
        route, reason = "hybrid", "mixed_content"
    return {"schema_version": "1.0", "route": route, "reason": reason, "features": features}


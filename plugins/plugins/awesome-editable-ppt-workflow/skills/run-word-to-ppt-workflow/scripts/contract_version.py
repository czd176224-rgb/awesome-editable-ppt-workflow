"""Current-only workflow contract authority."""

from __future__ import annotations


from workflow_contract import WORKFLOW_VERSION, require_v4


CURRENT_CONTRACT = WORKFLOW_VERSION
SUPPORTED_CONTRACTS = frozenset({CURRENT_CONTRACT})
OLD_PROJECT_ERROR = "当前插件仅支持 word-ppt-workflow-v4 新项目；旧项目和旧缓存不读取。"


def require_supported_contract(run: dict) -> str:
    """Return the sole supported contract for a persisted project state."""
    try:
        require_v4(run)
    except ValueError as exc:
        raise ValueError(OLD_PROJECT_ERROR) from exc
    return CURRENT_CONTRACT


def contract_features(version: str) -> frozenset[str]:
    """Return the capabilities currently available to a Word-only project."""
    if version != CURRENT_CONTRACT:
        raise ValueError(OLD_PROJECT_ERROR)
    return frozenset({
        "style_confirmation", "fixed_cm_region", "dynamic_source_size", "required_svg_logo",
        "bounded_evidence", "risk_qa", "sealed_page_material_bundle",
        "complete_image2_body", "page_image_trace_roles", "physical_page_title_inference",
    })

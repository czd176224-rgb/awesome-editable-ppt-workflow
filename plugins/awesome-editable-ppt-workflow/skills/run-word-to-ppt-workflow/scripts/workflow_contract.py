"""Independent version authority for the current V4 Word-to-PPT workflow."""

from __future__ import annotations

from typing import Any, Mapping

from fixed_region_contract import CONTRACT_VERSION as GEOMETRY_VERSION
WORKFLOW_VERSION = "word-ppt-workflow-v4"
MATERIAL_BUNDLE_VERSION = "page-material-bundle-v4"
PROMPT_VERSION = "page-prompt-v8"
QA_POLICY_VERSION = "risk-qa-v5"
PAGE_CACHE_CONTRACT_VERSION = "v4-editable-page-cache-v3"
EFFECTIVE_PAGE_AUTHORITY_VERSION = "effective-page-authority-v3"
RECONSTRUCTION_VERSION = "editable-image-v3"
FIXED_LAYER_VERSION = "native-layer-v3"


def version_vector() -> dict[str, str]:
    return {
        "workflow_contract_version": WORKFLOW_VERSION,
        "geometry_contract_version": GEOMETRY_VERSION,
        "prompt_contract_version": PROMPT_VERSION,
        "qa_policy_version": QA_POLICY_VERSION,
        "reconstruction_version": RECONSTRUCTION_VERSION,
        "fixed_layer_version": FIXED_LAYER_VERSION,
    }


def require_v4(run: Mapping[str, Any]) -> None:
    if run.get("workflow_contract_version") != WORKFLOW_VERSION:
        raise ValueError(
            f"Only {WORKFLOW_VERSION} projects are supported; create a new project with the current plugin."
        )

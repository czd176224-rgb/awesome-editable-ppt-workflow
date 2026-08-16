"""Build deterministic visual-contract recommendations from locked page contracts."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from page_requirement_summary import (
    SUMMARY_PATH,
    build_page_requirement_summary,
    load_current_project_page_contracts,
    verify_page_requirement_summary,
)
from project_artifact_path import project_artifact_path


CATALOG_PATH = Path(__file__).resolve().parent / "confirm_ui" / "static" / "catalogs.json"
RECOMMENDATIONS_PATH = Path("confirm_ui") / "recommendations.json"
CANVAS_ID = "ppt169"

_SIGNAL_TERMS = {
    "policy-project-brief": (
        "政策", "项目", "政府", "国资", "进展", "阶段", "规划", "实施", "会议", "汇报",
    ),
    "brand-narrative-business": (
        "品牌", "发布", "传播", "活动", "用户", "市场", "合作", "赛事", "故事", "体验",
    ),
    "evidence-investment-bp": (
        "技术", "研发", "实验", "专利", "产品", "融资", "投资", "数据", "平台", "客户",
    ),
}


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _write_json(project: Path, path: Path, data: dict[str, Any]) -> None:
    safe_path = project_artifact_path(project, RECOMMENDATIONS_PATH, create_parent=True)
    if Path(path) != safe_path:
        raise ValueError("prepare recommendations path is not project-local")
    path = safe_path
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary = project_artifact_path(project, temporary)
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _locked_contracts(project: Path) -> list[dict[str, Any]]:
    state = _read_json(project / "workflow_run.json")
    jobs = state.get("jobs")
    confirmation = state.get("style_confirmation")
    _authority, contracts = load_current_project_page_contracts(project)
    if (
        not isinstance(jobs, list)
        or not isinstance(confirmation, dict)
        or confirmation.get("status") != "pending"
        or any(not isinstance(job, dict) or job.get("status") != "pending_style_confirmation" for job in jobs)
    ):
        raise ValueError("workflow is not locked for style confirmation")
    return contracts


def _body_signal_text(contracts: list[dict[str, Any]]) -> str:
    fields = ("source_text", "page_purpose")
    chunks: list[str] = []
    for contract in contracts:
        chunks.extend(str(contract.get(field, "")) for field in fields)
        for asset in contract.get("asset_bindings", []):
            if isinstance(asset, dict):
                chunks.append(str(asset.get("media_type", "")))
                chunks.append(str(asset.get("asset_role", "")))
    return "\n".join(chunks).lower()


def _recommended_template_index(contracts: list[dict[str, Any]], templates: list[dict[str, Any]]) -> int:
    text = _body_signal_text(contracts)
    scores = {
        template["id"]: sum(text.count(term.lower()) for term in _SIGNAL_TERMS[template["id"]])
        for template in templates
    }
    return max(range(len(templates)), key=lambda index: (scores[templates[index]["id"]], -index))


def _template_direction(template: dict[str, Any]) -> dict[str, Any]:
    substyles = template.get("substyles")
    substyle = substyles[0] if isinstance(substyles, list) and substyles else None
    defaults = substyle["defaults"] if substyle is not None else template.get("defaults")
    if not isinstance(defaults, dict):
        raise ValueError(f"template {template.get('id')} must provide visual defaults")
    required = {
        "visual_style", "color", "icons", "typography", "image_rendering", "style_axes",
        "layout_preferences", "information_density", "background_system", "image_role",
        "evidence_strength", "composition_tendency", "brand_device",
    }
    missing = sorted(required.difference(defaults))
    if missing:
        raise ValueError(f"template {template.get('id')} is missing defaults: {', '.join(missing)}")
    direction = {
        "name_zh": substyle.get("name") if substyle is not None else template.get("name"),
        "note_zh": substyle.get("impact") if substyle is not None else template.get("impact"),
        "template_selection": {
            "id": template["id"],
            "label": template["name"],
            "version": "1.0",
            "substyle_id": substyle.get("id") if substyle is not None else None,
            "override_fields": [],
        },
    }
    direction.update(defaults)
    return direction


def _recommendations(contracts: list[dict[str, Any]]) -> dict[str, Any]:
    catalog = _read_json(CATALOG_PATH)
    templates = catalog.get("template_presets")
    if not isinstance(templates, list) or len(templates) < 3:
        raise ValueError("the bundled style catalog must provide at least three templates")
    templates = templates[:3]
    if any(not isinstance(template, dict) or template.get("id") not in _SIGNAL_TERMS for template in templates):
        raise ValueError("the bundled style catalog has unsupported template identifiers")
    selected = _recommended_template_index(contracts, templates)
    return {
        "stage": "final",
        "lang": "zh",
        "recommend": {
            "direction": selected,
            "canvas": CANVAS_ID,
            "regional_style": {"enabled": False},
            "additional_requirements": "",
            "production_profile": "balanced",
        },
        "design_directions": {
            "selected": selected,
            "candidates": [_template_direction(template) for template in templates],
        },
    }


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def verify_prepare_artifacts(project: Path) -> bool:
    """Prove the pending project has complete authority-bound inputs for its sole UI."""
    try:
        project = Path(project)
        contracts = _locked_contracts(project)
        summary = _read_json(project_artifact_path(project, SUMMARY_PATH))
        recommendations = _read_json(project_artifact_path(project, RECOMMENDATIONS_PATH))
        return (
            verify_page_requirement_summary(project, summary)
            and _canonical_json(recommendations) == _canonical_json(_recommendations(contracts))
        )
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return False


def build_recommendations(project: Path) -> dict[str, Any]:
    """Create the final one-screen recommendation file for a prepared project."""
    project = Path(project)
    recommendations_path = project_artifact_path(
        project, RECOMMENDATIONS_PATH, create_parent=True,
    )
    contracts = _locked_contracts(project)
    # Comments are resolved before the only UI session. The global template
    # recommendation itself remains based on Word facts, not raw comment text.
    build_page_requirement_summary(project, contracts)
    recommendations = _recommendations(contracts)
    _write_json(project, recommendations_path, recommendations)
    return recommendations

"""Cheap deterministic page checks; uncertainty is explicit and never fabricated."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from page_qa import qa_issue


def run_deterministic_qa(
    image: Path | None, contract: Mapping[str, Any], fact_plan: Mapping[str, Any],
    route: Mapping[str, Any], observations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    observations = observations or {}
    issues: list[dict[str, Any]] = []
    advisories: list[dict[str, Any]] = []
    route_name = route.get("route")
    native_text_authority = route.get("text_authority") == "native_overlay"
    if route_name in {"image", "hybrid"} and observations.get("native_artifact_checked") is not True:
        if observations.get("background_text_detection_available") is not True:
            issues.append(qa_issue(
                "background_text_detection_unavailable",
                "本地背景文字检测不可用，页面不能继续。",
                "structural",
                "background_text_scan",
                str(observations.get("background_text_detection_error") or "local_detector"),
            ))
        elif observations.get("background_text_detected") is True:
            issues.append(qa_issue(
                "background_text_detected",
                "生成背景包含文字、数字或表格文字，必须重新生成无字背景。",
                "local",
                "background_text_scan",
                "generated_background",
            ))
    if observations.get("aspect_mapping") == "contain":
        advisories.append(qa_issue(
            "contained_body_image", "后端返回了非17:8图像，已确定性等比居中映射；无需语义复审或再次生图。",
            "advisory", "body_image_mapping", "contain_mapping",
        ))
    required = [
        item.get("asset_id") for item in contract.get("asset_bindings", [])
        if isinstance(item, Mapping) and item.get("asset_role") == "mandatory_inline_image"
    ]
    presence = observations.get("inline_image_presence", {})
    if isinstance(presence, Mapping):
        for asset_id in required:
            if presence.get(asset_id) is False:
                issues.append(qa_issue(
                    "missing_inline_image", f"必需图片缺失：{asset_id}", "local",
                    "inline_image_presence", str(asset_id),
                ))
    if route_name in {"native", "image", "hybrid"} and native_text_authority and observations.get("native_artifact_checked") is not None:
        if observations.get("native_artifact_checked") is not True:
            issues.append(qa_issue(
                "native_artifact_unchecked", "原生页成品未执行确定性检查。", "structural",
                "native_pptx_scan", "native_pptx",
            ))
        if observations.get("native_body_present") is not True:
            issues.append(qa_issue(
                "native_body_missing", "原生页缺少权威正文，固定标题框不能替代正文。", "structural",
                "native_pptx_scan", "native-body-text",
            ))
        for coverage_id, present in observations.get("native_table_presence", {}).items():
            if present is not True:
                issues.append(qa_issue(
                    "native_table_missing", f"原生页缺少必需表格：{coverage_id}", "structural",
                    "native_pptx_scan", str(coverage_id),
                ))
        for coverage_id, present in observations.get("native_supplement_presence", {}).items():
            if present is not True:
                issues.append(qa_issue(
                    "attachment_supplement_missing", f"原生页缺少批准的附件补充：{coverage_id}", "local",
                    "native_pptx_scan", str(coverage_id),
                ))
        if observations.get("native_coverage_receipt_valid") is not True:
            issues.append(qa_issue(
                "native_coverage_receipt_mismatch", "原生页 coverage receipt 与当前成品或期望值不一致。", "structural",
                "native_pptx_scan", observations.get("native_coverage_receipt_error") or "coverage_receipt",
            ))
    evidence = [{"fact": item["code"], "source": item["evidence"]} for item in issues]
    for conflict in fact_plan.get("conflicts", []):
        if not isinstance(conflict, Mapping) or conflict.get("qa_advisory") is not True:
            continue
        source = conflict.get("source") if isinstance(conflict.get("source"), Mapping) else {}
        advisories.append(qa_issue(
            "word_attachment_conflict",
            f"附件 {source.get('file', 'unknown')} 与Word事实冲突，已按Word为准。",
            "advisory", "fact_plan_conflict", dict(source),
        ))
        evidence.append({"fact": "word_attachment_conflict", "source": dict(source)})
    for supplement in fact_plan.get("attachment_supplements", []):
        if not isinstance(supplement, Mapping):
            continue
        source = supplement.get("source") if isinstance(supplement.get("source"), Mapping) else None
        if source is not None:
            evidence.append({
                "fact": supplement.get("authorization", "attachment_supplement"),
                "source": dict(source),
                "evidence_id": supplement.get("evidence_id"),
            })
    uncertain = False
    all_issues = issues + advisories
    return {
        "schema_version": "1.0", "status": "repair" if issues else ("pass_with_advisory" if advisories else "pass"),
        "repair_scope": (
            "structural" if any(item.get("severity") == "structural" for item in issues)
            else ("local" if issues else "none")
        ), "issues": all_issues,
        "evidence": evidence,
        "confidence": "low" if uncertain else "high", "uncertain": uncertain,
        "trigger_reason": "deterministic_mismatch" if issues else ("missing_deterministic_observation" if uncertain else "checks_passed"),
        "checked_scope": "targeted" if observations.get("targeted") else "full",
    }

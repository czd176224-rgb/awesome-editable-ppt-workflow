from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "page_image_prompt_v1.schema.json"
DESIGN_PLAN_SCHEMA_PATH = ROOT / "schemas" / "design_plan_v1.schema.json"
MATERIAL_SCHEMA = ROOT / "schemas" / "awesome_page_materials_v1.schema.json"
HEADINGS = ("Task", "Original Materials", "Visual Presentation", "Fixed Boundaries")
BOUNDARIES = (
    "Generate only the 17:8 body region at exactly 1904 x 896 and respect the safe area.",
    "Do not generate the fixed page title.", "Do not generate the fixed logo.",
    "Do not generate the footer.", "Do not generate the page number.",
)
VISUAL_KEYS = {"primary_color", "secondary_color", "background_color", "cjk_font", "latin_font", "title_size_pt", "body_size_pt", "caption_size_pt"}
LEGACY_VISUAL_KEYS = {"regional_characteristics", "visual_description"}
KNOWN_BODY_FRAME = {"geometry_version": "fixed-canvas-cm-v2", "body_bounds_cm": {"x": 0.81, "y": 2.3, "w": 23.78, "h": 11.18}, "body_pixels": {"width": 1904, "height": 896}, "fixed_layers": ["title", "logo", "footer", "page_number"]}
COMMENT_ACTIONS = ("emphasize", "de-emphasize", "restructure", "abstract-to-model", "preserve-exact", "use-reference", "prohibit-promotional", "change-reading-order")


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def load_json_no_duplicates(raw: str) -> Any:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs_hook)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc


def _owned_reference_ids(materials: Mapping[str, Any]) -> set[str]:
    owned = {f"word:{item['asset_id']}" for item in materials.get("word_images", [])}
    for attachment in materials.get("attachment_inputs", []):
        asset_id = attachment["asset_id"]
        if str(attachment.get("media_type", "")).startswith("image/"):
            owned.add(f"attachment:{asset_id}:original")
        receipt = attachment.get("render_receipt")
        if isinstance(receipt, Mapping):
            owned.update(f"attachment:{asset_id}:page:{page['page_number']}" for page in receipt.get("pages", []))
    return owned


def _sections(prompt: str) -> dict[str, str]:
    prompt = _normalize_newlines(prompt)
    matches = list(re.finditer(r"^## ([^\n]+)$", prompt, re.MULTILINE))
    if [item.group(1) for item in matches] != list(HEADINGS):
        raise ValueError("image_prompt sections are out of order")
    return {item.group(1): prompt[item.end():matches[index + 1].start() if index + 1 < len(matches) else len(prompt)] for index, item in enumerate(matches)}


def _embedded_json(section: str, label: str) -> Any:
    section = _normalize_newlines(section)
    match = re.search(rf"^{re.escape(label)}_LENGTH=(\d+)\n{re.escape(label)}_BEGIN\n(.*?)\n{re.escape(label)}_END$", section, re.MULTILINE | re.DOTALL)
    if not match:
        raise ValueError(f"{label} block is missing or duplicated")
    raw = match.group(2)
    if len(raw.encode("utf-8")) != int(match.group(1)):
        raise ValueError(f"{label} byte length does not match")
    value = load_json_no_duplicates(raw)
    if raw != _compact(value):
        raise ValueError(f"{label} is not canonical JSON")
    return value


def _block(label: str, value: Any) -> str:
    raw = _compact(value)
    return f"{label}_LENGTH={len(raw.encode('utf-8'))}\n{label}_BEGIN\n{raw}\n{label}_END"


def _validate_materials(materials: Mapping[str, Any]) -> None:
    schema_path = MATERIAL_SCHEMA.resolve(strict=True)
    expected = (ROOT.parent / "run-word-to-ppt-workflow" / "schemas" / "awesome_page_materials_v1.schema.json").resolve(strict=True)
    if schema_path != expected:
        raise ValueError("unexpected page-material schema location")
    schema = load_json_no_duplicates(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(dict(materials)), key=lambda e: list(e.path))
    if errors:
        raise ValueError(f"invalid awesome_page_materials_v1: {errors[0].message}")
    visual = materials.get("visual_contract")
    if (
        not isinstance(visual, Mapping)
        or not VISUAL_KEYS.issubset(visual)
        or not set(visual).issubset(VISUAL_KEYS | LEGACY_VISUAL_KEYS)
    ):
        raise ValueError("visual contract must contain the eight confirmed visual fields")
    for key in ("primary_color", "secondary_color", "background_color"):
        if not isinstance(visual[key], str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", visual[key]):
            raise ValueError(f"{key} must be a hex color")
    for key in ("cjk_font", "latin_font", *sorted(LEGACY_VISUAL_KEYS.intersection(visual))):
        if not isinstance(visual[key], str):
            raise ValueError(f"{key} must be a string")
    for key in ("title_size_pt", "body_size_pt", "caption_size_pt"):
        if isinstance(visual[key], bool) or not isinstance(visual[key], (int, float)) or not 1 <= float(visual[key]) <= 200:
            raise ValueError(f"{key} must be between 1 and 200")
    if materials.get("body_frame") != KNOWN_BODY_FRAME:
        raise ValueError("body_frame does not match the fixed v1 safe contract")


def _parse_original(original: str) -> tuple[Any, Any, list[str]]:
    original = _normalize_newlines(original)
    pattern = re.compile(
        r"^\n?WORD_CONTENT_JSON_LENGTH=(?P<wl>\d+)\nWORD_CONTENT_JSON_BEGIN\n(?P<w>.*?)\nWORD_CONTENT_JSON_END\n"
        r"ORIGINAL_COMMENTS_JSON_LENGTH=(?P<cl>\d+)\nORIGINAL_COMMENTS_JSON_BEGIN\n(?P<c>.*?)\nORIGINAL_COMMENTS_JSON_END\n"
        r"SELECTED_REFERENCE_IDS_JSON_LENGTH=(?P<rl>\d+)\nSELECTED_REFERENCE_IDS_JSON_BEGIN\n(?P<r>.*?)\nSELECTED_REFERENCE_IDS_JSON_END\n*$",
        re.DOTALL,
    )
    match = pattern.fullmatch(original)
    if not match:
        raise ValueError("Original Materials does not match the closed grammar")
    raws = [match.group("w"), match.group("c"), match.group("r")]
    lengths = [int(match.group("wl")), int(match.group("cl")), int(match.group("rl"))]
    if any(len(raw.encode("utf-8")) != length for raw, length in zip(raws, lengths)):
        raise ValueError("source block byte length does not match")
    values = [load_json_no_duplicates(raw) for raw in raws]
    if any(raw != _compact(value) for raw, value in zip(raws, values)):
        raise ValueError("source block is not canonical JSON")
    if not isinstance(values[2], list) or any(not isinstance(item, str) for item in values[2]):
        raise ValueError("selected reference block must be a JSON string array")
    return values[0], values[1], values[2]


def _validate_design_plan(materials: Mapping[str, Any], selected: list[str], plan: Any) -> None:
    design_schema = load_json_no_duplicates(DESIGN_PLAN_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(design_schema).iter_errors(plan), key=lambda e: list(e.path))
    if errors:
        raise ValueError(f"invalid DESIGN_PLAN_JSON: {errors[0].message}")
    word_ids = [block["source_block_id"] for block in materials["complete_word_content"]]
    comment_ids = [comment["comment_id"] for comment in materials["original_comments"]]
    if len(word_ids) != len(set(word_ids)):
        raise ValueError("source block IDs must be unique")
    if len(comment_ids) != len(set(comment_ids)):
        raise ValueError("original comment IDs must be unique")
    if plan["content_sequence"] != list(dict.fromkeys(plan["content_sequence"])) or set(plan["content_sequence"]) != set(word_ids) or len(plan["content_sequence"]) != len(word_ids):
        raise ValueError("content_sequence must contain every Word source block exactly once")
    directive_ids = [item["comment_id"] for item in plan["comment_directives"]]
    if directive_ids != comment_ids:
        raise ValueError("comment_directives must cover every original comment exactly once in source order")
    allowed_targets = set(word_ids) | set(selected)
    for directive in plan["comment_directives"]:
        targets = directive.get("target_ids", [])
        if set(targets) - allowed_targets:
            raise ValueError("comment directive target is not a page-owned Word block or selected reference")
        if "use-reference" in directive["actions"] and not (set(targets) & set(selected)):
            raise ValueError("use-reference directive must target a selected reference")
    substitutions = plan["reference_substitutions"]
    substitution_ids = [item["substitution_id"] for item in substitutions]
    substitution_sources = [item["source_id"] for item in substitutions]
    substitution_refs = [item["reference_id"] for item in substitutions]
    if len(substitution_ids) != len(set(substitution_ids)) or len(substitution_sources) != len(set(substitution_sources)) or len(substitution_refs) != len(set(substitution_refs)):
        raise ValueError("reference substitutions must have unique IDs, Word sources, and references")
    if set(substitution_sources) - set(word_ids) or set(substitution_refs) - set(selected):
        raise ValueError("reference substitutions must pair Word blocks with selected references")
    cited_substitutions = [item for directive in plan["comment_directives"] for item in directive.get("substitution_ids", [])]
    if set(cited_substitutions) != set(substitution_ids) or len(cited_substitutions) != len(set(cited_substitutions)):
        raise ValueError("every reference substitution must be cited once by a comment directive")
    for directive in plan["comment_directives"]:
        if directive.get("substitution_ids") and "use-reference" not in directive["actions"]:
            raise ValueError("substitution directives require use-reference")
        cited = [next(item for item in substitutions if item["substitution_id"] == substitution_id) for substitution_id in directive.get("substitution_ids", [])]
        if cited:
            exact_targets = {value for substitution in cited for value in (substitution["source_id"], substitution["reference_id"])}
            if set(directive.get("target_ids", [])) != exact_targets:
                raise ValueError("a substitution directive must target exactly all cited Word-source/reference pairs")
            if "page_scope" in directive:
                raise ValueError("a substitution directive cannot use whole-page scope")
    owned = set(word_ids)
    owned.update(comment["comment_id"] for comment in materials["original_comments"])
    owned.update(selected)
    visual_ids = set(word_ids) | set(selected)
    used = set(plan["hierarchy_order"]) | set(plan["emphasis_ids"])
    used.update(item for group in plan["groups"] for item in group["member_ids"])
    if used - owned:
        raise ValueError(f"design plan contains non-page IDs: {sorted(used - owned)}")
    if set(plan["emphasis_ids"]) - visual_ids:
        raise ValueError("emphasis IDs must be Word blocks or selected references")
    treatments = [item["reference_id"] for item in plan["reference_treatments"]]
    if treatments != selected or len(treatments) != len(set(treatments)):
        raise ValueError("reference treatments must exactly match selected references in order, once each")
    hierarchy = plan["hierarchy_order"]
    if len(hierarchy) != len(set(hierarchy)) or set(hierarchy) != visual_ids:
        raise ValueError("hierarchy_order must equal the exact visual ID set")
    group_ids = [group["group_id"] for group in plan["groups"]]
    grouped = [item for group in plan["groups"] for item in group["member_ids"]]
    if len(group_ids) != len(set(group_ids)) or len(grouped) != len(set(grouped)):
        raise ValueError("groups must have unique IDs and disjoint members")
    if set(grouped) != visual_ids:
        raise ValueError("flattened groups must equal the exact visual ID set")


def validate_page_image_prompt(materials: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    _validate_materials(materials)
    schema = load_json_no_duplicates(SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(dict(result)), key=lambda e: list(e.path))
    if errors:
        raise ValueError(errors[0].message)
    if result["page_number"] != materials.get("page_number"):
        raise ValueError("page_number does not match source materials")
    selected = result["selected_reference_images"]
    unknown = set(selected) - _owned_reference_ids(materials)
    if unknown:
        raise ValueError(f"selected references are not page-owned: {sorted(unknown)}")
    sections = _sections(result["image_prompt"])
    word, comments, declared = _parse_original(sections["Original Materials"])
    if word != materials.get("complete_word_content", []):
        raise ValueError("complete Word content is missing or changed")
    if comments != materials.get("original_comments", []):
        raise ValueError("original comments are missing, changed, or reordered")
    if declared != selected:
        raise ValueError("selected reference block must exactly match selected references in order")
    if sections["Task"].strip() != "Generate one 1904 x 896 PowerPoint body-region image.":
        raise ValueError("Task section must contain only the fixed artifact instruction")
    visual_section = _normalize_newlines(sections["Visual Presentation"])
    visual = _embedded_json(visual_section, "VISUAL_CONTRACT_JSON")
    plan = _embedded_json(visual_section, "DESIGN_PLAN_JSON")
    if visual != materials.get("visual_contract", {}):
        raise ValueError("visual contract is missing or changed")
    if visual_section != "\n" + _block("VISUAL_CONTRACT_JSON", visual) + "\n" + _block("DESIGN_PLAN_JSON", plan) + "\n\n":
        raise ValueError("Visual Presentation must contain only the visual contract and closed design plan")
    _validate_design_plan(materials, selected, plan)
    if sections["Fixed Boundaries"].strip().splitlines() != list(BOUNDARIES):
        raise ValueError("Fixed Boundaries must contain only the five required lines")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    materials = load_json_no_duplicates(args.materials.read_text(encoding="utf-8"))
    result = load_json_no_duplicates(args.result.read_text(encoding="utf-8"))
    validate_page_image_prompt(materials, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Current-Codex creative direction for the isolated page-1 experiment."""

from __future__ import annotations

import json
import hashlib
import hmac
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator

from codex_subscription_runtime import CodexStructuredResult, invoke_structured
IMAGE_PROVIDER_SCRIPTS = Path(__file__).resolve().parents[3] / "generate-slide-body-image" / "scripts"
if str(IMAGE_PROVIDER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(IMAGE_PROVIDER_SCRIPTS))
from provider_keyring import signing_key, verification_key
from workflow_v6_secure_io import atomic_write_bytes, read_bytes

from .materials import (
    CompletePageMaterialView,
    validate_published_complete_page_material_view,
)
from .workspace import ExperimentWorkspace


SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "schemas"
    / "complex_page_director_v1.schema.json"
)
VISUAL_DIRECTOR_REFERENCE = (
    Path(__file__).resolve().parent / "references" / "visual_director.md"
)
_SECTION_SPECS = (
    ("Scene or Background", "scene_or_background"),
    ("Subject and Core Expression", "subject_and_core_expression"),
    ("Key Details", "key_details"),
    (
        "Composition Viewpoint Hierarchy and Medium",
        "composition_viewpoint_hierarchy_and_medium",
    ),
    ("Reference Roles and Combination", "reference_roles_and_combination"),
    ("Preservation and Fixed Exclusions", "preservation_and_fixed_exclusions"),
)
_FIXED_TERMS = {
    "title": re.compile(r"\btitle\b", re.IGNORECASE),
    "logo": re.compile(r"\blogo\b", re.IGNORECASE),
    "footer": re.compile(r"\bfooter\b", re.IGNORECASE),
    "page_number": re.compile(r"\bpage(?:[_ -]+)number\b", re.IGNORECASE),
}

_CENTER_17_8_SAFE_REGION = (
    "Regardless of the provider's eventual canvas aspect ratio, place all body text, charts, "
    "people, real references, connections, and key decoration inside the central largest 17:8 "
    "content region; leave a visibly empty perimeter on all four sides, and outside the safe "
    "region use only discardable background, texture, or whitespace."
)
_FIXED_LAYER_EXCLUSION = (
    "Do not generate title, logo, footer, or page number; those are supplied as fixed layers."
)
_CUSTODY_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/]"),
    re.compile(r"(?:^|\s)/(?:[^/\s]+/)*[^/\s]+"),
    re.compile(r"(?:^|\s)(?:\.\.?[\\/]|\\\\)"),
    re.compile(r"\b(?:00_source|01_source_assets|02_v6)[\\/]", re.IGNORECASE),
    re.compile(r"\b[0-9a-f]{64}\b", re.IGNORECASE),
    re.compile(r"\b(?:sha-?256|digest|receipt[_ -]?id)\b", re.IGNORECASE),
)
_SENTENCE_PARTS = re.compile(r"[^.!?;。！？；\r\n]+[.!?;。！？；]?")

_CANVAS_BACKGROUND_CLAUSE = re.compile(
    r"(?:\bcanvas\s+background\b|"
    r"\b(?:canvas|background)\b.*\b(?:color|grid|texture|gradient|glow)\b|"
    r"\b(?:color|grid|texture|gradient|glow)\b.*\b(?:canvas|background)\b)",
    re.IGNORECASE,
)
_SCENE_BACKGROUND_EFFECT = re.compile(
    r"\b(?:texture|gradient|glow|fog|vortex|burlap|linen|paper)\b",
    re.IGNORECASE,
)
_FOREGROUND_SCENE_CONTEXT = re.compile(
    r"\b(?:foreground|subject|evidence|person|people|object|arrange|arrangement|relationship)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DirectorArtifact:
    value: Mapping[str, object]
    actual_prompt: str
    selected_reference_ids: tuple[str, ...]
    quality: Literal["medium", "high"]
    model: str
    effort: str | None
    duration_seconds: float
    model_provider: str
    usage: Mapping[str, Any]
    runtime_trace: Mapping[str, Any]
    thread_id: str
    turn_id: str

    @property
    def creative_direction(self) -> Mapping[str, object]:
        value = self.value["creative_direction"]
        assert isinstance(value, Mapping)
        return value


@dataclass(frozen=True)
class CorrectionDecision:
    strategy: Literal["edit_previous", "regenerate_from_materials"]
    actual_prompt: str
    selected_reference_ids: tuple[str, ...]
    problem_addressed: tuple[str, ...]
    preserve: tuple[str, ...]
    model: str
    effort: str | None
    duration_seconds: float
    model_provider: str
    usage: Mapping[str, Any]
    runtime_trace: Mapping[str, Any]
    thread_id: str
    turn_id: str
    value: Mapping[str, object]


def _load_schema() -> dict[str, Any]:
    value = json.loads(SCHEMA.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("director schema must be a JSON object")
    Draft202012Validator.check_schema(value)
    return value


def _canonical_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _director_relative(workspace: ExperimentWorkspace) -> Path:
    return Path("02_v6") / "experiments" / workspace.experiment_id / "director.json"


def _director_authority_value(
    workspace: ExperimentWorkspace,
    material_view: CompletePageMaterialView,
    artifact: DirectorArtifact,
) -> dict[str, object]:
    return {
        "schema_version": "awesome-page-director-authority-v1",
        "experiment_id": workspace.experiment_id,
        "page_number": workspace.page_number,
        "source_snapshot_sha256": workspace.source_snapshot_sha256,
        "material_view_sha256": material_view.sha256,
        "actual_prompt": artifact.actual_prompt,
        "selected_reference_ids": list(artifact.selected_reference_ids),
        "quality": artifact.quality,
        "model": artifact.model,
        "effort": artifact.effort,
        "duration_seconds": artifact.duration_seconds,
        "model_provider": artifact.model_provider,
        "usage": dict(artifact.usage),
        "runtime_trace": dict(artifact.runtime_trace),
        "thread_id": artifact.thread_id,
        "turn_id": artifact.turn_id,
        "value": dict(artifact.value),
    }


def _publish_director_authority(
    workspace: ExperimentWorkspace,
    material_view: CompletePageMaterialView,
    artifact: DirectorArtifact,
) -> None:
    unsigned = _director_authority_value(workspace, material_view, artifact)
    key_id, key = signing_key()
    signed = {**unsigned, "key_id": key_id}
    signed["hmac_sha256"] = hmac.new(
        key, _canonical_bytes(signed).rstrip(b"\n"), hashlib.sha256
    ).hexdigest()
    payload = _canonical_bytes(signed)
    relative = _director_relative(workspace)
    try:
        atomic_write_bytes(workspace.project_copy, relative, payload)
    except FileExistsError:
        if read_bytes(workspace.project_copy, relative, max_bytes=4 * 1024 * 1024) != payload:
            raise ValueError("published director authority already differs")


def validate_published_director_authority(
    workspace: ExperimentWorkspace,
    material_view: CompletePageMaterialView,
    artifact: DirectorArtifact,
) -> None:
    """Require the passed artifact to equal the signed canonical director authority."""
    relative = _director_relative(workspace)
    try:
        raw = read_bytes(workspace.project_copy, relative, max_bytes=4 * 1024 * 1024)
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("published director authority is missing or invalid") from exc
    keys = {
        "schema_version", "experiment_id", "page_number", "source_snapshot_sha256",
        "material_view_sha256", "actual_prompt", "selected_reference_ids", "quality",
        "model", "effort", "duration_seconds", "model_provider", "usage",
        "runtime_trace", "thread_id", "turn_id", "value", "key_id", "hmac_sha256",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("published director authority has an invalid closed shape")
    signature = value.get("hmac_sha256")
    unsigned = dict(value)
    unsigned.pop("hmac_sha256")
    try:
        key = verification_key(str(value.get("key_id")))
    except (OSError, ValueError) as exc:
        raise ValueError("published director authority key is invalid") from exc
    expected = hmac.new(
        key, _canonical_bytes(unsigned).rstrip(b"\n"), hashlib.sha256
    ).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise ValueError("published director authority signature is invalid")
    projection = {**_director_authority_value(workspace, material_view, artifact), "key_id": value["key_id"]}
    if unsigned != projection:
        raise ValueError("passed DirectorArtifact differs from published director authority")


def _material_records(material_view: CompletePageMaterialView) -> list[Mapping[str, object]]:
    records = material_view.value.get("materials")
    if not isinstance(records, list) or any(not isinstance(item, Mapping) for item in records):
        raise ValueError("complete material view has invalid material records")
    ids = tuple(str(item.get("material_id", "")) for item in records)
    if ids != material_view.material_ids or len(ids) != len(set(ids)) or any(not item for item in ids):
        raise ValueError("complete material view material IDs are inconsistent")
    return records


def _image_material_ids(material_view: CompletePageMaterialView) -> tuple[str, ...]:
    records = _material_records(material_view)
    omitted_raw = material_view.value.get("deduplicated_derivatives", [])
    if not isinstance(omitted_raw, list):
        raise ValueError("complete material view duplicate records are invalid")
    omitted = {
        str(item.get("material_id"))
        for item in omitted_raw
        if isinstance(item, Mapping)
    }
    result = tuple(
        str(item["material_id"])
        for item in records
        if item.get("viewable_image") is True and str(item["material_id"]) not in omitted
    )
    if len(result) != len(material_view.multimodal_images):
        raise ValueError("multimodal image order does not match retained material records")
    return result


def _validate_material_view(
    workspace: ExperimentWorkspace, material_view: CompletePageMaterialView
) -> tuple[str, ...]:
    if material_view.value.get("experiment_id") != workspace.experiment_id:
        raise ValueError("complete material view experiment does not match the workspace")
    if material_view.value.get("page_number") != workspace.page_number:
        raise ValueError("complete material view page does not match the experiment workspace")
    validate_published_complete_page_material_view(workspace, material_view)
    if material_view.value.get("page_number") != workspace.page_number:
        raise ValueError("complete material view page does not match the experiment workspace")
    if material_view.value.get("experiment_id") != workspace.experiment_id:
        raise ValueError("complete material view experiment does not match the workspace")
    if hashlib.sha256(_canonical_bytes(material_view.value)).hexdigest() != material_view.sha256:
        raise ValueError("complete material view digest does not match its canonical value")

    source_receipts = material_view.value["source_receipts"]
    assert isinstance(source_receipts, Mapping)
    project = workspace.project_copy.resolve(strict=True)
    for label in ("paginated_word_source", "source_asset_manifest"):
        receipt = source_receipts[label]
        assert isinstance(receipt, Mapping)
        relative = receipt["path"]
        assert isinstance(relative, str)
        try:
            source_path = project.joinpath(*relative.split("/")).resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError(f"complete material view source receipt is missing: {label}") from exc
        data = source_path.read_bytes()
        if (
            hashlib.sha256(data).hexdigest() != receipt.get("sha256")
            or len(data) != receipt.get("byte_size")
        ):
            raise ValueError(f"complete material view source receipt is invalid: {label}")

    image_ids = _image_material_ids(material_view)
    by_id = {
        str(item["material_id"]): item for item in _material_records(material_view)
    }
    expected: list[Path] = []
    for material_id in image_ids:
        record = by_id[material_id]
        authority = record["authority_path"]
        if not isinstance(authority, str):
            raise ValueError("viewable image authority path is invalid")
        try:
            authority_path = project.joinpath(*authority.split("/")).resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError("viewable image authority file is missing") from exc
        data = authority_path.read_bytes()
        if (
            hashlib.sha256(data).hexdigest() != record.get("sha256")
            or len(data) != record.get("byte_size")
        ):
            raise ValueError("viewable image digest or byte size differs from its authority")
        expected.append(authority_path)
    try:
        actual = tuple(Path(path).resolve(strict=True) for path in material_view.multimodal_images)
    except FileNotFoundError as exc:
        raise ValueError("multimodal image authority file is missing") from exc
    if actual != tuple(expected):
        raise ValueError("multimodal image mapping does not exactly match material authority order")
    return image_ids


def _mapping_text(ids: Sequence[str], *, start: int = 1) -> str:
    return "\n".join(f"Image-{index} = {material_id}" for index, material_id in enumerate(ids, start=start))


def _visual_director_reference() -> str:
    try:
        data = VISUAL_DIRECTOR_REFERENCE.read_bytes()
    except OSError as exc:
        raise ValueError("visual director reference is missing") from exc
    if not data or len(data) > 16_384:
        raise ValueError("visual director reference size is invalid")
    try:
        text = data.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("visual director reference is not valid UTF-8") from exc
    if not text:
        raise ValueError("visual director reference is empty")
    return text


def _without_compiler_owned_boundaries(text: str) -> str:
    kept: list[str] = []
    for match in _SENTENCE_PARTS.finditer(text):
        part = match.group(0).strip()
        if not part:
            continue
        if "17:8" in part or any(pattern.search(part) for pattern in _FIXED_TERMS.values()):
            continue
        kept.append(part)
    return " ".join(kept)


def _validate_prompt_sections(value: Mapping[str, object]) -> Mapping[str, str]:
    raw = value.get("prompt_sections")
    if not isinstance(raw, Mapping):
        raise ValueError("prompt sections are missing")
    sections: dict[str, str] = {}
    expected_keys = {key for _heading, key in _SECTION_SPECS}
    if set(raw) != expected_keys:
        raise ValueError("prompt sections must have the approved six-part shape")
    for _heading, key in _SECTION_SPECS:
        text = raw.get(key)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"prompt section {key} must be non-empty natural language")
        if any(pattern.search(text) for pattern in _CUSTODY_PATTERNS):
            raise ValueError("compiled prompt contains a local custody path, digest, or receipt ID")
        cleaned = _without_compiler_owned_boundaries(text.strip())
        if not cleaned and key == "preservation_and_fixed_exclusions":
            cleaned = (
                "Preserve source-exact facts, identities, selected-reference roles, and relationships."
            )
        if not cleaned:
            raise ValueError(f"prompt section {key} contains only compiler-owned boundaries")
        sections[key] = cleaned
    return sections


def _canvas_background_constraint(
    material_view: CompletePageMaterialView,
) -> str:
    visual_contract = material_view.value.get("visual_contract")
    if not isinstance(visual_contract, Mapping):
        raise ValueError("complete material view visual contract is missing")
    background_color = visual_contract.get("background_color")
    if not isinstance(background_color, str) or not background_color.strip():
        raise ValueError("complete material view background color is missing")
    return (
        "Fill the entire canvas with the source-authoritative background color "
        f"{background_color.strip()}; do not add any background scene, gradient, pattern, or texture."
    )


def _without_director_canvas_background_clauses(
    text: str, *, scene_section: bool = False,
) -> str:
    kept: list[str] = []
    for match in _SENTENCE_PARTS.finditer(text):
        clause = match.group(0).strip()
        scene_effect_without_foreground = (
            scene_section
            and _SCENE_BACKGROUND_EFFECT.search(clause)
            and not _FOREGROUND_SCENE_CONTEXT.search(clause)
        )
        if (
            clause
            and not _CANVAS_BACKGROUND_CLAUSE.search(clause)
            and not scene_effect_without_foreground
        ):
            kept.append(clause)
    return " ".join(kept)


def compile_six_part_prompt(
    value: Mapping[str, object], material_view: CompletePageMaterialView
) -> str:
    """Compile six natural-language sections in the approved order, each constraint once."""
    sections = dict(_validate_prompt_sections(value))
    for _heading, key in _SECTION_SPECS:
        sections[key] = _without_director_canvas_background_clauses(
            sections[key], scene_section=key == "scene_or_background",
        )
        if key != "scene_or_background" and not sections[key]:
            raise ValueError(
                f"prompt section {key} contains only compiler-owned canvas background clauses"
            )
    background = _canvas_background_constraint(material_view)
    foreground_scene = sections["scene_or_background"]
    sections["scene_or_background"] = (
        f"{foreground_scene} {background}" if foreground_scene else background
    )
    final_key = "preservation_and_fixed_exclusions"
    sections[final_key] = (
        sections[final_key]
        + "\n"
        + _FIXED_LAYER_EXCLUSION
        + "\n"
        + _CENTER_17_8_SAFE_REGION
    )
    return "\n\n".join(f"## {heading}\n{sections[key]}" for heading, key in _SECTION_SPECS)


def _complete_material_use(
    value: Mapping[str, object], material_view: CompletePageMaterialView
) -> Mapping[str, object]:
    machine = value.get("machine_record")
    if not isinstance(machine, Mapping):
        return value
    material_use = machine.get("material_use")
    selected = machine.get("selected_references")
    if not isinstance(material_use, list) or not isinstance(selected, list):
        return value

    expected = set(material_view.material_ids)
    selected_ids = {
        str(item.get("material_id"))
        for item in selected
        if isinstance(item, Mapping)
    }
    records: dict[str, dict[str, object]] = {}
    for item in material_use:
        if not isinstance(item, Mapping):
            continue
        material_id = str(item.get("material_id"))
        if material_id not in expected:
            raise ValueError("material_use contains a material outside this page")
        records.setdefault(material_id, dict(item))

    for material_id in material_view.material_ids:
        if material_id not in records:
            selected_for_image = material_id in selected_ids
            records[material_id] = {
                "material_id": material_id,
                "status": "used_in_image" if selected_for_image else "background_understanding",
                "reason": (
                    "Selected by the page director as an Image2 reference."
                    if selected_for_image
                    else "Provided to the page director for context but not selected as an Image2 reference."
                ),
            }
        elif material_id in selected_ids:
            records[material_id]["status"] = "used_in_image"

    normalized_machine = dict(machine)
    normalized_machine["material_use"] = [
        records[material_id] for material_id in material_view.material_ids
    ]
    normalized = dict(value)
    normalized["machine_record"] = normalized_machine
    return normalized


def _validate_director_value(
    value: Mapping[str, object], material_view: CompletePageMaterialView
) -> tuple[str, ...]:
    schema = _load_schema()
    errors = sorted(
        Draft202012Validator(schema).iter_errors(dict(value)),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        path = "/".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise ValueError(f"director schema rejected {path}: {errors[0].message}")

    machine = value["machine_record"]
    assert isinstance(machine, Mapping)
    for field in (
        "facts_and_sources",
        "must_preserve_entities",
        "core_content_and_comment_direction",
    ):
        strings = machine[field]
        assert isinstance(strings, list)
        if any(not isinstance(item, str) or not item.strip() for item in strings):
            raise ValueError(f"{field} entries must contain non-whitespace facts")
    creative = value["creative_direction"]
    assert isinstance(creative, Mapping)
    for field in (
        "core_objective",
        "visual_concept",
        "scene_subject_hierarchy_reading_path",
        "composition_viewpoint_whitespace_medium_texture",
    ):
        if not str(creative[field]).strip():
            raise ValueError(
                f"creative_direction {field} must contain non-whitespace text"
            )
    material_use = machine["material_use"]
    assert isinstance(material_use, list)
    if any(not str(item["reason"]).strip() for item in material_use):
        raise ValueError("material_use reason must contain non-whitespace text")
    observed = [str(item["material_id"]) for item in material_use]
    expected = list(material_view.material_ids)
    if len(observed) != len(set(observed)) or set(observed) != set(expected):
        raise ValueError("material_use must name every material ID exactly once")

    allowed_references = set(_image_material_ids(material_view))
    selected = machine["selected_references"]
    assert isinstance(selected, list)
    selected_text_fields = (
        "identity",
        "use",
        "preserve",
        "allowed_changes",
        "composition_relationship",
    )
    for reference in selected:
        for field in selected_text_fields:
            if not str(reference[field]).strip():
                raise ValueError(
                    f"selected reference {field} must contain non-whitespace text"
                )
    selected_ids = tuple(str(item["material_id"]) for item in selected)
    if len(selected_ids) != len(set(selected_ids)) or any(
        item not in allowed_references for item in selected_ids
    ):
        raise ValueError("selected reference must be a unique viewable project-owned material ID")
    compile_six_part_prompt(value, material_view)
    return selected_ids


def _duration(result: CodexStructuredResult) -> float:
    if not isinstance(result.duration_seconds, (int, float)):
        raise ValueError("Codex result did not record duration")
    return float(result.duration_seconds)


def direct_page(
    workspace: ExperimentWorkspace,
    material_view: CompletePageMaterialView,
    *,
    timeout: float,
    invoke: Callable[..., CodexStructuredResult] = invoke_structured,
) -> DirectorArtifact:
    """Run one page-level multimodal director turn in role awesome-page-director."""
    image_ids = _validate_material_view(workspace, material_view)
    visual_reference = _visual_director_reference()
    prompt = (
        "WORD BODY AND MATERIAL AUTHORITY\n"
        "Word body text is the primary authority for page facts, theme, and narrative. "
        "Original comments guide the direction of expression but must not replace or rewrite "
        "Word facts. Word images, tables, charts, and attachments may supplement evidence, "
        "preserve identity, and support visual expression; unless a comment explicitly requires "
        "it, they must not replace the core conclusion of the Word body. Visual creativity may "
        "change presentation but must not alter, invent, or omit the core meaning.\n\n"
        "HARD BOUNDARIES\n"
        "Use only source-supported facts and explicitly mapped page-owned images. The generated "
        f"body must exclude the fixed title, logo, footer, and page number. {_CENTER_17_8_SAFE_REGION}\n\n"
        "GENERAL VISUAL DIRECTOR PRINCIPLES\n"
        f"{visual_reference}\n\n"
        "COMPLETE PAGE MATERIAL VIEW AND VIEWABLE IMAGES\n"
        "IMAGE INPUT MAP (input order is authoritative)\n"
        f"{_mapping_text(image_ids)}\n\n"
        "COMPLETE PAGE MATERIAL VIEW\n"
        f"{_canonical_text(material_view.value)}\n\n"
        "STRUCTURED OUTPUT REQUIREMENTS\n"
        "Act as the page-level visual director and produce the existing machine audit plus open "
        "natural-language creative direction. Audit statuses do not impose a coverage score or "
        "retry gate. In Key Details, list only the short, source-exact names, dates, numbers, and "
        "phrases that Image2 may visibly render; do not authorize any other factual prose. Use "
        "visual hierarchy, "
        "relationships, grouping, and concise labels for dense material. Use only the mapped "
        "images as selectable references. If, after material completion, a specifically requested "
        "real asset is still absent from the mapped images, continue using only its source-exact "
        "formal name from the Word body. Never generate or imply a fake logo, fake person, or fake "
        "factual image, and do not claim the original comment has been fully implemented. Use "
        "scene_or_background only for foreground environment and spatial arrangement. Do not "
        "specify the canvas background, its background color, grid, texture, gradient, or glow "
        "in any prompt_sections field; the compiler supplies the canvas background. Do not restate "
        "compiler-owned hard-boundary language inside prompt_sections. Return exactly the "
        "existing structured-output shape."
    )
    result = invoke(
        workspace.project_copy,
        role="awesome-page-director",
        prompt=prompt,
        images=material_view.multimodal_images,
        output_schema=_load_schema(),
        timeout=timeout,
    )
    director_value = _complete_material_use(result.value, material_view)
    selected_ids = _validate_director_value(director_value, material_view)
    quality = director_value["quality"]
    assert quality in {"medium", "high"}
    artifact = DirectorArtifact(
        value=director_value,
        actual_prompt=compile_six_part_prompt(director_value, material_view),
        selected_reference_ids=selected_ids,
        quality=quality,
        model=result.model,
        effort=result.effort,
        duration_seconds=_duration(result),
        model_provider=result.model_provider,
        usage=dict(result.usage),
        runtime_trace=dict(result.safe_trace),
        thread_id=result.thread_id,
        turn_id=result.turn_id,
    )
    _publish_director_authority(workspace, material_view, artifact)
    return artifact


def _correction_schema(page_number: int = 1) -> dict[str, object]:
    prompt_sections = _load_schema()["$defs"]["promptSections"]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "page_number",
            "strategy",
            "problem_addressed",
            "preserve",
            "selected_reference_ids",
            "prompt_sections",
        ],
        "properties": {
            "schema_version": {"type": "string", "const": "awesome-page-correction-v1"},
            "page_number": {"type": "integer", "const": page_number},
            "strategy": {
                "type": "string",
                "enum": ["edit_previous", "regenerate_from_materials"],
            },
            "problem_addressed": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "minLength": 1},
            },
            "preserve": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "minLength": 1},
            },
            "selected_reference_ids": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "prompt_sections": prompt_sections,
        },
    }


def _candidate_under_project(workspace: ExperimentWorkspace, candidate: Path) -> Path:
    project = workspace.project_copy.resolve(strict=True)
    resolved = Path(candidate).resolve(strict=True)
    try:
        resolved.relative_to(project)
    except ValueError as exc:
        raise ValueError("previous candidate must be project-owned") from exc
    if not resolved.is_file():
        raise ValueError("previous candidate must be a file")
    return resolved


def decide_correction(
    workspace: ExperimentWorkspace,
    material_view: CompletePageMaterialView,
    director: DirectorArtifact,
    *,
    previous_candidate: Path,
    problems: Sequence[str],
    timeout: float,
    invoke: Callable[..., CodexStructuredResult] = invoke_structured,
    previous_decision: CorrectionDecision | None = None,
    previous_request_candidate: Path | None = None,
) -> CorrectionDecision:
    """Let Codex directly choose edit_previous or regenerate_from_materials for stated problems."""
    if not problems or any(not isinstance(problem, str) or not problem.strip() for problem in problems):
        raise ValueError("correction requires explicit review problems")
    candidate = _candidate_under_project(workspace, previous_candidate)
    image_ids = _validate_material_view(workspace, material_view)
    prior_candidate: Path | None = None
    if (previous_decision is None) != (previous_request_candidate is None):
        raise ValueError(
            "previous decision and previous request candidate must be provided together"
        )
    if previous_request_candidate is not None:
        prior_candidate = _candidate_under_project(workspace, previous_request_candidate)
    candidate_number = len(image_ids) + 1
    problem_list = tuple(problem.strip() for problem in problems)
    correction_schema = _correction_schema(workspace.page_number)
    problem_items = correction_schema["properties"]["problem_addressed"]["items"]
    assert isinstance(problem_items, dict)
    problem_items["enum"] = list(problem_list)
    selected_references = correction_schema["properties"]["selected_reference_ids"]
    assert isinstance(selected_references, dict)
    selected_references["maxItems"] = len(image_ids)
    selected_reference_items = selected_references["items"]
    assert isinstance(selected_reference_items, dict)
    if image_ids:
        selected_reference_items["enum"] = list(image_ids)
    prompt = (
        "Correct only the explicit independent-review problems below. Directly choose either "
        "edit_previous or regenerate_from_materials; there is no classifier or routing table. "
        "Copy each addressed problem verbatim from the supplied list, state what changes and "
        "what must be preserved, and return a materially changed prompt "
        "or source-reference input tuple. The previous candidate is not source material. "
        "selected_reference_ids may contain only exact IDs from IMAGE INPUT MAP. If IMAGE INPUT "
        "MAP has no source image, return an empty list. Never invent, search for, or mint a "
        "reference ID for an unavailable identity asset; instead make the source-safe fallback "
        "visually primary and keep its exact name only as a small identity caption.\n\n"
        "EXPLICIT REVIEW PROBLEMS\n"
        f"{_canonical_text(problem_list)}\n\n"
        "IMAGE INPUT MAP\n"
        f"{_mapping_text(image_ids)}\n"
        f"Image-{candidate_number} = previous-candidate (not a source material ID)\n\n"
        "COMPLETE PAGE MATERIAL VIEW\n"
        f"{_canonical_text(material_view.value)}\n\n"
        "PRIOR DIRECTOR ARTIFACT\n"
        f"{_canonical_text(director.value)}"
    )
    result = invoke(
        workspace.project_copy,
        role="awesome-page-correction",
        prompt=prompt,
        images=(*material_view.multimodal_images, candidate),
        output_schema=correction_schema,
        timeout=timeout,
    )
    errors = sorted(
        Draft202012Validator(correction_schema).iter_errors(dict(result.value)),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        path = "/".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise ValueError(f"correction schema rejected {path}: {errors[0].message}")

    addressed = tuple(str(item).strip() for item in result.value["problem_addressed"])
    if any(item not in problem_list for item in addressed):
        raise ValueError("correction problem must be one of the stated review problems")
    allowed_references = set(image_ids)
    selected_ids = tuple(str(item) for item in result.value["selected_reference_ids"])
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("correction contains a duplicate selected reference")
    if any(item not in allowed_references for item in selected_ids):
        raise ValueError("selected reference must be a viewable source material ID")
    actual_prompt = compile_six_part_prompt(result.value, material_view)
    if actual_prompt == director.actual_prompt and selected_ids == director.selected_reference_ids:
        raise ValueError("correction cannot reproduce the unchanged prior prompt and input request")
    strategy = result.value["strategy"]
    assert strategy in {"edit_previous", "regenerate_from_materials"}
    if previous_decision is not None:
        assert prior_candidate is not None
        previous_signature = (
            previous_decision.strategy,
            previous_decision.actual_prompt,
            previous_decision.selected_reference_ids,
            hashlib.sha256(prior_candidate.read_bytes()).hexdigest(),
        )
        current_signature = (
            strategy,
            actual_prompt,
            selected_ids,
            hashlib.sha256(candidate.read_bytes()).hexdigest(),
        )
        if previous_signature == current_signature:
            raise ValueError("consecutive correction cannot repeat the unchanged request")
    preserve = tuple(str(item).strip() for item in result.value["preserve"])
    if not preserve or any(not item for item in preserve):
        raise ValueError("correction preserve must contain non-whitespace text")
    return CorrectionDecision(
        strategy=strategy,
        actual_prompt=actual_prompt,
        selected_reference_ids=selected_ids,
        problem_addressed=addressed,
        preserve=preserve,
        model=result.model,
        effort=result.effort,
        duration_seconds=_duration(result),
        model_provider=result.model_provider,
        usage=dict(result.usage),
        runtime_trace=dict(result.safe_trace),
        thread_id=result.thread_id,
        turn_id=result.turn_id,
        value=result.value,
    )

"""Current-Codex creative direction for the isolated page-1 experiment."""

from __future__ import annotations

import json
import hashlib
import hmac
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
from director_taskbook import confirmed_taskbook_prompt, project_emphasis_pages

from .consulting_prompt import compile_consulting_six_part_prompt

from .materials import (
    CompletePageMaterialView,
    validate_published_complete_page_material_view,
)
from .workspace import ExperimentWorkspace


SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "schemas"
    / "consulting_page_director_v3.schema.json"
)
VISUAL_DIRECTOR_REFERENCE = (
    Path(__file__).resolve().parent / "references" / "visual_director.md"
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
    def page_plan(self) -> Mapping[str, object]:
        value = self.value["page_plan"]
        assert isinstance(value, Mapping)
        return value


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
    return Path("02_v6") / "experiments" / workspace.experiment_id / "director_v2.json"


def _director_authority_value(
    workspace: ExperimentWorkspace,
    material_view: CompletePageMaterialView,
    artifact: DirectorArtifact,
) -> dict[str, object]:
    return {
        "schema_version": "awesome-consulting-page-director-authority-v2",
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
        text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("visual director reference is not valid UTF-8") from exc
    if not text:
        raise ValueError("visual director reference is empty")
    return text


def _validate_fact_allocation(
    page_plan: Mapping[str, object], material_view: CompletePageMaterialView
) -> None:
    expected = {
        str(block["source_block_id"])
        for block in material_view.value["complete_word_content"]
    }
    core = page_plan["core_exhibit"]
    groups = page_plan["support_groups"]
    allocated = [str(item) for item in core["fact_ids"]]
    allocated.extend(
        str(item)
        for group in groups
        for item in group["fact_ids"]
    )
    def fact_ids(value: object):
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key == "fact_ids":
                    yield from child
                else:
                    yield from fact_ids(child)
        elif isinstance(value, list):
            for child in value:
                yield from fact_ids(child)

    if any(str(item) not in expected for item in fact_ids(page_plan)):
        raise ValueError("page plan contains an unknown fact source_block_id")
    if len(allocated) != len(set(allocated)):
        raise ValueError("each Word fact must be allocated exactly once")
    if set(allocated) != expected:
        raise ValueError("page plan must allocate every Word fact exactly once")

    relationship = page_plan["primary_relationship"]
    nodes = relationship["nodes"]
    node_ids = [str(node["node_id"]) for node in nodes]
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("primary relationship node IDs must be unique")
    declared = set(node_ids)
    for edge in relationship["edges"]:
        if str(edge["from_node"]) not in declared or str(edge["to_node"]) not in declared:
            raise ValueError("each edge endpoint must name a declared node")


def _validate_director_value(
    value: Mapping[str, object], material_view: CompletePageMaterialView, *,
    font_accent_allowed: bool = False,
) -> tuple[str, ...]:
    schema = _load_schema()
    validated_value = json.loads(json.dumps(value))
    page_plan = validated_value.get("page_plan")
    if isinstance(page_plan, dict):
        relationship = page_plan.get("primary_relationship")
        if isinstance(relationship, dict):
            edges = relationship.get("edges")
            if isinstance(edges, list):
                for edge in edges:
                    if isinstance(edge, dict):
                        edge.setdefault("label", None)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(validated_value),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        path = "/".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise ValueError(f"director schema rejected {path}: {errors[0].message}")

    page_plan = value["page_plan"]
    assert isinstance(page_plan, Mapping)
    if value["page_number"] != material_view.value["page_number"]:
        raise ValueError("director page_number must match the material page")
    if not str(page_plan["page_purpose"]).strip():
        raise ValueError("page_purpose must contain non-whitespace text")
    relationship = page_plan["primary_relationship"]
    assert isinstance(relationship, Mapping)
    nodes = relationship["nodes"]
    edges = relationship["edges"]
    assert isinstance(nodes, list) and isinstance(edges, list)
    for node in nodes:
        if not str(node["node_id"]).strip() or not str(node["label"]).strip():
            raise ValueError("relationship node_id and label must contain non-whitespace text")
    for edge in edges:
        if edge.get("label") is not None and not str(edge["label"]).strip():
            raise ValueError("relationship edge label must contain non-whitespace text")
    if relationship["grammar"] in {"flow", "hierarchy", "geography", "causality"}:
        if not str(relationship["visual_instruction"]).strip():
            raise ValueError("structural visual_instruction must contain non-whitespace text")
        if not nodes:
            raise ValueError("structural primary relationship requires source-bound nodes")
        if not edges:
            raise ValueError("structural primary relationship requires at least one edge")
    _validate_fact_allocation(page_plan, material_view)
    selected = value["selected_references"]
    assert isinstance(selected, list)
    for reference in selected:
        for field in ("use", "preserve"):
            if not str(reference[field]).strip():
                raise ValueError(
                    f"selected reference {field} must contain non-whitespace text"
                )
    selected_ids = tuple(str(item["material_id"]) for item in selected)
    allowed_references = set(_image_material_ids(material_view)) if selected_ids else set()
    if len(selected_ids) != len(set(selected_ids)) or any(
        item not in allowed_references for item in selected_ids
    ):
        raise ValueError("selected reference must be a unique viewable project-owned material ID")
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
    font_accent_allowed = workspace.page_number in project_emphasis_pages(
        workspace.project_copy
    )
    visual_reference = _visual_director_reference()
    taskbook = confirmed_taskbook_prompt(workspace.project_copy)
    director_material_view = dict(material_view.value)
    visual_contract = director_material_view.get("visual_contract")
    if isinstance(visual_contract, Mapping):
        director_material_view["visual_contract"] = {
            key: value
            for key, value in visual_contract.items()
            if key not in {
                "background_color",
                "primary_color",
                "secondary_color",
                "highlight_color",
            }
        }
    prompt = (
        "WORD BODY AND MATERIAL AUTHORITY\n"
        "Word body text is the primary authority for page facts, theme, and narrative. Comments "
        "guide expression, not facts. Images and attachments supplement evidence and identity; "
        "they do not replace the core Word conclusion unless a comment explicitly requires it.\n\n"
        "GENERAL VISUAL DIRECTOR PRINCIPLES\n"
        f"{visual_reference}\n\n"
        "CONFIRMED PRESENTATION TASKBOOK\n"
        f"{taskbook}\n\n"
        "COMPLETE PAGE MATERIAL VIEW AND VIEWABLE IMAGES\n"
        "IMAGE INPUT MAP (input order is authoritative)\n"
        f"{_mapping_text(image_ids)}\n\n"
        "COMPLETE PAGE MATERIAL VIEW\n"
        f"{_canonical_text(director_material_view)}\n\n"
        "STRUCTURED OUTPUT REQUIREMENTS\n"
        "Your authority is limited to spatial composition, source-supported relationships, core "
        "exhibit choice, and reading path; do not make color or palette decisions. "
        "Return only selected references and these compact v3 page-plan fields: page_purpose, "
        "primary_relationship, core_exhibit, support_groups, reading_path, and local_visuals. Allocate every Word "
        "source_block_id exactly once across the core exhibit and support groups; bind every other "
        "fact reference to those source IDs. Choose only analytical_table, flow, hierarchy, geography, "
        "causality, quantitative_chart, or composition_architecture. Use analytical_table for a "
        "comparison when it is the clearest core exhibit. For flow, hierarchy, geography, or causality, "
        "provide source-bound nodes, directed from_node -> to_node edges, and a non-empty visual "
        "instruction. Select only mapped image material IDs and state their use and what to preserve."
    )
    result = invoke(
        workspace.project_copy,
        role="awesome-page-director",
        prompt=prompt,
        images=material_view.multimodal_images,
        output_schema=_load_schema(),
        timeout=timeout,
    )
    director_value = result.value
    selected_ids = _validate_director_value(
        director_value, material_view, font_accent_allowed=font_accent_allowed
    )
    quality = director_value["quality"]
    assert quality in {"medium", "high"}
    artifact = DirectorArtifact(
        value=director_value,
        actual_prompt=compile_consulting_six_part_prompt(
            director_value, material_view, font_accent_allowed=font_accent_allowed
        ),
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

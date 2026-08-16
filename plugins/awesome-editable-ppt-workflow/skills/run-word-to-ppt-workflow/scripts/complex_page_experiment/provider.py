"""Experiment-only adapter over the frozen signed Image2 Provider path."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Literal, cast

import workflow_v6_image
from provider_keyring import signing_key, verification_key
from workflow_v6_contract import request_identity
from workflow_v6_secure_io import atomic_write_bytes, read_bytes
from workflow_v6_state import load

from .evidence import EvidenceRecorder
from .materials import (
    CompletePageMaterialView,
    validate_published_complete_page_material_view,
)
from .workspace import ExperimentWorkspace


Operation = Literal["generate", "edit"]
Quality = Literal["medium", "high"]
Strategy = Literal["initial", "edit_previous", "regenerate_from_materials"]
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_INPUTS = 16


@dataclass(frozen=True)
class CandidateArtifact:
    attempt: int
    path: Path
    trace_path: Path
    prompt_path: Path
    operation: Operation
    quality: Quality
    selected_reference_ids: tuple[str, ...]
    input_sha256s: tuple[str, ...]
    prompt_sha256: str
    request_identity: str
    duration_seconds: float | None
    duration_unavailable_reason: str | None = None


def _canonical(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _json_object(data: bytes, label: str) -> dict[str, object]:
    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"{label} contains duplicate keys")
            value[key] = item
        return value
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} is invalid")
    return value


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strict_attempt(attempt: object) -> int:
    if type(attempt) is not int or attempt < 1 or attempt > 3:
        raise ValueError("attempt must be an integer from 1 through 3")
    return attempt


def _inside_copy(workspace: ExperimentWorkspace, path: Path, label: str) -> Path:
    root = workspace.project_copy.resolve(strict=True)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} must remain inside the isolated project copy") from exc
    return resolved


def _selected_materials(
    workspace: ExperimentWorkspace,
    material_view: CompletePageMaterialView,
    selected_reference_ids: Sequence[str],
) -> tuple[tuple[Path, ...], tuple[str, ...], tuple[str, ...]]:
    if isinstance(selected_reference_ids, (str, bytes)):
        raise ValueError("selected reference IDs must be an ordered sequence")
    selected = tuple(selected_reference_ids)
    if any(not isinstance(value, str) or not value for value in selected):
        raise ValueError("selected reference IDs must be nonempty strings")
    if len(set(selected)) != len(selected):
        raise ValueError("selected reference IDs must be unique")

    duplicate_ids = {
        str(item["material_id"])
        for item in material_view.value["deduplicated_derivatives"]
    }
    records = {
        str(record["material_id"]): record
        for record in material_view.value["materials"]
        if bool(record["viewable_image"]) and str(record["material_id"]) not in duplicate_ids
    }
    paths: list[Path] = []
    roles: list[str] = []
    digests: list[str] = []
    for material_id in selected:
        record = records.get(material_id)
        if record is None:
            raise ValueError(f"selected reference is not retained page-owned image authority: {material_id}")
        relative = PurePosixPath(str(record["authority_path"]))
        try:
            data = read_bytes(workspace.project_copy, relative)
        except (OSError, ValueError) as exc:
            raise ValueError(f"selected reference authority is unavailable: {material_id}") from exc
        expected = str(record["sha256"])
        if _digest(data) != expected:
            raise ValueError(f"selected reference changed after material-view publication: {material_id}")
        path = _inside_copy(workspace, workspace.project_copy / Path(*relative.parts), "selected reference")
        paths.append(path)
        roles.append(f"page-material:{material_id}")
        digests.append(expected)
    return tuple(paths), tuple(roles), tuple(digests)


def _validated_previous_candidate(
    workspace: ExperimentWorkspace, previous: CandidateArtifact, *, attempt: int
) -> tuple[Path, str, str]:
    if previous.attempt != attempt - 1:
        raise ValueError("edit_previous requires the immediately preceding candidate")
    root = workspace.project_copy.resolve(strict=True)
    canonical_archive = root / Path(*_archive_relative(workspace, previous.attempt).parts)
    try:
        if previous.prompt_path.resolve(strict=True) != canonical_archive.resolve(strict=True):
            raise ValueError
        archive = _json_object(
            read_bytes(root, _archive_relative(workspace, previous.attempt)),
            "previous candidate attempt archive",
        )
    except (OSError, ValueError) as exc:
        raise ValueError(
            "previous candidate attempt authority must remain inside the isolated project copy and be the canonical archive"
        ) from exc
    trace = _inside_copy(workspace, previous.trace_path, "previous candidate trace")
    try:
        trace_value = _json_object(
            read_bytes(root, trace.relative_to(root)), "previous candidate trace"
        )
        trace_inputs = trace_value.get("input_images")
        if not isinstance(trace_inputs, list):
            raise ValueError
        input_paths = tuple(Path(str(item["path"])) for item in trace_inputs if isinstance(item, dict))
        if len(input_paths) != len(trace_inputs):
            raise ValueError
        request = workflow_v6_image.ImageRequest(
            operation=cast(Operation, archive["operation"]), quality=cast(Quality, archive["quality"]),
            prompt=str(archive["actual_prompt"]), input_images=input_paths,
            image_roles=tuple(cast(list[str], archive["image_roles"])),
            input_sha256s=tuple(cast(list[str], archive["input_sha256s"])),
            selected_reference_ids=tuple(cast(list[str], archive["ordered_transport_input_ids"])),
            ui_revision=int(archive["ui_revision"]), ui_digest=str(archive["ui_digest"]),
            page_material_digest=str(archive["page_material_digest"]),
            prompt_output_sha256=str(archive["prompt_output_sha256"]),
            source_identity=str(archive["source_identity"]), project_root=root,
            page_number=workspace.page_number,
        )
        seal = _load_request_seal(workspace, request, attempt=previous.attempt)
        validated = _load_completed_archive(
            workspace, request, attempt=previous.attempt, seal=seal,
        )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("previous candidate trace or archive binding is unavailable") from exc
    if validated is None or validated != previous:
        raise ValueError("previous CandidateArtifact differs from canonical completed authority")
    path = validated.path
    digest = _digest(read_bytes(root, path.relative_to(root)))
    return path, digest, f"candidate:{previous.attempt}:{digest}"


def build_experiment_image_request(
    workspace: ExperimentWorkspace,
    material_view: CompletePageMaterialView,
    *,
    attempt: int,
    prompt: str,
    quality: Quality,
    selected_reference_ids: Sequence[str],
    strategy: Strategy,
    previous_candidate: CandidateArtifact | None,
) -> workflow_v6_image.ImageRequest:
    """Resolve selected owned bytes and optional preceding candidate without writes."""
    attempt = _strict_attempt(attempt)
    if workspace.page_number < 1:
        raise ValueError("complex-page experiment provider requires a positive page number")
    if not isinstance(prompt, str) or not prompt.strip() or prompt != prompt.strip():
        raise ValueError("prompt must be nonempty exact text without outer whitespace")
    if quality not in {"medium", "high"}:
        raise ValueError("quality must be medium or high")
    if strategy not in {"initial", "edit_previous", "regenerate_from_materials"}:
        raise ValueError("strategy is invalid")
    if strategy == "initial" and attempt != 1:
        raise ValueError("initial strategy is valid only for attempt 1")
    if strategy == "initial" and previous_candidate is not None:
        raise ValueError("initial strategy cannot carry a previous candidate")
    if strategy != "initial" and attempt == 1:
        raise ValueError("correction strategy requires attempt 2 or 3")
    if strategy == "edit_previous" and previous_candidate is None:
        raise ValueError("edit_previous requires the immediately preceding candidate")
    if strategy == "regenerate_from_materials" and previous_candidate is None:
        raise ValueError("regenerate correction requires the immediately preceding candidate authority")
    if len(selected_reference_ids) + (1 if strategy == "edit_previous" else 0) > _MAX_INPUTS:
        raise ValueError("Image2 accepts at most 16 total image inputs")

    validate_published_complete_page_material_view(workspace, material_view)
    material_paths, material_roles, material_digests = _selected_materials(
        workspace, material_view, selected_reference_ids
    )
    selected_material_ids = tuple(selected_reference_ids)
    paths = list(material_paths)
    roles = list(material_roles)
    digests = list(material_digests)
    transport_ids = list(selected_material_ids)
    if strategy == "regenerate_from_materials":
        assert previous_candidate is not None
        _validated_previous_candidate(workspace, previous_candidate, attempt=attempt)
    if strategy == "edit_previous":
        assert previous_candidate is not None
        candidate_path, candidate_digest, candidate_id = _validated_previous_candidate(
            workspace, previous_candidate, attempt=attempt
        )
        paths.insert(0, candidate_path)
        roles.insert(0, "previous-candidate-to-correct")
        digests.insert(0, candidate_digest)
        transport_ids.insert(0, candidate_id)
    if len(paths) > _MAX_INPUTS:
        raise ValueError("Image2 accepts at most 16 total image inputs")

    prompt_sha256 = _digest(prompt.encode("utf-8"))
    if previous_candidate is not None and strategy != "initial":
        if previous_candidate.attempt != attempt - 1:
            raise ValueError("correction requires the immediately preceding candidate authority")
        if (
            previous_candidate.prompt_sha256 == prompt_sha256
            and previous_candidate.input_sha256s == tuple(digests)
        ):
            raise ValueError("correction request is identical to the preceding prompt and input digest tuple")

    state = load(workspace.project_copy)
    page_receipt = state["pages"][workspace.page_number - 1]["material_receipt"]
    request = workflow_v6_image.ImageRequest(
        operation="edit" if paths else "generate",
        quality=quality,
        prompt=prompt,
        input_images=tuple(paths),
        image_roles=tuple(roles),
        input_sha256s=tuple(digests),
        selected_reference_ids=tuple(transport_ids),
        ui_revision=int(state["confirmed_ui_revision"]),
        ui_digest=str(state["confirmed_ui_digest"]),
        page_material_digest=str(page_receipt["digest"]),
        prompt_output_sha256=prompt_sha256,
        source_identity=str(state["source_identity"]),
        project_root=workspace.project_copy.resolve(strict=True),
        page_number=workspace.page_number,
    )
    _publish_request_seal(
        workspace, request, attempt=attempt, strategy=cast(Strategy, strategy),
        material_view_sha256=material_view.sha256,
        selected_material_reference_ids=selected_material_ids,
        previous_candidate=previous_candidate,
    )
    return request


def _bridge_receipt(
    workspace: ExperimentWorkspace,
    request: workflow_v6_image.ImageRequest,
    *,
    attempt: int,
) -> dict[str, object]:
    state = load(workspace.project_copy)
    transport = list(request.selected_reference_ids)
    candidate = None
    selected_materials = transport
    if transport and transport[0].startswith("candidate:"):
        candidate = {
            "transport_id": transport[0],
            "role": request.image_roles[0],
            "sha256": request.input_sha256s[0],
            "path": request.input_images[0].relative_to(workspace.project_copy).as_posix(),
        }
        selected_materials = transport[1:]
    return {
        "schema_version": "awesome-page-image-prompt-receipt-v1",
        "plugin_id": request.plugin_id,
        "plugin_version": request.plugin_version,
        "workflow_contract": request.workflow_contract,
        "source_identity": request.source_identity,
        "page_number": workspace.page_number,
        "ui_revision": request.ui_revision,
        "ui_digest": request.ui_digest,
        "page_material_digest": request.page_material_digest,
        "prompt_output_path": f"02_v6/page_image_prompts/page_{workspace.page_number:03d}.output.json",
        "prompt_output_sha256": request.prompt_output_sha256,
        "selected_reference_ids": transport,
        "attempt": attempt,
        "selected_material_reference_ids": selected_materials,
        "correction_candidate_input": candidate,
        "ordered_transport_input_ids": transport,
        "source_snapshot_sha256": workspace.source_snapshot_sha256,
        "experiment_id": workspace.experiment_id,
        "model": request.model,
        "size": request.size,
        "quality": request.quality,
        "operation": request.operation,
        "input_sha256s": list(request.input_sha256s),
        "image_roles": list(request.image_roles),
        "actual_prompt": request.prompt,
        "project_identity": {
            "plugin_id": state["plugin_id"],
            "plugin_version": state["plugin_version"],
            "workflow_contract": state["workflow_contract"],
        },
    }


def _publish_bridge(
    workspace: ExperimentWorkspace,
    request: workflow_v6_image.ImageRequest,
    *,
    attempt: int,
) -> tuple[Path, dict[str, object]]:
    root = workspace.project_copy
    prompt_relative = PurePosixPath(
        "02_v6", "page_image_prompts", f"page_{workspace.page_number:03d}.output.json"
    )
    receipt_relative = prompt_relative.with_name(
        f"page_{workspace.page_number:03d}.receipt.json"
    )
    prompt_bytes = request.prompt.encode("utf-8")
    receipt = _bridge_receipt(workspace, request, attempt=attempt)
    def publish(relative: PurePosixPath, data: bytes) -> None:
        try:
            atomic_write_bytes(root, relative, data, replace=attempt > 1)
        except FileExistsError:
            if read_bytes(root, relative) != data:
                raise
    publish(prompt_relative, prompt_bytes)
    try:
        publish(receipt_relative, _canonical(receipt))
    except BaseException:
        # A prompt without a matching receipt is intentionally unusable by the frozen gate.
        raise
    return root / Path(*prompt_relative.parts), receipt


def _request_identity(request: workflow_v6_image.ImageRequest, material_view_sha256: str) -> str:
    return request_identity(
        revision_digest=material_view_sha256,
        prompt_sha256=_digest(request.prompt.encode("utf-8")),
        operation=request.operation,
        quality=request.quality,
        input_sha256s=request.input_sha256s,
        plugin_id=request.plugin_id,
        plugin_version=request.plugin_version,
        workflow_contract=request.workflow_contract,
        page_material_digest=request.page_material_digest,
        prompt_output_sha256=request.prompt_output_sha256,
        selected_reference_ids=request.selected_reference_ids,
        model=request.model,
        size=request.size,
        source_identity=request.source_identity,
    )


def _request_seal_relative(workspace: ExperimentWorkspace, attempt: int) -> PurePosixPath:
    return PurePosixPath(
        "04_v6", "experiments", workspace.experiment_id,
        f"request_attempt_{attempt}.json",
    )


def _request_projection(
    workspace: ExperimentWorkspace,
    request: workflow_v6_image.ImageRequest,
    *,
    attempt: int,
    strategy: Strategy,
    material_view_sha256: str,
    selected_material_reference_ids: Sequence[str],
    previous_candidate: CandidateArtifact | None,
) -> dict[str, object]:
    root = workspace.project_copy.resolve(strict=True)
    candidate = None
    predecessor = None
    if previous_candidate is not None:
        archive_relative = _archive_relative(workspace, previous_candidate.attempt)
        archive = _json_object(read_bytes(root, archive_relative), "immediate predecessor archive")
        predecessor = {
            "attempt": previous_candidate.attempt,
            "archive_path": archive_relative.as_posix(),
            "archive_sha256": _digest(read_bytes(root, archive_relative)),
            "candidate_path": previous_candidate.path.relative_to(root).as_posix(),
            "candidate_sha256": _digest(read_bytes(root, previous_candidate.path.relative_to(root))),
            "trace_path": previous_candidate.trace_path.relative_to(root).as_posix(),
            "trace_sha256": _digest(read_bytes(root, previous_candidate.trace_path.relative_to(root))),
            "request_identity": previous_candidate.request_identity,
            "capability_path": archive["capability_path"],
            "capability_sha256": archive["capability_sha256"],
            "capability_nonce": archive["capability_nonce"],
            "journal_path": archive["journal_path"],
            "journal_sha256": archive["journal_sha256"],
        }
    if strategy == "edit_previous":
        assert previous_candidate is not None
        candidate = {
            "attempt": previous_candidate.attempt,
            "path": previous_candidate.path.relative_to(root).as_posix(),
            "sha256": _digest(read_bytes(root, previous_candidate.path.relative_to(root))),
            "request_identity": previous_candidate.request_identity,
        }
    identity = _request_identity(request, material_view_sha256)
    return {
        "schema_version": "awesome-experiment-image-request-seal-v1",
        "experiment_id": workspace.experiment_id,
        "page_number": workspace.page_number,
        "source_snapshot_sha256": workspace.source_snapshot_sha256,
        "attempt": attempt,
        "strategy": strategy,
        "material_view_sha256": material_view_sha256,
        "request_identity": identity,
        "immediate_predecessor_authority": predecessor,
        "request": {
            "prompt_sha256": _digest(request.prompt.encode("utf-8")),
            "quality": request.quality,
            "operation": request.operation,
            "model": request.model,
            "size": request.size,
            "selected_material_reference_ids": list(selected_material_reference_ids),
            "correction_candidate_input": candidate,
            "ordered_transport_input_ids": list(request.selected_reference_ids),
            "input_paths": [path.relative_to(root).as_posix() for path in request.input_images],
            "image_roles": list(request.image_roles),
            "input_sha256s": list(request.input_sha256s),
            "plugin_id": request.plugin_id,
            "plugin_version": request.plugin_version,
            "workflow_contract": request.workflow_contract,
            "source_identity": request.source_identity,
            "ui_revision": request.ui_revision,
            "ui_digest": request.ui_digest,
            "page_material_digest": request.page_material_digest,
            "prompt_output_sha256": request.prompt_output_sha256,
        },
    }


def _publish_request_seal(
    workspace: ExperimentWorkspace,
    request: workflow_v6_image.ImageRequest,
    *,
    attempt: int,
    strategy: Strategy,
    material_view_sha256: str,
    selected_material_reference_ids: Sequence[str],
    previous_candidate: CandidateArtifact | None,
) -> None:
    value = _request_projection(
        workspace, request, attempt=attempt, strategy=strategy,
        material_view_sha256=material_view_sha256,
        selected_material_reference_ids=selected_material_reference_ids,
        previous_candidate=previous_candidate,
    )
    relative = _request_seal_relative(workspace, attempt)
    key_id, key = signing_key()
    signed = {**value, "key_id": key_id}
    signed["hmac_sha256"] = hmac.new(key, _canonical(signed).rstrip(b"\n"), hashlib.sha256).hexdigest()
    payload = _canonical(signed)
    try:
        atomic_write_bytes(workspace.project_copy, relative, payload)
    except FileExistsError:
        if read_bytes(workspace.project_copy, relative) != payload:
            raise ValueError("canonical request seal already exists with different authority")


def _load_request_seal(
    workspace: ExperimentWorkspace,
    request: workflow_v6_image.ImageRequest,
    *,
    attempt: int,
) -> dict[str, object]:
    relative = _request_seal_relative(workspace, attempt)
    try:
        data = read_bytes(workspace.project_copy, relative)
        value = _json_object(data, "sealed request authority")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("sealed request attempt authority is missing or invalid") from exc
    top_keys = {
        "schema_version", "experiment_id", "page_number", "source_snapshot_sha256",
        "attempt", "strategy", "material_view_sha256", "request_identity", "request",
        "immediate_predecessor_authority", "key_id", "hmac_sha256",
    }
    request_keys = {
        "prompt_sha256", "quality", "operation", "model", "size",
        "selected_material_reference_ids", "correction_candidate_input",
        "ordered_transport_input_ids", "input_paths", "image_roles", "input_sha256s",
        "plugin_id", "plugin_version", "workflow_contract", "source_identity",
        "ui_revision", "ui_digest", "page_material_digest", "prompt_output_sha256",
    }
    sealed = value.get("request")
    if set(value) != top_keys or not isinstance(sealed, dict) or set(sealed) != request_keys:
        raise ValueError("sealed request authority schema is invalid")
    signature = value.get("hmac_sha256")
    unsigned = dict(value)
    unsigned.pop("hmac_sha256")
    try:
        key = verification_key(value.get("key_id"))
    except (OSError, ValueError) as exc:
        raise ValueError("sealed request authority key is invalid") from exc
    expected = hmac.new(key, _canonical(unsigned).rstrip(b"\n"), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise ValueError("sealed request authority signature is invalid")
    if (
        value.get("schema_version") != "awesome-experiment-image-request-seal-v1"
        or value.get("experiment_id") != workspace.experiment_id
        or value.get("page_number") != workspace.page_number
        or value.get("source_snapshot_sha256") != workspace.source_snapshot_sha256
        or value.get("attempt") != attempt
    ):
        raise ValueError("sealed request attempt authority is invalid")
    root = workspace.project_copy.resolve(strict=True)
    material_relative = PurePosixPath(
        "02_v6", "experiments", workspace.experiment_id, "complete_page_material_view.json"
    )
    material_bytes = read_bytes(root, material_relative)
    material_value = _json_object(material_bytes, "published complete material view")
    material_view = CompletePageMaterialView(
        value=material_value, multimodal_images=(), material_ids=(), sha256=_digest(material_bytes)
    )
    validate_published_complete_page_material_view(workspace, material_view)
    if value.get("material_view_sha256") != _digest(material_bytes):
        raise ValueError("sealed request material view authority is invalid")
    actual = {
        "prompt_sha256": _digest(request.prompt.encode("utf-8")),
        "quality": request.quality,
        "operation": request.operation,
        "model": request.model,
        "size": request.size,
        "selected_material_reference_ids": list(
            request.selected_reference_ids[1:]
            if request.selected_reference_ids and request.selected_reference_ids[0].startswith("candidate:")
            else request.selected_reference_ids
        ),
        "correction_candidate_input": sealed.get("correction_candidate_input"),
        "ordered_transport_input_ids": list(request.selected_reference_ids),
        "input_paths": [path.relative_to(root).as_posix() for path in request.input_images],
        "image_roles": list(request.image_roles),
        "input_sha256s": list(request.input_sha256s),
        "plugin_id": request.plugin_id,
        "plugin_version": request.plugin_version,
        "workflow_contract": request.workflow_contract,
        "source_identity": request.source_identity,
        "ui_revision": request.ui_revision,
        "ui_digest": request.ui_digest,
        "page_material_digest": request.page_material_digest,
        "prompt_output_sha256": request.prompt_output_sha256,
    }
    if sealed != actual:
        raise ValueError("ImageRequest differs from canonical sealed request authority")
    if value.get("request_identity") != _request_identity(request, _digest(material_bytes)):
        raise ValueError("sealed request identity is invalid")
    strategy = value.get("strategy")
    candidate = sealed.get("correction_candidate_input")
    predecessor = value.get("immediate_predecessor_authority")
    if (
        (attempt == 1 and strategy != "initial")
        or (attempt > 1 and strategy not in {"edit_previous", "regenerate_from_materials"})
        or (strategy == "edit_previous") != (candidate is not None)
        or (attempt == 1) != (predecessor is None)
    ):
        raise ValueError("sealed request strategy/attempt authority is invalid")
    return value


def _validate_request_transport_authority(
    workspace: ExperimentWorkspace, request: workflow_v6_image.ImageRequest
) -> None:
    lengths = {
        len(request.input_images),
        len(request.image_roles),
        len(request.input_sha256s),
        len(request.selected_reference_ids),
    }
    if len(lengths) != 1:
        raise ValueError("request selected transport inputs are not aligned")
    expects_edit = bool(request.input_images)
    if request.operation != ("edit" if expects_edit else "generate"):
        raise ValueError("request operation does not match selected transport inputs")
    for index, (reference_id, role, path, digest) in enumerate(zip(
        request.selected_reference_ids,
        request.image_roles,
        request.input_images,
        request.input_sha256s,
    )):
        owned = _inside_copy(workspace, path, "request input")
        actual = _digest(read_bytes(
            workspace.project_copy, owned.relative_to(workspace.project_copy)
        ))
        if actual != digest:
            raise ValueError("request selected input changed after construction")
        if reference_id.startswith("candidate:"):
            if index != 0 or role != "previous-candidate-to-correct":
                raise ValueError("request candidate transport authority is invalid")
            parts = reference_id.split(":")
            if len(parts) != 3 or not parts[1].isdigit() or parts[2] != digest:
                raise ValueError("request candidate transport authority is invalid")
            continue
        if role != f"page-material:{reference_id}":
            raise ValueError("request selected material input authority is invalid")


def _validate_immediate_predecessor(
    workspace: ExperimentWorkspace,
    seal: Mapping[str, object],
    *,
    attempt: int,
) -> None:
    authority = seal.get("immediate_predecessor_authority")
    if attempt == 1:
        if authority is not None:
            raise ValueError("initial attempt forbids immediate predecessor authority")
        return
    if not isinstance(authority, dict) or authority.get("attempt") != attempt - 1:
        raise ValueError("correction attempt requires immediate predecessor authority")
    root = workspace.project_copy.resolve(strict=True)
    expected_archive = _archive_relative(workspace, attempt - 1)
    archive_path = authority.get("archive_path")
    if archive_path != expected_archive.as_posix():
        raise ValueError("immediate predecessor archive path is not canonical")
    archive_bytes = read_bytes(root, expected_archive)
    archive = _json_object(archive_bytes, "immediate predecessor archive")
    expected = {
        "archive_sha256": _digest(archive_bytes),
        "candidate_path": archive.get("candidate_path"),
        "candidate_sha256": archive.get("candidate_sha256"),
        "trace_path": archive.get("trace_path"),
        "trace_sha256": archive.get("trace_sha256"),
        "request_identity": archive.get("request_identity"),
        "capability_path": archive.get("capability_path"),
        "capability_sha256": archive.get("capability_sha256"),
        "capability_nonce": archive.get("capability_nonce"),
        "journal_path": archive.get("journal_path"),
        "journal_sha256": archive.get("journal_sha256"),
    }
    if any(authority.get(key) != item for key, item in expected.items()):
        raise ValueError("immediate predecessor authority differs from canonical archive")
    for path_key, sha_key in (("candidate_path", "candidate_sha256"), ("trace_path", "trace_sha256")):
        relative = PurePosixPath(str(authority[path_key]))
        if _digest(read_bytes(root, relative)) != authority[sha_key]:
            raise ValueError("immediate predecessor candidate or trace changed")
    capability_relative = PurePosixPath(str(authority["capability_path"]))
    try:
        capability_bytes = read_bytes(root, capability_relative)
    except OSError as exc:
        raise ValueError("immediate predecessor capability is missing") from exc
    if _digest(capability_bytes) != authority["capability_sha256"]:
        raise ValueError("immediate predecessor capability changed")
    capability = _json_object(capability_bytes, "immediate predecessor capability")
    signature = capability.get("hmac_sha256")
    unsigned_capability = dict(capability)
    unsigned_capability.pop("hmac_sha256", None)
    try:
        capability_key = verification_key(capability.get("key_id"))
    except (OSError, ValueError) as exc:
        raise ValueError("immediate predecessor capability key is invalid") from exc
    expected_signature = hmac.new(
        capability_key,
        json.dumps(unsigned_capability, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected_signature):
        raise ValueError("immediate predecessor capability signature is invalid")
    capability_expected = {
        "schema_version": "awesome-image-request-capability-v3",
        "nonce": authority["capability_nonce"],
        "attempt": attempt - 1,
        "page_number": workspace.page_number,
        "source_identity": archive["source_identity"],
        "operation": archive["operation"],
        "quality": archive["quality"],
        "model": archive["model"],
        "size": archive["size"],
        "prompt_sha256": archive["prompt_sha256"],
        "prompt_output_sha256": archive["prompt_output_sha256"],
        "selected_reference_ids": archive["ordered_transport_input_ids"],
        "input_sha256s": archive["input_sha256s"],
        "image_roles": archive["image_roles"],
        "output_path": archive["candidate_path"],
        "trace_path": archive["trace_path"],
    }
    if any(capability.get(key) != item for key, item in capability_expected.items()):
        raise ValueError("immediate predecessor capability request binding is invalid")
    if capability.get("project_binding") != _digest(str(root).encode("utf-8")):
        raise ValueError("immediate predecessor capability project binding is invalid")
    project_identity = capability.get("project_identity")
    if not isinstance(project_identity, dict) or any(
        project_identity.get(key) != archive["project_identity"].get(key)
        for key in ("plugin_id", "plugin_version", "workflow_contract")
    ):
        raise ValueError("immediate predecessor capability project identity is invalid")

    try:
        journal, journal_path, journal_bytes = _verified_journal(workspace, capability)
    except OSError as exc:
        raise ValueError("immediate predecessor journal is missing") from exc
    if journal_path.relative_to(root).as_posix() != authority["journal_path"]:
        raise ValueError("immediate predecessor journal path binding is invalid")
    if _digest(journal_bytes) != authority["journal_sha256"]:
        raise ValueError("immediate predecessor journal changed")
    if journal.get("state") != "submitted" or journal.get("outputs") != [authority["candidate_sha256"]]:
        raise ValueError("immediate predecessor journal state or output binding is invalid")


def _capability_candidates(workspace: ExperimentWorkspace, attempt: int) -> list[Path]:
    directory = workspace.project_copy / "04_v6" / "image_request_capabilities"
    if not directory.is_dir():
        return []
    return sorted(directory.glob(f"page_{workspace.page_number:03d}.attempt_{attempt}.*.json"))


def _verified_capability(
    workspace: ExperimentWorkspace,
    request: workflow_v6_image.ImageRequest,
    *,
    attempt: int,
    output_relative: PurePosixPath,
    trace_relative: PurePosixPath,
    capability_path: Path,
) -> tuple[dict[str, object], bytes]:
    root = workspace.project_copy.resolve(strict=True)
    path = _inside_copy(workspace, capability_path, "Provider capability")
    data = read_bytes(root, path.relative_to(root))
    value = _json_object(data, "Provider capability")
    signature = value.get("hmac_sha256")
    unsigned = dict(value)
    unsigned.pop("hmac_sha256", None)
    try:
        key = verification_key(value.get("key_id"))
    except (OSError, ValueError) as exc:
        raise ValueError("Provider capability signing key is unavailable") from exc
    expected_signature = hmac.new(
        key,
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected_signature):
        raise ValueError("Provider capability signature is invalid")
    expected = {
        "schema_version": "awesome-image-request-capability-v3",
        "page_number": workspace.page_number,
        "attempt": attempt,
        "operation": request.operation,
        "model": request.model,
        "size": request.size,
        "quality": request.quality,
        "prompt_sha256": _digest(request.prompt.encode("utf-8")),
        "prompt_output_sha256": request.prompt_output_sha256,
        "selected_reference_ids": list(request.selected_reference_ids),
        "image_roles": list(request.image_roles),
        "input_sha256s": list(request.input_sha256s),
        "output_path": output_relative.as_posix(),
        "trace_path": trace_relative.as_posix(),
        "plugin_id": request.plugin_id,
        "plugin_version": request.plugin_version,
        "workflow_contract": request.workflow_contract,
        "source_identity": request.source_identity,
        "ui_revision": request.ui_revision,
        "ui_digest": request.ui_digest,
        "page_material_digest": request.page_material_digest,
    }
    if any(value.get(key) != item for key, item in expected.items()):
        raise ValueError("Provider capability differs from sealed experiment request")
    selected = value.get("selected_references")
    expected_selected = [
        {"reference_id": reference_id, "role": role, "sha256": digest}
        for reference_id, role, digest in zip(
            request.selected_reference_ids, request.image_roles, request.input_sha256s
        )
    ]
    if not isinstance(selected, list) or len(selected) != len(expected_selected):
        raise ValueError("Provider capability selected reference authority is invalid")
    for actual, expected_item in zip(selected, expected_selected):
        if not isinstance(actual, dict) or any(actual.get(k) != v for k, v in expected_item.items()):
            raise ValueError("Provider capability selected reference authority is invalid")
    return value, data


def _verified_journal(
    workspace: ExperimentWorkspace,
    capability: Mapping[str, object],
) -> tuple[dict[str, object], Path, bytes]:
    nonce = capability.get("nonce")
    if not isinstance(nonce, str) or not nonce:
        raise ValueError("Provider capability nonce is invalid")
    relative = PurePosixPath("04_v6", "image_request_capabilities", "journal", f"{nonce}.json")
    data = read_bytes(workspace.project_copy, relative)
    value = _json_object(data, "Provider submission journal")
    signature = value.get("journal_hmac_sha256")
    unsigned = dict(value)
    unsigned.pop("journal_hmac_sha256", None)
    try:
        key = verification_key(value.get("key_id"))
    except (OSError, ValueError) as exc:
        raise ValueError("Provider submission journal signing key is unavailable") from exc
    expected = hmac.new(
        key,
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise ValueError("Provider submission journal signature is invalid")
    capability_digest = _digest(json.dumps(
        capability, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"))
    bindings = {
        "schema_version": "awesome-image-submission-v2",
        "nonce": nonce,
        "attempt": capability.get("attempt"),
        "operation": capability.get("operation"),
        "input_sha256s": capability.get("input_sha256s"),
        "capability_sha256": capability_digest,
    }
    if any(value.get(key) != item for key, item in bindings.items()):
        raise ValueError("Provider submission journal request binding is invalid")
    return value, workspace.project_copy / Path(*relative.parts), data


def _strict_trace(
    workspace: ExperimentWorkspace,
    request: workflow_v6_image.ImageRequest,
    *,
    output: Path,
    output_sha256: str,
    trace_relative: PurePosixPath,
) -> tuple[dict[str, object], bytes]:
    root = workspace.project_copy.resolve(strict=True)
    trace_bytes = read_bytes(root, trace_relative)
    value = _json_object(trace_bytes, "Provider trace")
    allowed = {"operation", "endpoint", "model", "size", "quality", "auth", "input_images", "outputs", "warnings"}
    if not set(value).issubset(allowed) or set(value) - {"warnings"} != allowed - {"warnings"}:
        raise ValueError("Provider trace schema is invalid")
    expected_inputs = [
        {"role": role, "path": str(path.resolve(strict=True)), "sha256": digest}
        for role, path, digest in zip(request.image_roles, request.input_images, request.input_sha256s)
    ]
    expected = {
        "operation": request.operation,
        "endpoint": (
            "images/edits"
            if request.operation == "edit" else
            "images/generations"
        ),
        "model": request.model,
        "size": request.size,
        "quality": request.quality,
        "auth": "codex_oauth",
        "input_images": expected_inputs,
        "outputs": [{"path": str(output.resolve(strict=True)), "sha256": output_sha256, "mime_type": "image/png"}],
    }
    if any(value.get(key) != item for key, item in expected.items()):
        raise ValueError("Provider trace differs from sealed request or candidate")
    return value, trace_bytes


def _archive_relative(workspace: ExperimentWorkspace, attempt: int) -> PurePosixPath:
    return PurePosixPath("04_v6", "experiments", workspace.experiment_id, f"attempt_{attempt}.json")


def _call_state_relative(workspace: ExperimentWorkspace, attempt: int) -> PurePosixPath:
    return PurePosixPath(
        "04_v6", "experiments", workspace.experiment_id, f"provider_call_attempt_{attempt}.json"
    )


def _publish_call_state(
    workspace: ExperimentWorkspace,
    request: workflow_v6_image.ImageRequest,
    *,
    attempt: int,
    seal: Mapping[str, object],
    capability_path: Path,
    capability: Mapping[str, object],
) -> dict[str, object]:
    root = workspace.project_copy.resolve(strict=True)
    relative = _call_state_relative(workspace, attempt)
    value: dict[str, object] = {
        "schema_version": "awesome-experiment-provider-call-v1",
        "experiment_id": workspace.experiment_id,
        "page_number": workspace.page_number,
        "source_snapshot_sha256": workspace.source_snapshot_sha256,
        "attempt": attempt,
        "model": request.model,
        "operation": request.operation,
        "quality": request.quality,
        "request_identity": seal["request_identity"],
        "capability_nonce": capability["nonce"],
        "capability_path": capability_path.relative_to(root).as_posix(),
        "journal_path": PurePosixPath(
            "04_v6", "image_request_capabilities", "journal", f"{capability['nonce']}.json"
        ).as_posix(),
    }
    key_id, key = signing_key()
    signed = {**value, "key_id": key_id}
    signed["hmac_sha256"] = hmac.new(key, _canonical(signed).rstrip(b"\n"), hashlib.sha256).hexdigest()
    payload = _canonical(signed)
    try:
        atomic_write_bytes(root, relative, payload)
    except FileExistsError:
        if read_bytes(root, relative) != payload:
            raise ValueError("durable Provider-call state differs from canonical authority")
    return signed


def _verified_call_state(
    workspace: ExperimentWorkspace,
    request: workflow_v6_image.ImageRequest,
    *,
    attempt: int,
    seal: Mapping[str, object],
) -> dict[str, object]:
    value = _json_object(
        read_bytes(workspace.project_copy, _call_state_relative(workspace, attempt)),
        "durable Provider-call state",
    )
    keys = {
        "schema_version", "experiment_id", "page_number", "source_snapshot_sha256",
        "attempt", "model", "operation", "quality", "request_identity",
        "capability_nonce", "capability_path", "journal_path", "key_id", "hmac_sha256",
    }
    if set(value) != keys:
        raise ValueError("durable Provider-call state schema is invalid")
    signature = value.get("hmac_sha256")
    unsigned = dict(value)
    unsigned.pop("hmac_sha256")
    try:
        key = verification_key(value.get("key_id"))
    except (OSError, ValueError) as exc:
        raise ValueError("durable Provider-call state key is invalid") from exc
    expected = hmac.new(key, _canonical(unsigned).rstrip(b"\n"), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise ValueError("durable Provider-call state signature is invalid")
    nonce = value.get("capability_nonce")
    bindings = {
        "schema_version": "awesome-experiment-provider-call-v1",
        "experiment_id": workspace.experiment_id,
        "page_number": workspace.page_number,
        "source_snapshot_sha256": workspace.source_snapshot_sha256,
        "attempt": attempt,
        "model": request.model,
        "operation": request.operation,
        "quality": request.quality,
        "request_identity": seal["request_identity"],
        "journal_path": PurePosixPath(
            "04_v6", "image_request_capabilities", "journal", f"{nonce}.json"
        ).as_posix(),
    }
    if any(value.get(key) != item for key, item in bindings.items()):
        raise ValueError("durable Provider-call state binding is invalid")
    capability_path = value.get("capability_path")
    if not isinstance(nonce, str) or not isinstance(capability_path, str) or not capability_path.endswith(
        f".attempt_{attempt}.{nonce}.json"
    ):
        raise ValueError("durable Provider-call capability binding is invalid")
    return value


def _reconcile_provider_call(
    workspace: ExperimentWorkspace,
    recorder: EvidenceRecorder,
    request: workflow_v6_image.ImageRequest,
    *,
    attempt: int,
    seal: Mapping[str, object],
    journal_state: str,
    duration_seconds: float | None = None,
    duration_unavailable_reason: str | None = None,
) -> None:
    if recorder.has_call(kind="image2", attempt=attempt):
        return
    _verified_call_state(workspace, request, attempt=attempt, seal=seal)
    statuses = {
        "response_received": "recovered",
        "submitted": "submitted",
        "outcome_unknown": "outcome_unknown",
        "completed_archive": "recovered",
        "submitting": "outcome_unknown",
    }
    status = statuses.get(journal_state)
    if status is None:
        return
    if duration_seconds is None:
        duration_unavailable_reason = (
            duration_unavailable_reason
            or "process ended before Image2 duration evidence was committed"
        )
    recorder.record_call(
        kind="image2", attempt=attempt, model=request.model, effort=None,
        operation=request.operation, duration_seconds=duration_seconds,
        unavailable_reason=duration_unavailable_reason,
        status=status,
        metadata={
            "quality": request.quality, "size": request.size,
            "input_count": len(request.input_images),
            "request_identity_sha256": seal["request_identity"],
        },
    )


def _publish_completed_archive(
    workspace: ExperimentWorkspace,
    request: workflow_v6_image.ImageRequest,
    *,
    attempt: int,
    seal: Mapping[str, object],
    receipt: Mapping[str, object],
    output_relative: PurePosixPath,
    trace_relative: PurePosixPath,
    capability_path: Path,
    duration: float | None,
    duration_unavailable_reason: str | None = None,
) -> CandidateArtifact:
    root = workspace.project_copy.resolve(strict=True)
    output = root / Path(*output_relative.parts)
    output_bytes = read_bytes(root, output_relative)
    output_sha256 = _digest(output_bytes)
    _trace, trace_bytes = _strict_trace(
        workspace, request, output=output, output_sha256=output_sha256,
        trace_relative=trace_relative,
    )
    capability, capability_bytes = _verified_capability(
        workspace, request, attempt=attempt, output_relative=output_relative,
        trace_relative=trace_relative, capability_path=capability_path,
    )
    journal, journal_path, journal_bytes = _verified_journal(workspace, capability)
    if journal.get("state") != "submitted" or journal.get("outputs") != [output_sha256]:
        raise ValueError("Provider submission journal is not a completed candidate authority")
    selected_materials = seal.get("request", {}).get("selected_material_reference_ids")
    if not isinstance(selected_materials, list):
        raise ValueError("sealed selected material authority is invalid")
    identity = str(seal.get("request_identity"))
    if identity != _request_identity(request, str(seal.get("material_view_sha256"))):
        raise ValueError("sealed request identity is invalid")
    archive_relative = _archive_relative(workspace, attempt)
    if duration is None:
        reason = duration_unavailable_reason or "process ended before Image2 duration evidence was committed"
    else:
        if duration_unavailable_reason is not None:
            raise ValueError("known candidate duration cannot have an unavailable reason")
        reason = None
    archive = {
        **receipt,
        "candidate_path": output_relative.as_posix(),
        "candidate_sha256": output_sha256,
        "candidate_byte_size": len(output_bytes),
        "trace_path": trace_relative.as_posix(),
        "trace_sha256": _digest(trace_bytes),
        "prompt_path": archive_relative.as_posix(),
        "request_seal_path": _request_seal_relative(workspace, attempt).as_posix(),
        "request_seal_sha256": _digest(read_bytes(root, _request_seal_relative(workspace, attempt))),
        "capability_path": capability_path.relative_to(root).as_posix(),
        "capability_sha256": _digest(capability_bytes),
        "capability_nonce": capability["nonce"],
        "journal_path": journal_path.relative_to(root).as_posix(),
        "journal_sha256": _digest(journal_bytes),
        "journal_state": "submitted",
        "request_identity": identity,
        "prompt_sha256": _digest(request.prompt.encode("utf-8")),
        "duration_seconds": duration,
        "duration_unavailable_reason": reason,
        "status": "completed",
    }
    try:
        archive_path = atomic_write_bytes(root, archive_relative, _canonical(archive))
    except FileExistsError:
        if read_bytes(root, archive_relative) != _canonical(archive):
            raise ValueError("canonical completed attempt archive already differs")
        archive_path = root / Path(*archive_relative.parts)
    return CandidateArtifact(
        attempt=attempt, path=output, trace_path=root / Path(*trace_relative.parts),
        prompt_path=archive_path, operation=cast(Operation, request.operation),
        quality=cast(Quality, request.quality), selected_reference_ids=tuple(selected_materials),
        input_sha256s=tuple(request.input_sha256s), prompt_sha256=str(archive["prompt_sha256"]),
        request_identity=identity, duration_seconds=duration,
        duration_unavailable_reason=reason,
    )


def _load_completed_archive(
    workspace: ExperimentWorkspace,
    request: workflow_v6_image.ImageRequest,
    *,
    attempt: int,
    seal: Mapping[str, object],
) -> CandidateArtifact | None:
    root = workspace.project_copy.resolve(strict=True)
    relative = _archive_relative(workspace, attempt)
    path = root / Path(*relative.parts)
    if not path.exists():
        return None
    archive_bytes = read_bytes(root, relative)
    value = _json_object(archive_bytes, "completed attempt archive")
    output_relative = PurePosixPath(str(value.get("candidate_path")))
    trace_relative = PurePosixPath(str(value.get("trace_path")))
    capability_relative = PurePosixPath(str(value.get("capability_path")))
    expected_paths = {
        "candidate_path": PurePosixPath("04_v6", "images", f"page_{workspace.page_number:03d}.{workspace.experiment_id}.candidate_{attempt}.png").as_posix(),
        "trace_path": PurePosixPath("04_v6", "images", f"page_{workspace.page_number:03d}.{workspace.experiment_id}.candidate_{attempt}.trace.json").as_posix(),
        "prompt_path": relative.as_posix(),
        "request_seal_path": _request_seal_relative(workspace, attempt).as_posix(),
    }
    if any(value.get(key) != item for key, item in expected_paths.items()):
        raise ValueError("completed attempt archive path authority is invalid")
    receipt = _bridge_receipt(workspace, request, attempt=attempt)
    if any(value.get(key) != item for key, item in receipt.items()):
        raise ValueError("completed attempt archive request projection is invalid")
    output_bytes = read_bytes(root, output_relative)
    output_sha = _digest(output_bytes)
    if value.get("candidate_sha256") != output_sha or value.get("candidate_byte_size") != len(output_bytes):
        raise ValueError("completed attempt archive candidate binding is invalid")
    _trace, trace_bytes = _strict_trace(
        workspace, request, output=root / Path(*output_relative.parts),
        output_sha256=output_sha, trace_relative=trace_relative,
    )
    if value.get("trace_sha256") != _digest(trace_bytes):
        raise ValueError("completed attempt archive trace binding is invalid")
    capability_path = root / Path(*capability_relative.parts)
    capability, capability_bytes = _verified_capability(
        workspace, request, attempt=attempt, output_relative=output_relative,
        trace_relative=trace_relative, capability_path=capability_path,
    )
    journal, journal_path, journal_bytes = _verified_journal(workspace, capability)
    expected_archive = {
        "status": "completed",
        "attempt": attempt,
        "operation": request.operation,
        "quality": request.quality,
        "input_sha256s": list(request.input_sha256s),
        "prompt_sha256": _digest(request.prompt.encode("utf-8")),
        "request_identity": seal.get("request_identity"),
        "request_seal_sha256": _digest(read_bytes(root, _request_seal_relative(workspace, attempt))),
        "capability_sha256": _digest(capability_bytes),
        "capability_nonce": capability.get("nonce"),
        "journal_path": journal_path.relative_to(root).as_posix(),
        "journal_sha256": _digest(journal_bytes),
        "journal_state": "submitted",
    }
    if journal.get("state") != "submitted" or journal.get("outputs") != [output_sha]:
        raise ValueError("completed attempt journal authority is invalid")
    if any(value.get(key) != item for key, item in expected_archive.items()):
        raise ValueError("completed attempt archive authority is invalid")
    duration = value.get("duration_seconds")
    reason = value.get("duration_unavailable_reason")
    if duration is None:
        if not isinstance(reason, str) or not reason:
            raise ValueError("completed attempt unavailable duration reason is invalid")
    elif (
        not isinstance(duration, (int, float)) or isinstance(duration, bool)
        or duration < 0 or reason is not None
    ):
        raise ValueError("completed attempt duration authority is invalid")
    selected = value.get("selected_material_reference_ids")
    if not isinstance(selected, list):
        raise ValueError("completed attempt selected references are invalid")
    return CandidateArtifact(
        attempt=attempt, path=root / Path(*output_relative.parts),
        trace_path=root / Path(*trace_relative.parts), prompt_path=path,
        operation=cast(Operation, request.operation), quality=cast(Quality, request.quality),
        selected_reference_ids=tuple(cast(list[str], selected)),
        input_sha256s=tuple(request.input_sha256s),
        prompt_sha256=str(value["prompt_sha256"]), request_identity=str(value["request_identity"]),
        duration_seconds=None if duration is None else float(duration),
        duration_unavailable_reason=cast(str | None, reason),
    )


def run_provider_attempt(
    workspace: ExperimentWorkspace,
    request: workflow_v6_image.ImageRequest,
    *,
    attempt: int,
    timeout: int,
    recorder: EvidenceRecorder,
    runner: Callable[[list[str], int], None] = workflow_v6_image._run,
) -> CandidateArtifact:
    """Seal one bridge and invoke the unchanged signed Provider route once."""
    attempt = _strict_attempt(attempt)
    if type(timeout) is not int or timeout < 1:
        raise ValueError("timeout must be a positive integer")
    root = workspace.project_copy.resolve(strict=True)
    if request.project_root != root or request.page_number != workspace.page_number:
        raise ValueError("image request does not belong to this experiment workspace")
    if request.model != "gpt-image-2" or request.size != "1904x896":
        raise ValueError("experiment request must use gpt-image-2 at 1904x896")
    if request.quality not in {"medium", "high"}:
        raise ValueError("experiment request quality must be medium or high")
    if len(request.input_images) > _MAX_INPUTS:
        raise ValueError("Image2 accepts at most 16 total image inputs")
    seal = _load_request_seal(workspace, request, attempt=attempt)
    _validate_immediate_predecessor(workspace, seal, attempt=attempt)
    _validate_request_transport_authority(workspace, request)

    image_relative = PurePosixPath(
        "04_v6", "images", f"page_{workspace.page_number:03d}.{workspace.experiment_id}.candidate_{attempt}.png"
    )
    trace_relative = image_relative.with_suffix(".trace.json")
    output = root / Path(*image_relative.parts)
    trace = root / Path(*trace_relative.parts)
    completed = _load_completed_archive(
        workspace, request, attempt=attempt, seal=seal,
    )
    if completed is not None:
        _reconcile_provider_call(
            workspace, recorder, request, attempt=attempt, seal=seal,
            journal_state="completed_archive", duration_seconds=completed.duration_seconds,
            duration_unavailable_reason=completed.duration_unavailable_reason,
        )
        with recorder.stage("recovery", "local"):
            pass
        return completed
    if attempt > 1:
        prior = root / Path(*_archive_relative(workspace, attempt - 1).parts)
        if not prior.is_file():
            raise ValueError("preceding attempt must be completed and archived before bridge replacement")

    prompt_path, receipt = _publish_bridge(workspace, request, attempt=attempt)
    capabilities = _capability_candidates(workspace, attempt)
    if len(capabilities) > 1:
        raise RuntimeError("outcome_unknown: multiple Provider capabilities exist for this attempt")
    capability = capabilities[0] if capabilities else None
    recovering_response = False
    if capability is not None:
        capability_value, _capability_bytes = _verified_capability(
            workspace, request, attempt=attempt, output_relative=image_relative,
            trace_relative=trace_relative, capability_path=capability,
        )
        try:
            journal, _journal_path, _journal_bytes = _verified_journal(workspace, capability_value)
        except (OSError, ValueError):
            if output.exists() or trace.exists():
                raise RuntimeError(
                    "outcome_unknown: partial Provider artifacts lack a valid submission journal"
                )
            journal = None
        if journal is not None:
            state = journal.get("state")
            if state == "submitted":
                _reconcile_provider_call(
                    workspace, recorder, request, attempt=attempt, seal=seal, journal_state="submitted"
                )
                recovered = _publish_completed_archive(
                    workspace, request, attempt=attempt, seal=seal, receipt=receipt,
                    output_relative=image_relative, trace_relative=trace_relative,
                    capability_path=capability, duration=None,
                    duration_unavailable_reason="process ended before Image2 duration evidence was committed",
                )
                with recorder.stage("recovery", "local"):
                    pass
                return recovered
            if state == "response_received":
                _reconcile_provider_call(
                    workspace, recorder, request, attempt=attempt, seal=seal,
                    journal_state="response_received",
                )
                recovering_response = True
            elif state != "issued" or bool(journal.get("network_started")):
                _reconcile_provider_call(
                    workspace, recorder, request, attempt=attempt, seal=seal,
                    journal_state=str(state),
                )
                raise RuntimeError(
                    f"outcome_unknown: Provider submission journal state is {state!r}"
                )
    else:
        if output.exists() or trace.exists():
            raise RuntimeError("outcome_unknown: partial Provider artifacts have no signed capability")
        capability = workflow_v6_image._issue_capability(
            request, attempt=attempt, output=output, trace=trace
        )
    capability_value, _capability_bytes = _verified_capability(
        workspace, request, attempt=attempt, output_relative=image_relative,
        trace_relative=trace_relative, capability_path=capability,
    )
    _publish_call_state(
        workspace, request, attempt=attempt, seal=seal,
        capability_path=capability, capability=capability_value,
    )
    sealed_request = replace(request, capability_path=capability)
    command = workflow_v6_image.build_image_command(
        sealed_request, prompt_file=prompt_path, output=output, trace=trace
    )
    command.append("--allow-off-ratio-for-downstream-repair")
    start = time.monotonic()
    status = "ok"
    candidate: CandidateArtifact | None = None
    try:
        stage_name = "recovery" if recovering_response else "image2_execution"
        wait_kind = "local" if recovering_response else "image2_wait"
        with recorder.stage(stage_name, wait_kind):
            runner(command, timeout)
        if not output.is_file() or not trace.is_file():
            raise ValueError("Provider did not publish both candidate and trace")
        duration = time.monotonic() - start
        candidate = _publish_completed_archive(
            workspace, request, attempt=attempt, seal=seal, receipt=receipt,
            output_relative=image_relative, trace_relative=trace_relative,
            capability_path=capability, duration=duration,
        )
    except BaseException as exc:
        status = "error"
        duration = time.monotonic() - start
        if not recovering_response:
            try:
                journal, _path, _data = _verified_journal(workspace, capability_value)
            except (OSError, ValueError):
                journal = None
            state = str(journal.get("state")) if journal is not None else None
            if state in {"outcome_unknown", "submitting", "response_received", "submitted"}:
                _reconcile_provider_call(
                    workspace, recorder, request, attempt=attempt, seal=seal, journal_state=state,
                )
            elif not recorder.has_call(kind="image2", attempt=attempt):
                recorder.record_call(
                    kind="image2", attempt=attempt, model=request.model, effort=None,
                    operation=request.operation, duration_seconds=duration, status=status,
                    metadata={"quality": request.quality, "size": request.size,
                              "input_count": len(request.input_images),
                              "request_identity_sha256": seal["request_identity"]},
                )
        raise
    finally:
        pass
    if recovering_response:
        with recorder.stage("recovery", "local"):
            pass
    elif not recorder.has_call(kind="image2", attempt=attempt):
        recorder.record_call(
            kind="image2", attempt=attempt, model=request.model, effort=None,
            operation=request.operation, duration_seconds=time.monotonic() - start, status="ok",
            metadata={"quality": request.quality, "size": request.size,
                      "input_count": len(request.input_images),
                      "request_identity_sha256": seal["request_identity"]},
        )
    assert candidate is not None
    return candidate


__all__ = [
    "CandidateArtifact",
    "build_experiment_image_request",
    "run_provider_attempt",
]

"""Technical candidate preflight and the sole independent semantic review."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator
from PIL import Image, UnidentifiedImageError

import workflow_v6_image
from codex_subscription_runtime import CodexStructuredResult, invoke_structured
from provider_keyring import signing_key, verification_key
from workflow_v6_secure_io import atomic_write_bytes, read_bytes

from . import provider as experiment_provider
from .director import (
    DirectorArtifact,
    _director_relative,
    _validate_director_value,
    _validate_material_view,
    compile_consulting_six_part_prompt,
    validate_published_director_authority,
)
from .evidence import EvidenceRecorder
from .materials import CompletePageMaterialView
from .provider import CandidateArtifact
from .workspace import ExperimentWorkspace


SCHEMA_VERSION = "awesome-independent-visual-review-v1"
SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "schemas"
    / "complex_page_review_v1.schema.json"
)
AUTHORITY_SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "schemas"
    / "complex_page_review_authority_v1.schema.json"
)
_EXPECTED_SIZE = (1904, 896)
_MAX_CANDIDATE_BYTES = 64 * 1024 * 1024
_MAX_AUTHORITY_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class CandidatePreflight:
    passed: bool
    path: Path
    mime_type: str | None
    width: int | None
    height: int | None
    sha256: str | None
    problems: tuple[str, ...]


@dataclass(frozen=True)
class VisualReview:
    decision: Literal["accept", "correct"]
    problems: tuple[str, ...]
    model: str
    effort: str | None
    duration_seconds: float
    problem_records: tuple["ReviewProblem", ...] = ()
    authority_path: Path = Path()
    authority_sha256: str = ""


@dataclass(frozen=True)
class ReviewProblem:
    category: Literal[
        "technical_output",
        "fixed_layer_violation",
        "clear_subject_departure",
        "misleading_fabrication",
        "severe_identity_distortion",
        "core_comment_absent",
        "unusable_17_8_composition",
        "consulting_argument_failure",
        "ai_heavy_reporting_style",
        "semantic_color_misuse",
    ]
    detail: str


def _load_schema() -> dict[str, Any]:
    try:
        value = json.loads(SCHEMA.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("independent visual review schema is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("independent visual review schema must be an object")
    Draft202012Validator.check_schema(value)
    return value


def _canonical_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _workspace_identity(workspace: ExperimentWorkspace) -> str:
    return _digest(_canonical_bytes({
        "experiment_id": workspace.experiment_id,
        "source_snapshot_sha256": workspace.source_snapshot_sha256,
    }).rstrip(b"\n"))


def _review_result_relative(workspace: ExperimentWorkspace, candidate: CandidateArtifact) -> PurePosixPath:
    return _review_snapshot_root(workspace, candidate) / "review_result.json"


def _review_call_identity(candidate: CandidateArtifact, decision: str, model: str, duration: float) -> str:
    return _digest(_canonical_bytes({
        "kind": "visual_review", "attempt": candidate.attempt,
        "request_identity": candidate.request_identity, "decision": decision,
        "model": model, "duration_seconds": duration,
    }))


def _project_root_from_candidate(candidate: CandidateArtifact) -> Path:
    archive = Path(candidate.prompt_path)
    if len(archive.parents) < 4:
        raise ValueError("candidate completed attempt authority is not canonical")
    root = archive.parents[3]
    expected_parent = root / "04_v6" / "experiments"
    try:
        resolved_archive = archive.resolve(strict=True)
        resolved_parent = archive.parent.parent.resolve(strict=True)
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise ValueError("candidate completed attempt authority is unavailable") from exc
    if (
        resolved_parent != expected_parent.resolve(strict=True)
        or resolved_archive.parent.parent.parent.name != "04_v6"
        or resolved_archive != resolved_root / "04_v6" / "experiments" / archive.parent.name / archive.name
        or archive.name != f"attempt_{candidate.attempt}.json"
    ):
        raise ValueError("candidate completed attempt authority is not canonical")
    return resolved_root


def _technical_preflight(candidate: CandidateArtifact) -> CandidatePreflight:
    path = Path(candidate.path)
    problems: list[str] = []
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    sha256: str | None = None
    try:
        root = _project_root_from_candidate(candidate)
        resolved = path.resolve(strict=True)
        relative = resolved.relative_to(root)
        data = read_bytes(root, relative, max_bytes=_MAX_CANDIDATE_BYTES)
    except (OSError, ValueError):
        return CandidatePreflight(
            passed=False,
            path=path,
            mime_type=None,
            width=None,
            height=None,
            sha256=None,
            problems=("Candidate is not an existing readable regular file in the isolated project.",),
        )

    sha256 = _digest(data)
    try:
        archive_relative = candidate.prompt_path.resolve(strict=True).relative_to(root)
        archive_bytes = read_bytes(root, archive_relative, max_bytes=_MAX_AUTHORITY_BYTES)
        archive = experiment_provider._json_object(
            archive_bytes, "candidate completed attempt authority"
        )
        expected_path = relative.as_posix()
        if (
            archive.get("status") != "completed"
            or archive.get("attempt") != candidate.attempt
            or archive.get("candidate_path") != expected_path
            or archive.get("candidate_sha256") != sha256
            or archive.get("candidate_byte_size") != len(data)
            or archive.get("request_identity") != candidate.request_identity
            or archive.get("prompt_sha256") != candidate.prompt_sha256
        ):
            problems.append("Candidate bytes do not match the canonical completed attempt authority.")
    except (OSError, ValueError):
        problems.append("Candidate completed attempt authority is missing or invalid.")

    try:
        with Image.open(BytesIO(data)) as opened:
            detected = opened.format
            dimensions = opened.size
            opened.verify()
        with Image.open(BytesIO(data)) as decoded:
            decoded.load()
    except (OSError, ValueError, UnidentifiedImageError):
        problems.append("Candidate failed PNG decoding or corruption verification.")
        return CandidatePreflight(
            passed=False,
            path=path,
            mime_type=None,
            width=None,
            height=None,
            sha256=sha256,
            problems=tuple(problems),
        )

    width, height = dimensions
    if detected != "PNG":
        problems.append("Candidate is not native PNG format.")
    else:
        mime_type = "image/png"
    if dimensions != _EXPECTED_SIZE:
        problems.append("Candidate dimensions must be exactly 1904x896 pixels.")
    return CandidatePreflight(
        passed=not problems,
        path=path,
        mime_type=mime_type,
        width=width,
        height=height,
        sha256=sha256,
        problems=tuple(problems),
    )


def preflight_candidate(candidate: CandidateArtifact) -> CandidatePreflight:
    """Check readable sealed bytes, PNG decode/corruption, and exact dimensions only."""
    return _technical_preflight(candidate)


def _request_from_archive(
    workspace: ExperimentWorkspace,
    archive: Mapping[str, object],
    candidate: CandidateArtifact,
) -> workflow_v6_image.ImageRequest:
    root = workspace.project_copy.resolve(strict=True)
    raw_roles = archive.get("image_roles")
    raw_digests = archive.get("input_sha256s")
    raw_transport = archive.get("ordered_transport_input_ids")
    if not all(isinstance(item, list) for item in (raw_roles, raw_digests, raw_transport)):
        raise ValueError("candidate archive transport authority is invalid")
    trace_relative = candidate.trace_path.resolve(strict=True).relative_to(root)
    trace = experiment_provider._json_object(
        read_bytes(root, trace_relative, max_bytes=_MAX_AUTHORITY_BYTES),
        "candidate Provider trace",
    )
    trace_inputs = trace.get("input_images")
    if not isinstance(trace_inputs, list) or any(
        not isinstance(item, Mapping) or not isinstance(item.get("path"), str)
        for item in trace_inputs
    ):
        raise ValueError("candidate trace input authority is invalid")
    input_paths = tuple(Path(cast(str, item["path"])) for item in trace_inputs)
    return workflow_v6_image.ImageRequest(
        operation=cast(Literal["generate", "edit"], archive["operation"]),
        quality=cast(Literal["medium", "high"], archive["quality"]),
        prompt=str(archive["actual_prompt"]),
        input_images=input_paths,
        image_roles=tuple(str(item) for item in raw_roles),
        input_sha256s=tuple(str(item) for item in raw_digests),
        selected_reference_ids=tuple(str(item) for item in raw_transport),
        plugin_id=str(archive["plugin_id"]),
        plugin_version=str(archive["plugin_version"]),
        workflow_contract=str(archive["workflow_contract"]),
        ui_revision=int(archive["ui_revision"]),
        ui_digest=str(archive["ui_digest"]),
        page_material_digest=str(archive["page_material_digest"]),
        prompt_output_sha256=str(archive["prompt_output_sha256"]),
        source_identity=str(archive["source_identity"]),
        project_root=root,
        page_number=workspace.page_number,
    )


def _validated_candidate_authority(
    workspace: ExperimentWorkspace, candidate: CandidateArtifact
) -> tuple[dict[str, object], str]:
    root = workspace.project_copy.resolve(strict=True)
    expected_archive = (
        root
        / "04_v6"
        / "experiments"
        / workspace.experiment_id
        / f"attempt_{candidate.attempt}.json"
    )
    try:
        if candidate.prompt_path.resolve(strict=True) != expected_archive.resolve(strict=True):
            raise ValueError
        archive_relative = expected_archive.relative_to(root)
        archive = experiment_provider._json_object(
            read_bytes(root, archive_relative, max_bytes=_MAX_AUTHORITY_BYTES),
            "candidate completed attempt archive",
        )
        request = _request_from_archive(workspace, archive, candidate)
        seal = experiment_provider._load_request_seal(
            workspace, request, attempt=candidate.attempt
        )
        validated = experiment_provider._load_completed_archive(
            workspace, request, attempt=candidate.attempt, seal=seal
        )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("candidate trace/archive authority is invalid") from exc
    if validated is None or validated != candidate:
        raise ValueError("CandidateArtifact differs from canonical completed authority")

    prompt_relative = PurePosixPath(str(archive.get("prompt_output_path")))
    receipt_relative = prompt_relative.with_name(
        f"page_{workspace.page_number:03d}.receipt.json"
    )
    try:
        prompt_bytes = read_bytes(root, prompt_relative, max_bytes=_MAX_AUTHORITY_BYTES)
        prompt = prompt_bytes.decode("utf-8")
        receipt_bytes = read_bytes(root, receipt_relative, max_bytes=_MAX_AUTHORITY_BYTES)
        expected_receipt = experiment_provider._bridge_receipt(
            workspace, request, attempt=candidate.attempt
        )
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("candidate actual prompt authority is missing or invalid") from exc
    expected_receipt_bytes = experiment_provider._canonical(expected_receipt)
    if receipt_bytes != expected_receipt_bytes:
        raise ValueError("candidate actual prompt receipt bytes are stale or invalid")
    if (
        prompt != request.prompt
        or prompt != archive.get("actual_prompt")
        or _digest(prompt_bytes) != candidate.prompt_sha256
        or _digest(prompt_bytes) != archive.get("prompt_sha256")
        or _digest(prompt_bytes) != archive.get("prompt_output_sha256")
    ):
        raise ValueError("candidate actual prompt bytes are stale or invalid")
    return archive, prompt


def _validate_director(
    director: DirectorArtifact, material_view: CompletePageMaterialView
) -> None:
    selected = _validate_director_value(director.value, material_view)
    if (
        compile_consulting_six_part_prompt(director.value, material_view)
        != director.actual_prompt
        or selected != director.selected_reference_ids
        or director.value.get("quality") != director.quality
        or not isinstance(director.model, str)
        or not director.model.strip()
        or not isinstance(director.model_provider, str)
        or not director.model_provider.strip()
        or not isinstance(director.thread_id, str)
        or not director.thread_id.strip()
        or not isinstance(director.turn_id, str)
        or not director.turn_id.strip()
        or not isinstance(director.duration_seconds, (int, float))
        or isinstance(director.duration_seconds, bool)
        or not math.isfinite(float(director.duration_seconds))
        or director.duration_seconds < 0
    ):
        raise ValueError("director artifact identity is invalid")


def _review_snapshot_root(
    workspace: ExperimentWorkspace, candidate: CandidateArtifact
) -> PurePosixPath:
    return PurePosixPath(
        "04_v6", "experiments", workspace.experiment_id,
        "review_inputs", f"attempt_{candidate.attempt}",
    )


def _publish_review_snapshot(
    workspace: ExperimentWorkspace,
    material_view: CompletePageMaterialView,
    candidate: CandidateArtifact,
    image_ids: tuple[str, ...],
) -> tuple[Path, ...]:
    root = workspace.project_copy.resolve(strict=True)
    source_paths = (candidate.path, *material_view.multimodal_images)
    roles = ("candidate_under_review", *(f"page_material:{item}" for item in image_ids))
    by_id = {
        str(record["material_id"]): record
        for record in cast(list[Mapping[str, object]], material_view.value["materials"])
    }
    expected_digests = (preflight_candidate(candidate).sha256, *(str(by_id[item]["sha256"]) for item in image_ids))
    prefix = _review_snapshot_root(workspace, candidate)
    ordered: list[dict[str, object]] = []
    snapshot_paths: list[Path] = []
    for index, (source, role, expected_digest) in enumerate(
        zip(source_paths, roles, expected_digests, strict=True)
    ):
        source_relative = source.resolve(strict=True).relative_to(root)
        payload = read_bytes(root, source_relative, max_bytes=_MAX_CANDIDATE_BYTES)
        if expected_digest is None or _digest(payload) != expected_digest:
            raise ValueError("verified review input changed before snapshot publication")
        suffix = source.suffix.lower() or ".bin"
        relative = prefix / f"input_{index:02d}{suffix}"
        try:
            target = atomic_write_bytes(root, relative, payload)
        except FileExistsError:
            if read_bytes(root, relative, max_bytes=len(payload) + 1) != payload:
                raise ValueError("review input snapshot already differs")
            target = root / Path(*relative.parts)
        ordered.append(
            {
                "index": index,
                "role": role,
                "path": relative.as_posix(),
                "sha256": _digest(payload),
                "byte_size": len(payload),
            }
        )
        snapshot_paths.append(target)
    unsigned: dict[str, object] = {
        "schema_version": "awesome-review-input-snapshot-v1",
        "experiment_id": workspace.experiment_id,
        "page_number": workspace.page_number,
        "attempt": candidate.attempt,
        "candidate_request_identity": candidate.request_identity,
        "material_view_sha256": material_view.sha256,
        "ordered_inputs": ordered,
    }
    key_id, key = signing_key()
    signed = {**unsigned, "key_id": key_id}
    signed["hmac_sha256"] = hmac.new(
        key,
        json.dumps(signed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    receipt = (
        json.dumps(signed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    receipt_relative = prefix / "receipt.json"
    try:
        atomic_write_bytes(root, receipt_relative, receipt)
    except FileExistsError:
        if read_bytes(root, receipt_relative, max_bytes=len(receipt) + 1) != receipt:
            raise ValueError("review input snapshot receipt already differs")
    _validate_review_snapshot(workspace, candidate, tuple(snapshot_paths))
    return tuple(snapshot_paths)


def _validate_review_snapshot(
    workspace: ExperimentWorkspace,
    candidate: CandidateArtifact,
    paths: tuple[Path, ...],
) -> None:
    root = workspace.project_copy.resolve(strict=True)
    prefix = _review_snapshot_root(workspace, candidate)
    try:
        raw = read_bytes(root, prefix / "receipt.json", max_bytes=_MAX_AUTHORITY_BYTES)
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("review input snapshot receipt is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("review input snapshot receipt is invalid")
    signature = value.pop("hmac_sha256", None)
    key_id = value.get("key_id")
    try:
        key = verification_key(str(key_id))
    except (OSError, ValueError) as exc:
        raise ValueError("review input snapshot key is invalid") from exc
    expected = hmac.new(
        key,
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise ValueError("review input snapshot signature is invalid")
    records = value.get("ordered_inputs")
    if (
        value.get("schema_version") != "awesome-review-input-snapshot-v1"
        or value.get("experiment_id") != workspace.experiment_id
        or value.get("page_number") != workspace.page_number
        or value.get("attempt") != candidate.attempt
        or value.get("candidate_request_identity") != candidate.request_identity
        or not isinstance(records, list)
        or len(records) != len(paths)
    ):
        raise ValueError("review input snapshot identity is invalid")
    for index, (record, path) in enumerate(zip(records, paths, strict=True)):
        if not isinstance(record, dict) or record.get("index") != index:
            raise ValueError("review input snapshot order is invalid")
        relative = PurePosixPath(str(record.get("path")))
        expected_path = root / Path(*relative.parts)
        if path.resolve(strict=True) != expected_path.resolve(strict=True):
            raise ValueError("review input snapshot path is invalid")
        data = read_bytes(root, relative, max_bytes=_MAX_CANDIDATE_BYTES)
        if record.get("sha256") != _digest(data) or record.get("byte_size") != len(data):
            raise ValueError("review input snapshot bytes changed")


def _review_prompt(
    material_view: CompletePageMaterialView,
    director: DirectorArtifact,
    candidate: CandidateArtifact,
    actual_prompt: str,
    image_ids: tuple[str, ...],
) -> str:
    selected = tuple(candidate.selected_reference_ids)
    selected_set = set(selected)
    if len(selected) != len(selected_set) or any(item not in image_ids for item in selected):
        raise ValueError("candidate selected reference mapping is invalid")
    context_positions = {material_id: index + 1 for index, material_id in enumerate(image_ids)}
    selected_lines = [
        f"Selected-Reference-{index} = {material_id} = Context-Image-{context_positions[material_id]}"
        for index, material_id in enumerate(selected, start=1)
    ] or ["No real page reference was selected for this candidate."]
    context_lines = [
        f"Context-Image-{index} = {material_id} = "
        + ("SELECTED REAL REFERENCE" if material_id in selected_set else "UNSELECTED BACKGROUND AUTHORITY")
        for index, material_id in enumerate(image_ids, start=1)
    ]
    return (
        "You are the fresh independent visual reviewer and the only semantic QA after image generation. "
        "Review the actual candidate, not the director's intentions. Default to accept reasonable Image2 randomness. "
        "Return correct on ONLY these ten serious grounds: (1) damaged output or wrong size/aspect; "
        "(2) generated fixed title, fixed logo, footer, or page number; (3) clear departure from this page's subject; "
        "(4) clearly misleading fabrication; (5) severe distortion of a must-preserve real identity; "
        "(6) the core original comment direction is entirely absent; (7) the composition is plainly unusable "
        "in the 17:8 body region; (8) the page fails as one coherent body image and argument: it lacks a clear "
        "business proposition, analytical backbone, explanatory copy, evidence → interpretation → conclusion flow, "
        "or explicit takeaway, including disconnected module grids; (9) AI-heavy spectacle unsuitable for a formal "
        "report dominates the body, including decorative hero scenes, 3D machinery, miniature factories or parks, "
        "neon, cyberpunk, glowing tracks, or toy-model aesthetics; or (10) visible colors contradict the confirmed "
        "semantic color meaning in the page authority. Every correction problem must name the visible defect and a "
        "concrete repair. Harmless rendering variance, noncritical omission, incomplete verbatim text, and possible polish "
        "must be accepted. For a comment that specifically requests a real logo, person, product, project, or factual "
        "image, judge availability against all mapped Context-Images, not merely the selected references. After the "
        "completed project material search/import stage, if no corresponding mapped Context-Image exists, accept the "
        "source-exact formal-name fallback and do not classify core_comment_absent solely because that unavailable real "
        "asset is missing. Still return correct for fake, synthesized, mismatched, or severely distorted identity assets, "
        "and evaluate every other visible defect normally. Do not use numerical grading, material-by-material completeness "
        "thresholds, or a check matrix.\n\n"
        "IMAGE INPUT ORDER\n"
        "Candidate-1 = actual candidate under review\n"
        + "\n".join(context_lines)
        + "\n\nSELECTED REAL REFERENCE MAP\n"
        + "\n".join(selected_lines)
        + "\n\nCOMPLETE ORIGINAL PAGE MATERIAL VIEW (not a summary)\n"
        + _canonical_text(material_view.value)
        + "\n\nPAGE DIRECTOR MACHINE AND CREATIVE PROSE AUTHORITY\n"
        + _canonical_text(director.value)
        + "\n\nACTUAL GPT IMAGE 2 PROMPT (exact decoded UTF-8 bytes)\n"
        + "<<<ACTUAL-PROMPT>>>\n"
        + actual_prompt
        + "\n<<<END-ACTUAL-PROMPT>>>"
    )


def _validated_review(
    value: Mapping[str, object],
) -> tuple[Literal["accept", "correct"], tuple[ReviewProblem, ...]]:
    errors = sorted(
        Draft202012Validator(_load_schema()).iter_errors(dict(value)),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        path = "/".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise ValueError(f"independent visual review schema rejected {path}: {errors[0].message}")
    decision = value["decision"]
    assert decision in {"accept", "correct"}
    raw_problems = value["problems"]
    assert isinstance(raw_problems, list)
    problems = tuple(
        ReviewProblem(
            category=cast(Any, item["category"]),
            detail=str(item["detail"]),
        )
        for item in raw_problems
        if isinstance(item, Mapping)
    )
    if len(problems) != len(raw_problems) or any(
        not item.detail.strip() or item.detail != item.detail.strip()
        for item in problems
    ):
        raise ValueError("independent visual review problems must be concrete nonblank text")
    if (decision == "accept") != (not problems):
        raise ValueError("independent visual review decision and problems are inconsistent")
    return cast(Literal["accept", "correct"], decision), problems


def _result_duration(result: CodexStructuredResult) -> float:
    value = result.duration_seconds
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ValueError("independent visual review result did not record valid duration")
    return float(value)


def _review_authority_value(
    workspace: ExperimentWorkspace,
    material_view: CompletePageMaterialView,
    director: DirectorArtifact,
    candidate: CandidateArtifact,
    *,
    decision: Literal["accept", "correct"],
    problem_records: tuple[ReviewProblem, ...],
    result: CodexStructuredResult,
    duration: float,
) -> dict[str, object]:
    root = workspace.project_copy.resolve(strict=True)
    director_relative = _director_relative(workspace)
    receipt_relative = _review_snapshot_root(workspace, candidate) / "receipt.json"
    candidate_relative = candidate.path.resolve(strict=True).relative_to(root).as_posix()
    candidate_bytes = read_bytes(root, PurePosixPath(candidate_relative), max_bytes=_MAX_CANDIDATE_BYTES)
    receipt_bytes = read_bytes(root, receipt_relative, max_bytes=_MAX_AUTHORITY_BYTES)
    director_bytes = read_bytes(root, PurePosixPath(director_relative.as_posix()), max_bytes=_MAX_AUTHORITY_BYTES)
    return {
        "schema_version": "awesome-independent-visual-review-authority-v1",
        "experiment_id": workspace.experiment_id,
        "page_number": workspace.page_number,
        "source_snapshot_sha256": workspace.source_snapshot_sha256,
        "workspace_identity_sha256": _workspace_identity(workspace),
        "material_view_sha256": material_view.sha256,
        "director_authority_sha256": _digest(director_bytes),
        "candidate": {
            "attempt": candidate.attempt, "path": candidate_relative,
            "sha256": _digest(candidate_bytes), "request_identity": candidate.request_identity,
        },
        "review_input_receipt": {"path": receipt_relative.as_posix(), "sha256": _digest(receipt_bytes)},
        "decision": decision,
        "problems": [{"category": item.category, "detail": item.detail} for item in problem_records],
        "model": result.model, "model_provider": result.model_provider,
        "effort": result.effort, "usage": dict(result.usage),
        "runtime_trace": dict(result.safe_trace), "thread_id": result.thread_id,
        "turn_id": result.turn_id, "duration_seconds": duration,
        "evidence_call_identity": _review_call_identity(candidate, decision, result.model, duration),
    }


def _publish_review_authority(
    workspace: ExperimentWorkspace,
    material_view: CompletePageMaterialView,
    director: DirectorArtifact,
    candidate: CandidateArtifact,
    *,
    decision: Literal["accept", "correct"],
    problem_records: tuple[ReviewProblem, ...],
    result: CodexStructuredResult,
    duration: float,
) -> tuple[Path, str]:
    unsigned = _review_authority_value(
        workspace, material_view, director, candidate, decision=decision,
        problem_records=problem_records, result=result, duration=duration,
    )
    key_id, key = signing_key()
    signed = {**unsigned, "key_id": key_id}
    signed["hmac_sha256"] = hmac.new(key, _canonical_bytes(signed).rstrip(b"\n"), hashlib.sha256).hexdigest()
    errors = list(Draft202012Validator(json.loads(AUTHORITY_SCHEMA.read_text(encoding="utf-8"))).iter_errors(signed))
    if errors:
        raise ValueError(f"review result authority schema is invalid: {errors[0].message}")
    payload = _canonical_bytes(signed)
    relative = _review_result_relative(workspace, candidate)
    try:
        path = atomic_write_bytes(workspace.project_copy, relative, payload)
    except FileExistsError:
        if read_bytes(workspace.project_copy, relative, max_bytes=_MAX_AUTHORITY_BYTES) != payload:
            raise ValueError("published review result authority already differs")
        path = workspace.project_copy / Path(*relative.parts)
    return path, _digest(payload)


def validate_published_review_authority(
    workspace: ExperimentWorkspace,
    material_view: CompletePageMaterialView,
    director: DirectorArtifact,
    candidate: CandidateArtifact,
    review: VisualReview,
    *,
    recorder: EvidenceRecorder,
) -> Mapping[str, object]:
    """Require the in-memory review to equal its signed candidate-specific authority."""
    relative = _review_result_relative(workspace, candidate)
    try:
        payload = read_bytes(workspace.project_copy, relative, max_bytes=_MAX_AUTHORITY_BYTES)
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("published review authority is missing or invalid") from exc
    schema = json.loads(AUTHORITY_SCHEMA.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        raise ValueError("published review authority has an invalid closed shape")
    unsigned = dict(value)
    signature = unsigned.pop("hmac_sha256")
    key = verification_key(str(unsigned.get("key_id")))
    if key is None or not hmac.compare_digest(str(signature), hmac.new(key, _canonical_bytes(unsigned).rstrip(b"\n"), hashlib.sha256).hexdigest()):
        raise ValueError("published review authority signature is invalid")
    receipt = value["review_input_receipt"]
    assert isinstance(receipt, Mapping)
    receipt_relative = PurePosixPath(str(receipt["path"]))
    if _digest(read_bytes(workspace.project_copy, receipt_relative, max_bytes=_MAX_AUTHORITY_BYTES)) != receipt["sha256"]:
        raise ValueError("published review authority snapshot receipt changed")
    candidate_value = value["candidate"]
    assert isinstance(candidate_value, Mapping)
    candidate_relative = candidate.path.resolve(strict=True).relative_to(workspace.project_copy.resolve(strict=True))
    if (
        candidate_value != {"attempt": candidate.attempt, "path": candidate_relative.as_posix(),
                            "sha256": _digest(read_bytes(workspace.project_copy, PurePosixPath(candidate_relative.as_posix()), max_bytes=_MAX_CANDIDATE_BYTES)),
                            "request_identity": candidate.request_identity}
        or value["experiment_id"] != workspace.experiment_id
        or value["source_snapshot_sha256"] != workspace.source_snapshot_sha256
        or value["workspace_identity_sha256"] != recorder.workspace_identity_sha256
        or value["material_view_sha256"] != material_view.sha256
        or value["decision"] != review.decision
        or value["problems"] != [{"category": item.category, "detail": item.detail} for item in review.problem_records]
        or value["model"] != review.model or value["effort"] != review.effort
        or value["duration_seconds"] != review.duration_seconds
        or Path(review.authority_path).resolve(strict=True) != (workspace.project_copy / Path(*relative.parts)).resolve(strict=True)
        or review.authority_sha256 != _digest(payload)
    ):
        raise ValueError("passed VisualReview differs from published review authority")
    validate_published_director_authority(workspace, material_view, director)
    return cast(Mapping[str, object], value)


def load_signed_review_authority(
    workspace: ExperimentWorkspace, *, authority_path: str, authority_sha256: str,
) -> Mapping[str, object]:
    """Verify only the sealed review-result bytes; do not follow material references."""
    relative = PurePosixPath(authority_path)
    if relative.is_absolute() or "\\" in authority_path or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("sealed review authority path is invalid")
    try:
        payload = read_bytes(workspace.project_copy, relative, max_bytes=_MAX_AUTHORITY_BYTES)
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("sealed review authority is missing or invalid") from exc
    if _digest(payload) != authority_sha256:
        raise ValueError("sealed review authority digest changed")
    schema = json.loads(AUTHORITY_SCHEMA.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        raise ValueError("sealed review authority has an invalid closed shape")
    unsigned = dict(value)
    signature = unsigned.pop("hmac_sha256")
    key = verification_key(str(unsigned.get("key_id")))
    if key is None or not hmac.compare_digest(str(signature), hmac.new(key, _canonical_bytes(unsigned).rstrip(b"\n"), hashlib.sha256).hexdigest()):
        raise ValueError("sealed review authority signature is invalid")
    return cast(Mapping[str, object], value)


def review_candidate_once(
    workspace: ExperimentWorkspace,
    material_view: CompletePageMaterialView,
    director: DirectorArtifact,
    candidate: CandidateArtifact,
    preflight: CandidatePreflight,
    *,
    timeout: float,
    recorder: EvidenceRecorder,
    invoke: Callable[..., CodexStructuredResult] = invoke_structured,
) -> VisualReview:
    """Run the sole semantic review in a fresh independent Codex role."""
    current_preflight = preflight_candidate(candidate)
    if preflight != current_preflight or not current_preflight.passed:
        raise ValueError("candidate must pass the current exact technical preflight")
    image_ids = _validate_material_view(workspace, material_view)
    _validate_director(director, material_view)
    validate_published_director_authority(workspace, material_view, director)
    _archive, actual_prompt = _validated_candidate_authority(workspace, candidate)
    final_preflight = preflight_candidate(candidate)
    if final_preflight != current_preflight or not final_preflight.passed:
        raise ValueError("candidate changed after technical preflight")
    prompt = _review_prompt(
        material_view, director, candidate, actual_prompt, image_ids
    )
    images = _publish_review_snapshot(workspace, material_view, candidate, image_ids)
    start = time.monotonic()
    result: CodexStructuredResult | None = None
    try:
        with recorder.stage("visual_review", "codex_wait"):
            _validate_review_snapshot(workspace, candidate, images)
            result = invoke(
                workspace.project_copy,
                role="awesome-independent-visual-review",
                prompt=prompt,
                images=images,
                output_schema=_load_schema(),
                timeout=timeout,
            )
        decision, problem_records = _validated_review(result.value)
        problems = tuple(item.detail for item in problem_records)
        duration = _result_duration(result)
        if result.thread_id == director.thread_id:
            raise ValueError("independent visual review reused the director context")
    except BaseException:
        elapsed = time.monotonic() - start
        model = result.model if result is not None and result.model else "unavailable"
        effort = result.effort if result is not None else None
        duration = elapsed
        if result is not None and isinstance(result.duration_seconds, (int, float)):
            numeric = float(result.duration_seconds)
            if math.isfinite(numeric) and numeric >= 0:
                duration = numeric
        if not recorder.has_call(kind="visual_review", attempt=candidate.attempt):
            recorder.record_call(
                kind="visual_review",
                attempt=candidate.attempt,
                model=model,
                effort=effort,
                operation="independent_semantic_review",
                duration_seconds=duration,
                status="error",
                metadata={"result_validated": False},
            )
        raise
    authority_path, authority_sha256 = _publish_review_authority(
        workspace, material_view, director, candidate, decision=decision,
        problem_records=problem_records, result=result, duration=duration,
    )
    recorder.record_call(
        kind="visual_review",
        attempt=candidate.attempt,
        model=result.model,
        effort=result.effort,
        operation="independent_semantic_review",
        duration_seconds=duration,
        status="ok",
        metadata={"decision": decision, "problem_count": len(problems),
                  "request_identity_sha256": candidate.request_identity,
                  "review_result_sha256": authority_sha256},
    )
    return VisualReview(
        decision=decision,
        problems=problems,
        model=result.model,
        effort=result.effort,
        duration_seconds=duration,
        problem_records=problem_records,
        authority_path=authority_path,
        authority_sha256=authority_sha256,
    )


__all__ = [
    "CandidatePreflight",
    "VisualReview",
    "ReviewProblem",
    "preflight_candidate",
    "review_candidate_once",
    "validate_published_review_authority",
    "load_signed_review_authority",
]

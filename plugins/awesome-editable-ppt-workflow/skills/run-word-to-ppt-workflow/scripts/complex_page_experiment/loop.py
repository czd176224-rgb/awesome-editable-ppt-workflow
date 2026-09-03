"""Bounded candidate loop and sole accepted-image authority for page 1."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator
from PIL import Image

import workflow_v6_image
from awesome_attachment_render import _page_render_lease
from codex_subscription_runtime import CodexStructuredResult, invoke_structured
from provider_keyring import signing_key, verification_key
from workflow_v6_contract import transition_page, validate_project
from workflow_v6_secure_io import atomic_write_bytes, read_bytes
from workflow_v6_state import mutation_lock, save

from .director import DirectorArtifact, direct_page
from .evidence import EvidenceRecorder, RECOVERY_CALLS
from .materials import CompletePageMaterialView, build_complete_page_material_view
from .provider import (
    CandidateArtifact,
    build_experiment_image_request,
    run_provider_attempt,
)
from .review import (
    ReviewProblem, VisualReview, load_signed_review_authority, preflight_candidate,
    review_candidate_once, validate_published_review_authority,
)
from .workspace import ExperimentWorkspace, verify_source_unchanged


SCHEMA = Path(__file__).resolve().parents[2] / "schemas" / "complex_page_acceptance_v1.schema.json"
EXPERIMENT_RECEIPT = "accepted_image.json"
FAILED_RECEIPT = "failed_outcome.json"
_EXACT_TEXT_REPAIR = re.compile(
    r"将[^“\"]{0,24}[“\"](?P<find>[^”\"]{1,100})[”\"]"
    r"[^。；]{0,48}?(?:修正|改为|更正)[^“\"]{0,12}[“\"]"
    r"(?P<replace>[^”\"]{1,100})[”\"]"
)


def _canonical_receipt(workspace: ExperimentWorkspace) -> PurePosixPath:
    return PurePosixPath("04_v6", "images", f"page_{workspace.page_number:03d}.json")


@dataclass(frozen=True)
class AcceptedImageSeal:
    receipt_path: Path
    candidate: CandidateArtifact
    receipt_sha256: str
    recovered: bool


@dataclass(frozen=True)
class LoopOutcome:
    status: Literal["accepted", "failed"]
    attempts: tuple[CandidateArtifact, ...]
    accepted: AcceptedImageSeal | None
    failure_problems: tuple[str, ...]
    correction_count: int


def _canonical(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(data: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} is invalid")
    return value


def _schema() -> dict[str, Any]:
    value = _json(SCHEMA.read_bytes(), "acceptance schema")
    return cast(dict[str, Any], value)


def _experiment_relative(workspace: ExperimentWorkspace) -> PurePosixPath:
    return PurePosixPath("04_v6", "experiments", workspace.experiment_id, EXPERIMENT_RECEIPT)


def _safe_relative(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or "\\" in value:
        raise ValueError(f"accepted {label} path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts) or str(path) != value or ":" in path.parts[0]:
        raise ValueError(f"accepted {label} path is invalid")
    return path


def _copied_state_without_material_reads(workspace: ExperimentWorkspace) -> dict[str, Any]:
    value = _json(read_bytes(workspace.project_copy, "workflow_v6.json", max_bytes=4 * 1024 * 1024), "copied workflow state")
    validate_project(value)
    return cast(dict[str, Any], value)


def verify_signed_acceptance_receipt(workspace: ExperimentWorkspace, data: bytes) -> dict[str, object]:
    value = _json(data, "accepted-image receipt")
    errors = sorted(Draft202012Validator(_schema()).iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        raise ValueError(f"accepted-image receipt schema is invalid: {errors[0].message}")
    unsigned = dict(value)
    signature = str(unsigned.pop("hmac_sha256"))
    key = verification_key(str(unsigned.get("key_id")))
    if key is None or not hmac.compare_digest(signature, hmac.new(key, _canonical(unsigned).rstrip(b"\n"), hashlib.sha256).hexdigest()):
        raise ValueError("accepted-image receipt signature is invalid")
    if value["experiment_id"] != workspace.experiment_id or value["page_number"] != workspace.page_number or value["source_snapshot_sha256"] != workspace.source_snapshot_sha256:
        raise ValueError("accepted-image receipt identity is invalid")
    return value


def _candidate_from_receipt(workspace: ExperimentWorkspace, value: Mapping[str, object]) -> CandidateArtifact:
    item = cast(Mapping[str, object], value["candidate"])
    path_rel = _safe_relative(item["path"], "candidate")
    trace_rel = _safe_relative(item["trace_path"], "trace")
    prompt_rel = _safe_relative(item["prompt_path"], "prompt")
    image = read_bytes(workspace.project_copy, path_rel)
    if _digest(image) != item["sha256"] or len(image) != item["byte_size"]:
        raise ValueError("accepted image bytes do not match the seal")
    try:
        with Image.open(workspace.project_copy / Path(*path_rel.parts)) as opened:
            opened.load()
            if opened.format != "PNG" or opened.size != (1904, 896):
                raise ValueError
    except Exception as exc:
        raise ValueError("accepted image is damaged or not 1904x896 PNG") from exc
    authority = cast(Mapping[str, object], value["provider_authority"])
    for name in ("trace", "capability", "journal"):
        relative = _safe_relative(authority[f"{name}_path"], name)
        if _digest(read_bytes(workspace.project_copy, relative, max_bytes=4 * 1024 * 1024)) != authority[f"{name}_sha256"]:
            raise ValueError(f"accepted Provider {name} authority changed")
    if authority["trace_path"] != item["trace_path"] or authority["request_identity"] != item["request_identity"]:
        raise ValueError("accepted Provider authority identity is inconsistent")
    return CandidateArtifact(
        attempt=cast(int, item["attempt"]),
        path=workspace.project_copy / Path(*path_rel.parts),
        trace_path=workspace.project_copy / Path(*trace_rel.parts),
        prompt_path=workspace.project_copy / Path(*prompt_rel.parts),
        operation=cast(Any, item["operation"]), quality=cast(Any, item["quality"]),
        selected_reference_ids=tuple(cast(Sequence[str], item["selected_reference_ids"])),
        input_sha256s=tuple(cast(Sequence[str], item["input_sha256s"])),
        prompt_sha256=str(item["prompt_sha256"]), request_identity=str(item["request_identity"]),
        duration_seconds=cast(float | None, item["duration_seconds"]),
        duration_unavailable_reason=cast(str | None, item["duration_unavailable_reason"]),
    )


def _failed_relative(workspace: ExperimentWorkspace) -> PurePosixPath:
    return PurePosixPath("04_v6", "experiments", workspace.experiment_id, FAILED_RECEIPT)


def _failure_candidate_value(workspace: ExperimentWorkspace, candidate: CandidateArtifact) -> dict[str, object]:
    path = candidate.path.resolve(strict=True).relative_to(workspace.project_copy.resolve(strict=True))
    payload = read_bytes(workspace.project_copy, PurePosixPath(path.as_posix()), max_bytes=64 * 1024 * 1024)
    return {
        "attempt": candidate.attempt, "path": path.as_posix(), "sha256": _digest(payload),
        "trace_path": candidate.trace_path.relative_to(workspace.project_copy).as_posix(),
        "prompt_path": candidate.prompt_path.relative_to(workspace.project_copy).as_posix(),
        "operation": candidate.operation, "quality": candidate.quality,
        "selected_reference_ids": list(candidate.selected_reference_ids),
        "input_sha256s": list(candidate.input_sha256s), "prompt_sha256": candidate.prompt_sha256,
        "request_identity": candidate.request_identity, "duration_seconds": candidate.duration_seconds,
        "duration_unavailable_reason": candidate.duration_unavailable_reason,
    }


def _failure_candidate(workspace: ExperimentWorkspace, item: Mapping[str, object]) -> CandidateArtifact:
    expected_keys = {
        "attempt", "path", "sha256", "trace_path", "prompt_path", "operation", "quality",
        "selected_reference_ids", "input_sha256s", "prompt_sha256", "request_identity",
        "duration_seconds", "duration_unavailable_reason",
    }
    if set(item) != expected_keys:
        raise ValueError("failed outcome candidate shape is invalid")
    path = _safe_relative(item["path"], "failed candidate")
    payload = read_bytes(workspace.project_copy, path, max_bytes=64 * 1024 * 1024)
    if _digest(payload) != item["sha256"]:
        raise ValueError("failed outcome candidate bytes changed")
    return CandidateArtifact(
        attempt=cast(int, item["attempt"]), path=workspace.project_copy / Path(*path.parts),
        trace_path=workspace.project_copy / Path(*_safe_relative(item["trace_path"], "failed trace").parts),
        prompt_path=workspace.project_copy / Path(*_safe_relative(item["prompt_path"], "failed prompt").parts),
        operation=cast(Any, item["operation"]), quality=cast(Any, item["quality"]),
        selected_reference_ids=tuple(cast(Sequence[str], item["selected_reference_ids"])),
        input_sha256s=tuple(cast(Sequence[str], item["input_sha256s"])),
        prompt_sha256=str(item["prompt_sha256"]), request_identity=str(item["request_identity"]),
        duration_seconds=cast(float | None, item["duration_seconds"]),
        duration_unavailable_reason=cast(str | None, item["duration_unavailable_reason"]),
    )


def _publish_failed_outcome(
    workspace: ExperimentWorkspace, *, attempts: Sequence[CandidateArtifact],
    problems: Sequence[str], correction_count: int, recorder: EvidenceRecorder,
) -> LoopOutcome:
    raw = read_bytes(workspace.project_copy, PurePosixPath("04_v6", "experiments", workspace.experiment_id, "evidence.jsonl"), max_bytes=64 * 1024)
    value: dict[str, object] = {
        "schema_version": "awesome-complex-page-failed-outcome-v1", "status": "failed",
        "experiment_id": workspace.experiment_id, "page_number": workspace.page_number,
        "source_snapshot_sha256": workspace.source_snapshot_sha256,
        "workspace_identity_sha256": recorder.workspace_identity_sha256,
        "attempts": [_failure_candidate_value(workspace, item) for item in attempts],
        "failure_problems": list(problems), "correction_count": correction_count,
        "evidence_checkpoint": {"event_count": len(raw.splitlines()), "sha256": _digest(raw)},
    }
    key_id, key = signing_key(); value["key_id"] = key_id
    value["hmac_sha256"] = hmac.new(key, _canonical(value).rstrip(b"\n"), hashlib.sha256).hexdigest()
    _publish_same_or_new(workspace.project_copy, _failed_relative(workspace), _canonical(value))
    return LoopOutcome("failed", tuple(attempts), None, tuple(problems), correction_count)


def _recover_existing_attempts(workspace: ExperimentWorkspace) -> tuple[CandidateArtifact, ...]:
    recovered: list[CandidateArtifact] = []
    root = workspace.project_copy
    for attempt in range(1, 4):
        relative = PurePosixPath("04_v6", "experiments", workspace.experiment_id, f"attempt_{attempt}.json")
        if not (root / Path(*relative.parts)).exists():
            break
        value = _json(read_bytes(root, relative, max_bytes=4 * 1024 * 1024), "interrupted candidate archive")
        item = {
            "attempt": attempt, "path": value["candidate_path"], "sha256": value["candidate_sha256"],
            "trace_path": value["trace_path"], "prompt_path": value["prompt_path"],
            "operation": value["operation"], "quality": value["quality"],
            "selected_reference_ids": value["selected_material_reference_ids"],
            "input_sha256s": value["input_sha256s"], "prompt_sha256": value["prompt_sha256"],
            "request_identity": value["request_identity"], "duration_seconds": value["duration_seconds"],
            "duration_unavailable_reason": value["duration_unavailable_reason"],
        }
        recovered.append(_failure_candidate(workspace, item))
    return tuple(recovered)


def _publish_interrupted_outcome(workspace: ExperimentWorkspace, recorder: EvidenceRecorder) -> LoopOutcome:
    attempts = _recover_existing_attempts(workspace)
    problems = ("interrupted prior candidate run; automatic resubmission blocked",)
    if attempts:
        return _publish_failed_outcome(
            workspace, attempts=attempts, problems=problems,
            correction_count=max(0, len(attempts) - 1), recorder=recorder,
        )
    raw = read_bytes(workspace.project_copy, PurePosixPath("04_v6", "experiments", workspace.experiment_id, "evidence.jsonl"), max_bytes=64 * 1024)
    value: dict[str, object] = {
        "schema_version": "awesome-complex-page-failed-outcome-v1", "status": "failed",
        "experiment_id": workspace.experiment_id, "page_number": workspace.page_number,
        "source_snapshot_sha256": workspace.source_snapshot_sha256,
        "workspace_identity_sha256": recorder.workspace_identity_sha256,
        "attempts": [], "failure_problems": list(problems), "correction_count": 0,
        "evidence_checkpoint": {"event_count": len(raw.splitlines()), "sha256": _digest(raw)},
    }
    key_id, key = signing_key(); value["key_id"] = key_id
    value["hmac_sha256"] = hmac.new(key, _canonical(value).rstrip(b"\n"), hashlib.sha256).hexdigest()
    _publish_same_or_new(workspace.project_copy, _failed_relative(workspace), _canonical(value))
    return LoopOutcome("failed", (), None, problems, 0)


def _load_failed_outcome(workspace: ExperimentWorkspace, recorder: EvidenceRecorder) -> LoopOutcome | None:
    relative = _failed_relative(workspace); path = workspace.project_copy / Path(*relative.parts)
    if not path.exists():
        return None
    value = _json(read_bytes(workspace.project_copy, relative, max_bytes=4 * 1024 * 1024), "failed outcome")
    keys = {"schema_version", "status", "experiment_id", "page_number", "source_snapshot_sha256",
            "workspace_identity_sha256", "attempts", "failure_problems", "correction_count",
            "evidence_checkpoint", "key_id", "hmac_sha256"}
    if set(value) != keys or value.get("schema_version") != "awesome-complex-page-failed-outcome-v1" or value.get("status") != "failed":
        raise ValueError("failed outcome receipt shape is invalid")
    unsigned = dict(value); signature = str(unsigned.pop("hmac_sha256")); key = verification_key(str(unsigned.get("key_id")))
    if key is None or not hmac.compare_digest(signature, hmac.new(key, _canonical(unsigned).rstrip(b"\n"), hashlib.sha256).hexdigest()):
        raise ValueError("failed outcome receipt signature is invalid")
    if value["experiment_id"] != workspace.experiment_id or value["page_number"] != workspace.page_number or value["source_snapshot_sha256"] != workspace.source_snapshot_sha256 or value["workspace_identity_sha256"] != recorder.workspace_identity_sha256:
        raise ValueError("failed outcome receipt identity is invalid")
    checkpoint = cast(Mapping[str, object], value["evidence_checkpoint"])
    if set(checkpoint) != {"event_count", "sha256"}:
        raise ValueError("failed outcome evidence checkpoint is invalid")
    raw = read_bytes(workspace.project_copy, PurePosixPath("04_v6", "experiments", workspace.experiment_id, "evidence.jsonl"), max_bytes=64 * 1024)
    if checkpoint["event_count"] != len(raw.splitlines()) or checkpoint["sha256"] != _digest(raw):
        raise ValueError("failed outcome evidence changed")
    attempts_raw = value["attempts"]
    problems = value["failure_problems"]
    corrections = value["correction_count"]
    if not isinstance(attempts_raw, list) or len(attempts_raw) > 3 or any(not isinstance(item, Mapping) for item in attempts_raw) or not isinstance(problems, list) or not problems or any(not isinstance(item, str) or not item for item in problems) or type(corrections) is not int or corrections < 0 or corrections > 2:
        raise ValueError("failed outcome receipt content is invalid")
    attempts = tuple(_failure_candidate(workspace, cast(Mapping[str, object], item)) for item in attempts_raw)
    return LoopOutcome("failed", attempts, None, tuple(problems), corrections)


def _validate_sealed_checkpoint(workspace: ExperimentWorkspace, value: Mapping[str, object]) -> None:
    checkpoint = cast(Mapping[str, object], value["evidence_checkpoint"])
    candidate = cast(Mapping[str, object], value["candidate"])
    accepted_review = cast(Mapping[str, object], value["accepted_review"])
    review_path = str(accepted_review["authority_path"])
    review_sha = str(accepted_review["authority_sha256"])
    authority = load_signed_review_authority(
        workspace, authority_path=review_path, authority_sha256=review_sha,
    )
    authority_candidate = cast(Mapping[str, object], authority["candidate"])
    if (
        authority["decision"] != "accept" or authority["problems"] != []
        or authority_candidate["attempt"] != candidate["attempt"]
        or authority_candidate["path"] != candidate["path"]
        or authority_candidate["sha256"] != candidate["sha256"]
        or authority_candidate["request_identity"] != candidate["request_identity"]
        or authority["model"] != accepted_review["model"]
        or authority["effort"] != accepted_review["effort"]
        or authority["duration_seconds"] != accepted_review["duration_seconds"]
        or checkpoint["selected_attempt"] != candidate["attempt"]
        or checkpoint["candidate_sha256"] != candidate["sha256"]
        or checkpoint["request_identity"] != candidate["request_identity"]
        or checkpoint["review_authority_sha256"] != review_sha
        or checkpoint["experiment_id"] != workspace.experiment_id
        or checkpoint["source_snapshot_sha256"] != workspace.source_snapshot_sha256
    ):
        raise ValueError("sealed review/evidence checkpoint does not match accepted candidate")
    unsigned_checkpoint = dict(checkpoint)
    checkpoint_digest = unsigned_checkpoint.pop("checkpoint_sha256", None)
    if checkpoint_digest != _digest(json.dumps(unsigned_checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")):
        raise ValueError("sealed evidence checkpoint digest is invalid")
    relative = PurePosixPath("04_v6", "experiments", workspace.experiment_id, "evidence.jsonl")
    raw = read_bytes(workspace.project_copy, relative, max_bytes=64 * 1024)
    count = cast(int, checkpoint["event_count"])
    lines = raw.splitlines(keepends=True)
    if count > len(lines) or _digest(b"".join(lines[:count])) != checkpoint["evidence_prefix_sha256"]:
        raise ValueError("sealed evidence checkpoint prefix changed")
    for line in lines[count:]:
        event = _json(line, "post-acceptance evidence event")
        if event.get("event") != "recovery" or event.get("skipped_calls") != list(RECOVERY_CALLS):
            raise ValueError("sealed evidence checkpoint has a later workflow event")


def load_accepted_image_seal(workspace: ExperimentWorkspace) -> AcceptedImageSeal | None:
    """Verify the copied page-1 seal without consulting any material-chain file."""
    root = workspace.project_copy
    experiment_relative = _experiment_relative(workspace)
    experiment_path = root / Path(*experiment_relative.parts)
    canonical_receipt = _canonical_receipt(workspace)
    canonical_path = root / Path(*canonical_receipt.parts)
    present = (experiment_path.exists(), canonical_path.exists())
    if present == (False, False):
        state = _copied_state_without_material_reads(workspace)
        page = state["pages"][workspace.page_number - 1]
        if page.get("state") == "accepted" or page.get("selected_candidate") is not None:
            raise ValueError("copied accepted state is missing both accepted-image receipts")
        return None
    if present == (True, False):
        experiment_bytes = read_bytes(root, experiment_relative, max_bytes=4 * 1024 * 1024)
        verify_signed_acceptance_receipt(workspace, experiment_bytes)
        atomic_write_bytes(root, canonical_receipt, experiment_bytes)
    elif present == (False, True):
        canonical_bytes = read_bytes(root, canonical_receipt, max_bytes=4 * 1024 * 1024)
        verify_signed_acceptance_receipt(workspace, canonical_bytes)
        atomic_write_bytes(root, experiment_relative, canonical_bytes)
    experiment_bytes = read_bytes(root, experiment_relative, max_bytes=4 * 1024 * 1024)
    canonical_bytes = read_bytes(root, canonical_receipt, max_bytes=4 * 1024 * 1024)
    if experiment_bytes != canonical_bytes:
        raise ValueError("accepted-image receipts do not match")
    value = verify_signed_acceptance_receipt(workspace, experiment_bytes)
    _validate_sealed_checkpoint(workspace, value)
    state = _copied_state_without_material_reads(workspace)
    page = state["pages"][workspace.page_number - 1]
    candidate_value = cast(Mapping[str, object], value["candidate"])
    selected = page.get("selected_candidate")
    if state["source_identity"] != value["source_identity"] or state["confirmed_ui_revision"] != value["ui_revision"] or state["confirmed_ui_digest"] != value["ui_digest"] or state["geometry"] != value["fixed_frame"]:
        raise ValueError("copied workflow identity does not match the accepted-image seal")
    state_candidate = {
        "path": candidate_value["path"], "sha256": candidate_value["sha256"],
        "attempt": candidate_value["attempt"], "receipt_path": canonical_receipt.as_posix(),
    }
    if page.get("state") != "accepted":
        _transition_copied_page(workspace, state_candidate, cast(int, candidate_value["attempt"]))
        state = _copied_state_without_material_reads(workspace)
        page = state["pages"][workspace.page_number - 1]
        selected = page.get("selected_candidate")
    if not isinstance(selected, Mapping) or dict(selected) != state_candidate:
        raise ValueError("copied accepted state does not match the accepted-image seal")
    candidate = _candidate_from_receipt(workspace, value)
    return AcceptedImageSeal(experiment_path, candidate, _digest(experiment_bytes), True)


def _attempt_archive(workspace: ExperimentWorkspace, candidate: CandidateArtifact) -> dict[str, object]:
    relative = candidate.prompt_path.resolve(strict=True).relative_to(workspace.project_copy.resolve(strict=True))
    return _json(read_bytes(workspace.project_copy, relative, max_bytes=4 * 1024 * 1024), "candidate attempt archive")


def _history_digest(workspace: ExperimentWorkspace, attempts: Sequence[CandidateArtifact], *, prompt: bool) -> str:
    history: list[object] = []
    for candidate in attempts:
        archive = _attempt_archive(workspace, candidate)
        if prompt:
            history.append({"attempt": candidate.attempt, "prompt_sha256": candidate.prompt_sha256, "request_identity": candidate.request_identity})
        else:
            history.append({"attempt": candidate.attempt, "candidate_path": archive["candidate_path"], "candidate_sha256": archive["candidate_sha256"], "request_identity": candidate.request_identity})
    return _digest(_canonical({"history": history}))


def _repair_value(problem: ReviewProblem) -> dict[str, str]:
    value = {"category": problem.category, "detail": problem.detail}
    match = _EXACT_TEXT_REPAIR.search(problem.detail)
    if match and match.group("find") != match.group("replace"):
        value.update(find=match.group("find"), replace=match.group("replace"))
    return value


def _acceptance_value(workspace: ExperimentWorkspace, material_view: CompletePageMaterialView, candidate: CandidateArtifact, review: VisualReview, director: DirectorArtifact, attempts: Sequence[CandidateArtifact], recorder: EvidenceRecorder, reconstruction_repairs: Sequence[ReviewProblem]) -> dict[str, object]:
    if review.decision != "accept" or review.problems or sum(item == candidate for item in attempts) != 1:
        raise ValueError("only one exactly selected accepted candidate may be sealed")
    archive = _attempt_archive(workspace, candidate)
    state = _copied_state_without_material_reads(workspace)
    image = read_bytes(workspace.project_copy, candidate.path.relative_to(workspace.project_copy))
    review_authority = validate_published_review_authority(
        workspace, material_view, director, candidate, review, recorder=recorder,
    )
    candidate_value = {
        "attempt": candidate.attempt, "path": candidate.path.relative_to(workspace.project_copy).as_posix(),
        "sha256": _digest(image), "byte_size": len(image), "width": 1904, "height": 896,
        "trace_path": candidate.trace_path.relative_to(workspace.project_copy).as_posix(),
        "prompt_path": candidate.prompt_path.relative_to(workspace.project_copy).as_posix(),
        "operation": candidate.operation, "quality": candidate.quality,
        "selected_reference_ids": list(candidate.selected_reference_ids),
        "input_sha256s": list(candidate.input_sha256s), "prompt_sha256": candidate.prompt_sha256,
        "request_identity": candidate.request_identity, "duration_seconds": candidate.duration_seconds,
        "duration_unavailable_reason": candidate.duration_unavailable_reason,
    }
    value: dict[str, object] = {
        "schema_version": "awesome-complex-page-acceptance-v1", "status": "accepted",
        "experiment_id": workspace.experiment_id, "page_number": workspace.page_number,
        "source_snapshot_sha256": workspace.source_snapshot_sha256,
        "source_identity": archive["source_identity"], "ui_revision": archive["ui_revision"],
        "ui_digest": archive["ui_digest"], "material_view_sha256": archive["page_material_digest"],
        "page_plan": director.page_plan,
        "candidate": candidate_value,
        "candidate_history_sha256": _history_digest(workspace, attempts, prompt=False),
        "prompt_history_sha256": _history_digest(workspace, attempts, prompt=True),
        "selected_real_reference_ids": list(candidate.selected_reference_ids),
        "reconstruction_repairs": [
            _repair_value(item)
            for item in reconstruction_repairs
        ],
        "accepted_review": {"decision": "accept", "problems": [], "model": review.model, "effort": review.effort, "duration_seconds": review.duration_seconds,
                            "authority_path": review.authority_path.relative_to(workspace.project_copy).as_posix(),
                            "authority_sha256": review.authority_sha256},
        "provider_authority": {
            "trace_path": archive["trace_path"], "trace_sha256": archive["trace_sha256"],
            "capability_path": archive["capability_path"], "capability_sha256": archive["capability_sha256"],
            "capability_nonce": archive["capability_nonce"], "journal_path": archive["journal_path"],
            "journal_sha256": archive["journal_sha256"], "request_identity": archive["request_identity"],
        },
        "fixed_frame": state["geometry"],
        "evidence_checkpoint": recorder.acceptance_checkpoint(
            attempt=candidate.attempt, candidate_sha256=candidate_value["sha256"],
            request_identity=candidate.request_identity,
            review_authority_sha256=review.authority_sha256,
        ),
    }
    key_id, key = signing_key()
    value["key_id"] = key_id
    value["hmac_sha256"] = hmac.new(key, _canonical(value).rstrip(b"\n"), hashlib.sha256).hexdigest()
    verify_signed_acceptance_receipt(workspace, _canonical(value))
    return value


def _publish_same_or_new(root: Path, relative: PurePosixPath, payload: bytes) -> Path:
    try:
        return atomic_write_bytes(root, relative, payload)
    except FileExistsError:
        if read_bytes(root, relative, max_bytes=4 * 1024 * 1024) != payload:
            raise ValueError("a different accepted-image seal already exists")
        return root / Path(*relative.parts)


def _transition_copied_page(workspace: ExperimentWorkspace, candidate_value: Mapping[str, object], attempt_count: int) -> None:
    with mutation_lock(workspace.project_copy):
        state = _copied_state_without_material_reads(workspace)
        page = state["pages"][workspace.page_number - 1]
        if page["state"] in {"accepted", "page_complete"}:
            if page["selected_candidate"] != candidate_value:
                raise ValueError("copied page already accepted a different image")
            return
        if page["state"] != "prepared":
            raise ValueError("copied page state is not eligible for experiment acceptance")
        page = transition_page(page, "generating")
        page["first_candidate"] = dict(candidate_value)
        page = transition_page(page, "qa_review")
        page["qa_attempts"] = attempt_count
        page = transition_page(page, "accepted")
        page["selected_candidate"] = dict(candidate_value)
        state["pages"][workspace.page_number - 1] = page
        save(workspace.project_copy, state)


def seal_accepted_image(workspace: ExperimentWorkspace, *, material_view: CompletePageMaterialView, candidate: CandidateArtifact, review: VisualReview, director: DirectorArtifact, attempts: Sequence[CandidateArtifact], recorder: EvidenceRecorder, reconstruction_repairs: Sequence[ReviewProblem] = ()) -> AcceptedImageSeal:
    """Publish and verify the sole signed accepted-image authority."""
    value = _acceptance_value(workspace, material_view, candidate, review, director, attempts, recorder, reconstruction_repairs)
    payload = _canonical(value)
    experiment_relative = _experiment_relative(workspace)
    experiment_path = _publish_same_or_new(workspace.project_copy, experiment_relative, payload)
    canonical_receipt = _canonical_receipt(workspace)
    _publish_same_or_new(workspace.project_copy, canonical_receipt, payload)
    verify_signed_acceptance_receipt(workspace, read_bytes(workspace.project_copy, experiment_relative))
    verify_signed_acceptance_receipt(workspace, read_bytes(workspace.project_copy, canonical_receipt))
    candidate_value = cast(Mapping[str, object], value["candidate"])
    state_candidate = {"path": candidate_value["path"], "sha256": candidate_value["sha256"], "attempt": candidate_value["attempt"], "receipt_path": canonical_receipt.as_posix()}
    _transition_copied_page(workspace, state_candidate, len(attempts))
    loaded = load_accepted_image_seal(workspace)
    assert loaded is not None
    return AcceptedImageSeal(experiment_path, loaded.candidate, loaded.receipt_sha256, False)


def _record_director(recorder: EvidenceRecorder, director: DirectorArtifact) -> None:
    recorder.record_call(kind="page_director", attempt=1, model=director.model, effort=director.effort, operation="page_creative_direction", duration_seconds=director.duration_seconds, status="ok", metadata={"selected_reference_count": len(director.selected_reference_ids), "quality": director.quality})


def _record_local_correction(
    recorder: EvidenceRecorder, attempt: int,
) -> None:
    recorder.record_call(
        kind="correction_decision",
        attempt=attempt,
        model="deterministic-local",
        effort=None,
        operation="edit_previous",
        duration_seconds=0.0,
        status="ok",
        metadata={"problem_count": 1, "quota_bearing": False},
    )


def _local_correction(
    review: VisualReview,
    director: DirectorArtifact,
    *,
    next_attempt: int,
) -> tuple[str, tuple[str, ...], Literal["edit_previous"]]:
    if len(review.problem_records) != 1 or review.problems != (
        review.problem_records[0].detail,
    ):
        raise ValueError("local correction requires exactly one signed review problem")
    problem = review.problem_records[0]
    if not problem.detail.strip():
        raise ValueError("local correction requires a concrete visible defect")
    if not director.page_plan:
        raise ValueError("local correction requires the frozen director page plan")
    prompt = (
        f"VISIBLE DEFECT\n{problem.category}: {problem.detail.strip()}\n\n"
        f"REQUIRED REPAIR\nCorrection attempt {next_attempt}: repair exactly this visible defect "
        "on the previous candidate; make no other change.\n\n"
        "MUST STAY UNCHANGED\nPreserve the accepted composition, every already-correct region, "
        "the frozen page plan, all complete facts, the confirmed colors, and the fixed title, "
        "logo, footer, and page-number boundaries."
    )
    return prompt, (), "edit_previous"


def _run_candidate_loop_owned(workspace: ExperimentWorkspace, *, timeout: int, recorder: EvidenceRecorder, material_view_factory: Callable[[ExperimentWorkspace], CompletePageMaterialView], director_invoke: Callable[..., CodexStructuredResult], reviewer_invoke: Callable[..., CodexStructuredResult], provider_runner: Callable[[list[str], int], None], max_corrections: int) -> LoopOutcome:
    """Generate one candidate by default and at most two problem-specific corrections."""
    verify_source_unchanged(workspace)
    if type(max_corrections) is not int or not 0 <= max_corrections <= 2:
        raise ValueError("max_corrections must be an integer from 0 through 2")
    accepted = load_accepted_image_seal(workspace)
    if accepted is not None:
        recorder.record_recovery(skipped_calls=RECOVERY_CALLS)
        return LoopOutcome("accepted", (accepted.candidate,), accepted, (), 0)
    director_root = workspace.project_copy / "02_v6" / "experiments" / workspace.experiment_id
    legacy_director = director_root / "director.json"
    v2_director = director_root / "director_v2.json"
    if legacy_director.exists() and not v2_director.exists():
        raise ValueError(
            "unfinished v1 page cannot reuse its legacy director or candidates; "
            "restart this page from the consulting director v2 in a fresh page run"
        )
    failed = _load_failed_outcome(workspace, recorder)
    if failed is not None:
        return failed
    if recorder.durable_call_count():
        return _publish_interrupted_outcome(workspace, recorder)
    attempts: list[CandidateArtifact] = []
    corrections = 0
    last_problems: tuple[str, ...] = ()
    reconstruction_repairs: list[ReviewProblem] = []
    try:
        material_view = material_view_factory(workspace)
        verify_source_unchanged(workspace)
        director = direct_page(workspace, material_view, timeout=timeout, invoke=director_invoke)
        _record_director(recorder, director)
        prompt = director.actual_prompt
        selected = director.selected_reference_ids
        strategy: Literal["initial", "edit_previous"] = "initial"
        while True:
            attempt = len(attempts) + 1
            previous = attempts[-1] if attempts else None
            request = build_experiment_image_request(workspace, material_view, attempt=attempt, prompt=prompt, quality=director.quality, selected_reference_ids=selected, strategy=strategy, previous_candidate=previous)
            verify_source_unchanged(workspace)
            candidate = run_provider_attempt(workspace, request, attempt=attempt, timeout=timeout, recorder=recorder, runner=provider_runner)
            attempts.append(candidate)
            preflight = preflight_candidate(candidate)
            recorder.record_candidate_preflight(
                attempt=attempt,
                candidate_sha256=preflight.sha256 or hashlib.sha256(b"").hexdigest(),
                request_identity=candidate.request_identity,
                passed=preflight.passed,
                problems=preflight.problems,
            )
            if preflight.passed:
                verify_source_unchanged(workspace)
                review = review_candidate_once(workspace, material_view, director, candidate, preflight, timeout=timeout, recorder=recorder, invoke=reviewer_invoke)
                if review.decision == "accept":
                    seal = seal_accepted_image(workspace, material_view=material_view, candidate=candidate, review=review, director=director, attempts=attempts, recorder=recorder, reconstruction_repairs=reconstruction_repairs)
                    return LoopOutcome("accepted", tuple(attempts), seal, (), corrections)
                last_problems = review.problems
                if corrections >= max_corrections:
                    return _publish_failed_outcome(
                        workspace, attempts=attempts, problems=last_problems,
                        correction_count=corrections, recorder=recorder,
                    )
                verify_source_unchanged(workspace)
                prompt, selected, strategy = _local_correction(
                    review, director, next_attempt=attempt + 1,
                )
                _record_local_correction(recorder, attempt)
            else:
                last_problems = preflight.problems
                if corrections >= max_corrections:
                    return _publish_failed_outcome(
                        workspace, attempts=attempts, problems=last_problems,
                        correction_count=corrections, recorder=recorder,
                    )
                prompt = (
                    f"TECHNICAL DEFECT\n{'; '.join(last_problems)}\n\n"
                    f"REQUIRED REPAIR\nCorrection attempt {attempt + 1}: repair exactly this "
                    "technical defect on the previous candidate; make no other change.\n\n"
                    "MUST STAY UNCHANGED\nPreserve the accepted composition, every correct region, "
                    "the frozen page plan, all complete facts, the confirmed colors, and the fixed "
                    "title, logo, footer, and page-number boundaries."
                )
                selected = ()
                strategy = "edit_previous"
            corrections += 1
    finally:
        verify_source_unchanged(workspace)


def run_candidate_loop(workspace: ExperimentWorkspace, *, timeout: int, recorder: EvidenceRecorder, material_view_factory: Callable[[ExperimentWorkspace], CompletePageMaterialView] = build_complete_page_material_view, director_invoke: Callable[..., CodexStructuredResult] = invoke_structured, reviewer_invoke: Callable[..., CodexStructuredResult] = invoke_structured, provider_runner: Callable[[list[str], int], None] = workflow_v6_image._run, max_corrections: int = 2) -> LoopOutcome:
    """Single-flight one complete page transaction, including accepted recovery."""
    wait_timeout = max(float(timeout) * 20.0, 30.0)
    with _page_render_lease(workspace.project_copy, workspace.page_number, timeout=wait_timeout):
        recorder.refresh_from_disk()
        return _run_candidate_loop_owned(
            workspace, timeout=timeout, recorder=recorder,
            material_view_factory=material_view_factory,
            director_invoke=director_invoke, reviewer_invoke=reviewer_invoke,
            provider_runner=provider_runner, max_corrections=max_corrections,
        )


__all__ = ["AcceptedImageSeal", "LoopOutcome", "load_accepted_image_seal", "run_candidate_loop", "seal_accepted_image"]

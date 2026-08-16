from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from complex_page_experiment.evidence import (
    EvidenceRecorder,
    _safe_experiment_id,
    sample_resources,
)


SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "schemas"
    / "complex_page_evidence_v1.schema.json"
)


class FakeClock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


class ResourceSamples:
    def __init__(self, *samples: dict[str, int]) -> None:
        self._samples = iter(samples)

    def __call__(self, _project_copy: Path) -> dict[str, int]:
        return next(self._samples)


def _recorder(
    tmp_path: Path,
    *,
    clock: FakeClock | None = None,
    samples: ResourceSamples | None = None,
) -> EvidenceRecorder:
    project = tmp_path / "project"
    (tmp_path / "source_snapshot.json").write_text(
        json.dumps({
            "experiment_id": "evidence-unit",
            "page_number": 1,
            "source_snapshot_sha256": "a" * 64,
        }),
        encoding="utf-8",
    )
    root = project / "04_v6" / "experiments" / "evidence-unit"
    root.mkdir(parents=True)
    return EvidenceRecorder(
        root,
        project_copy=project,
        experiment_id="evidence-unit",
        clock=clock or FakeClock(1.0, 2.0),
        resource_sampler=samples
        or ResourceSamples(
            {
                "rss_bytes": 100,
                "handle_count": 4,
                "active_external_calls": 0,
                "temp_file_count": 1,
            },
            {
                "rss_bytes": 100,
                "handle_count": 4,
                "active_external_calls": 0,
                "temp_file_count": 1,
            },
        ),
    )


def _jsonl(root: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (root / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def _passed_preflight(recorder: EvidenceRecorder, attempt: int) -> None:
    recorder.record_candidate_preflight(
        attempt=attempt,
        candidate_sha256=f"{attempt}" * 64,
        request_identity=f"{attempt + 3}" * 64,
        passed=True,
        problems=(),
    )


def test_stage_records_exact_local_and_external_durations_and_resource_peaks(
    tmp_path: Path,
) -> None:
    recorder = _recorder(
        tmp_path,
        clock=FakeClock(10.0, 12.5, 20.0, 24.0),
        samples=ResourceSamples(
            {
                "rss_bytes": 100,
                "handle_count": 4,
                "active_external_calls": 0,
                "temp_file_count": 1,
            },
            {
                "rss_bytes": 140,
                "handle_count": 5,
                "active_external_calls": 0,
                "temp_file_count": 2,
            },
            {
                "rss_bytes": 130,
                "handle_count": 5,
                "active_external_calls": 1,
                "temp_file_count": 1,
            },
            {
                "rss_bytes": 120,
                "handle_count": 3,
                "active_external_calls": 1,
                "temp_file_count": 1,
            },
        ),
    )

    with recorder.stage("material_preparation", "local"):
        pass
    with recorder.stage("image2_execution", "image2_wait"):
        pass

    summary = recorder.finalize()
    stages = {event["name"]: event for event in summary["stages"]}
    assert stages["material_preparation"] == {
        "event": "stage",
        "experiment_id": "evidence-unit",
        "workspace_identity_sha256": recorder.workspace_identity_sha256,
        "source_snapshot_sha256": "a" * 64,
        "page_number": 1,
        "name": "material_preparation",
        "kind": "local",
        "start_seconds": 10.0,
        "end_seconds": 12.5,
        "duration_seconds": 2.5,
        "local_duration_seconds": 2.5,
        "external_wait_seconds": 0.0,
        "unavailable_reason": None,
        "status": "ok",
        "resource_start": {
            "rss_bytes": 100,
            "handle_count": 4,
            "active_external_calls": 0,
            "temp_file_count": 1,
        },
        "resource_end": {
            "rss_bytes": 140,
            "handle_count": 5,
            "active_external_calls": 0,
            "temp_file_count": 2,
        },
    }
    assert stages["image2_execution"]["duration_seconds"] == 4.0
    assert stages["image2_execution"]["local_duration_seconds"] == 0.0
    assert stages["image2_execution"]["external_wait_seconds"] == 4.0
    assert summary["duration_totals"] == {
        "local_duration_seconds": 2.5,
        "external_wait_seconds": 4.0,
        "reconstruction_duration_seconds": 0.0,
    }
    assert summary["resource_peaks"] == {
        "rss_bytes": 140,
        "handle_count": 5,
        "active_external_calls": 1,
        "temp_file_count": 2,
    }
    assert all(
        stage["duration_seconds"]
        == stage["local_duration_seconds"] + stage["external_wait_seconds"]
        for stage in summary["stages"]
    )


def test_calls_are_individual_and_image2_totals_remain_separate(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_call(
        kind="page_director",
        attempt=1,
        model="gpt-5.6-sol",
        effort="high",
        operation="direct",
        duration_seconds=2.0,
        status="ok",
        metadata={"request_identity": "a" * 64},
    )
    for attempt in (1, 2, 3):
        recorder.record_call(
            kind="image2",
            attempt=attempt,
            model="gpt-image-2",
            effort=None,
            operation="generate" if attempt == 1 else "edit",
            duration_seconds=float(attempt),
            status="ok",
            metadata={"quality": "high", "selected_reference_count": 2},
        )
        _passed_preflight(recorder, attempt)
        recorder.record_call(
            kind="visual_review", attempt=attempt, model="gpt-5.6-sol", effort="high",
            operation="review", duration_seconds=1.0, status="ok", metadata={},
        )
        if attempt < 3:
            recorder.record_call(
                kind="correction_decision", attempt=attempt, model="gpt-5.6-sol", effort="high",
                operation="decide", duration_seconds=1.0, status="ok", metadata={},
            )
    recorder.record_call(
        kind="reconstruct_edit",
        attempt=1,
        model="gpt-image-2",
        effort=None,
        operation="edit",
        duration_seconds=5.0,
        status="ok",
        metadata={"reason": "complex visual asset cannot be separated locally"},
    )

    summary = recorder.finalize()
    assert summary["call_totals"] == {
        "page_director": 1,
        "correction_decision": 2,
        "image2": 3,
        "visual_review": 3,
        "reconstruct_edit": 1,
    }
    assert summary["image2_total_calls"] == 3
    assert summary["reconstruct_image2_total_calls"] == 1
    assert [call["attempt"] for call in summary["calls"] if call["kind"] == "image2"] == [1, 2, 3]
    assert len(_jsonl(recorder.experiment_root)) == 13

    with pytest.raises(ValueError, match="call budget"):
        recorder.record_call(
            kind="image2",
            attempt=3,
            model="gpt-image-2",
            effort=None,
            operation="edit",
            duration_seconds=1.0,
            status="ok",
            metadata={},
        )


def test_image2_attempts_must_be_contiguous_from_candidate_one(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    with pytest.raises(ValueError, match="candidate"):
        recorder.record_call(
            kind="image2",
            attempt=2,
            model="gpt-image-2",
            effort=None,
            operation="generate",
            duration_seconds=1.0,
            status="ok",
            metadata={},
        )
    assert not (recorder.experiment_root / "evidence.jsonl").exists()


@pytest.mark.parametrize(
    "metadata",
    [
        {"nested": [{"accessToken": "secret"}]},
        {"nested": [{"Authorization": "Bearer secret"}]},
        {"safe": {"deeper": {"capability_hmac": "secret"}}},
        {"safe": [{"inlineImage": "data:image/png;base64,AAAA"}]},
        {"safe": {"document_bytes": "AAAA"}},
        {"safe": {"bytes_b64": "AAAA"}},
        {"safe": {"request_payload": {"prompt": "private document"}}},
        {"safe": {"prompt": "private user source text"}},
        {"safe": {"raw": b"not-json-and-not-safe"}},
        {"safe": "Bearer abcdefghijklmnopqrstuvwxyz"},
    ],
)
def test_recursive_secret_like_metadata_is_rejected(
    tmp_path: Path, metadata: dict[str, object]
) -> None:
    recorder = _recorder(tmp_path)
    with pytest.raises(ValueError, match="metadata"):
        recorder.record_call(
            kind="visual_review",
            attempt=1,
            model="gpt-5.6-sol",
            effort="high",
            operation="review",
            duration_seconds=1.0,
            status="ok",
            metadata=metadata,
        )
    assert not (recorder.experiment_root / "evidence.jsonl").exists()


def test_metadata_is_bounded_and_nonfinite_durations_are_rejected(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    with pytest.raises(ValueError, match="metadata"):
        recorder.record_call(
            kind="visual_review",
            attempt=1,
            model="gpt-5.6-sol",
            effort="high",
            operation="review",
            duration_seconds=1.0,
            status="ok",
            metadata={"notes": "x" * 513},
        )
    with pytest.raises(ValueError, match="duration_seconds"):
        recorder.record_call(
            kind="visual_review",
            attempt=1,
            model="gpt-5.6-sol",
            effort="high",
            operation="review",
            duration_seconds=float("nan"),
            status="ok",
            metadata={},
        )


def test_recorded_metadata_is_detached_from_caller_mutation(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    metadata: dict[str, object] = {"detail": {"quality": "high"}}
    recorder.record_call(
        kind="image2", attempt=1, model="gpt-image-2", effort=None,
        operation="generate", duration_seconds=1.0, status="ok", metadata={},
    )
    _passed_preflight(recorder, 1)
    recorder.record_call(
        kind="visual_review",
        attempt=1,
        model="gpt-5.6-sol",
        effort="high",
        operation="review",
        duration_seconds=1.0,
        status="ok",
        metadata=metadata,
    )
    metadata["detail"]["accessToken"] = "late-secret"
    summary = recorder.finalize()
    assert summary["calls"][1]["metadata"] == {"detail": {"quality": "high"}}


@pytest.mark.parametrize(
    ("kind", "maximum"),
    [
        ("page_director", 1),
        ("correction_decision", 2),
        ("visual_review", 3),
        ("reconstruct_edit", 3),
    ],
)
def test_each_quota_call_stream_is_bounded(
    tmp_path: Path, kind: str, maximum: int
) -> None:
    recorder = _recorder(tmp_path)
    for attempt in range(1, maximum + 1):
        if kind in {"correction_decision", "visual_review"}:
            recorder.record_call(
                kind="image2",
                attempt=attempt,
                model="gpt-image-2",
                effort=None,
                operation="generate" if attempt == 1 else "edit",
                duration_seconds=1.0,
                status="ok",
                metadata={},
            )
            _passed_preflight(recorder, attempt)
            recorder.record_call(
                kind="visual_review", attempt=attempt, model="gpt-5.6-sol", effort="high",
                operation="review", duration_seconds=1.0, status="ok", metadata={},
            )
            if kind == "visual_review":
                if attempt < maximum:
                    recorder.record_call(
                        kind="correction_decision", attempt=attempt, model="gpt-5.6-sol", effort="high",
                        operation="decide", duration_seconds=1.0, status="ok", metadata={},
                    )
                continue
        recorder.record_call(
            kind=kind,
            attempt=attempt,
            model="gpt-image-2" if kind == "reconstruct_edit" else "gpt-5.6-sol",
            effort=None if kind == "reconstruct_edit" else "high",
            operation="edit" if kind == "reconstruct_edit" else "model_call",
            duration_seconds=1.0,
            status="ok",
            metadata={},
        )
    with pytest.raises(ValueError, match="call budget"):
        recorder.record_call(
            kind=kind,
            attempt=maximum,
            model="gpt-image-2" if kind == "reconstruct_edit" else "gpt-5.6-sol",
            effort=None if kind == "reconstruct_edit" else "high",
            operation="edit" if kind == "reconstruct_edit" else "model_call",
            duration_seconds=1.0,
            status="ok",
            metadata={},
        )


def test_cache_and_zero_call_recovery_are_explicit(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_attachment_cache(hits=7, misses=0)
    recorder.record_recovery(
        skipped_calls=("page_director", "correction_decision", "image2", "visual_review", "reconstruct_edit")
    )
    summary = recorder.finalize()
    assert summary["attachment_cache"] == {"hits": 7, "misses": 0}
    assert summary["recovery"] == {
        "events": 1,
        "skipped_calls": [
            "page_director",
            "correction_decision",
            "image2",
            "visual_review",
            "reconstruct_edit",
        ],
    }
    assert summary["call_totals"]["page_director"] == 0
    assert summary["call_totals"]["image2"] == 0
    assert summary["call_totals"]["visual_review"] == 0
    assert summary["call_totals"]["reconstruct_edit"] == 0

    with pytest.raises(ValueError, match="misses=0"):
        recorder.record_attachment_cache(hits=6, misses=1)


def test_recovery_requires_complete_zero_call_set_and_blocks_later_calls(
    tmp_path: Path,
) -> None:
    recorder = _recorder(tmp_path)
    with pytest.raises(ValueError, match="complete approved"):
        recorder.record_recovery(skipped_calls=("page_director", "image2"))
    recorder.record_recovery(
        skipped_calls=(
            "page_director",
            "correction_decision",
            "image2",
            "visual_review",
            "reconstruct_edit",
        )
    )
    with pytest.raises(ValueError, match="after recovery"):
        recorder.record_call(
            kind="image2",
            attempt=1,
            model="gpt-image-2",
            effort=None,
            operation="generate",
            duration_seconds=1.0,
            status="ok",
            metadata={},
        )


def test_recovery_cannot_claim_a_call_was_skipped_after_it_was_recorded(
    tmp_path: Path,
) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_call(
        kind="page_director",
        attempt=1,
        model="gpt-5.6-sol",
        effort="high",
        operation="direct",
        duration_seconds=1.0,
        status="ok",
        metadata={},
    )
    with pytest.raises(ValueError, match="zero-call recovery"):
        recorder.record_recovery(
            skipped_calls=(
                "page_director",
                "correction_decision",
                "image2",
                "visual_review",
                "reconstruct_edit",
            )
        )


def test_reopened_recorder_appends_zero_call_recovery_without_losing_history(
    tmp_path: Path,
) -> None:
    recorder = _recorder(tmp_path)
    for kind, model, operation in (
        ("page_director", "gpt-5.6-sol", "direct"),
        ("image2", "gpt-image-2", "generate"),
        ("visual_review", "gpt-5.6-sol", "review"),
    ):
        recorder.record_call(
            kind=kind,
            attempt=1,
            model=model,
            effort=None if kind == "image2" else "high",
            operation=operation,
            duration_seconds=1.0,
            status="ok",
            metadata={},
        )
        if kind == "image2":
            _passed_preflight(recorder, 1)
    recorder.finalize()
    evidence = recorder.experiment_root / "evidence.jsonl"
    identity = evidence.stat().st_ino

    resumed = EvidenceRecorder(
        recorder.experiment_root,
        project_copy=recorder.project_copy,
        experiment_id="evidence-unit",
    )
    resumed.record_recovery(
        skipped_calls=("page_director", "correction_decision", "image2", "visual_review", "reconstruct_edit")
    )
    summary = resumed.finalize()

    assert evidence.stat().st_ino == identity
    assert summary["call_totals"]["page_director"] == 1
    assert summary["call_totals"]["image2"] == 1
    assert summary["call_totals"]["visual_review"] == 1
    assert summary["call_totals"]["reconstruct_edit"] == 0
    assert summary["recovery"]["events"] == 1
    assert summary["event_count"] == 5


def test_resume_rejects_tampered_existing_evidence(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_attachment_cache(hits=1, misses=0)
    recorder.finalize()
    evidence = recorder.experiment_root / "evidence.jsonl"
    event = json.loads(evidence.read_text(encoding="utf-8"))
    event["hits"] = 2
    evidence.write_bytes(
        json.dumps(
            event,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )

    with pytest.raises(ValueError, match="summary|modified|integrity"):
        EvidenceRecorder(
            recorder.experiment_root,
            project_copy=recorder.project_copy,
            experiment_id="evidence-unit",
        )


def test_resume_rejects_tampered_summary(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_attachment_cache(hits=1, misses=0)
    recorder.finalize()
    summary_path = recorder.experiment_root / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["evidence_sha256"] = "0" * 64
    summary_path.write_bytes(
        json.dumps(
            summary,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    with pytest.raises(ValueError, match="summary"):
        EvidenceRecorder(
            recorder.experiment_root,
            project_copy=recorder.project_copy,
            experiment_id="evidence-unit",
        )


def test_restart_accepts_valid_tail_after_old_summary_and_rejects_tampered_tail(
    tmp_path: Path,
) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_attachment_cache(hits=1, misses=0)
    recorder.finalize()
    evidence = recorder.experiment_root / "evidence.jsonl"
    with evidence.open("ab") as handle:
        handle.write(
            json.dumps(
                {
                    "event": "recovery",
                    "experiment_id": "evidence-unit",
                    "workspace_identity_sha256": recorder.workspace_identity_sha256,
                    "source_snapshot_sha256": "a" * 64,
                    "page_number": 1,
                    "skipped_calls": [
                        "page_director",
                        "correction_decision",
                        "image2",
                        "visual_review",
                        "reconstruct_edit",
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
    resumed = EvidenceRecorder(
        recorder.experiment_root,
        project_copy=recorder.project_copy,
        experiment_id="evidence-unit",
    )
    assert resumed.finalize()["recovery"]["events"] == 1

    evidence.write_bytes(evidence.read_bytes() + b'{"event":"recovery"}\n')
    with pytest.raises(ValueError, match="tail|recovery|evidence"):
        EvidenceRecorder(
            recorder.experiment_root,
            project_copy=recorder.project_copy,
            experiment_id="evidence-unit",
        )


def test_candidate_call_streams_share_contiguous_attempts(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_call(
        kind="image2", attempt=1, model="gpt-image-2", effort=None,
        operation="generate", duration_seconds=1.0, status="ok", metadata={},
    )
    _passed_preflight(recorder, 1)
    recorder.record_call(
        kind="visual_review", attempt=1, model="gpt-5.6-sol", effort="high",
        operation="review", duration_seconds=1.0, status="ok", metadata={},
    )
    with pytest.raises(ValueError, match="attempt|candidate"):
        recorder.record_call(
            kind="visual_review", attempt=3, model="gpt-5.6-sol", effort="high",
            operation="review", duration_seconds=1.0, status="ok", metadata={},
        )
    with pytest.raises(ValueError, match="candidate|Image2"):
        recorder.record_call(
            kind="visual_review", attempt=2, model="gpt-5.6-sol", effort="high",
            operation="review", duration_seconds=1.0, status="ok", metadata={},
        )
    recorder.record_call(
        kind="correction_decision", attempt=1, model="gpt-5.6-sol", effort="high",
        operation="decide", duration_seconds=1.0, status="ok", metadata={},
    )
    with pytest.raises(ValueError, match="attempt|candidate|preflight"):
        recorder.record_call(
            kind="correction_decision", attempt=3, model="gpt-5.6-sol", effort="high",
            operation="decide", duration_seconds=1.0, status="ok", metadata={},
        )


def test_rehydration_rejects_noncontiguous_candidate_stream(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_call(
        kind="image2", attempt=1, model="gpt-image-2", effort=None,
        operation="generate", duration_seconds=1.0, status="ok", metadata={},
    )
    recorder.finalize()
    evidence = recorder.experiment_root / "evidence.jsonl"
    invalid = {
        "event": "call",
        "experiment_id": "evidence-unit",
        "workspace_identity_sha256": recorder.workspace_identity_sha256,
        "source_snapshot_sha256": "a" * 64,
        "page_number": 1,
        "kind": "visual_review",
        "attempt": 2,
        "model": "gpt-5.6-sol",
        "effort": "high",
        "operation": "review",
        "duration_seconds": 1.0,
        "status": "ok",
        "metadata": {},
    }
    evidence.write_bytes(
        evidence.read_bytes()
        + json.dumps(invalid, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )
    with pytest.raises(ValueError, match="attempt|candidate"):
        EvidenceRecorder(
            recorder.experiment_root,
            project_copy=recorder.project_copy,
            experiment_id="evidence-unit",
        )


def test_unavailable_wait_uses_null_and_a_nonempty_reason(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_unavailable_stage(
        "image2_queue",
        "image2_wait",
        unavailable_reason="frozen Provider boundary exposes only total execution time",
    )
    summary = recorder.finalize()
    stage = summary["stages"][0]
    assert stage["start_seconds"] is None
    assert stage["end_seconds"] is None
    assert stage["duration_seconds"] is None
    assert stage["local_duration_seconds"] is None
    assert stage["external_wait_seconds"] is None
    assert stage["unavailable_reason"] == (
        "frozen Provider boundary exposes only total execution time"
    )


def test_reconstruction_duration_is_local_and_separately_aggregated(
    tmp_path: Path,
) -> None:
    recorder = _recorder(
        tmp_path,
        clock=FakeClock(4.0, 7.0),
    )
    with recorder.stage("reconstruction", "reconstruction"):
        pass
    summary = recorder.finalize()
    stage = summary["stages"][0]
    assert stage["duration_seconds"] == 3.0
    assert stage["local_duration_seconds"] == 3.0
    assert stage["external_wait_seconds"] == 0.0
    assert summary["duration_totals"]["reconstruction_duration_seconds"] == 3.0


def test_external_stage_samples_one_active_external_call(tmp_path: Path) -> None:
    project = tmp_path / "project"
    root = project / "04_v6" / "experiments" / "external-unit"
    root.mkdir(parents=True)
    (tmp_path / "source_snapshot.json").write_text(
        json.dumps({
            "experiment_id": "external-unit",
            "page_number": 1,
            "source_snapshot_sha256": "b" * 64,
        }),
        encoding="utf-8",
    )
    recorder = EvidenceRecorder(
        root,
        project_copy=project,
        experiment_id="external-unit",
        clock=FakeClock(1.0, 2.0),
    )
    with recorder.stage("page_director", "codex_wait"):
        pass
    summary = recorder.finalize()
    assert summary["resource_peaks"]["active_external_calls"] == 1


def test_only_explicit_stages_and_the_workspace_page_are_accepted(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    with pytest.raises(ValueError, match="stage name"):
        with recorder.stage("made_up_stage", "local"):
            pass
    with pytest.raises(ValueError, match="does not match"):
        with recorder.stage("recovery", "local", page_number=2):
            pass


def test_jsonl_is_canonical_bounded_and_summary_validates(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_attachment_cache(hits=1, misses=0)
    first_identity = (recorder.experiment_root / "evidence.jsonl").stat().st_ino
    recorder.record_recovery(skipped_calls=("page_director", "correction_decision", "image2", "visual_review", "reconstruct_edit"))
    assert (recorder.experiment_root / "evidence.jsonl").stat().st_ino == first_identity
    summary = recorder.finalize()

    lines = (recorder.experiment_root / "evidence.jsonl").read_bytes().splitlines()
    assert lines
    for line in lines:
        parsed = json.loads(line)
        assert line == json.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    assert summary["event_count"] == len(lines)
    assert summary["event_count"] <= 64
    published = json.loads((recorder.experiment_root / "summary.json").read_text("utf-8"))
    assert published == summary
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(published)


def test_resource_peaks_never_decrease_across_stages(tmp_path: Path) -> None:
    recorder = _recorder(
        tmp_path,
        clock=FakeClock(1.0, 2.0, 3.0, 4.0),
        samples=ResourceSamples(
            {"rss_bytes": 50, "handle_count": 2, "active_external_calls": 0, "temp_file_count": 1},
            {"rss_bytes": 90, "handle_count": 8, "active_external_calls": 0, "temp_file_count": 3},
            {"rss_bytes": 10, "handle_count": 1, "active_external_calls": 0, "temp_file_count": 0},
            {"rss_bytes": 20, "handle_count": 2, "active_external_calls": 0, "temp_file_count": 1},
        ),
    )
    with recorder.stage("material_preparation", "local"):
        pass
    first_peaks = dict(recorder.resource_peaks)
    with recorder.stage("fixed_layer_assembly", "local"):
        pass
    assert recorder.resource_peaks == first_peaks


def test_sample_resources_has_the_stable_integer_contract(tmp_path: Path) -> None:
    (tmp_path / ".candidate.tmp").write_bytes(b"temporary")
    sample = sample_resources(tmp_path)
    assert set(sample) == {
        "rss_bytes",
        "handle_count",
        "active_external_calls",
        "temp_file_count",
    }
    assert all(type(value) is int and value >= 0 for value in sample.values())
    assert sample["rss_bytes"] > 0
    assert sample["handle_count"] > 0
    assert sample["temp_file_count"] == 1


def test_recorder_requires_canonical_workspace_experiment_layout(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError, match="04_v6/experiments"):
        EvidenceRecorder(outside, project_copy=project, experiment_id="outside")

    wrong = project / "04_v6" / "experiments" / "actual"
    wrong.mkdir(parents=True)
    with pytest.raises(ValueError, match="experiment_id"):
        EvidenceRecorder(wrong, project_copy=project, experiment_id="claimed")


def test_restart_counts_uncheckpointed_tail_calls_as_current_session_calls(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_attachment_cache(hits=1, misses=0)
    recorder.finalize()
    interrupted = EvidenceRecorder(
        recorder.experiment_root, project_copy=recorder.project_copy,
        experiment_id="evidence-unit",
    )
    interrupted.record_call(
        kind="image2", attempt=1, model="gpt-image-2", effort=None,
        operation="generate", duration_seconds=1.0, status="ok", metadata={},
    )
    restarted = EvidenceRecorder(
        recorder.experiment_root, project_copy=recorder.project_copy,
        experiment_id="evidence-unit",
    )
    with pytest.raises(ValueError, match="zero-call recovery"):
        restarted.record_recovery(
            skipped_calls=("page_director", "correction_decision", "image2", "visual_review", "reconstruct_edit")
        )


def test_restart_rejects_a_tail_that_claims_recovery_after_a_tail_call(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_attachment_cache(hits=1, misses=0)
    recorder.finalize()
    interrupted = EvidenceRecorder(
        recorder.experiment_root, project_copy=recorder.project_copy,
        experiment_id="evidence-unit",
    )
    interrupted.record_call(
        kind="image2", attempt=1, model="gpt-image-2", effort=None,
        operation="generate", duration_seconds=1.0, status="ok", metadata={},
    )
    recovery = {
        "event": "recovery", "experiment_id": "evidence-unit",
        "workspace_identity_sha256": recorder.workspace_identity_sha256,
        "source_snapshot_sha256": "a" * 64, "page_number": 1,
        "skipped_calls": ["page_director", "correction_decision", "image2", "visual_review", "reconstruct_edit"],
    }
    with (recorder.experiment_root / "evidence.jsonl").open("ab") as handle:
        handle.write(json.dumps(recovery, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    with pytest.raises(ValueError, match="zero-call recovery"):
        EvidenceRecorder(
            recorder.experiment_root, project_copy=recorder.project_copy,
            experiment_id="evidence-unit",
        )


@pytest.mark.parametrize(
    "experiment_id",
    [
        ".", "..", "../escape", "nested/name", r"nested\name", "CON", "Lpt1",
        "NUL.txt", "COM1.foo", "bad:id", "experiment.", "experiment ", "NUL.", "COM1 ",
    ],
)
def test_experiment_id_must_be_one_safe_nonreserved_component(
    tmp_path: Path, experiment_id: str
) -> None:
    project = tmp_path / "project"
    root = tmp_path / "root"
    project.mkdir()
    root.mkdir()
    with pytest.raises(ValueError, match="experiment_id"):
        EvidenceRecorder(root, project_copy=project, experiment_id=experiment_id)


def test_windows_alias_experiment_id_cannot_collide_with_canonical_component(
    tmp_path: Path,
) -> None:
    canonical = "experiment"
    alias = "experiment."
    assert canonical.rstrip(" .").casefold() == alias.rstrip(" .").casefold()
    project = tmp_path / "project"
    root = project / "04_v6" / "experiments" / alias
    root.mkdir(parents=True)
    (tmp_path / "source_snapshot.json").write_text(
        json.dumps({"experiment_id": alias, "source_snapshot_sha256": "c" * 64}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="experiment_id"):
        EvidenceRecorder(root, project_copy=project, experiment_id=alias)


@pytest.mark.parametrize(
    ("experiment_id", "valid"),
    [
        ("CON", False),
        ("Lpt1", False),
        ("NUL.txt", False),
        ("COM1.foo", False),
        ("evidence-unit", True),
        ("experiment.v2", True),
        ("company", True),
        ("com10.foo", True),
    ],
)
def test_schema_and_runtime_share_safe_experiment_id_rules(
    experiment_id: str, valid: bool
) -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    identifier_schema = schema["$defs"]["safeExperimentId"]
    schema_accepts = Draft202012Validator(identifier_schema).is_valid(experiment_id)
    try:
        _safe_experiment_id(experiment_id)
    except ValueError:
        runtime_accepts = False
    else:
        runtime_accepts = True
    assert schema_accepts is valid
    assert runtime_accepts is valid


def test_copying_evidence_bundle_to_another_experiment_identity_is_rejected(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_attachment_cache(hits=1, misses=0)
    recorder.finalize()
    copied_root = recorder.project_copy / "04_v6" / "experiments" / "copied-unit"
    copied_root.mkdir(parents=True)
    shutil.copy2(recorder.experiment_root / "evidence.jsonl", copied_root / "evidence.jsonl")
    shutil.copy2(recorder.experiment_root / "summary.json", copied_root / "summary.json")
    with pytest.raises(ValueError, match="identity|experiment_id"):
        EvidenceRecorder(copied_root, project_copy=recorder.project_copy, experiment_id="copied-unit")


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("call_totals", {"page_director": 0, "correction_decision": 0, "image2": 1, "visual_review": 0, "reconstruct_edit": 0}),
        ("duration_totals", {"local_duration_seconds": 1.0, "external_wait_seconds": 0.0, "reconstruction_duration_seconds": 0.0}),
        ("resource_peaks", {"rss_bytes": 999, "handle_count": 0, "active_external_calls": 0, "temp_file_count": 0}),
        ("recovery", {"events": 1, "skipped_calls": ["page_director"]}),
    ],
)
def test_resume_exactly_reaggregates_checkpoint_summary_fields(
    tmp_path: Path, field: str, replacement: object
) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_attachment_cache(hits=1, misses=0)
    recorder.finalize()
    summary_path = recorder.experiment_root / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary[field] = replacement
    summary_path.write_text(
        json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="aggregate|summary"):
        EvidenceRecorder(
            recorder.experiment_root, project_copy=recorder.project_copy,
            experiment_id="evidence-unit",
        )


def test_live_candidate_calls_enforce_review_and_correction_causality(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_call(
        kind="image2", attempt=1, model="gpt-image-2", effort=None,
        operation="generate", duration_seconds=1.0, status="ok", metadata={},
    )
    recorder.record_candidate_preflight(
        attempt=1, candidate_sha256="b" * 64, request_identity="c" * 64,
        passed=True, problems=(),
    )
    with pytest.raises(ValueError, match="review"):
        recorder.record_call(
            kind="correction_decision", attempt=1, model="gpt-5.6-sol", effort="high",
            operation="decide", duration_seconds=1.0, status="ok", metadata={},
        )
    with pytest.raises(ValueError, match="preflight|causal|review|correction"):
        recorder.record_call(
            kind="image2", attempt=2, model="gpt-image-2", effort=None,
            operation="edit", duration_seconds=1.0, status="ok", metadata={},
        )
    recorder.record_call(
        kind="visual_review", attempt=1, model="gpt-5.6-sol", effort="high",
        operation="review", duration_seconds=1.0, status="ok", metadata={},
    )
    recorder.record_call(
        kind="correction_decision", attempt=1, model="gpt-5.6-sol", effort="high",
        operation="decide", duration_seconds=1.0, status="ok", metadata={},
    )
    recorder.record_call(
        kind="image2", attempt=2, model="gpt-image-2", effort=None,
        operation="edit", duration_seconds=1.0, status="ok", metadata={},
    )


def test_technical_preflight_failure_is_the_only_reviewless_regeneration_path(
    tmp_path: Path,
) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_call(
        kind="image2", attempt=1, model="gpt-image-2", effort=None,
        operation="generate", duration_seconds=1.0, status="ok",
        metadata={"request_identity_sha256": "c" * 64},
    )
    recorder.record_candidate_preflight(
        attempt=1, candidate_sha256="b" * 64, request_identity="c" * 64,
        passed=False, problems=("Candidate dimensions must be exactly 1904x896 pixels.",),
    )
    with pytest.raises(ValueError, match="failed preflight|preflight"):
        recorder.record_call(
            kind="visual_review", attempt=1, model="review", effort="high",
            operation="review", duration_seconds=1.0, status="ok", metadata={},
        )
    with pytest.raises(ValueError, match="failed preflight|preflight"):
        recorder.record_call(
            kind="correction_decision", attempt=1, model="director", effort="high",
            operation="decide", duration_seconds=1.0, status="ok", metadata={},
        )
    recorder.record_call(
        kind="image2", attempt=2, model="gpt-image-2", effort=None,
        operation="generate", duration_seconds=1.0, status="ok",
        metadata={"request_identity_sha256": "d" * 64},
    )


@pytest.mark.parametrize(
    "problems",
    [
        (),
        ("semantic content is wrong",),
        ("Candidate dimensions must be exactly 1904x896 pixels.", "semantic content is wrong"),
    ],
)
def test_failed_preflight_requires_one_or_more_exact_task6_technical_problems(
    tmp_path: Path, problems: tuple[str, ...]
) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_call(
        kind="image2", attempt=1, model="gpt-image-2", effort=None,
        operation="generate", duration_seconds=1.0, status="ok", metadata={},
    )
    with pytest.raises(ValueError, match="technical|problems"):
        recorder.record_candidate_preflight(
            attempt=1, candidate_sha256="b" * 64, request_identity="c" * 64,
            passed=False, problems=problems,
        )


def test_preflight_is_bound_bounded_and_rehydrated_with_same_causality(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_call(
        kind="image2", attempt=1, model="gpt-image-2", effort=None,
        operation="generate", duration_seconds=1.0, status="ok", metadata={},
    )
    recorder.record_candidate_preflight(
        attempt=1, candidate_sha256="b" * 64, request_identity="c" * 64,
        passed=False, problems=("Candidate is not native PNG format.",),
    )
    with pytest.raises(ValueError, match="repeated|budget"):
        recorder.record_candidate_preflight(
            attempt=1, candidate_sha256="b" * 64, request_identity="c" * 64,
            passed=False, problems=("Candidate is not native PNG format.",),
        )
    recorder.finalize()
    resumed = EvidenceRecorder(
        recorder.experiment_root, project_copy=recorder.project_copy,
        experiment_id="evidence-unit",
    )
    resumed.record_call(
        kind="image2", attempt=2, model="gpt-image-2", effort=None,
        operation="generate", duration_seconds=1.0, status="ok", metadata={},
    )


def test_passed_preflight_requires_empty_problems_and_visual_review(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_call(
        kind="image2", attempt=1, model="gpt-image-2", effort=None,
        operation="generate", duration_seconds=1.0, status="ok", metadata={},
    )
    with pytest.raises(ValueError, match="passed|problems"):
        recorder.record_candidate_preflight(
            attempt=1, candidate_sha256="b" * 64, request_identity="c" * 64,
            passed=True, problems=("Candidate is not native PNG format.",),
        )
    recorder.record_candidate_preflight(
        attempt=1, candidate_sha256="b" * 64, request_identity="c" * 64,
        passed=True, problems=(),
    )
    with pytest.raises(ValueError, match="preflight|causal|review|correction"):
        recorder.record_call(
            kind="image2", attempt=2, model="gpt-image-2", effort=None,
            operation="edit", duration_seconds=1.0, status="ok", metadata={},
        )


def test_rehydration_uses_the_same_candidate_causality_validator(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_call(
        kind="image2", attempt=1, model="gpt-image-2", effort=None,
        operation="generate", duration_seconds=1.0, status="ok", metadata={},
    )
    recorder.finalize()
    event = {
        "event": "call", "experiment_id": "evidence-unit",
        "workspace_identity_sha256": recorder.workspace_identity_sha256,
        "source_snapshot_sha256": "a" * 64, "page_number": 1,
        "kind": "image2", "attempt": 2, "model": "gpt-image-2", "effort": None,
        "operation": "edit", "duration_seconds": 1.0, "status": "ok", "metadata": {},
    }
    with (recorder.experiment_root / "evidence.jsonl").open("ab") as handle:
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    with pytest.raises(ValueError, match="preflight|causal|review|correction"):
        EvidenceRecorder(
            recorder.experiment_root, project_copy=recorder.project_copy,
            experiment_id="evidence-unit",
        )


def test_unavailable_call_duration_requires_reason_and_round_trips(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    recorder.record_call(
        kind="image2", attempt=1, model="gpt-image-2", effort=None,
        operation="generate", duration_seconds=None,
        unavailable_reason="process ended before duration was committed",
        status="recovered", metadata={},
    )
    resumed = EvidenceRecorder(
        recorder.experiment_root, project_copy=recorder.project_copy,
        experiment_id="evidence-unit",
    )
    assert resumed.has_call(kind="image2", attempt=1)
    missing = tmp_path / "missing-reason"
    missing.mkdir()
    with pytest.raises(ValueError, match="unavailable_reason"):
        _recorder(missing).record_call(
            kind="image2", attempt=1, model="gpt-image-2", effort=None,
            operation="generate", duration_seconds=None, status="recovered", metadata={},
        )


def _accepted_evidence(recorder: EvidenceRecorder) -> None:
    recorder.record_call(
        kind="image2", attempt=1, model="gpt-image-2", effort=None,
        operation="generate", duration_seconds=1.0, status="ok",
        metadata={"request_identity_sha256": "4" * 64},
    )
    recorder.record_candidate_preflight(
        attempt=1, candidate_sha256="1" * 64, request_identity="4" * 64,
        passed=True, problems=(),
    )
    recorder.record_call(
        kind="visual_review", attempt=1, model="review", effort="high",
        operation="independent_semantic_review", duration_seconds=1.0, status="ok",
        metadata={"decision": "accept", "problem_count": 0,
                  "review_result_sha256": "9" * 64,
                  "request_identity_sha256": "4" * 64},
    )


def test_uncheckpointed_accepted_recovery_reopens_idempotently(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    _accepted_evidence(recorder)
    resumed = EvidenceRecorder(
        recorder.experiment_root,
        project_copy=recorder.project_copy,
        experiment_id="evidence-unit",
    )
    resumed.refresh_from_disk()
    resumed.record_recovery(
        skipped_calls=(
            "page_director", "correction_decision", "image2",
            "visual_review", "reconstruct_edit",
        )
    )
    evidence = recorder.experiment_root / "evidence.jsonl"
    event_count = len(evidence.read_text(encoding="utf-8").splitlines())

    reopened = EvidenceRecorder(
        recorder.experiment_root,
        project_copy=recorder.project_copy,
        experiment_id="evidence-unit",
    )
    reopened.record_recovery(
        skipped_calls=(
            "page_director", "correction_decision", "image2",
            "visual_review", "reconstruct_edit",
        )
    )

    assert len(evidence.read_text(encoding="utf-8").splitlines()) == event_count


def test_acceptance_checkpoint_binds_exact_final_candidate_causality(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    _accepted_evidence(recorder)

    checkpoint = recorder.acceptance_checkpoint(
        attempt=1, candidate_sha256="1" * 64,
        request_identity="4" * 64, review_authority_sha256="9" * 64,
    )

    assert checkpoint["selected_attempt"] == 1
    assert checkpoint["terminal_event_index"] == checkpoint["event_count"] - 1
    assert checkpoint["review_authority_sha256"] == "9" * 64
    recorder.validate_acceptance_checkpoint(checkpoint)


def test_acceptance_checkpoint_rejects_wrong_identity_or_later_call(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    _accepted_evidence(recorder)
    with pytest.raises(ValueError, match="identity|request"):
        recorder.acceptance_checkpoint(
            attempt=1, candidate_sha256="1" * 64,
            request_identity="5" * 64, review_authority_sha256="9" * 64,
        )
    recorder.record_call(
        kind="reconstruct_edit", attempt=1, model="local", effort=None,
        operation="unexpected", duration_seconds=1.0, status="ok", metadata={},
    )
    with pytest.raises(ValueError, match="later|terminal|sequence"):
        recorder.acceptance_checkpoint(
            attempt=1, candidate_sha256="1" * 64,
            request_identity="4" * 64, review_authority_sha256="9" * 64,
        )

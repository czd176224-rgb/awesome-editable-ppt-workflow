from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

SOURCE_IDENTITY = "a" * 64
WORKSPACE_IDENTITY = "b" * 64


def _stage(
    page: int,
    name: str,
    kind: str,
    duration: float | None,
    *,
    experiment: str = "run-a",
    resources: dict[str, int] | None = None,
    unavailable_reason: str | None = None,
) -> dict:
    return {
        "event": "stage",
        "experiment_id": experiment,
        "page_number": page,
        "name": name,
        "kind": kind,
        "duration_seconds": duration,
        "local_duration_seconds": (
            duration if duration is not None and kind in {"local", "reconstruction"} else 0.0
        ) if duration is not None else None,
        "external_wait_seconds": (
            duration if duration is not None and kind in {"codex_wait", "image2_wait", "office_wait"} else 0.0
        ) if duration is not None else None,
        "unavailable_reason": unavailable_reason,
        "status": "unavailable" if duration is None else "ok",
        "resource_start": resources,
        "resource_end": resources,
    }


def _call(
    page: int,
    kind: str,
    duration: float | None,
    *,
    attempt: int = 1,
    experiment: str = "run-a",
    status: str = "ok",
    metadata: dict | None = None,
    unavailable_reason: str | None = None,
) -> dict:
    return {
        "event": "call",
        "experiment_id": experiment,
        "page_number": page,
        "kind": kind,
        "attempt": attempt,
        "duration_seconds": duration,
        "unavailable_reason": unavailable_reason,
        "status": status,
        "metadata": metadata or {},
    }


def _path_call(page: int, *, experiment: str = "path-run", attempt: int = 1) -> dict:
    return {
        **_call(page, "image2", 7.0, attempt=attempt, experiment=experiment),
        "workspace_identity_sha256": WORKSPACE_IDENTITY,
        "source_snapshot_sha256": SOURCE_IDENTITY,
        "model": "gpt-image-2",
        "effort": None,
        "operation": "generate",
    }


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _write_path_evidence(
    root: Path,
    events: list[dict],
    *,
    summary_cache: dict[str, int] | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    payload = b"".join(_canonical(event) + b"\n" for event in events)
    (root / "evidence.jsonl").write_bytes(payload)
    first = events[0]
    calls = [event for event in events if event["event"] == "call"]
    stages = [event for event in events if event["event"] == "stage"]
    cache_events = [event for event in events if event["event"] == "attachment_cache"]
    cache = summary_cache or {
        "hits": sum(event["hits"] for event in cache_events),
        "misses": sum(event["misses"] for event in cache_events),
    }
    call_kinds = (
        "page_director", "correction_decision", "image2", "visual_review", "reconstruct_edit",
    )
    resources = {
        "rss_bytes": 0, "handle_count": 0,
        "active_external_calls": 0, "temp_file_count": 0,
    }
    summary = {
        "schema_version": "awesome-complex-page-evidence-v1",
        "experiment_id": first["experiment_id"],
        "workspace_identity_sha256": first["workspace_identity_sha256"],
        "source_snapshot_sha256": first["source_snapshot_sha256"],
        "page_number": first["page_number"],
        "event_count": len(events),
        "evidence_sha256": hashlib.sha256(payload).hexdigest(),
        "stages": stages,
        "duration_totals": {
            "local_duration_seconds": sum(
                event["local_duration_seconds"] for event in stages
                if event["local_duration_seconds"] is not None
            ),
            "external_wait_seconds": sum(
                event["external_wait_seconds"] for event in stages
                if event["external_wait_seconds"] is not None
            ),
            "reconstruction_duration_seconds": sum(
                event["duration_seconds"] for event in stages
                if event["kind"] == "reconstruction" and event["duration_seconds"] is not None
            ),
        },
        "calls": calls,
        "candidate_preflights": [],
        "call_totals": {kind: sum(call["kind"] == kind for call in calls) for kind in call_kinds},
        "image2_total_calls": sum(call["kind"] == "image2" for call in calls),
        "reconstruct_image2_total_calls": sum(call["kind"] == "reconstruct_edit" for call in calls),
        "attachment_cache": cache,
        "recovery": {"events": 0, "skipped_calls": []},
        "resource_peaks": resources,
    }
    (root / "summary.json").write_bytes(_canonical(summary) + b"\n")
    return root


def test_report_aggregates_truthful_cross_page_runtime_without_double_counting() -> None:
    # Break caught: the report adds call and stage clocks for the same remote operation,
    # counts replayed events twice, or loses scheduler/cache/failure/resource evidence.
    from workflow_v6_performance import build_performance_report

    resources_1 = {
        "rss_bytes": 100,
        "process_count": 2,
        "handle_count": 7,
        "temp_file_count": 1,
        "cache_entry_count": 3,
        "active_external_calls": 1,
    }
    resources_2 = {
        "rss_bytes": 250,
        "process_count": 3,
        "handle_count": 11,
        "temp_file_count": 4,
        "cache_entry_count": 5,
        "active_external_calls": 2,
    }
    image_stage_1 = _stage(1, "image2_execution", "image2_wait", 10.0, resources=resources_1)
    events = [
        _stage(1, "material_preparation", "local", 2.0, resources=resources_1),
        _stage(2, "material_preparation", "local", 4.0, resources=resources_2),
        _call(1, "page_director", 8.0),
        _call(2, "page_director", 12.0),
        _stage(1, "image2_queue", "image2_wait", 1.0, resources=resources_1),
        _stage(2, "image2_queue", "image2_wait", 3.0, resources=resources_2),
        image_stage_1,
        dict(image_stage_1),  # replayed JSONL/summary event must be ignored
        _stage(2, "image2_execution", "image2_wait", 20.0, resources=resources_2),
        _call(1, "image2", 11.0),  # overlaps queue + execution; count call, not duration
        _call(2, "image2", 23.0),
        _stage(1, "visual_review", "codex_wait", 5.0, resources=resources_1),
        _stage(2, "visual_review", "codex_wait", 7.0, resources=resources_2),
        _call(1, "visual_review", 5.5, metadata={"decision": "accept", "problem_count": 0}),
        _call(2, "visual_review", 7.5, metadata={
            "decision": "correct", "problem_count": 1, "reason_category": "text_legibility"
        }),
        _call(2, "correction_decision", 6.0, attempt=1, metadata={
            "reason_category": "text_legibility"
        }),
        _call(2, "image2", 9.0, attempt=2),
        _stage(1, "reconstruction", "reconstruction", 14.0, resources=resources_1),
        _stage(2, "reconstruction", "reconstruction", 18.0, resources=resources_2),
        _call(2, "reconstruct_edit", 4.0, metadata={"reason_category": "overlay_fit"}),
        _stage(1, "fixed_layer_assembly", "local", 2.0, resources=resources_1),
        _stage(2, "fixed_layer_assembly", "local", 4.0, resources=resources_2),
        _stage(1, "lock_wait", "local", 1.0, resources=resources_1),
        _stage(2, "lock_wait", "local", 3.0, resources=resources_2),
        _stage(1, "oauth_wait", "codex_wait", 2.0, resources=resources_1),
        _stage(1, "office_wait", "office_wait", 6.0, resources=resources_1),
        _stage(1, "network_wait", "codex_wait", 4.0, resources=resources_1),
        _stage(1, "recovery", "local", 0.5, resources=resources_1),
        {
            "event": "attachment_cache", "experiment_id": "run-a", "page_number": 1,
            "hits": 3, "misses": 1,
        },
        {
            "event": "recovery", "experiment_id": "run-a", "page_number": 1,
            "skipped_calls": ["page_director", "image2", "visual_review", "reconstruct_edit"],
        },
        _call(2, "image2", None, attempt=3, status="error", metadata={
            "status_code": 429, "concurrency_before": 4, "concurrency_after": 1,
        }, unavailable_reason="request failed before a durable duration was available"),
        {
            "event": "concurrency", "experiment_id": "run-a", "page_number": 2,
            "trigger": "stable_success", "concurrency_before": 1, "concurrency_after": 2,
        },
    ]
    report = build_performance_report({
        "environment": {"os": "Windows", "runtime": "CPython 3.13"},
        "plugin_commit": "abc123",
        "measurement_scopes": {
            "external_stage_speed": "real_provider",
            "scheduler_speed": "deterministic_test",
            "development_time_is_user_runtime": False,
        },
        "events": events,
        "pipeline_report": {
            "completed_pages": [1],
            "failed_pages": {"2": "provider rate limit"},
            "stage_peaks": {"director": 2, "image2": 2, "review": 1},
        },
        "reconstruction_image2_by_page": {"1": 0, "2": 1},
        "scale_result": {
            "page_peak": 3,
            "requested_page_count": 100,
            "completed_page_count": 98,
            "elapsed_seconds": 5.0,
            "serial_baseline_seconds": 10.0,
            "elapsed_to_serial_ratio": 0.5,
            "throughput_pages_per_second": 20.0,
        },
    })

    assert report["environment"] == {"os": "Windows", "runtime": "CPython 3.13"}
    assert report["plugin_commit"] == "abc123"
    assert report["measurement_scopes"] == {
        "external_stage_speed": "real_provider",
        "scheduler_speed": "deterministic_test",
        "development_time_is_user_runtime": False,
    }
    assert report["pages"] == [1, 2]
    assert report["stages"]["materials"] == {
        "status": "measured", "count": 2, "total_seconds": 6.0,
        "median_seconds": 3.0, "max_seconds": 4.0,
    }
    assert report["stages"]["codex_director"]["total_seconds"] == 20.0
    assert report["stages"]["image2_queue"]["median_seconds"] == 2.0
    assert report["stages"]["image2_execution"] == {
        "status": "measured", "count": 2, "total_seconds": 30.0,
        "median_seconds": 15.0, "max_seconds": 20.0,
    }
    assert report["stages"]["independent_review"]["total_seconds"] == 13.0
    assert report["stages"]["reconstruction"]["total_seconds"] == 32.0
    assert report["stages"]["fixed_and_assembly"]["total_seconds"] == 6.0
    assert report["stages"]["lock_waits"]["total_seconds"] == 4.0
    assert report["stages"]["recovery"]["total_seconds"] == 0.5
    assert report["waits"]["local"]["total_seconds"] == 48.5
    assert report["waits"]["codex"]["total_seconds"] == 39.0
    assert report["waits"]["image2"]["total_seconds"] == 43.0
    assert report["waits"]["oauth"]["total_seconds"] == 2.0
    assert report["waits"]["office"]["total_seconds"] == 6.0
    assert report["waits"]["network"]["total_seconds"] == 4.0
    assert report["stages"]["external_waits"]["total_seconds"] == 94.0

    assert report["calls"]["image2"] == {"total": 4, "per_page": {"1": 1, "2": 3}}
    assert report["calls"]["reconstruction_image2"] == {
        "total": 1, "per_page": {"1": 0, "2": 1}
    }
    assert report["qa_corrections"] == {
        "triggered": 1,
        "applied": 1,
        "reason_categories": {"text_legibility": 1},
        "uncategorized": 0,
    }
    assert report["rate_limits"] == {
        "http_429_events": 1,
        "contractions": [{"before": 4, "after": 1, "page_number": 2}],
        "recoveries": [{"before": 1, "after": 2, "page_number": 2}],
    }
    assert report["attachment_cache"] == {
        "hits": 3, "misses": 1, "hit_rate": 0.75,
    }
    assert report["completed_page_recovery"] == {
        "events": 1,
        "pages": [1],
        "skipped_calls": {
            "image2": 1, "page_director": 1, "reconstruct_edit": 1, "visual_review": 1,
        },
    }
    assert report["failures"] == {
        "count": 1, "pages": {"2": "provider rate limit"}, "isolated": True,
    }
    assert report["concurrency"] == {
        "peak_pages": 3,
        "peak_stages": {"director": 2, "image2": 2, "review": 1},
    }
    assert report["deterministic_scheduler"] == {
        "status": "measured",
        "requested_page_count": 100,
        "completed_page_count": 98,
        "elapsed_seconds": 5.0,
        "serial_baseline_seconds": 10.0,
        "elapsed_to_serial_ratio": 0.5,
        "throughput_pages_per_second": 20.0,
        "scope": "deterministic test workload; not real provider speed",
    }
    assert report["resources"] == {
        "peak_rss_bytes": 250,
        "peak_process_count": 3,
        "peak_handle_count": 11,
        "peak_temp_file_count": 4,
        "peak_cache_entry_count": 5,
    }
    assert report["integrity"] == {
        "input_event_count": 32,
        "unique_event_count": 31,
        "duplicate_events_ignored": 1,
    }


def test_report_marks_absent_and_partial_measurements_unavailable_instead_of_zero() -> None:
    # Break caught: missing clocks, queue telemetry, cache misses, or process samples
    # are silently presented as measured zeroes.
    from workflow_v6_performance import build_performance_report

    report = build_performance_report({
        "scale_result": {"first_run_scheduler_concurrency_history": [4, 1, 2]},
        "events": [
            _call(1, "page_director", None, unavailable_reason="old wrapper omitted duration"),
            _call(1, "image2", 9.0),
            _stage(1, "visual_review", "codex_wait", None,
                   unavailable_reason="review clock was not persisted"),
            _stage(1, "lock_wait", "local", 0.0),
        ]
    })

    assert report["environment"]["status"] == "unavailable"
    assert report["plugin_commit"]["status"] == "unavailable"
    assert report["stages"]["codex_director"] == {
        "status": "unavailable",
        "count": 0,
        "total_seconds": None,
        "median_seconds": None,
        "max_seconds": None,
        "reason": "old wrapper omitted duration",
    }
    assert report["stages"]["image2_queue"]["status"] == "unavailable"
    assert report["stages"]["image2_execution"]["status"] == "unavailable"
    assert report["stages"]["image2_total"]["total_seconds"] == 9.0
    assert report["stages"]["independent_review"]["reason"] == "review clock was not persisted"
    assert report["stages"]["external_waits"]["status"] == "partial"
    assert report["waits"]["local"]["total_seconds"] == 0.0
    assert report["attachment_cache"]["hits"] is None
    assert report["attachment_cache"]["hit_rate"] is None
    assert report["failures"]["count"]["status"] == "unavailable"
    assert report["failures"]["isolated"]["status"] == "unavailable"
    assert report["concurrency"]["peak_pages"]["status"] == "unavailable"
    assert report["resources"]["peak_process_count"]["status"] == "unavailable"
    assert report["rate_limits"]["contractions"] == [{
        "before": 4, "after": 1, "page_number": None,
        "source": "first_run_scheduler_concurrency_history", "trigger": "unavailable",
    }]

    empty = build_performance_report({"events": []})
    assert empty["calls"]["image2"]["total"]["status"] == "unavailable"


def test_report_discovers_jsonl_runs_and_ignores_replayed_summary_events(tmp_path: Path) -> None:
    # Break caught: project/run path input cannot discover durable evidence, or reads
    # the same JSONL event again from summary.json and doubles its duration/call.
    from workflow_v6_performance import build_performance_report

    root = tmp_path / "run"
    event = _path_call(3)
    cache_event = {
        "event": "attachment_cache", "experiment_id": "path-run",
        "page_number": 3, "hits": 2, "misses": 0,
        "workspace_identity_sha256": WORKSPACE_IDENTITY,
        "source_snapshot_sha256": SOURCE_IDENTITY,
    }
    _write_path_evidence(root, [event, cache_event])
    jobs = root / "page-003" / "pages" / "page_001" / "imagegen-jobs.json"
    jobs.parent.mkdir(parents=True)
    jobs.write_text(json.dumps({"jobs": []}), encoding="utf-8")

    report = build_performance_report(root)

    assert report["pages"] == [3]
    assert report["calls"]["image2"] == {"total": 1, "per_page": {"3": 1}}
    assert report["calls"]["reconstruction_image2"] == {
        "total": 0, "per_page": {"3": 0},
    }
    assert report["attachment_cache"] == {"hits": 2, "misses": 0, "hit_rate": 1.0}
    assert report["stages"]["external_waits"]["status"] == "partial"
    assert report["integrity"]["duplicate_events_ignored"] == 1


def test_image2_component_fallback_composes_one_sample_per_stable_call_identity() -> None:
    # Break caught: queue and execution components are counted as four calls instead
    # of two composed per-call totals when durable Image2 call events are absent.
    from workflow_v6_performance import build_performance_report

    events = []
    for page, identity, queue, execution in ((1, "request-a", 1.0, 10.0), (2, "request-b", 3.0, 20.0)):
        queued = _stage(page, "image2_queue", "image2_wait", queue)
        queued.update({"attempt": 1, "request_identity": identity})
        executed = _stage(page, "image2_execution", "image2_wait", execution)
        executed.update({"attempt": 1, "request_identity": identity})
        events.extend((queued, executed))

    report = build_performance_report({"events": events})

    assert report["stages"]["image2_total"] == {
        "status": "measured", "count": 2, "total_seconds": 34.0,
        "median_seconds": 17.0, "max_seconds": 23.0,
    }
    assert report["waits"]["image2"]["total_seconds"] == 34.0


def test_image2_total_selects_call_or_composed_components_per_stable_identity() -> None:
    # Break caught: the presence of one Image2 call globally hides another call
    # identity whose only durable authority is a queue/execution component pair.
    from workflow_v6_performance import build_performance_report

    call = _call(1, "image2", 5.0, metadata={"request_identity": "request-a"})
    call.update({
        "workspace_identity_sha256": WORKSPACE_IDENTITY,
        "source_snapshot_sha256": SOURCE_IDENTITY,
    })
    queued = _stage(2, "image2_queue", "image2_wait", 3.0)
    queued.update({
        "attempt": 1, "request_identity": "request-b",
        "workspace_identity_sha256": WORKSPACE_IDENTITY,
        "source_snapshot_sha256": SOURCE_IDENTITY,
    })
    executed = _stage(2, "image2_execution", "image2_wait", 20.0)
    executed.update({
        "attempt": 1, "request_identity": "request-b",
        "workspace_identity_sha256": WORKSPACE_IDENTITY,
        "source_snapshot_sha256": SOURCE_IDENTITY,
    })

    report = build_performance_report({"events": [call, queued, executed]})

    expected = {
        "status": "measured", "count": 2, "total_seconds": 28.0,
        "median_seconds": 14.0, "max_seconds": 23.0,
    }
    assert report["stages"]["image2_total"] == expected
    assert report["waits"]["image2"] == expected
    assert report["stages"]["external_waits"]["total_seconds"] == 28.0


def test_mixed_image2_authority_keeps_unpairable_components_partial() -> None:
    # Break caught: unmatched or duplicate components are silently omitted when a
    # different identity has an authoritative Image2 call event.
    from workflow_v6_performance import build_performance_report

    call = _call(1, "image2", 5.0, metadata={"request_identity": "request-a"})
    call.update({
        "workspace_identity_sha256": WORKSPACE_IDENTITY,
        "source_snapshot_sha256": SOURCE_IDENTITY,
    })
    unmatched = _stage(2, "image2_queue", "image2_wait", 3.0)
    unmatched.update({
        "attempt": 1, "request_identity": "request-b",
        "workspace_identity_sha256": WORKSPACE_IDENTITY,
        "source_snapshot_sha256": SOURCE_IDENTITY,
    })

    report = build_performance_report({"events": [call, unmatched]})

    assert report["stages"]["image2_total"]["status"] == "partial"
    assert report["stages"]["image2_total"]["count"] == 1
    assert report["stages"]["image2_total"]["total_seconds"] == 5.0
    assert report["stages"]["image2_total"]["unavailable_count"] == 1


def test_mixed_image2_authority_keeps_distinct_identities_on_the_same_page() -> None:
    # Break caught: a page-level fallback suppresses a pair whose stable request
    # identity differs from an authoritative call on the same page.
    from workflow_v6_performance import build_performance_report

    call = _call(1, "image2", 5.0, metadata={"request_identity": "request-a"})
    call.update({
        "workspace_identity_sha256": WORKSPACE_IDENTITY,
        "source_snapshot_sha256": SOURCE_IDENTITY,
    })
    components = []
    for name, duration in (("image2_queue", 3.0), ("image2_execution", 20.0)):
        component = _stage(1, name, "image2_wait", duration)
        component.update({
            "attempt": 2, "request_identity": "request-b",
            "workspace_identity_sha256": WORKSPACE_IDENTITY,
            "source_snapshot_sha256": SOURCE_IDENTITY,
        })
        components.append(component)

    report = build_performance_report({"events": [call, *components]})

    assert report["stages"]["image2_total"]["count"] == 2
    assert report["stages"]["image2_total"]["total_seconds"] == 28.0


def test_reconstruction_counts_keep_pages_without_a_ledger_unknown() -> None:
    # Break caught: one authoritative page ledger causes every other discovered
    # page to be silently reported as zero reconstruction Image2 calls.
    from workflow_v6_performance import build_performance_report

    report = build_performance_report({
        "events": [_call(1, "image2", 2.0), _call(2, "image2", 3.0)],
        "reconstruction_image2_by_page": {"1": 0},
    })

    assert report["calls"]["reconstruction_image2"]["total"]["status"] == "partial"
    assert report["calls"]["reconstruction_image2"]["total"]["known_total"] == 0
    assert report["calls"]["reconstruction_image2"]["per_page"]["1"] == 0
    assert report["calls"]["reconstruction_image2"]["per_page"]["2"]["status"] == "unavailable"


def test_path_loader_rejects_invalid_identity_and_implicit_multi_project_scope(tmp_path: Path) -> None:
    # Break caught: recursive discovery silently mixes unrelated descendant projects,
    # nonpositive pages, or summary events whose identity differs from their root.
    from workflow_v6_performance import build_performance_report

    multi = tmp_path / "multi"
    _write_path_evidence(multi / "project-a", [_path_call(1, experiment="project-a")])
    _write_path_evidence(multi / "project-b", [_path_call(2, experiment="project-b")])
    with pytest.raises(ValueError, match="multiple evidence roots"):
        build_performance_report(multi)

    out_of_range = tmp_path / "out-of-range"
    invalid = _path_call(0)
    out_of_range.mkdir()
    (out_of_range / "evidence.jsonl").write_bytes(_canonical(invalid) + b"\n")
    with pytest.raises(ValueError, match="valid complex-page evidence event"):
        build_performance_report(out_of_range)

    mismatch = _write_path_evidence(tmp_path / "mismatch", [_path_call(1)])
    summary = json.loads((mismatch / "summary.json").read_text(encoding="utf-8"))
    summary["calls"][0]["source_snapshot_sha256"] = "c" * 64
    (mismatch / "summary.json").write_bytes(_canonical(summary) + b"\n")
    with pytest.raises(ValueError, match="valid complex-page evidence summary"):
        build_performance_report(mismatch)

    summary_only = _write_path_evidence(tmp_path / "summary-only", [_path_call(1)])
    (summary_only / "evidence.jsonl").unlink()
    summary = json.loads((summary_only / "summary.json").read_text(encoding="utf-8"))
    summary["calls"][0]["experiment_id"] = "other-run"
    (summary_only / "summary.json").write_bytes(_canonical(summary) + b"\n")
    with pytest.raises(ValueError, match="valid complex-page evidence summary"):
        build_performance_report(summary_only / "summary.json")

    malformed_hash = tmp_path / "malformed-hash"
    malformed_cache = {
        "event": "attachment_cache", "experiment_id": "path-run", "page_number": 1,
        "workspace_identity_sha256": "z" * 64,
        "source_snapshot_sha256": SOURCE_IDENTITY,
        "hits": 1, "misses": 0,
    }
    malformed_hash.mkdir()
    (malformed_hash / "evidence.jsonl").write_bytes(_canonical(malformed_cache) + b"\n")
    with pytest.raises(ValueError, match="valid complex-page evidence event"):
        build_performance_report(malformed_hash)


def test_recovery_skip_requires_completed_authority_and_no_later_call() -> None:
    # Break caught: skipped_calls alone is labeled completed-page recovery even when
    # a later duplicate call proves the page was not actually skipped.
    from workflow_v6_performance import build_performance_report

    recovery = {
        "event": "recovery", "experiment_id": "run-a", "page_number": 1,
        "skipped_calls": ["page_director", "image2", "visual_review", "reconstruct_edit"],
    }
    report = build_performance_report({
        "events": [recovery, _call(1, "page_director", 2.0, attempt=2)],
        "pipeline_report": {"completed_pages": [1], "failed_pages": {}, "stage_peaks": {}},
    })
    no_authority = build_performance_report({"events": [recovery]})

    assert report["completed_page_recovery"]["status"] == "unverified"
    assert report["completed_page_recovery"]["verified_events"] == 0
    assert report["completed_page_recovery"]["observed_events"] == 1
    assert no_authority["completed_page_recovery"]["status"] == "unavailable"


def test_recovery_later_call_check_is_scoped_to_one_stream_identity() -> None:
    # Break caught: a fresh-copy page-4 call in another experiment invalidates a
    # completed-page recovery checkpoint from the rejected page-4 experiment.
    from workflow_v6_performance import build_performance_report

    recovery = {
        "event": "recovery", "experiment_id": "rejected-page-4", "page_number": 4,
        "workspace_identity_sha256": WORKSPACE_IDENTITY,
        "source_snapshot_sha256": SOURCE_IDENTITY,
        "skipped_calls": ["page_director", "image2", "visual_review", "reconstruct_edit"],
    }
    fresh_copy = _call(
        4, "image2", 5.0, experiment="fresh-page-4",
        metadata={"request_identity": "fresh-request"},
    )
    fresh_copy.update({
        "workspace_identity_sha256": WORKSPACE_IDENTITY,
        "source_snapshot_sha256": SOURCE_IDENTITY,
    })
    same_stream = _call(
        4, "image2", 5.0, experiment="rejected-page-4", attempt=2,
        metadata={"request_identity": "retry-request"},
    )
    same_stream.update({
        "workspace_identity_sha256": WORKSPACE_IDENTITY,
        "source_snapshot_sha256": SOURCE_IDENTITY,
    })
    authority = {"completed_pages": [4], "failed_pages": {}, "stage_peaks": {}}

    fresh_report = build_performance_report({
        "events": [recovery, fresh_copy], "pipeline_report": authority,
    })
    duplicate_report = build_performance_report({
        "events": [recovery, same_stream], "pipeline_report": authority,
    })

    assert fresh_report["completed_page_recovery"] == {
        "events": 1,
        "pages": [4],
        "skipped_calls": {
            "image2": 1, "page_director": 1, "reconstruct_edit": 1, "visual_review": 1,
        },
    }
    assert duplicate_report["completed_page_recovery"]["status"] == "unverified"


def test_path_cache_authority_is_selected_per_metric(tmp_path: Path) -> None:
    # Break caught: any JSONL file suppresses the summary cache aggregate even when
    # JSONL has no cache event, while a real JSONL cache event must still win once.
    from workflow_v6_performance import build_performance_report

    summary_only = _write_path_evidence(
        tmp_path / "summary-cache", [_path_call(1)],
        summary_cache={"hits": 2, "misses": 0},
    )
    cache_event = {
        "event": "attachment_cache", "experiment_id": "path-run", "page_number": 1,
        "workspace_identity_sha256": WORKSPACE_IDENTITY,
        "source_snapshot_sha256": SOURCE_IDENTITY,
        "hits": 3, "misses": 0,
    }
    raw_cache = _write_path_evidence(tmp_path / "raw-cache", [_path_call(1), cache_event])

    assert build_performance_report(summary_only)["attachment_cache"] == {
        "hits": 2, "misses": 0, "hit_rate": 1.0,
    }
    assert build_performance_report(raw_cache)["attachment_cache"] == {
        "hits": 3, "misses": 0, "hit_rate": 1.0,
    }

    invalid = json.loads((summary_only / "summary.json").read_text(encoding="utf-8"))
    invalid["attachment_cache"]["misses"] = 1
    (summary_only / "summary.json").write_bytes(_canonical(invalid) + b"\n")
    with pytest.raises(ValueError, match="valid complex-page evidence summary"):
        build_performance_report(summary_only)

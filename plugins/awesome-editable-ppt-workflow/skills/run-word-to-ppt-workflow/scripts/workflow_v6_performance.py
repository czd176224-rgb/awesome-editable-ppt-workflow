"""Truthful cross-page aggregation of existing V6 performance evidence.

This module reads evidence; it does not write into candidate evidence streams.
Missing clocks and counters remain explicitly unavailable rather than becoming zero.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import median
from typing import Any

from jsonschema import Draft202012Validator


_RESOURCE_OUTPUTS = {
    "rss_bytes": "peak_rss_bytes",
    "process_count": "peak_process_count",
    "handle_count": "peak_handle_count",
    "temp_file_count": "peak_temp_file_count",
    "cache_entry_count": "peak_cache_entry_count",
}
_EVIDENCE_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "schemas" / "complex_page_evidence_v1.schema.json"
)


def _unavailable(reason: str) -> dict[str, object]:
    return {"status": "unavailable", "value": None, "reason": reason}


def _event_key(event: Mapping[str, object]) -> str:
    return json.dumps(dict(event), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _finite_duration(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) and result >= 0 else None


def _reason(event: Mapping[str, object], fallback: str) -> str:
    value = event.get("unavailable_reason")
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _stats(
    records: Sequence[Mapping[str, object]],
    *,
    label: str,
    value_field: str = "duration_seconds",
) -> dict[str, object]:
    values: list[float] = []
    reasons: list[str] = []
    for record in records:
        value = _finite_duration(record.get(value_field))
        if value is None:
            reasons.append(_reason(record, f"{label} duration was not durably recorded"))
        else:
            values.append(value)
    if not values:
        reason = "; ".join(dict.fromkeys(reasons)) or f"no durable {label} duration events were provided"
        return {
            "status": "unavailable",
            "count": 0,
            "total_seconds": None,
            "median_seconds": None,
            "max_seconds": None,
            "reason": reason,
        }
    result: dict[str, object] = {
        "status": "partial" if reasons else "measured",
        "count": len(values),
        "total_seconds": round(sum(values), 6),
        "median_seconds": round(float(median(values)), 6),
        "max_seconds": round(max(values), 6),
    }
    if reasons:
        result["unavailable_count"] = len(reasons)
        result["reason"] = "; ".join(dict.fromkeys(reasons))
    return result


def _merge_stats(records: Sequence[Mapping[str, object]], label: str) -> dict[str, object]:
    return _stats(records, label=label)


def _group_key(event: Mapping[str, object]) -> tuple[object, object]:
    return event.get("experiment_id", event.get("run_id")), event.get("page_number")


def _prefer_calls_per_page(
    calls: Sequence[Mapping[str, object]],
    stages: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    grouped_calls: dict[tuple[object, object], list[Mapping[str, object]]] = defaultdict(list)
    grouped_stages: dict[tuple[object, object], list[Mapping[str, object]]] = defaultdict(list)
    for event in calls:
        grouped_calls[_group_key(event)].append(event)
    for event in stages:
        grouped_stages[_group_key(event)].append(event)
    selected: list[Mapping[str, object]] = []
    for key in sorted(set(grouped_calls) | set(grouped_stages), key=repr):
        selected.extend(grouped_calls[key] or grouped_stages[key])
    return selected


def _image_component_identity(event: Mapping[str, object]) -> tuple[object, ...] | None:
    metadata = event.get("metadata")
    request_identity = event.get("request_identity")
    if request_identity is None and isinstance(metadata, Mapping):
        request_identity = metadata.get("request_identity_sha256", metadata.get("request_identity"))
    attempt = event.get("attempt")
    page = event.get("page_number")
    if request_identity is None or type(attempt) is not int or type(page) is not int:
        return None
    return (
        event.get("experiment_id", event.get("run_id")),
        event.get("workspace_identity_sha256"),
        event.get("source_snapshot_sha256"),
        page,
        attempt,
        request_identity,
    )


def _compose_image2_components(
    queue: Sequence[Mapping[str, object]],
    execution: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    components = list(queue) + list(execution)
    if not components:
        return []
    grouped: dict[tuple[object, ...], dict[str, list[Mapping[str, object]]]] = defaultdict(
        lambda: {"image2_queue": [], "image2_execution": []}
    )
    for event in components:
        identity = _image_component_identity(event)
        name = event.get("name")
        if identity is None or name not in {"image2_queue", "image2_execution"}:
            return [{
                "duration_seconds": None,
                "unavailable_reason": (
                    "Image2 queue/execution components lack a stable shared "
                    "page/attempt/request identity"
                ),
            }]
        grouped[identity][str(name)].append(event)
    composed: list[Mapping[str, object]] = []
    for identity in sorted(grouped, key=repr):
        pair = grouped[identity]
        if len(pair["image2_queue"]) != 1 or len(pair["image2_execution"]) != 1:
            composed.append({
                "duration_seconds": None,
                "unavailable_reason": "Image2 call components are incomplete or duplicated",
            })
            continue
        queue_duration = _finite_duration(pair["image2_queue"][0].get("duration_seconds"))
        execution_duration = _finite_duration(pair["image2_execution"][0].get("duration_seconds"))
        if queue_duration is None or execution_duration is None:
            composed.append({
                "duration_seconds": None,
                "unavailable_reason": "Image2 call components contain an unavailable duration",
            })
        else:
            composed.append({"duration_seconds": queue_duration + execution_duration})
    return composed


def _select_image2_total_records(
    calls: Sequence[Mapping[str, object]],
    queue: Sequence[Mapping[str, object]],
    execution: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    calls_by_identity: dict[tuple[object, ...], list[Mapping[str, object]]] = defaultdict(list)
    unidentified_calls: list[Mapping[str, object]] = []
    for call in calls:
        identity = _image_component_identity(call)
        if identity is None:
            unidentified_calls.append(call)
        else:
            calls_by_identity[identity].append(call)

    selected: list[Mapping[str, object]] = list(unidentified_calls)
    for identity in sorted(calls_by_identity, key=repr):
        authoritative = calls_by_identity[identity]
        if len(authoritative) == 1:
            selected.append(authoritative[0])
        else:
            selected.append({
                "duration_seconds": None,
                "unavailable_reason": "multiple Image2 call events share one stable call identity",
            })

    call_groups = {_group_key(call) for call in calls}
    remaining_components: list[Mapping[str, object]] = []
    for component in list(queue) + list(execution):
        identity = _image_component_identity(component)
        if identity in calls_by_identity:
            continue
        if identity is None and _group_key(component) in call_groups:
            continue
        remaining_components.append(component)
    selected.extend(_compose_image2_components(
        [component for component in remaining_components if component.get("name") == "image2_queue"],
        [
            component for component in remaining_components
            if component.get("name") == "image2_execution"
        ],
    ))
    return selected


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _evidence_schema() -> dict[str, object]:
    value = json.loads(_EVIDENCE_SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("complex-page evidence schema must be an object")
    return value


def _validate_path_event(event: Mapping[str, object], *, path: Path) -> None:
    schema = _evidence_schema()
    event_type = event.get("event")
    definition = {
        "stage": "stage", "call": "call", "candidate_preflight": "candidatePreflight",
    }.get(str(event_type))
    if definition is not None:
        validator = Draft202012Validator({
            "$schema": schema["$schema"],
            "$defs": schema["$defs"],
            "$ref": f"#/$defs/{definition}",
        })
        errors = sorted(validator.iter_errors(dict(event)), key=lambda error: list(error.absolute_path))
        if errors:
            raise ValueError(f"path does not contain a valid complex-page evidence event: {path}")
        return
    common_valid = (
        isinstance(event.get("experiment_id"), str)
        and _is_sha256(event.get("workspace_identity_sha256"))
        and _is_sha256(event.get("source_snapshot_sha256"))
        and type(event.get("page_number")) is int
        and 1 <= int(event["page_number"]) <= 4
    )
    if event_type == "attachment_cache":
        valid = (
            common_valid
            and type(event.get("hits")) is int and int(event["hits"]) >= 0
            and event.get("misses") == 0
            and set(event) == {
                "event", "experiment_id", "workspace_identity_sha256",
                "source_snapshot_sha256", "page_number", "hits", "misses",
            }
        )
    elif event_type == "recovery":
        valid = (
            common_valid
            and event.get("skipped_calls") == [
                "page_director", "correction_decision", "image2",
                "visual_review", "reconstruct_edit",
            ]
            and set(event) == {
                "event", "experiment_id", "workspace_identity_sha256",
                "source_snapshot_sha256", "page_number", "skipped_calls",
            }
        )
    else:
        valid = False
    if not valid:
        raise ValueError(f"path does not contain a valid complex-page evidence event: {path}")


def _validate_path_summary(
    summary: Mapping[str, object],
    *,
    path: Path,
    raw_events: Sequence[Mapping[str, object]],
    raw_payload: bytes,
) -> None:
    errors = sorted(
        Draft202012Validator(_evidence_schema()).iter_errors(dict(summary)),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        raise ValueError(f"path does not contain a valid complex-page evidence summary: {path}")
    identity_fields = (
        "experiment_id", "workspace_identity_sha256", "source_snapshot_sha256", "page_number",
    )
    identity = tuple(summary.get(field) for field in identity_fields)
    summary_events = [
        event
        for group in ("stages", "calls", "candidate_preflights")
        for event in summary.get(group, [])
    ]
    if any(tuple(event.get(field) for field in identity_fields) != identity for event in summary_events):
        raise ValueError(f"path does not contain a valid complex-page evidence summary: {path}")
    if not raw_events:
        return
    if any(tuple(event.get(field) for field in identity_fields) != identity for event in raw_events):
        raise ValueError(f"path does not contain a valid complex-page evidence summary: {path}")
    expected_groups = {
        "stages": [dict(event) for event in raw_events if event.get("event") == "stage"],
        "calls": [dict(event) for event in raw_events if event.get("event") == "call"],
        "candidate_preflights": [
            dict(event) for event in raw_events if event.get("event") == "candidate_preflight"
        ],
    }
    if (
        summary.get("event_count") != len(raw_events)
        or summary.get("evidence_sha256") != hashlib.sha256(raw_payload).hexdigest()
        or any(summary.get(name) != values for name, values in expected_groups.items())
    ):
        raise ValueError(f"path does not contain a valid complex-page evidence summary: {path}")
    raw_cache = [event for event in raw_events if event.get("event") == "attachment_cache"]
    if raw_cache and summary.get("attachment_cache") != {
        "hits": sum(int(event["hits"]) for event in raw_cache),
        "misses": sum(int(event["misses"]) for event in raw_cache),
    }:
        raise ValueError(f"path does not contain a valid complex-page evidence summary: {path}")


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _events_from_summary(
    value: Mapping[str, object], *, include_cache: bool, include_resources: bool,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for key in ("stages", "calls", "candidate_preflights"):
        items = value.get(key)
        if isinstance(items, list):
            events.extend(dict(item) for item in items if isinstance(item, Mapping))
    cache = value.get("attachment_cache")
    if include_cache and isinstance(cache, Mapping):
        events.append({
            "event": "attachment_cache",
            "experiment_id": value.get("experiment_id"),
            "page_number": value.get("page_number"),
            "hits": cache.get("hits"),
            "misses": cache.get("misses"),
        })
    resource_peaks = value.get("resource_peaks")
    if include_resources and isinstance(resource_peaks, Mapping):
        events.append({"event": "resource", **dict(resource_peaks)})
    return events


def _read_path(path: Path) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(path)
    result: dict[str, object] = {"events": [], "source_files": []}
    if path.is_file():
        evidence_files = [path] if path.name == "evidence.jsonl" else []
        summary_files = [path] if path.name == "summary.json" else []
        if evidence_files and path.with_name("summary.json").is_file():
            summary_files.append(path.with_name("summary.json"))
        other_files = [] if evidence_files or summary_files else [path]
    else:
        direct_evidence = path / "evidence.jsonl"
        evidence_files = [direct_evidence] if direct_evidence.is_file() else sorted(
            path.rglob("evidence.jsonl")
        )
        evidence_roots = {file.resolve().parent for file in evidence_files}
        if len(evidence_roots) > 1:
            raise ValueError(
                "path contains multiple evidence roots; pass each intentional run root explicitly"
            )
        summary_files = [
            file.with_name("summary.json") for file in evidence_files
            if file.with_name("summary.json").is_file()
        ]
        if not evidence_files and (path / "summary.json").is_file():
            summary_files = [path / "summary.json"]
        other_files = sorted(path.rglob("assembly-report.json"))
        other_files += sorted(path.rglob("*100-page-scale.json"))
        other_files += sorted(path.rglob("imagegen-jobs.json"))

    raw_by_root: dict[Path, list[dict[str, object]]] = {}
    payload_by_root: dict[Path, bytes] = {}
    for file in evidence_files:
        resolved = file.resolve()
        result["source_files"].append(str(resolved))  # type: ignore[union-attr]
        payload = file.read_bytes()
        lines = payload.splitlines(keepends=True)
        if len(lines) > 64 or any(not line.endswith(b"\n") for line in lines):
            raise ValueError(f"path does not contain valid canonical evidence JSONL: {file}")
        events: list[dict[str, object]] = []
        identity: tuple[object, ...] | None = None
        for line in lines:
            try:
                value = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"path does not contain valid canonical evidence JSONL: {file}") from exc
            if not isinstance(value, dict) or line != _canonical_bytes(value) + b"\n":
                raise ValueError(f"path does not contain valid canonical evidence JSONL: {file}")
            _validate_path_event(value, path=file)
            current_identity = tuple(value.get(field) for field in (
                "experiment_id", "workspace_identity_sha256", "source_snapshot_sha256", "page_number",
            ))
            if identity is None:
                identity = current_identity
            elif current_identity != identity:
                raise ValueError(f"path evidence events do not share one internal identity: {file}")
            events.append(value)
        raw_by_root[resolved.parent] = events
        payload_by_root[resolved.parent] = payload
        result["events"].extend(events)  # type: ignore[union-attr]

    for file in summary_files:
        resolved = file.resolve()
        result["source_files"].append(str(resolved))  # type: ignore[union-attr]
        value = _read_json(file)
        if not isinstance(value, dict):
            raise ValueError(f"performance artifact must be an object: {file}")
        if value.get("schema_version") != "awesome-complex-page-evidence-v1":
            raise ValueError(f"path does not contain a valid complex-page evidence summary: {file}")
        raw_events = raw_by_root.get(resolved.parent, [])
        _validate_path_summary(
            value, path=file, raw_events=raw_events,
            raw_payload=payload_by_root.get(resolved.parent, b""),
        )
        raw_has_cache = any(event.get("event") == "attachment_cache" for event in raw_events)
        raw_has_resources = any(
            isinstance(event.get("resource_start"), Mapping)
            or isinstance(event.get("resource_end"), Mapping)
            or event.get("event") == "resource"
            for event in raw_events
        )
        result["events"].extend(_events_from_summary(  # type: ignore[union-attr]
            value, include_cache=not raw_has_cache, include_resources=not raw_has_resources,
        ))

    seen_other: set[Path] = set()
    for file in other_files:
        resolved = file.resolve()
        if resolved in seen_other:
            continue
        seen_other.add(resolved)
        result["source_files"].append(str(resolved))  # type: ignore[union-attr]
        value = _read_json(file)
        if not isinstance(value, dict):
            raise ValueError(f"performance artifact must be an object: {file}")
        schema = value.get("schema_version")
        if schema == "awesome-production-100-page-scale-v1":
            result.setdefault("scale_results", []).append(value)  # type: ignore[union-attr]
        elif "completed_pages" in value and "failed_pages" in value and "stage_peaks" in value:
            result.setdefault("pipeline_reports", []).append(value)  # type: ignore[union-attr]
        elif file.name == "assembly-report.json":
            result.setdefault("assembly_reports", []).append(value)  # type: ignore[union-attr]
        elif file.name == "imagegen-jobs.json":
            result.setdefault("imagegen_jobs", []).append(value)  # type: ignore[union-attr]
            jobs = value.get("jobs")
            if not isinstance(jobs, list):
                raise ValueError(f"reconstruction Image2 jobs must be an array: {file}")
            page_number = next((
                int(parent.name[5:])
                for parent in file.parents
                if parent.name.startswith("page-") and parent.name[5:].isdigit()
            ), None)
            if page_number is not None:
                counts = result.setdefault("reconstruction_image2_by_page", {})
                page_key = str(page_number)
                counts[page_key] = int(counts.get(page_key, 0)) + len(jobs)  # type: ignore[union-attr]
    return result


def _normalize_inputs(project_or_runs: object) -> dict[str, object]:
    merged: dict[str, object] = {
        "events": [], "pipeline_reports": [], "scale_results": [],
        "assembly_reports": [], "imagegen_jobs": [], "source_files": [],
        "reconstruction_image2_by_page": {},
    }

    def merge(value: object) -> None:
        if isinstance(value, (str, Path)):
            merge(_read_path(Path(value)))
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                merge(item)
            return
        if not isinstance(value, Mapping):
            raise TypeError("project_or_runs must be a path, mapping, or sequence of those")
        for name in ("environment", "plugin_commit", "measurement_scopes"):
            if name in value and name not in merged:
                merged[name] = value[name]
        for name in ("events", "callback_stage_events"):
            items = value.get(name)
            if isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
                merged["events"].extend(dict(item) for item in items if isinstance(item, Mapping))  # type: ignore[union-attr]
        for singular, plural in (
            ("pipeline_report", "pipeline_reports"),
            ("scale_result", "scale_results"),
            ("assembly_report", "assembly_reports"),
        ):
            item = value.get(singular)
            if isinstance(item, Mapping):
                merged[plural].append(dict(item))  # type: ignore[union-attr]
            items = value.get(plural)
            if isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
                merged[plural].extend(dict(item) for item in items if isinstance(item, Mapping))  # type: ignore[union-attr]
        jobs = value.get("imagegen_jobs")
        if isinstance(jobs, Sequence) and not isinstance(jobs, (str, bytes, bytearray)):
            merged["imagegen_jobs"].extend(dict(item) for item in jobs if isinstance(item, Mapping))  # type: ignore[union-attr]
        sources = value.get("source_files")
        if isinstance(sources, Sequence) and not isinstance(sources, (str, bytes, bytearray)):
            merged["source_files"].extend(str(item) for item in sources)  # type: ignore[union-attr]
        recon = value.get("reconstruction_image2_by_page")
        if isinstance(recon, Mapping):
            for page, count in recon.items():
                if type(count) is not int or count < 0:
                    raise ValueError("reconstruction Image2 counts must be non-negative integers")
                merged["reconstruction_image2_by_page"][str(int(page))] = count  # type: ignore[index]

    merge(project_or_runs)
    return merged


def _history_transitions(
    history: object, *, source: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    contractions: list[dict[str, object]] = []
    recoveries: list[dict[str, object]] = []
    if not isinstance(history, list):
        return contractions, recoveries
    values = [value for value in history if type(value) is int]
    for before, after in zip(values, values[1:]):
        if after < before:
            contractions.append({
                "before": before, "after": after, "page_number": None,
                "source": source, "trigger": "unavailable",
            })
        elif after > before:
            recoveries.append({
                "before": before, "after": after, "page_number": None,
                "source": source, "trigger": "unavailable",
            })
    return contractions, recoveries


def build_performance_report(project_or_runs: object) -> dict[str, object]:
    """Build a cross-page report from durable evidence and runner artifacts."""
    inputs = _normalize_inputs(project_or_runs)
    raw_events = list(inputs["events"])  # type: ignore[arg-type]
    unique_events: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in raw_events:
        if not isinstance(raw, Mapping):
            continue
        event = dict(raw)
        key = _event_key(event)
        if key in seen:
            continue
        seen.add(key)
        unique_events.append(event)

    stages = [event for event in unique_events if event.get("event") == "stage"]
    calls = [event for event in unique_events if event.get("event") == "call"]
    stage_by_name: dict[str, list[dict[str, object]]] = defaultdict(list)
    call_by_kind: dict[str, list[dict[str, object]]] = defaultdict(list)
    for event in stages:
        if isinstance(event.get("name"), str):
            stage_by_name[str(event["name"])].append(event)
    for event in calls:
        if isinstance(event.get("kind"), str):
            call_by_kind[str(event["kind"])].append(event)

    pages = {
        int(event["page_number"])
        for event in unique_events
        if type(event.get("page_number")) is int and int(event["page_number"]) > 0
    }
    pipeline_reports = list(inputs["pipeline_reports"])  # type: ignore[arg-type]
    scale_results = list(inputs["scale_results"])  # type: ignore[arg-type]
    for pipeline in pipeline_reports:
        if not isinstance(pipeline, Mapping):
            continue
        completed = pipeline.get("completed_pages")
        if isinstance(completed, list):
            pages.update(int(page) for page in completed if type(page) is int and page > 0)
        failed = pipeline.get("failed_pages")
        if isinstance(failed, Mapping):
            pages.update(int(page) for page in failed if str(page).isdigit() and int(page) > 0)

    director_records = _prefer_calls_per_page(
        call_by_kind["page_director"], stage_by_name["page_director"]
    )
    review_records = _prefer_calls_per_page(
        call_by_kind["visual_review"], stage_by_name["visual_review"]
    )
    correction_records = _prefer_calls_per_page(
        call_by_kind["correction_decision"], stage_by_name["correction_decision"]
    )
    image_total_records = _select_image2_total_records(
        call_by_kind["image2"],
        stage_by_name["image2_queue"],
        stage_by_name["image2_execution"],
    )

    stage_report = {
        "materials": _merge_stats(stage_by_name["material_preparation"], "materials"),
        "codex_director": _merge_stats(director_records, "Codex director"),
        "image2_total": _merge_stats(image_total_records, "Image2 total"),
        "image2_queue": _merge_stats(stage_by_name["image2_queue"], "Image2 queue"),
        "image2_execution": _merge_stats(stage_by_name["image2_execution"], "Image2 execution"),
        "independent_review": _merge_stats(review_records, "independent review"),
        "reconstruction": _merge_stats(stage_by_name["reconstruction"], "reconstruction"),
        "fixed_and_assembly": _merge_stats(
            stage_by_name["fixed_layer_assembly"], "fixed layer and assembly"
        ),
        "lock_waits": _merge_stats(stage_by_name["lock_wait"], "lock wait"),
        "recovery": _merge_stats(stage_by_name["recovery"], "recovery"),
    }

    codex_records = director_records + review_records + correction_records
    oauth_records = stage_by_name["oauth_wait"]
    office_records = stage_by_name["office_wait"]
    network_records = stage_by_name["network_wait"]
    local_records: list[dict[str, object]] = []
    for stage in stages:
        if stage.get("kind") in {"local", "reconstruction"}:
            local_records.append({**stage, "duration_seconds": stage.get("local_duration_seconds")})
    external_groups = {
        "Codex": codex_records,
        "Image2": image_total_records,
        "OAuth": oauth_records,
        "Office": office_records,
        "network": network_records,
    }
    external_records: list[Mapping[str, object]] = []
    for service, records in external_groups.items():
        if records:
            external_records.extend(records)
        else:
            external_records.append({
                "duration_seconds": None,
                "unavailable_reason": f"no durable {service} wait events were provided",
            })
    stage_report["external_waits"] = _merge_stats(external_records, "external wait")
    waits = {
        "local": _merge_stats(local_records, "local work"),
        "codex": _merge_stats(codex_records, "Codex wait"),
        "image2": _merge_stats(image_total_records, "Image2 wait"),
        "oauth": _merge_stats(oauth_records, "OAuth wait"),
        "office": _merge_stats(office_records, "Office wait"),
        "network": _merge_stats(network_records, "network wait"),
    }

    image_per_page = Counter(
        int(call["page_number"])
        for call in call_by_kind["image2"]
        if type(call.get("page_number")) is int
    )
    if call_by_kind["image2"]:
        image_total_count: object = len(call_by_kind["image2"])
        image_counts: dict[str, object] = {
            str(page): image_per_page.get(page, 0) for page in sorted(pages)
        }
    else:
        image_total_count = _unavailable("no durable Image2 call evidence was provided")
        image_counts = {
            str(page): _unavailable("no page-level Image2 call evidence was provided")
            for page in sorted(pages)
        }
    explicit_recon = dict(inputs["reconstruction_image2_by_page"])  # type: ignore[arg-type]
    recon_counts: dict[str, object]
    if explicit_recon:
        pages.update(int(page) for page in explicit_recon)
        unknown_recon_pages = [page for page in sorted(pages) if str(page) not in explicit_recon]
        recon_counts = {
            str(page): explicit_recon[str(page)] if str(page) in explicit_recon
            else _unavailable("no authoritative reconstruction Image2 ledger for this page")
            for page in sorted(pages)
        }
        known_total = sum(int(value) for value in explicit_recon.values())
        if unknown_recon_pages:
            recon_total = {
                "status": "partial",
                "known_total": known_total,
                "known_pages": sorted(int(page) for page in explicit_recon),
                "unknown_pages": unknown_recon_pages,
                "reason": "one or more discovered pages lack an authoritative reconstruction ledger",
            }
        else:
            recon_total = known_total
    elif call_by_kind["reconstruct_edit"]:
        observed = Counter(
            int(call["page_number"])
            for call in call_by_kind["reconstruct_edit"]
            if type(call.get("page_number")) is int
        )
        recon_counts = {
            str(page): observed.get(page, _unavailable("no reconstruction call-count authority for this page"))
            for page in sorted(pages)
        }
        recon_total = sum(observed.values())
    else:
        recon_counts = {
            str(page): _unavailable("candidate evidence ends before reconstruction")
            for page in sorted(pages)
        }
        recon_total = _unavailable("no run-level reconstruction Image2 call evidence was provided")

    review_corrections = [
        call for call in call_by_kind["visual_review"]
        if isinstance(call.get("metadata"), Mapping)
        and call["metadata"].get("decision") == "correct"  # type: ignore[union-attr]
    ]
    categories: Counter[str] = Counter()
    uncategorized = 0
    for call in review_corrections:
        metadata = call.get("metadata")
        category = metadata.get("reason_category") if isinstance(metadata, Mapping) else None
        if isinstance(category, str) and category.strip():
            categories[category.strip()] += 1
        else:
            uncategorized += 1

    contractions: list[dict[str, object]] = []
    recoveries: list[dict[str, object]] = []
    http_429 = 0
    for event in unique_events:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else event
        status_code = metadata.get("status_code") if isinstance(metadata, Mapping) else None
        if status_code == 429 or str(event.get("status", "")).strip() == "429":
            http_429 += 1
        before = metadata.get("concurrency_before") if isinstance(metadata, Mapping) else None
        after = metadata.get("concurrency_after") if isinstance(metadata, Mapping) else None
        if type(before) is int and type(after) is int and before != after:
            transition = {"before": before, "after": after, "page_number": event.get("page_number")}
            if after < before:
                contractions.append(transition)
            else:
                recoveries.append(transition)
    for scale in scale_results:
        if not isinstance(scale, Mapping):
            continue
        for key in ("first_run_scheduler_concurrency_history", "first_run_gate_concurrency_history"):
            down, up = _history_transitions(scale.get(key), source=key)
            contractions.extend(down)
            recoveries.extend(up)

    cache_events = [event for event in unique_events if event.get("event") == "attachment_cache"]
    cache_values_valid = bool(cache_events) and all(
        type(event.get("hits")) is int and int(event["hits"]) >= 0
        and type(event.get("misses")) is int and int(event["misses"]) >= 0
        for event in cache_events
    )
    if cache_values_valid:
        hits = sum(int(event["hits"]) for event in cache_events)
        misses = sum(int(event["misses"]) for event in cache_events)
        cache_total = hits + misses
        attachment_cache: dict[str, object] = {
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hits / cache_total, 6) if cache_total else None,
        }
        if cache_total == 0:
            attachment_cache["reason"] = "no attachment cache lookups were recorded"
    else:
        attachment_cache = {
            "hits": None, "misses": None, "hit_rate": None,
            "reason": "no durable attachment cache events were provided",
        }

    recovery_events = [event for event in unique_events if event.get("event") == "recovery"]

    failed_pages: dict[str, str] = {}
    completed_pages: set[int] = set()
    for pipeline in pipeline_reports:
        if not isinstance(pipeline, Mapping):
            continue
        completed = pipeline.get("completed_pages")
        if isinstance(completed, list):
            completed_pages.update(int(page) for page in completed if type(page) is int)
        failed = pipeline.get("failed_pages")
        if isinstance(failed, Mapping):
            failed_pages.update({str(page): str(reason) for page, reason in failed.items()})
    for event in unique_events:
        if event.get("status") == "error" and type(event.get("page_number")) is int:
            failed_pages.setdefault(str(event["page_number"]), "stage or call recorded status=error")
    isolation: object
    if pipeline_reports:
        isolation = not bool(completed_pages.intersection(int(page) for page in failed_pages))
        failure_count: object = len(failed_pages)
    else:
        isolation = _unavailable("no production pipeline result was provided")
        failure_count = _unavailable(
            "no production pipeline result was provided; event errors are not a complete page-failure set"
        )

    if not pipeline_reports:
        recovery_report: dict[str, object] = {
            "status": "unavailable",
            "observed_events": len(recovery_events),
            "verified_events": 0,
            "pages": [],
            "skipped_calls": {},
            "reason": "completed-page recovery requires a production PipelineReport",
        }
    else:
        verified_recoveries: list[dict[str, object]] = []
        invalid_recoveries = 0
        for index, event in enumerate(unique_events):
            if event.get("event") != "recovery" or type(event.get("page_number")) is not int:
                continue
            page_number = int(event["page_number"])
            skipped_values = event.get("skipped_calls")
            stream_identity = tuple(event.get(field) for field in (
                "experiment_id", "workspace_identity_sha256",
                "source_snapshot_sha256", "page_number",
            ))
            later_duplicate = any(
                later.get("event") == "call"
                and tuple(later.get(field) for field in (
                    "experiment_id", "workspace_identity_sha256",
                    "source_snapshot_sha256", "page_number",
                )) == stream_identity
                and isinstance(skipped_values, list)
                and later.get("kind") in skipped_values
                for later in unique_events[index + 1:]
            )
            if page_number in completed_pages and str(page_number) not in failed_pages and not later_duplicate:
                verified_recoveries.append(event)
            else:
                invalid_recoveries += 1
        verified_skipped = Counter(
            str(value)
            for event in verified_recoveries
            for value in event.get("skipped_calls", [])
        )
        verified_pages = sorted({int(event["page_number"]) for event in verified_recoveries})
        if invalid_recoveries:
            recovery_report = {
                "status": "unverified",
                "observed_events": len(recovery_events),
                "verified_events": len(verified_recoveries),
                "pages": verified_pages,
                "skipped_calls": dict(sorted(verified_skipped.items())),
                "reason": (
                    "a recovery event lacks completed-page authority or is followed by a skipped call"
                ),
            }
        else:
            recovery_report = {
                "events": len(verified_recoveries),
                "pages": verified_pages,
                "skipped_calls": dict(sorted(verified_skipped.items())),
            }

    peak_stages: dict[str, int] = {}
    peak_pages: list[int] = []
    for artifact in pipeline_reports + scale_results:
        if not isinstance(artifact, Mapping):
            continue
        stage_peaks = artifact.get("stage_peaks")
        if isinstance(stage_peaks, Mapping):
            for name, value in stage_peaks.items():
                if type(value) is int and value >= 0:
                    peak_stages[str(name)] = max(peak_stages.get(str(name), 0), value)
        page_peak = artifact.get("page_peak")
        if type(page_peak) is int and page_peak >= 0:
            peak_pages.append(page_peak)

    resource_values: dict[str, list[int]] = defaultdict(list)
    for event in unique_events:
        samples: list[Mapping[str, object]] = []
        for key in ("resource_start", "resource_end", "resources"):
            value = event.get(key)
            if isinstance(value, Mapping):
                samples.append(value)
        if event.get("event") == "resource":
            samples.append(event)
        for sample in samples:
            for key in _RESOURCE_OUTPUTS:
                value = sample.get(key)
                if type(value) is int and value >= 0:
                    resource_values[key].append(value)
    resources = {
        output: max(resource_values[key]) if resource_values[key]
        else _unavailable(f"no durable {key} samples were provided")
        for key, output in _RESOURCE_OUTPUTS.items()
    }

    scheduler_fields = (
        "requested_page_count", "completed_page_count", "elapsed_seconds",
        "serial_baseline_seconds", "elapsed_to_serial_ratio",
        "throughput_pages_per_second",
    )
    deterministic_scheduler: dict[str, object]
    complete_scale = next((
        scale for scale in reversed(scale_results)
        if isinstance(scale, Mapping) and all(scale.get(field) is not None for field in scheduler_fields)
    ), None)
    if isinstance(complete_scale, Mapping):
        deterministic_scheduler = {
            "status": "measured",
            **{field: complete_scale[field] for field in scheduler_fields},
            "scope": "deterministic test workload; not real provider speed",
        }
    else:
        deterministic_scheduler = _unavailable(
            "no complete deterministic scheduler timing artifact was provided"
        )

    report: dict[str, object] = {
        "schema_version": "awesome-workflow-performance-v1",
        "environment": inputs.get("environment", _unavailable("environment metadata was not provided")),
        "plugin_commit": inputs.get("plugin_commit", _unavailable("plugin commit was not provided")),
        "measurement_scopes": inputs.get(
            "measurement_scopes",
            _unavailable("measurement scopes were not provided"),
        ),
        "pages": sorted(pages),
        "stages": stage_report,
        "waits": waits,
        "calls": {
            "image2": {"total": image_total_count, "per_page": image_counts},
            "reconstruction_image2": {"total": recon_total, "per_page": recon_counts},
        },
        "qa_corrections": {
            "triggered": len(review_corrections),
            "applied": len(call_by_kind["correction_decision"]),
            "reason_categories": dict(sorted(categories.items())),
            "uncategorized": uncategorized,
        },
        "rate_limits": {
            "http_429_events": http_429 if http_429 else _unavailable(
                "no durable HTTP status-code events were provided"
            ),
            "contractions": contractions,
            "recoveries": recoveries,
        },
        "attachment_cache": attachment_cache,
        "completed_page_recovery": recovery_report,
        "failures": {
            "count": failure_count,
            "pages": dict(sorted(failed_pages.items(), key=lambda item: int(item[0]))),
            "isolated": isolation,
        },
        "concurrency": {
            "peak_pages": max(peak_pages) if peak_pages else _unavailable(
                "no production page-concurrency peak was provided"
            ),
            "peak_stages": dict(sorted(peak_stages.items())) if peak_stages else _unavailable(
                "no production stage-concurrency peaks were provided"
            ),
        },
        "deterministic_scheduler": deterministic_scheduler,
        "resources": resources,
        "integrity": {
            "input_event_count": len(raw_events),
            "unique_event_count": len(unique_events),
            "duplicate_events_ignored": len(raw_events) - len(unique_events),
        },
        "source_files": sorted(set(inputs["source_files"])),  # type: ignore[arg-type]
    }
    return report


__all__ = ["build_performance_report"]

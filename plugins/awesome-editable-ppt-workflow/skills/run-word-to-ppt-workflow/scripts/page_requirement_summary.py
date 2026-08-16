"""Build and verify the read-only page requirement summary shown by Confirm UI."""

from __future__ import annotations

import hashlib
import copy
import json
import re
import uuid
from pathlib import Path
from typing import Any, Mapping

from codex_web_material_gateway import sign_project_payload, verify_project_payload_signature
from natural_comment_resolver import ResolvedDirective, SearchRequest, resolve_page_comments
from project_artifact_path import project_artifact_path


ARTIFACT_VERSION = "page-requirement-summary-v1"
PRECEDENCE_NOTICE = "分页Word批注覆盖本页的全局软风格；Word事实和固定层不可覆盖"
SUMMARY_PATH = Path("confirm_ui") / "page_requirement_summary.json"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SIGNATURE_PURPOSE = "page-comment-resolution-v1"
_ENTRY_SIGNATURE_PURPOSE = "page-comment-resolution-entry-v1"
_SOURCE_LOCK_PATH = Path("01_page_contracts") / "source_lock.json"

_PROTECTED = {
    "fixed.body_geometry": "fixed_layer_override_rejected",
    "fixed.logo": "fixed_layer_override_rejected",
    "fixed.page_title": "fixed_layer_override_rejected",
    "fixed.footer": "fixed_layer_override_rejected",
    "fixed.page_number": "fixed_layer_override_rejected",
    "word.body_text": "word_fact_override_rejected",
    "word.facts": "word_fact_override_rejected",
    "word.tables": "word_fact_override_rejected",
}
_MATERIAL_ACTIONS = {
    "material.search_evidence": "搜索并提供外部图片素材",
    "material.page_image": "使用本页Word图片素材",
    "material.attachment": "使用附件素材",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _seal(value: Mapping[str, Any]) -> str:
    return _digest({
        key: item for key, item in value.items()
        if key not in {"sealed_sha256", "projectSignature"}
    })


def _signature_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "projectSignature"}


def _entry_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: item for key, item in value.items()
        if key not in {"pageEntrySha256", "pageEntrySignature"}
    }


def _seal_page_entry(value: Mapping[str, Any]) -> str:
    return _digest(_entry_payload(value))


def _sign_page_entry(project: Path, value: Mapping[str, Any]) -> str:
    return sign_project_payload(
        project, _entry_payload(value), purpose=_ENTRY_SIGNATURE_PURPOSE,
    )


def _verify_summary_envelope(project: Path, value: Mapping[str, Any]) -> bool:
    """Authenticate persisted bytes without requiring them to match newer project authority."""
    try:
        return (
            isinstance(value, Mapping)
            and value.get("artifact_version") == ARTIFACT_VERSION
            and value.get("sealed_sha256") == _seal(value)
            and verify_project_payload_signature(
                project, _signature_payload(value), purpose=_SIGNATURE_PURPOSE,
                signature=value.get("projectSignature"),
            )
        )
    except (KeyError, TypeError, ValueError):
        return False


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _closed_record(directive: ResolvedDirective) -> dict[str, Any]:
    record = {
        "directive": directive.authority_directive(),
        "source_comment_id": directive.source_comment_id,
        "semantic_kind": directive.kind,
        "required": directive.required,
        "visual_overrides": dict(directive.visual_overrides),
        "search_required": directive.search_required,
        "search_query": directive.search_query,
        "resolution_receipt": dict(directive.resolution_receipt),
    }
    if directive.search_requests:
        record["search_requests"] = [dict(vars(request)) for request in directive.search_requests]
    return record


def _resolved_directive(record: Mapping[str, Any]) -> ResolvedDirective:
    directive = record["directive"]
    return ResolvedDirective(
        directive_id=str(directive["directive_id"]),
        source_comment_id=str(record["source_comment_id"]),
        raw_text=str(directive["text"]),
        kind=str(record["semantic_kind"]),
        required=bool(record["required"]),
        search_required=bool(record["search_required"]),
        search_query=record["search_query"],
        visual_overrides=dict(record["visual_overrides"]),
        authority_kind=str(directive["kind"]),
        decisions=tuple(dict(item) for item in directive["decisions"]),
        resolution_receipt=dict(record["resolution_receipt"]),
        search_requests=tuple(
            SearchRequest(**dict(item)) for item in record.get("search_requests", [])
        ),
    )


def _presentation(
    page: int,
    records: list[Mapping[str, Any]],
    *,
    page_contract_sha256: str,
) -> dict[str, Any]:
    directives: list[str] = []
    searches: list[str] = []
    actions: list[str] = []
    rejected: list[str] = []
    for record in records:
        directive = record["directive"]
        directives.append(str(directive["text"]))
        query = record.get("search_query")
        if record.get("search_required") is True and isinstance(query, str) and query:
            searches.append(query)
        for request in record.get("search_requests", []):
            if isinstance(request, Mapping) and isinstance(request.get("query"), str):
                searches.append(request["query"])
        for decision in directive["decisions"]:
            target = decision["target"]
            if target in _MATERIAL_ACTIONS:
                actions.append(_MATERIAL_ACTIONS[target])
            if target in _PROTECTED:
                rejected.append(_PROTECTED[target])
    return {
        "page": page,
        "pageContractSha256": page_contract_sha256,
        "directives": list(dict.fromkeys(directives)),
        "plannedSearches": list(dict.fromkeys(searches)),
        "materialActions": list(dict.fromkeys(actions)),
        "rejectedHardRuleOverrides": list(dict.fromkeys(rejected)),
    }


def _validate_closed_record(record: Any) -> None:
    legacy_fields = {
        "directive", "source_comment_id", "semantic_kind", "required", "visual_overrides",
        "search_required", "search_query", "resolution_receipt",
    }
    if not isinstance(record, Mapping) or frozenset(record) not in {frozenset(legacy_fields), frozenset(legacy_fields | {"search_requests"})}:
        raise ValueError("page requirement closed directive is invalid")
    directive = record["directive"]
    if not isinstance(directive, Mapping) or set(directive) != {
        "directive_id", "kind", "text", "decisions",
    }:
        raise ValueError("page requirement directive is invalid")
    if not isinstance(directive["directive_id"], str) or not directive["directive_id"]:
        raise ValueError("page requirement directive id is invalid")
    if not isinstance(directive["text"], str) or not directive["text"].strip():
        raise ValueError("page requirement directive text is invalid")
    if not isinstance(directive["decisions"], list):
        raise ValueError("page requirement directive decisions are invalid")
    if record["source_comment_id"] != receipt_source_id(record):
        raise ValueError("page requirement source comment identity is invalid")
    if not isinstance(record["semantic_kind"], str) or not record["semantic_kind"]:
        raise ValueError("page requirement semantic kind is invalid")
    if type(record["required"]) is not bool or not isinstance(record["visual_overrides"], Mapping):
        raise ValueError("page requirement resolved directive metadata is invalid")
    for decision in directive["decisions"]:
        if not isinstance(decision, Mapping):
            raise ValueError("page requirement decision is invalid")
        target = decision.get("target")
        action = decision.get("action")
        if target in _PROTECTED:
            if action != "set" or set(decision) != {"target", "action", "value"}:
                raise ValueError("page requirement protected override decision is invalid")
        elif target in _MATERIAL_ACTIONS:
            enterprise_fields = {
                "target", "action", "material_id", "directive_id", "parent_directive_id",
                "entity", "query", "material_role",
            }
            decision_fields = set(decision)
            if action != "require" or decision_fields not in ({"target", "action", "material_id"}, enterprise_fields):
                raise ValueError("page requirement material decision is invalid")
            if decision_fields == enterprise_fields and (
                target != "material.search_evidence" or decision.get("material_role") != "enterprise_logo"
            ):
                raise ValueError("page requirement enterprise Logo decision is invalid")
        elif target in {"visual.image_rendering", "visual.image_ratio", "visual.layout"}:
            if action != "set" or set(decision) != {"target", "action", "value"}:
                raise ValueError("page requirement visual decision is invalid")
        else:
            raise ValueError("page requirement decision target is invalid")
    if type(record["search_required"]) is not bool:
        raise ValueError("page requirement search flag is invalid")
    query = record["search_query"]
    search_requests = record.get("search_requests", [])
    if search_requests:
        if not isinstance(search_requests, list) or not all(isinstance(item, Mapping) for item in search_requests):
            raise ValueError("page requirement child searches are invalid")
        expected_fields = {
            "directive_id", "parent_directive_id", "source_comment_id", "entity", "query",
            "material_id", "material_role", "required", "max_results",
        }
        if any(set(item) != expected_fields for item in search_requests):
            raise ValueError("page requirement child search shape is invalid")
        if len({item["directive_id"] for item in search_requests}) != len(search_requests):
            raise ValueError("page requirement child directive identity is duplicated")
        if len({item["material_id"] for item in search_requests}) != len(search_requests):
            raise ValueError("page requirement child material identity is duplicated")
        for item in search_requests:
            if (
                item["parent_directive_id"] != directive["directive_id"]
                or item["source_comment_id"] != record["source_comment_id"]
                or item["material_role"] != "enterprise_logo"
                or item["required"] is not True
                or item["max_results"] != 1
            ):
                raise ValueError("page requirement child search binding is invalid")
        child_decisions = [
            item for item in directive["decisions"]
            if item.get("material_role") == "enterprise_logo"
        ]
        expected_bindings = [
            (item["directive_id"], item["material_id"], item["entity"], item["query"])
            for item in search_requests
        ]
        actual_bindings = [
            (item["directive_id"], item["material_id"], item["entity"], item["query"])
            for item in child_decisions
        ]
        if actual_bindings != expected_bindings:
            raise ValueError("page requirement child searches do not match their closed decisions")
    if record["search_required"] and not search_requests and (not isinstance(query, str) or not query.strip()):
        raise ValueError("page requirement planned search is missing")
    if search_requests and query is not None:
        raise ValueError("page requirement grouped search must not have a scalar query")
    if not record["search_required"] and (query is not None or search_requests):
        raise ValueError("page requirement has unexpected search query")
    has_search_decision = any(
        decision.get("target") == "material.search_evidence"
        for decision in directive["decisions"]
    )
    if has_search_decision != record["search_required"]:
        raise ValueError("page requirement search decision is inconsistent")
    receipt = record["resolution_receipt"]
    if not isinstance(receipt, Mapping):
        raise ValueError("page requirement resolution receipt is invalid")
    if receipt.get("directive_id") != directive["directive_id"]:
        raise ValueError("page requirement resolution receipt directive mismatch")
    if receipt.get("closed_directive_sha256") != _digest(directive):
        raise ValueError("page requirement resolution receipt closure mismatch")
    raw_sha = receipt.get("raw_comment_sha256")
    if not isinstance(raw_sha, str) or _SHA256.fullmatch(raw_sha) is None:
        raise ValueError("page requirement resolution receipt text identity is invalid")
    if receipt.get("resolution_mode") != "deterministic":
        raise ValueError("page requirement resolution mode is invalid")


def receipt_source_id(record: Mapping[str, Any]) -> Any:
    receipt = record.get("resolution_receipt")
    return receipt.get("source_comment_id") if isinstance(receipt, Mapping) else None


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write_summary(project: Path, path: Path, artifact: dict[str, Any]) -> dict[str, Any]:
    project = Path(project)
    safe_path = project_artifact_path(project, SUMMARY_PATH, create_parent=True)
    if Path(path) != safe_path:
        raise ValueError("page requirement summary path is not project-local")
    path = safe_path
    artifact["sealed_sha256"] = _seal(artifact)
    artifact["projectSignature"] = sign_project_payload(
        project, _signature_payload(artifact), purpose=_SIGNATURE_PURPOSE,
    )
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary = project_artifact_path(project, temporary)
    try:
        temporary.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return artifact


def _page_lock_digests(project: Path) -> dict[int, str]:
    source_lock = _read_object(Path(project) / _SOURCE_LOCK_PATH)
    records = source_lock.get("pages")
    if not isinstance(records, list):
        raise ValueError("source lock pages are invalid")
    result: dict[int, str] = {}
    for record in records:
        if not isinstance(record, Mapping) or type(record.get("page_number")) is not int:
            raise ValueError("source lock page record is invalid")
        page = record["page_number"]
        if page in result:
            raise ValueError("source lock page record is duplicated")
        result[page] = _digest(record)
    return result


def _verify_page_entry_against(
    project: Path,
    item: Any,
    expected_page: int,
    current_contract: tuple[str, Path, dict[str, Any]],
    page_lock_sha256: str,
) -> bool:
    try:
        if not isinstance(item, Mapping) or set(item) != {
            "page", "directives", "plannedSearches", "materialActions",
            "rejectedHardRuleOverrides", "closedDirectives", "pageContractSha256",
            "pageContractPath", "rawCommentsSha256", "pageLockSha256",
            "pageEntrySha256", "pageEntrySignature",
        }:
            return False
        if type(item["page"]) is not int or item["page"] != expected_page:
            return False
        if (
            item["pageEntrySha256"] != _seal_page_entry(item)
            or not verify_project_payload_signature(
                project, _entry_payload(item), purpose=_ENTRY_SIGNATURE_PURPOSE,
                signature=item["pageEntrySignature"],
            )
        ):
            return False
        relative, contract_path, contract = current_contract
        if (
            item["pageContractPath"] != relative
            or item["pageContractSha256"] != _file_sha256(contract_path)
            or item["rawCommentsSha256"] != _digest(contract.get("page_comments", []))
            or item["pageLockSha256"] != page_lock_sha256
        ):
            return False
        records = item["closedDirectives"]
        if not isinstance(records, list):
            return False
        for record in records:
            _validate_closed_record(record)
        comments = contract.get("page_comments", [])
        if not isinstance(comments, list) or len(records) != len(comments):
            return False
        for record, comment in zip(records, comments, strict=True):
            if not isinstance(comment, Mapping):
                return False
            if record["source_comment_id"] != comment.get("comment_id"):
                return False
            if record["resolution_receipt"].get("raw_comment_sha256") != hashlib.sha256(
                str(comment.get("text", "")).encode("utf-8")
            ).hexdigest():
                return False
        presentation = _presentation(
            item["page"], records, page_contract_sha256=item["pageContractSha256"]
        )
        return all(
            item[field] == presentation[field]
            for field in (
                "directives", "plannedSearches", "materialActions",
                "rejectedHardRuleOverrides",
            )
        )
    except (KeyError, TypeError, ValueError):
        return False


def _project_authority(project: Path) -> tuple[dict[str, Any], list[tuple[str, Path, dict[str, Any]]]]:
    project = Path(project).resolve(strict=True)
    state = _read_object(project / "workflow_run.json")
    word = state.get("word_source")
    pagination = state.get("pagination")
    jobs = state.get("jobs")
    if not isinstance(word, Mapping) or not isinstance(pagination, Mapping) or not isinstance(jobs, list):
        raise ValueError("workflow comment-resolution authority is incomplete")
    page_count = pagination.get("page_count")
    order = pagination.get("locked_page_order")
    if type(page_count) is not int or page_count < 1:
        raise ValueError("workflow page count is invalid")
    expected_order = list(range(1, page_count + 1))
    if (
        not isinstance(order, list)
        or len(order) != len(expected_order)
        or any(type(item) is not int or item != expected for item, expected in zip(order, expected_order))
    ):
        raise ValueError("workflow is missing the complete ordered page sequence lock")
    word_path = (project / str(word.get("path"))).resolve()
    if word_path.parent != (project / "00_source").resolve() or not word_path.is_file():
        raise ValueError("workflow Word source path is invalid")
    word_sha = _file_sha256(word_path)
    if word_sha != word.get("sha256"):
        raise ValueError("workflow Word source changed")
    source_lock_path = (project / _SOURCE_LOCK_PATH).resolve()
    if not source_lock_path.is_file():
        raise ValueError("source lock is missing")
    source_lock = _read_object(source_lock_path)
    lock_records = source_lock.get("pages")
    if not isinstance(lock_records, list):
        raise ValueError("source lock pages are invalid")
    if any(
        not isinstance(item, Mapping)
        or type(item.get("page_number")) is not int
        or not 1 <= item["page_number"] <= page_count
        for item in lock_records
    ):
        raise ValueError("source lock page identity is invalid")
    if any(
        not isinstance(item, Mapping)
        or type(item.get("page_number")) is not int
        or not 1 <= item["page_number"] <= page_count
        for item in jobs
    ):
        raise ValueError("workflow job page identity is invalid")
    contracts: list[tuple[str, Path, dict[str, Any]]] = []
    for page in expected_order:
        page_jobs = [item for item in jobs if isinstance(item, Mapping) and item.get("page_number") == page]
        if len(page_jobs) != 1:
            raise ValueError(f"workflow page {page} provenance is not unique")
        relative = page_jobs[0].get("contract_file")
        expected = f"01_page_contracts/page_{page:03d}.json"
        if relative != expected:
            raise ValueError(f"workflow page {page} contract path is invalid")
        path = (project / expected).resolve()
        if path.parent != (project / "01_page_contracts").resolve() or not path.is_file():
            raise ValueError(f"workflow page {page} contract is missing")
        contract = _read_object(path)
        if type(contract.get("page_number")) is not int or contract["page_number"] != page:
            raise ValueError(f"workflow page {page} contract identity is invalid")
        lock = [item for item in lock_records if isinstance(item, Mapping) and item.get("page_number") == page]
        if len(lock) != 1 or lock[0].get("contract_sha256") != _file_sha256(path):
            raise ValueError("source lock contract_sha256 does not match the locked page contract")
        contracts.append((expected, path, contract))
    authority = {
        "wordSourceSha256": word_sha,
        "sourceLockPath": _SOURCE_LOCK_PATH.as_posix(),
        "sourceLockSha256": _file_sha256(source_lock_path),
        "lockedPageOrder": expected_order,
    }
    return authority, contracts


def load_current_project_page_contracts(
    project: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return contracts only after the canonical strict project authority check."""
    authority, records = _project_authority(project)
    return authority, [contract for _relative, _path, contract in records]


def load_verified_project_page_contracts(
    project: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return UI-safe contracts only when the complete shared artifact also verifies."""
    authority, contracts = load_current_project_page_contracts(project)
    path = project_artifact_path(Path(project), SUMMARY_PATH)
    if not path.is_file() or not verify_page_requirement_summary(project, _read_object(path)):
        raise ValueError("page requirement summary seal or authority is invalid")
    return authority, contracts


def verify_page_requirement_summary(project: Path, value: Mapping[str, Any]) -> bool:
    try:
        if not isinstance(value, Mapping) or set(value) != {
            "artifact_version", "precedenceNotice", "projectAuthority", "page_count", "pages",
            "sealed_sha256", "projectSignature",
        }:
            return False
        if value["artifact_version"] != ARTIFACT_VERSION or value["precedenceNotice"] != PRECEDENCE_NOTICE:
            return False
        if not isinstance(value["pages"], list) or value["sealed_sha256"] != _seal(value):
            return False
        if not verify_project_payload_signature(
            project,
            _signature_payload(value),
            purpose=_SIGNATURE_PURPOSE,
            signature=value["projectSignature"],
        ):
            return False
        current_authority, current_contracts = _project_authority(project)
        artifact_order = value["projectAuthority"].get("lockedPageOrder")
        if (
            not isinstance(artifact_order, list)
            or any(type(item) is not int for item in artifact_order)
        ):
            return False
        if value["projectAuthority"] != current_authority:
            return False
        page_count = value["page_count"]
        locked_order = current_authority["lockedPageOrder"]
        lock_digests = _page_lock_digests(project)
        if (
            type(page_count) is not int
            or page_count < 1
            or page_count != len(value["pages"])
            or page_count != len(current_contracts)
            or page_count != len(locked_order)
        ):
            return False
        for expected_page, (item, current_contract) in enumerate(
            zip(value["pages"], current_contracts, strict=True), start=1,
        ):
            if not _verify_page_entry_against(
                project, item, expected_page, current_contract, lock_digests[expected_page],
            ):
                return False
        return True
    except (KeyError, TypeError, ValueError):
        return False


def public_requirement_summary(project: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    if not verify_page_requirement_summary(project, value):
        raise ValueError("page requirement summary seal or directive closure is invalid")
    return {
        "precedenceNotice": PRECEDENCE_NOTICE,
        "pageRequirementSummary": [
            {
                "page": item["page"],
                "directives": list(item["directives"]),
                "plannedSearches": list(item["plannedSearches"]),
                "materialActions": list(item["materialActions"]),
                "rejectedHardRuleOverrides": list(item["rejectedHardRuleOverrides"]),
                "readOnly": True,
            }
            for item in value["pages"]
        ],
    }


def build_page_requirement_summary(
    project: Path,
    contracts: list[Mapping[str, Any]],
    *,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Resolve page comments once before UI and seal their read-only projection."""
    project = Path(project)
    path = project_artifact_path(project, SUMMARY_PATH, create_parent=True)
    project_authority, current_contracts = _project_authority(project)
    if any(
        not isinstance(contract, Mapping)
        or type(contract.get("page_number")) is not int
        or contract["page_number"] < 1
        for contract in contracts
    ):
        raise ValueError("supplied page contract identity is invalid")
    if [dict(contract) for contract in contracts] != [item[2] for item in current_contracts]:
        raise ValueError("supplied page contracts do not equal the current locked project")
    reusable_pages: dict[int, Mapping[str, Any]] = {}
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if verify_page_requirement_summary(project, existing):
            return existing
        if not _verify_summary_envelope(project, existing):
            raise ValueError("existing page requirement summary seal is invalid")
        if isinstance(existing.get("pages"), list):
            reusable_pages = {
                item["page"]: item
                for item in existing["pages"]
                if isinstance(item, Mapping) and type(item.get("page")) is int
            }

    pages: list[dict[str, Any]] = []
    lock_digests = _page_lock_digests(project)
    for contract, (relative, contract_path, _persisted) in zip(contracts, current_contracts, strict=True):
        page = contract["page_number"]
        prior = reusable_pages.get(page)
        if _verify_page_entry_against(
            project, prior, page, (relative, contract_path, dict(contract)), lock_digests[page],
        ):
            pages.append(copy.deepcopy(dict(prior)))
            continue
        assets = contract.get("asset_bindings", [])
        if not isinstance(assets, list):
            raise ValueError("locked page asset bindings are invalid")
        resolved = resolve_page_comments(project, contract, assets, timeout)
        records = [_closed_record(item) for item in resolved]
        entry = {
            **_presentation(page, records, page_contract_sha256=_file_sha256(contract_path)),
            "pageContractPath": relative,
            "rawCommentsSha256": _digest(contract.get("page_comments", [])),
            "pageLockSha256": lock_digests[page],
            "closedDirectives": records,
        }
        entry["pageEntrySha256"] = _seal_page_entry(entry)
        entry["pageEntrySignature"] = _sign_page_entry(project, entry)
        pages.append(entry)
        _write_summary(project, path, {
            "artifact_version": ARTIFACT_VERSION,
            "precedenceNotice": PRECEDENCE_NOTICE,
            "projectAuthority": project_authority,
            "page_count": len(pages),
            "pages": copy.deepcopy(pages),
        })
    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "precedenceNotice": PRECEDENCE_NOTICE,
        "projectAuthority": project_authority,
        "page_count": len(pages),
        "pages": pages,
    }
    return _write_summary(project, path, artifact)


def load_verified_page_resolutions(
    project: Path,
    page_contract: Mapping[str, Any],
) -> tuple[list[ResolvedDirective], dict[str, str]]:
    """Load the sole authenticated resolution set for a current locked page."""
    project = Path(project)
    path = project_artifact_path(project, SUMMARY_PATH)
    if not path.is_file():
        raise ValueError("comment_resolution_pending: authenticated resolution artifact is missing")
    value = _read_object(path)
    if not verify_page_requirement_summary(project, value):
        raise ValueError("comment_resolution_blocked: authenticated resolution artifact is stale or invalid")
    page = page_contract.get("page_number")
    if type(page) is not int or not 1 <= page <= len(value["pages"]):
        raise ValueError("comment_resolution_blocked: page identity is invalid")
    item = value["pages"][page - 1]
    if _digest(page_contract.get("page_comments", [])) != item["rawCommentsSha256"]:
        raise ValueError("comment_resolution_blocked: page comments changed")
    return (
        [_resolved_directive(record) for record in item["closedDirectives"]],
        {
            "path": SUMMARY_PATH.as_posix(),
            "page_entry_sha256": str(item["pageEntrySha256"]),
            "page_entry_signature": str(item["pageEntrySignature"]),
            "page_contract_sha256": str(item["pageContractSha256"]),
            "page_lock_sha256": str(item["pageLockSha256"]),
            "raw_comments_sha256": str(item["rawCommentsSha256"]),
        },
    )

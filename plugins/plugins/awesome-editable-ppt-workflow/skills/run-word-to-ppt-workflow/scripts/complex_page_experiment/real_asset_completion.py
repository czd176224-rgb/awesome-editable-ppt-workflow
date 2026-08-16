"""One pre-material project pass for explicitly requested real visual assets."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from codex_web_material_gateway import (
    SearchMaterialBlocked,
    search_visual_materials,
    verify_search_material,
)
from natural_comment_resolver import (
    SearchRequest,
    resolve_comment_deterministically,
    search_material_id,
)
from workflow_v6_state import load, mutation_lock


_COMPLETION_FIELD = "real_asset_completion"
_MANIFEST_RELATIVE = Path("02_v6") / "source_assets.json"
_PAGINATED_RELATIVE = Path("02_v6") / "paginated_word_source.json"
_ASSET_ROOT_RELATIVE = Path("01_source_assets") / "00_source" / "word_assets" / "original"
_MANIFEST_ASSET_PREFIX = Path("00_source") / "word_assets" / "original"
_ATTESTATION_FIELDS = (
    "material_attestation_path",
    "material_attestation_sha256",
    "material_attestation_digest",
    "material_attestation_signature",
)


@dataclass(frozen=True)
class _PlannedRequest:
    page_number: int
    source_comment: str
    request: SearchRequest
    expected_subjects: tuple[str, ...]
    bound_relationship_ids: tuple[str, ...] = ()


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is missing or invalid") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _write_object_atomic(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, indent=2,
    ).encode("utf-8") + b"\n"
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:12]}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _block_text(value: object) -> list[str]:
    result: list[str] = []
    if isinstance(value, Mapping):
        text = value.get("text")
        if isinstance(text, str) and text.strip():
            result.append(text.strip())
        rows = value.get("rows")
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, list):
                    cells = [str(cell).strip() for cell in row if str(cell).strip()]
                    if cells:
                        result.append("; ".join(cells))
    return result


def _page_context(page: Mapping[str, Any]) -> dict[str, Any]:
    source = "\n".join(
        text
        for block in page.get("blocks", [])
        for text in _block_text(block)
    )
    return {
        "page_number": int(page["page_number"]),
        "page_title": str(page.get("fixed_page_title", "")),
        "body_text": source,
        "source_text": source,
        "key_facts": [],
        "detected_dates": [],
    }


def _external_subjects(comment: str) -> tuple[str, ...]:
    subjects: list[str] = []
    if "新闻稿" in comment:
        subjects.append("新闻稿")
    match = re.search(r"有(.+?)(?:讲话|发言).{0,8}(?:图片|照片)", comment)
    if match:
        for value in re.split(r"[、，,和与及]", match.group(1)):
            candidate = value.strip()
            if candidate and candidate not in subjects:
                subjects.append(candidate)
    return tuple(subjects or [comment.strip()])


def _owned_assets(
    assets: Sequence[object], page_number: int,
) -> list[Mapping[str, Any]]:
    return [
        item for item in assets
        if isinstance(item, Mapping) and page_number in item.get("page_numbers", [])
    ]


def _is_viewable_asset(project: Path, asset: Mapping[str, Any]) -> bool:
    generation_input = asset.get("generation_input")
    if (
        not str(asset.get("media_type", "")).startswith("image/")
        or not isinstance(generation_input, Mapping)
        or not str(generation_input.get("media_type", "")).startswith("image/")
        or not isinstance(generation_input.get("relative_path"), str)
        or not isinstance(generation_input.get("sha256"), str)
    ):
        return False
    source_root = (project / "01_source_assets").resolve(strict=True)
    try:
        path = source_root.joinpath(
            *Path(str(generation_input["relative_path"])).parts
        ).resolve(strict=True)
        path.relative_to(source_root)
        data = path.read_bytes()
    except (OSError, ValueError):
        return False
    digest = hashlib.sha256(data).hexdigest()
    return digest == generation_input["sha256"] == asset.get("sha256")


def _is_anchored(
    project: Path, plan: _PlannedRequest, assets: Sequence[object],
) -> bool:
    owned = _owned_assets(assets, plan.page_number)
    observed_subjects: set[str] = set()
    for asset in owned:
        if not _is_viewable_asset(project, asset):
            continue
        if asset.get("material_role") != plan.request.material_role:
            continue
        subject = asset.get("subject")
        if subject == plan.source_comment or subject == plan.request.entity:
            observed_subjects.update(plan.expected_subjects)
        elif isinstance(subject, str):
            observed_subjects.add(subject)
        elif isinstance(subject, list):
            observed_subjects.update(str(item) for item in subject)
    if set(plan.expected_subjects).issubset(observed_subjects):
        return True
    if plan.request.material_role != "external_image" or not plan.bound_relationship_ids:
        return False
    if len(plan.expected_subjects) != 1:
        return False
    for asset in owned:
        if (
            asset.get("relationship_id") in plan.bound_relationship_ids
            and asset.get("binding_status") == "bound"
            and _is_viewable_asset(project, asset)
        ):
            return True
    return False


def _bound_relationship_ids(page: Mapping[str, Any], comment_id: str) -> tuple[str, ...]:
    relationships: list[str] = []
    blocks = page.get("blocks", [])
    if not isinstance(blocks, list):
        return ()
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        comment_ids = block.get("comment_ids", [])
        if not isinstance(comment_ids, list) or comment_id not in {
            str(item) for item in comment_ids
        }:
            continue
        relationship_ids = block.get("relationship_ids", [])
        if not isinstance(relationship_ids, list):
            continue
        for relationship_id in relationship_ids:
            if isinstance(relationship_id, str) and relationship_id not in relationships:
                relationships.append(relationship_id)
    return tuple(relationships)


def _scan_requests(
    pages: Sequence[object], assets: Sequence[object],
) -> tuple[list[_PlannedRequest], dict[int, dict[str, Any]]]:
    plans: list[_PlannedRequest] = []
    contexts: dict[int, dict[str, Any]] = {}
    seen: set[tuple[int, str]] = set()
    for raw_page in pages:
        if not isinstance(raw_page, Mapping):
            continue
        page_number = raw_page.get("page_number")
        if type(page_number) is not int or page_number < 1:
            continue
        context = _page_context(raw_page)
        contexts[page_number] = context
        page_assets = _owned_assets(assets, page_number)
        comments = raw_page.get("page_comments", [])
        if not isinstance(comments, list):
            raise ValueError("paginated page comments must be an array")
        for order, comment_record in enumerate(comments, start=1):
            if not isinstance(comment_record, Mapping):
                continue
            comment = comment_record.get("text")
            if not isinstance(comment, str) or not comment.strip():
                continue
            comment_id = comment_record.get("comment_id")
            source_comment_id = (
                str(comment_id) if isinstance(comment_id, (str, int))
                else f"page-{page_number}-comment-{order}"
            )
            bound_relationship_ids = _bound_relationship_ids(raw_page, source_comment_id)
            resolved = resolve_comment_deterministically(
                comment,
                context,
                list(page_assets),
                source_comment_id=source_comment_id,
            )
            if resolved is None or resolved.kind != "external_image" or not resolved.search_required:
                continue
            if resolved.search_requests:
                for original in resolved.search_requests:
                    request = replace(original, required=False)
                    key = (page_number, request.material_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    plans.append(_PlannedRequest(
                        page_number=page_number,
                        source_comment=comment,
                        request=request,
                        expected_subjects=(request.entity,),
                        bound_relationship_ids=bound_relationship_ids,
                    ))
                continue
            query = comment.strip()
            material_id = search_material_id(query)
            request = SearchRequest(
                directive_id=resolved.directive_id,
                parent_directive_id=resolved.directive_id,
                source_comment_id=source_comment_id,
                entity=query,
                query=query,
                material_id=material_id,
                material_role="external_image",
                required=False,
                max_results=3,
            )
            key = (page_number, material_id)
            if key not in seen:
                seen.add(key)
                plans.append(_PlannedRequest(
                    page_number=page_number,
                    source_comment=comment,
                    request=request,
                    expected_subjects=_external_subjects(comment),
                    bound_relationship_ids=bound_relationship_ids,
                ))
    return plans, contexts


def _https_source(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    return value if parsed.scheme == "https" and parsed.netloc else None


def _official_source_claim(
    plan: _PlannedRequest,
    publisher: str,
    source_url: str,
    source_authority: object,
    authority_basis: object,
    matched_entities: Sequence[str],
) -> bool:
    publisher_key = publisher.casefold()
    parsed_host = urlsplit(source_url).hostname or ""
    decoded_labels: list[str] = []
    for label in parsed_host.split("."):
        try:
            decoded_labels.append(label.encode("ascii").decode("idna"))
        except UnicodeError:
            decoded_labels.append(label)
    host = ".".join(decoded_labels).casefold()

    def compact(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return "".join(character for character in normalized if character.isalnum())

    compact_host = compact(host)
    if not compact_host or any(
        len(compact(subject)) < 2 or compact(subject) not in compact_host
        for subject in matched_entities
    ):
        return False
    if (
        not isinstance(source_authority, str)
        or not isinstance(authority_basis, str)
        or publisher_key not in authority_basis.casefold()
        or host not in authority_basis.casefold()
    ):
        return False
    if any(marker in publisher_key or marker in host for marker in (
        "fan", "aggregator", "logo archive", "logo-archive", "stock photo",
        "brandfetch", "pinterest", "wikipedia", "wikimedia",
    )):
        return False
    if plan.request.material_role == "enterprise_logo":
        return source_authority in {"first_party_website", "official_brand_media"}
    return source_authority in {
        "first_party_website", "official_newsroom", "official_institution",
        "official_event_organizer",
    }


def _verified_group(
    project: Path,
    plan: _PlannedRequest,
    materials: Sequence[object],
    *,
    timeout: float,
    verify: Callable[..., Mapping[str, Any]],
) -> list[dict[str, Any]]:
    expected = set(plan.expected_subjects)
    accepted: list[dict[str, Any]] = []
    observed: set[str] = set()
    for raw in materials:
        if not isinstance(raw, Mapping):
            continue
        try:
            verified = dict(verify(
                project,
                raw,
                expected_material_id=plan.request.material_id,
                expected_directive_id=plan.request.directive_id,
                expected_query=plan.request.query,
                deadline=time.monotonic() + timeout,
            ))
        except (SearchMaterialBlocked, ValueError):
            continue
        source_url = _https_source(verified.get("source_page_url"))
        direct_image_url = _https_source(verified.get("direct_image_url"))
        publisher = verified.get("publisher")
        matched = verified.get("matched_entities")
        if (
            source_url is None
            or direct_image_url is None
            or not isinstance(publisher, str)
            or not publisher.strip()
            or not isinstance(matched, list)
            or not matched
            or any(not isinstance(item, str) or item not in expected for item in matched)
            or not _official_source_claim(
                plan,
                publisher,
                source_url,
                verified.get("source_authority"),
                verified.get("authority_basis"),
                matched,
            )
            or any(not isinstance(verified.get(field), str) or not verified[field] for field in _ATTESTATION_FIELDS)
        ):
            continue
        local = verified.get("local_path")
        digest = verified.get("sha256")
        media_type = verified.get("media_type")
        if not isinstance(local, str) or not isinstance(digest, str) or media_type not in {
            "image/png", "image/jpeg", "image/webp",
        }:
            continue
        try:
            source_path = project.joinpath(*Path(local).parts).resolve(strict=True)
            source_path.relative_to(project)
        except (OSError, ValueError):
            continue
        data = source_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            continue
        accepted.append(verified)
        observed.update(str(item) for item in matched)
    return accepted if observed == expected else []


def _next_asset_id(assets: Sequence[object]) -> str:
    indexes = [
        int(match.group(1))
        for item in assets
        if isinstance(item, Mapping)
        for match in [re.fullmatch(r"word_asset_([0-9]+)", str(item.get("asset_id", "")))]
        if match is not None
    ]
    return f"word_asset_{max(indexes, default=0) + 1:03d}"


def _materialize(
    project: Path,
    assets: list[dict[str, Any]],
    plan: _PlannedRequest,
    materials: Sequence[Mapping[str, Any]],
) -> list[str]:
    asset_ids: list[str] = []
    target_root = project / _ASSET_ROOT_RELATIVE
    target_root.mkdir(parents=True, exist_ok=True)
    for material in materials:
        asset_id = _next_asset_id(assets)
        media_type = str(material["media_type"])
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[media_type]
        filename = f"{asset_id}-{str(material['sha256'])[:12]}{extension}"
        target = target_root / filename
        source = project.joinpath(*Path(str(material["local_path"])).parts).resolve(strict=True)
        data = source.read_bytes()
        if target.exists() and target.read_bytes() != data:
            raise ValueError("real asset destination already differs")
        if not target.exists():
            target.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        relative = (_MANIFEST_ASSET_PREFIX / filename).as_posix()
        matched = [str(item) for item in material["matched_entities"]]
        subject: str | list[str] = matched[0] if len(matched) == 1 else matched
        readable_subject = "__".join(
            re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", item).strip("-.")
            or "subject"
            for item in matched
        )
        assets.append({
            "asset_id": asset_id,
            "relationship_id": f"external-{plan.request.directive_id}-{asset_id}",
            "source_part": f"external:{material['source_page_url']}",
            "original_filename": f"{readable_subject}__{plan.request.material_role}{extension}",
            "media_type": media_type,
            "sha256": digest,
            "byte_size": len(data),
            "page_numbers": [plan.page_number],
            "source_block_indexes": [],
            "binding_status": "bound",
            "relative_path": relative,
            "asset_role": "mandatory_inline_image",
            "processing": "direct_image",
            "blocking": False,
            "advisories": [],
            "generation_input": {
                "relative_path": relative,
                "sha256": digest,
                "media_type": media_type,
                "derivation": "original_supported",
            },
            "source_page_url": material["source_page_url"],
            "direct_image_url": material["direct_image_url"],
            "publisher": material["publisher"],
            "source_authority": material["source_authority"],
            "authority_basis": material["authority_basis"],
            "subject": subject,
            "material_role": plan.request.material_role,
            "provenance": "official_web_search",
            "search_query": plan.request.query,
            "search_material_id": plan.request.material_id,
            "search_directive_id": plan.request.directive_id,
            **{field: material[field] for field in _ATTESTATION_FIELDS},
        })
        asset_ids.append(asset_id)
    return asset_ids


def _result_row(
    plan: _PlannedRequest,
    outcome: str,
    *,
    asset_ids: Sequence[str] = (),
    source_urls: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "page_number": plan.page_number,
        "directive_id": plan.request.directive_id,
        "material_id": plan.request.material_id,
        "query": plan.request.query,
        "source_comment": plan.source_comment,
        "subject": list(plan.expected_subjects),
        "material_role": plan.request.material_role,
        "outcome": outcome,
        "asset_ids": list(asset_ids),
        "source_page_urls": list(source_urls),
    }


def _shared_search_key(plan: _PlannedRequest) -> tuple[object, ...]:
    return (
        tuple(subject.strip().casefold() for subject in plan.expected_subjects),
        plan.request.material_role,
        " ".join(plan.request.query.split()).casefold(),
        plan.request.max_results,
    )


def _bind_materialized_assets_to_pages(
    assets: list[dict[str, Any]],
    asset_ids: Sequence[str],
    page_numbers: Sequence[int],
) -> None:
    selected = set(asset_ids)
    pages = set(page_numbers)
    for asset in assets:
        if asset.get("asset_id") not in selected:
            continue
        existing = asset.get("page_numbers")
        if not isinstance(existing, list) or any(type(item) is not int for item in existing):
            raise ValueError("materialized real asset page ownership is invalid")
        asset["page_numbers"] = sorted(set(existing) | pages)


def complete_project_real_assets(
    project: Path,
    *,
    timeout: float,
    search: Callable[..., list[list[dict[str, Any]]]] = search_visual_materials,
    verify: Callable[..., Mapping[str, Any]] = verify_search_material,
) -> dict[str, Any]:
    """Complete explicit project real-image requests once, before material receipts exist."""
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    project = Path(project).resolve(strict=True)
    manifest_path = project / _MANIFEST_RELATIVE
    paginated_path = project / _PAGINATED_RELATIVE
    with mutation_lock(project, timeout=timeout):
        manifest = _read_object(manifest_path, "source asset manifest")
        persisted = manifest.get(_COMPLETION_FIELD)
        if isinstance(persisted, Mapping):
            return copy.deepcopy(dict(persisted))

        raw_state = _read_object(project / "workflow_v6.json", "workflow state")
        state_pages = raw_state.get("pages")
        if not isinstance(state_pages, list):
            raise ValueError("workflow state pages are invalid")
        if any(
            isinstance(page, Mapping) and page.get("material_receipt") is not None
            for page in state_pages
        ):
            raise ValueError(
                "real asset completion must run before any page material receipt is published"
            )
        load(project)

        paginated = _read_object(paginated_path, "paginated Word source")
        pages = paginated.get("pages")
        assets = manifest.get("assets")
        if not isinstance(pages, list) or not isinstance(assets, list):
            raise ValueError("project source pages or asset records are invalid")
        if any(not isinstance(item, dict) for item in assets):
            raise ValueError("source asset records must be objects")
        plans, contexts = _scan_requests(pages, assets)
        rows: list[dict[str, Any]] = []
        missing: list[_PlannedRequest] = []
        for plan in plans:
            if _is_anchored(project, plan, assets):
                rows.append(_result_row(plan, "already_available"))
            else:
                missing.append(plan)

        grouped_missing: list[list[_PlannedRequest]] = []
        groups_by_key: dict[tuple[object, ...], list[_PlannedRequest]] = {}
        for plan in missing:
            key = _shared_search_key(plan)
            if key not in groups_by_key:
                groups_by_key[key] = []
                grouped_missing.append(groups_by_key[key])
            groups_by_key[key].append(plan)
        representative_missing = [group[0] for group in grouped_missing]

        search_turns = 0
        outcomes: list[list[dict[str, Any]]] = []
        if representative_missing:
            first_page = min(plan.page_number for plan in missing)
            page_context = dict(contexts[first_page])
            page_context["page_title"] = "Project real-asset completion"
            page_context["body_text"] = "\n\n".join(
                f"Page {number}: {context['source_text']}"
                for number, context in sorted(contexts.items())
                if any(plan.page_number == number for plan in missing)
            )
            page_context["key_facts"] = list(dict.fromkeys(
                subject for plan in missing for subject in plan.expected_subjects
            ))
            try:
                outcomes = search(
                    project,
                    directives=[plan.request for plan in representative_missing],
                    page_context=page_context,
                    timeout=timeout,
                )
            except SearchMaterialBlocked:
                outcomes = [[] for _ in representative_missing]
            search_turns = 1
            if len(outcomes) != len(representative_missing):
                raise ValueError("project real-asset search did not return one result per request")

        mutable_assets = [dict(item) for item in assets]
        for group, materials in zip(grouped_missing, outcomes, strict=True):
            plan = group[0]
            verified = _verified_group(
                project, plan, materials, timeout=timeout, verify=verify,
            )
            if not verified:
                rows.extend(_result_row(item, "not_found") for item in group)
                continue
            asset_ids = _materialize(project, mutable_assets, plan, verified)
            _bind_materialized_assets_to_pages(
                mutable_assets,
                asset_ids,
                [item.page_number for item in group],
            )
            source_urls = [str(item["source_page_url"]) for item in verified]
            rows.extend(
                _result_row(
                    item,
                    "found",
                    asset_ids=asset_ids,
                    source_urls=source_urls,
                )
                for item in group
            )

        order = {
            (plan.page_number, plan.request.directive_id, plan.request.material_id): index
            for index, plan in enumerate(plans)
        }
        rows.sort(key=lambda item: order[
            (int(item["page_number"]), str(item["directive_id"]), str(item["material_id"]))
        ])
        result = {
            "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "search_turns": search_turns,
            "requests": rows,
        }
        manifest["assets"] = mutable_assets
        manifest[_COMPLETION_FIELD] = result
        _write_object_atomic(manifest_path, manifest)
        return copy.deepcopy(result)

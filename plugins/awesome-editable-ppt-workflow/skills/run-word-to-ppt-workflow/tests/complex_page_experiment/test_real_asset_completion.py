from __future__ import annotations

import copy
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from PIL import Image

from awesome_page_materials import collect_page_materials, publish_page_materials
from codex_subscription_runtime import CodexStructuredResult
from codex_web_material_gateway import search_visual_materials
from complex_page_experiment import open_live_page_workspace
from complex_page_experiment.materials import build_complete_page_material_view
from natural_comment_resolver import search_material_id
from workflow_v6_contract import canonical_sha256, new_page, new_project
from workflow_v6_state import create, load


P3_COMMENT = "添加新闻稿图片，并且有王巍和李耀武讲话图片"
P4_COMMENT = "这页的企业Logo都要添加，添加方式是企业logo代替企业名称"
P4_ENTITIES = ("AlphaCorp", "BetaCorp", "GammaCorp", "DeltaCorp", "EpsilonCorp", "ZetaCorp")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _subject_host(*subjects: str) -> str:
    label = "-".join(
        "".join(character for character in subject.casefold() if character.isalnum())
        for subject in subjects
    )
    return f"{label}.example"


def _asset_record(
    project: Path,
    *,
    asset_id: str,
    page_number: int,
    subject: str,
    role: str,
) -> dict[str, object]:
    relative = f"00_source/word_assets/original/{asset_id}.png"
    path = project / "01_source_assets" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (12, 8), (255, 255, 255)).save(path, format="PNG")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    return {
        "asset_id": asset_id,
        "relationship_id": f"external-{asset_id}",
        "source_part": f"external:{asset_id}",
        "original_filename": path.name,
        "media_type": "image/png",
        "sha256": digest,
        "byte_size": len(data),
        "page_numbers": [page_number],
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
            "media_type": "image/png",
            "derivation": "original_supported",
        },
        "subject": subject,
        "material_role": role,
        "source_page_url": "https://official.example/assets/source",
        "publisher": "Official publisher",
        "provenance": "official_web_search",
    }


def _project(
    tmp_path: Path,
    *,
    p3_comment: str | None = P3_COMMENT,
    p4_comment: str | None = P4_COMMENT,
    p5_comment: str | None = None,
    page_count: int = 4,
    assets: list[dict[str, object]] | None = None,
) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "00_source").mkdir()
    word = project / "00_source" / "source.docx"
    logo = project / "00_source" / "logo.svg"
    word.write_bytes(b"four-page-word")
    logo.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    pages = [new_page(number, title=f"Page {number}") for number in range(1, page_count + 1)]
    state = new_project(
        word_source={"path": "00_source/source.docx", "sha256": hashlib.sha256(word.read_bytes()).hexdigest()},
        logo_source={"path": "00_source/logo.svg", "sha256": hashlib.sha256(logo.read_bytes()).hexdigest()},
        pages=pages,
    )
    visual = {
        "primary_color": "#17365D",
        "secondary_color": "#C7352B",
        "background_color": "#FFFFFF",
        "cjk_font": "Microsoft YaHei",
        "latin_font": "Arial",
        "title_size_pt": 28,
        "body_size_pt": 12,
        "caption_size_pt": 9,
        "regional_characteristics": "",
        "visual_description": "Confirmed test UI",
    }
    state["style_confirmation"] = {"status": "confirmed", "contract": visual}
    state["confirmed_ui_revision"] = 1
    state["confirmed_ui_digest"] = canonical_sha256(visual)
    state["page_materials_status"] = "pending"
    create(project, state)

    source_pages: list[dict[str, object]] = []
    for page_number in range(1, page_count + 1):
        body = (
            "News release speakers 王巍 and 李耀武"
            if page_number in {3, 5}
            else "; ".join(P4_ENTITIES)
            if page_number == 4
            else f"Body {page_number}"
        )
        comment = (
            p3_comment if page_number == 3
            else p4_comment if page_number == 4
            else p5_comment if page_number == 5
            else None
        )
        source_pages.append(
            {
                "page_number": page_number,
                "fixed_page_title": f"Page {page_number}",
                "fixed_page_title_source_block_id": f"title-{page_number}",
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": f"Page {page_number}",
                        "source_block_id": f"title-{page_number}",
                        "source_block_index": 1,
                        "source_order": 1,
                        "relationship_ids": [],
                        "comment_ids": [],
                    },
                    {
                        "type": "paragraph",
                        "text": body,
                        "source_block_id": f"body-{page_number}",
                        "source_block_index": 2,
                        "source_order": 2,
                        "relationship_ids": [],
                        "comment_ids": [],
                    },
                ],
                "page_comments": (
                    [{"comment_id": f"comment-{page_number}", "text": comment}]
                    if comment is not None
                    else []
                ),
            }
        )
    _write_json(
        project / "02_v6" / "paginated_word_source.json",
        {"schema_version": "1.0", "page_count": page_count, "pages": source_pages},
    )
    _write_json(
        project / "02_v6" / "source_assets.json",
        {"schema_version": "1.0", "assets": list(assets or [])},
    )
    return project


def _completion_api():
    from complex_page_experiment.real_asset_completion import complete_project_real_assets

    return complete_project_real_assets


def _repeat_four_page_cycle(project: Path, repeats: int) -> None:
    paginated_path = project / "02_v6" / "paginated_word_source.json"
    paginated = json.loads(paginated_path.read_text(encoding="utf-8"))
    base_pages = paginated["pages"][:4]
    repeated_pages = []
    for cycle in range(repeats):
        for offset, base_page in enumerate(base_pages, start=1):
            page = copy.deepcopy(base_page)
            page["page_number"] = cycle * 4 + offset
            repeated_pages.append(page)
    paginated["page_count"] = len(repeated_pages)
    paginated["pages"] = repeated_pages
    _write_json(paginated_path, paginated)

    state_path = project / "workflow_v6.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["pages"] = [
        new_page(number, title=f"Page {number}")
        for number in range(1, len(repeated_pages) + 1)
    ]
    _write_json(state_path, state)


def test_no_explicit_real_asset_request_records_zero_turn_completion(tmp_path: Path):
    project = _project(tmp_path, p3_comment=None, p4_comment=None)

    def unexpected_search(*_args, **_kwargs):
        raise AssertionError("search must not run without an explicit external-image request")

    result = _completion_api()(project, timeout=30, search=unexpected_search)

    assert result["search_turns"] == 0
    assert result["requests"] == []
    manifest = json.loads((project / "02_v6" / "source_assets.json").read_text(encoding="utf-8"))
    assert manifest["real_asset_completion"] == result


def test_page_owned_subject_and_role_anchor_prevents_search(tmp_path: Path):
    project = _project(tmp_path, p4_comment=None)
    anchored = _asset_record(
        project,
        asset_id="word_asset_001",
        page_number=3,
        subject=P3_COMMENT,
        role="external_image",
    )
    _write_json(
        project / "02_v6" / "source_assets.json",
        {"schema_version": "1.0", "assets": [anchored]},
    )

    def unexpected_search(*_args, **_kwargs):
        raise AssertionError("an anchored page-owned real image must suppress search")

    result = _completion_api()(project, timeout=30, search=unexpected_search)

    assert result["search_turns"] == 0
    assert result["requests"][0]["outcome"] == "already_available"


def test_unviewable_subject_role_record_does_not_suppress_required_search(tmp_path: Path):
    project = _project(tmp_path, p4_comment=None)
    broken = _asset_record(
        project,
        asset_id="word_asset_001",
        page_number=3,
        subject=P3_COMMENT,
        role="external_image",
    )
    broken["media_type"] = "application/octet-stream"
    broken["generation_input"] = None
    _write_json(
        project / "02_v6" / "source_assets.json",
        {"schema_version": "1.0", "assets": [broken]},
    )
    calls: list[tuple[object, ...]] = []

    def not_found(_project: Path, *, directives, **_kwargs):
        calls.append(tuple(directives))
        return [[] for _ in directives]

    result = _completion_api()(project, timeout=30, search=not_found)

    assert len(calls) == 1
    assert result["requests"][0]["outcome"] == "not_found"


def test_three_viewable_subject_assets_jointly_satisfy_multi_subject_request(tmp_path: Path):
    project = _project(tmp_path, p4_comment=None)
    anchored = [
        _asset_record(
            project,
            asset_id=f"word_asset_{index:03d}",
            page_number=3,
            subject=subject,
            role="external_image",
        )
        for index, subject in enumerate(("新闻稿", "王巍", "李耀武"), start=1)
    ]
    _write_json(
        project / "02_v6/source_assets.json",
        {"schema_version": "1.0", "assets": anchored},
    )

    result = _completion_api()(
        project,
        timeout=30,
        search=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("all three page-owned subjects already satisfy the request")
        ),
    )

    assert result["search_turns"] == 0
    assert result["requests"][0]["outcome"] == "already_available"


def test_untyped_relationship_image_does_not_satisfy_multi_subject_request(tmp_path: Path):
    project = _project(tmp_path, p4_comment=None)
    paginated_path = project / "02_v6/paginated_word_source.json"
    paginated = json.loads(paginated_path.read_text(encoding="utf-8"))
    paginated["pages"][2]["blocks"][1]["comment_ids"] = ["comment-3"]
    paginated["pages"][2]["blocks"][1]["relationship_ids"] = ["rIdExisting"]
    _write_json(paginated_path, paginated)
    image = _asset_record(
        project,
        asset_id="word_asset_001",
        page_number=3,
        subject="untyped image",
        role="unrelated",
    )
    image["relationship_id"] = "rIdExisting"
    _write_json(
        project / "02_v6/source_assets.json",
        {"schema_version": "1.0", "assets": [image]},
    )
    calls: list[tuple[object, ...]] = []

    def not_found(_project: Path, *, directives, **_kwargs):
        calls.append(tuple(directives))
        return [[] for _ in directives]

    result = _completion_api()(project, timeout=30, search=not_found)

    assert len(calls) == 1
    assert result["requests"][0]["outcome"] == "not_found"


def test_one_verified_group_image_preserves_every_matched_subject(tmp_path: Path):
    project = _project(tmp_path, p4_comment=None)

    def group_search(project_root: Path, *, directives, **_kwargs):
        directive = directives[0]
        subjects = ("新闻稿", "王巍", "李耀武")
        host = _subject_host(*subjects)
        evidence = project_root / "03_evidence" / "official-group.png"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (24, 16), (255, 255, 255)).save(evidence, format="PNG")
        return [[{
            "material_id": directive.material_id,
            "directive_id": directive.directive_id,
            "query": directive.query,
            "local_path": evidence.relative_to(project_root).as_posix(),
            "media_type": "image/png",
            "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
            "source_page_url": f"https://{host}/event",
            "direct_image_url": f"https://{host}/event/group.png",
            "publisher": "Official event organizer",
            "source_authority": "official_event_organizer",
            "authority_basis": (
                f"Official event organizer publishes this source on {host}"
            ),
            "matched_entities": list(subjects),
            "material_attestation_path": "03_evidence/group.attestation.json",
            "material_attestation_sha256": "a" * 64,
            "material_attestation_digest": "b" * 64,
            "material_attestation_signature": "c" * 64,
        }]]

    result = _completion_api()(
        project,
        timeout=30,
        search=group_search,
        verify=lambda _project, material, **_kwargs: dict(material),
    )

    assert result["requests"][0]["outcome"] == "found"
    manifest = json.loads(
        (project / "02_v6/source_assets.json").read_text(encoding="utf-8")
    )
    imported = manifest["assets"][-1]
    assert imported["subject"] == ["新闻稿", "王巍", "李耀武"]
    assert all(name in imported["original_filename"] for name in imported["subject"])


def test_missing_p3_and_p4_requests_use_one_batch_and_materialize_into_page_authority(
    tmp_path: Path,
):
    project = _project(tmp_path)
    search_calls: list[tuple[object, ...]] = []
    verify_calls: list[str] = []

    def fake_search(project_root: Path, *, directives, page_context, **_kwargs):
        search_calls.append(tuple(directives))
        assert page_context["page_number"] == 3
        outputs: list[list[dict[str, Any]]] = []
        for directive in directives:
            if directive.max_results == 3:
                subjects = ("新闻稿", "王巍", "李耀武")
            else:
                subjects = (directive.entity,)
            materials: list[dict[str, Any]] = []
            for index, subject in enumerate(subjects, start=1):
                host = _subject_host(subject)
                evidence = project_root / "03_evidence" / f"{directive.material_id}-{index}.png"
                evidence.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (10 + index, 8), (index * 20, 40, 60)).save(evidence, format="PNG")
                payload = evidence.read_bytes()
                materials.append(
                    {
                        "material_id": directive.material_id,
                        "directive_id": directive.directive_id,
                        "query": directive.query,
                        "local_path": evidence.relative_to(project_root).as_posix(),
                        "media_type": "image/png",
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "source_page_url": f"https://{host}/{subject}",
                        "direct_image_url": f"https://{host}/assets/{subject}.png",
                        "publisher": f"{subject} official newsroom",
                        "source_authority": (
                            "official_brand_media"
                            if directive.material_role == "enterprise_logo"
                            else "official_newsroom"
                        ),
                        "authority_basis": (
                            f"{subject} official newsroom publishes this source on {host}"
                        ),
                        "matched_entities": [subject],
                        "material_attestation_path": f"03_evidence/{directive.material_id}-{index}.attestation.json",
                        "material_attestation_sha256": "a" * 64,
                        "material_attestation_digest": "b" * 64,
                        "material_attestation_signature": "c" * 64,
                    }
                )
            outputs.append(materials)
        return outputs

    def fake_verify(_project: Path, material: dict[str, Any], **_kwargs):
        verify_calls.append(str(material["local_path"]))
        return dict(material)

    complete = _completion_api()
    result = complete(project, timeout=30, search=fake_search, verify=fake_verify)

    assert len(search_calls) == 1
    directives = search_calls[0]
    assert len(directives) == 7
    p3 = next(item for item in directives if item.max_results == 3)
    assert p3.query == P3_COMMENT
    assert p3.search_query == P3_COMMENT
    assert {item.entity for item in directives if item.material_role == "enterprise_logo"} == set(P4_ENTITIES)
    assert len(verify_calls) == 9
    assert result["search_turns"] == 1
    assert {item["outcome"] for item in result["requests"]} == {"found"}

    page_three = collect_page_materials(project, 3)
    page_four = collect_page_materials(project, 4)
    assert len(page_three["word_images"]) == 3
    assert len(page_four["word_images"]) == 6
    assert all(
        any(subject in item["original_filename"] for subject in ("新闻稿", "王巍", "李耀武"))
        and "external_image" in item["original_filename"]
        for item in page_three["word_images"]
    )
    assert {item["original_filename"].split("__", 1)[0] for item in page_four["word_images"]} == set(P4_ENTITIES)
    assert all("enterprise_logo" in item["original_filename"] for item in page_four["word_images"])
    manifest = json.loads((project / "02_v6" / "source_assets.json").read_text(encoding="utf-8"))
    imported = [item for item in manifest["assets"] if item.get("provenance") == "official_web_search"]
    assert len(imported) == 9
    assert all(item["source_part"].startswith("external:") for item in imported)
    assert all(
        all(
            "".join(character for character in subject.casefold() if character.isalnum())
            in "".join(
                character
                for character in (urlsplit(item["source_page_url"]).hostname or "").casefold()
                if character.isalnum()
            )
            for subject in (
                item["subject"] if isinstance(item["subject"], list) else [item["subject"]]
            )
        )
        for item in imported
    )
    assert all(
        urlsplit(item["direct_image_url"]).scheme == "https"
        and urlsplit(item["direct_image_url"]).hostname
        == urlsplit(item["source_page_url"]).hostname
        for item in imported
    )
    assert all(item["subject"] and item["material_role"] for item in imported)
    assert all(item["source_comment"] in {P3_COMMENT, P4_COMMENT} for item in result["requests"])

    repeated = complete(
        project,
        timeout=30,
        search=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("persisted completion must prevent a second search")
        ),
        verify=fake_verify,
    )
    assert repeated == result

    output = project / "02_v6" / "awesome_page_materials" / "page_003.json"
    publish_page_materials(project, 3, output)
    workspace = open_live_page_workspace(project, 3)
    view = build_complete_page_material_view(workspace)
    assert len(view.multimodal_images) == 3
    assert sum(record["kind"] == "word_image" for record in view.value["materials"]) == 3
    assert all(
        "external_image" in record["original_filename"]
        for record in view.value["materials"]
        if record["kind"] == "word_image"
    )

    page_four_output = project / "02_v6" / "awesome_page_materials" / "page_004.json"
    publish_page_materials(project, 4, page_four_output)
    page_four_view = build_complete_page_material_view(open_live_page_workspace(project, 4))
    assert {
        record["original_filename"].split("__", 1)[0]
        for record in page_four_view.value["materials"]
        if record["kind"] == "word_image"
    } == set(P4_ENTITIES)


def test_repeated_page_cycles_search_each_real_asset_once_and_bind_every_page(
    tmp_path: Path,
):
    project = _project(tmp_path)
    _repeat_four_page_cycle(project, 3)
    search_calls: list[tuple[object, ...]] = []

    def fake_search(project_root: Path, *, directives, **_kwargs):
        search_calls.append(tuple(directives))
        outputs: list[list[dict[str, Any]]] = []
        for directive in directives:
            subjects = (
                ("新闻稿", "王巍", "李耀武")
                if directive.max_results == 3
                else (directive.entity,)
            )
            host = _subject_host(*subjects)
            evidence = project_root / "03_evidence" / f"{directive.material_id}.png"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (18, 12), (20, 40, 60)).save(evidence, format="PNG")
            outputs.append([{
                "material_id": directive.material_id,
                "directive_id": directive.directive_id,
                "query": directive.query,
                "local_path": evidence.relative_to(project_root).as_posix(),
                "media_type": "image/png",
                "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                "source_page_url": f"https://{host}/official",
                "direct_image_url": f"https://{host}/official/image.png",
                "publisher": "Official publisher",
                "source_authority": (
                    "official_brand_media"
                    if directive.material_role == "enterprise_logo"
                    else "official_newsroom"
                ),
                "authority_basis": f"Official publisher publishes this source on {host}",
                "matched_entities": list(subjects),
                "material_attestation_path": f"03_evidence/{directive.material_id}.attestation.json",
                "material_attestation_sha256": "a" * 64,
                "material_attestation_digest": "b" * 64,
                "material_attestation_signature": "c" * 64,
            }])
        return outputs

    result = _completion_api()(
        project,
        timeout=30,
        search=fake_search,
        verify=lambda _project, material, **_kwargs: dict(material),
    )

    assert len(search_calls) == 1
    assert len(search_calls[0]) == 7
    assert len(result["requests"]) == 21
    assert {item["outcome"] for item in result["requests"]} == {"found"}
    manifest = json.loads(
        (project / "02_v6" / "source_assets.json").read_text(encoding="utf-8")
    )
    imported = [
        item for item in manifest["assets"]
        if item.get("provenance") == "official_web_search"
    ]
    assert len(imported) == 7
    external = next(item for item in imported if item["material_role"] == "external_image")
    assert external["page_numbers"] == [3, 7, 11]
    assert {
        tuple(item["page_numbers"])
        for item in imported
        if item["material_role"] == "enterprise_logo"
    } == {(4, 8, 12)}
    assert all(len(collect_page_materials(project, page)["word_images"]) == 1 for page in (3, 7, 11))
    assert all(len(collect_page_materials(project, page)["word_images"]) == 6 for page in (4, 8, 12))


def test_nonofficial_or_subject_mismatched_results_persist_not_found_without_raising(
    tmp_path: Path,
):
    project = _project(tmp_path, p4_comment=None)

    def fake_search(project_root: Path, *, directives, **_kwargs):
        directive = directives[0]
        evidence = project_root / "03_evidence" / "wrong.png"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (10, 8), (0, 0, 0)).save(evidence, format="PNG")
        return [[{
            "material_id": directive.material_id,
            "directive_id": directive.directive_id,
            "query": directive.query,
            "local_path": evidence.relative_to(project_root).as_posix(),
            "media_type": "image/png",
            "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
            "source_page_url": "https://unofficial.example/image.png",
            "direct_image_url": "https://unofficial.example/image.png",
            "publisher": "Fan archive",
            "matched_entities": ["Different Person"],
            "material_attestation_path": "03_evidence/wrong.attestation.json",
            "material_attestation_sha256": "a" * 64,
            "material_attestation_digest": "b" * 64,
            "material_attestation_signature": "c" * 64,
        }]]

    result = _completion_api()(
        project,
        timeout=30,
        search=fake_search,
        verify=lambda _project, material, **_kwargs: dict(material),
    )

    assert result["search_turns"] == 1
    assert result["requests"][0]["outcome"] == "not_found"
    assert result["requests"][0]["source_comment"] == P3_COMMENT
    assert result["requests"][0]["subject"] == ["新闻稿", "王巍", "李耀武"]
    assert collect_page_materials(project, 3)["word_images"] == []


def test_subject_matched_fan_archive_is_not_accepted_as_official_source(tmp_path: Path):
    project = _project(tmp_path, p3_comment=None)

    def fan_search(project_root: Path, *, directives, **_kwargs):
        outcomes = []
        for index, directive in enumerate(directives):
            evidence = project_root / "03_evidence" / f"fan-logo-{index}.png"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (10, 8), (0, 0, 0)).save(evidence, format="PNG")
            outcomes.append([{
                "material_id": directive.material_id,
                "directive_id": directive.directive_id,
                "query": directive.query,
                "local_path": evidence.relative_to(project_root).as_posix(),
                "media_type": "image/png",
                "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                "source_page_url": "https://fan-archive.example/logo.png",
                "direct_image_url": "https://fan-archive.example/logo.png",
                "publisher": "Fan Logo Archive",
                "matched_entities": [directive.entity],
                "material_attestation_path": f"03_evidence/fan-{index}.attestation.json",
                "material_attestation_sha256": "a" * 64,
                "material_attestation_digest": "b" * 64,
                "material_attestation_signature": "c" * 64,
            }])
        return outcomes

    result = _completion_api()(
        project,
        timeout=30,
        search=fan_search,
        verify=lambda _project, material, **_kwargs: dict(material),
    )

    assert all(item["outcome"] == "not_found" for item in result["requests"])
    assert collect_page_materials(project, 4)["word_images"] == []


def test_self_asserted_official_source_without_subject_bound_host_is_not_imported(
    tmp_path: Path,
):
    project = _project(tmp_path, p3_comment=None)

    def self_asserted_search(project_root: Path, *, directives, **_kwargs):
        outcomes = []
        for index, directive in enumerate(directives):
            evidence = project_root / "03_evidence" / f"self-asserted-{index}.png"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (10, 8), (0, 0, 0)).save(evidence, format="PNG")
            outcomes.append([{
                "material_id": directive.material_id,
                "directive_id": directive.directive_id,
                "query": directive.query,
                "local_path": evidence.relative_to(project_root).as_posix(),
                "media_type": "image/png",
                "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                "source_page_url": "https://random.example/logo",
                "direct_image_url": "https://random.example/logo.png",
                "publisher": f"{directive.entity} Logo Library",
                "source_authority": "first_party_website",
                "authority_basis": (
                    f"{directive.entity} Logo Library publishes this source on "
                    "random.example as its first party website"
                ),
                "matched_entities": [directive.entity],
                "material_attestation_path": f"03_evidence/self-{index}.attestation.json",
                "material_attestation_sha256": "a" * 64,
                "material_attestation_digest": "b" * 64,
                "material_attestation_signature": "c" * 64,
            }])
        return outcomes

    result = _completion_api()(
        project,
        timeout=30,
        search=self_asserted_search,
        verify=lambda _project, material, **_kwargs: dict(material),
    )

    assert all(item["outcome"] == "not_found" for item in result["requests"])
    assert collect_page_materials(project, 4)["word_images"] == []


def test_concurrent_completion_calls_share_one_locked_search_turn(tmp_path: Path):
    project = _project(tmp_path, p4_comment=None)
    calls = 0

    def slow_not_found(*_args, directives, **_kwargs):
        nonlocal calls
        calls += 1
        time.sleep(0.1)
        return [[] for _ in directives]

    complete = _completion_api()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(complete, project, timeout=30, search=slow_not_found)
            for _ in range(2)
        ]
        results = [future.result() for future in futures]

    assert calls == 1
    assert results[0] == results[1]


def test_completion_is_rejected_after_any_page_material_receipt_is_published(tmp_path: Path):
    project = _project(tmp_path, p3_comment=None, p4_comment=None)
    state = load(project)
    state["pages"][0]["material_receipt"] = {
        "schema_version": "awesome-page-materials-v1",
        "page_number": 1,
        "path": "02_v6/awesome_page_materials/page_001.json",
        "digest": "a" * 64,
    }
    _write_json(project / "workflow_v6.json", state)

    with pytest.raises(ValueError, match="before.*material receipt|material receipt.*published"):
        _completion_api()(project, timeout=30)


def test_page_five_explicit_request_is_scanned_for_the_single_project_turn(tmp_path: Path):
    project = _project(
        tmp_path,
        p3_comment=None,
        p4_comment=None,
        p5_comment=P3_COMMENT,
        page_count=5,
    )
    calls: list[tuple[object, ...]] = []

    def not_found(_project: Path, *, directives, **_kwargs):
        calls.append(tuple(directives))
        return [[] for _ in directives]

    result = _completion_api()(project, timeout=30, search=not_found)

    assert len(calls) == 1
    assert len(calls[0]) == 1
    assert calls[0][0].query == P3_COMMENT
    assert result["requests"][0]["page_number"] == 5
    assert result["requests"][0]["outcome"] == "not_found"


def test_page_five_material_receipt_blocks_project_completion(tmp_path: Path):
    project = _project(
        tmp_path,
        p3_comment=None,
        p4_comment=None,
        page_count=5,
    )
    state = load(project)
    state["pages"][4]["material_receipt"] = {
        "schema_version": "awesome-page-materials-v1",
        "page_number": 5,
        "path": "02_v6/awesome_page_materials/page_005.json",
        "digest": "a" * 64,
    }
    _write_json(project / "workflow_v6.json", state)

    with pytest.raises(ValueError, match="before.*material receipt|material receipt.*published"):
        _completion_api()(project, timeout=30)


def test_bound_untyped_word_image_does_not_hide_multi_subject_request(tmp_path: Path):
    project = _project(tmp_path)
    paginated_path = project / "02_v6" / "paginated_word_source.json"
    paginated = json.loads(paginated_path.read_text(encoding="utf-8"))
    page_three = paginated["pages"][2]
    page_three["blocks"][1]["comment_ids"] = ["comment-3"]
    page_three["blocks"][1]["relationship_ids"] = ["rIdExisting"]
    _write_json(paginated_path, paginated)

    manifest_path = project / "02_v6" / "source_assets.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    anchored = _asset_record(
        project,
        asset_id="word_asset_001",
        page_number=3,
        subject="unrelated existing Word image",
        role="unrelated",
    )
    anchored["relationship_id"] = "rIdExisting"
    manifest["assets"].append(anchored)
    _write_json(manifest_path, manifest)
    calls: list[tuple[object, ...]] = []

    def not_found(_project: Path, *, directives, **_kwargs):
        calls.append(tuple(directives))
        return [[] for _ in directives]

    result = _completion_api()(project, timeout=30, search=not_found)

    assert len(calls) == 1
    assert len(calls[0]) == 7
    assert {item.entity for item in calls[0] if item.material_role == "enterprise_logo"} == set(P4_ENTITIES)
    p3 = next(item for item in result["requests"] if item["page_number"] == 3)
    assert p3["outcome"] == "not_found"


def test_gateway_batches_seven_project_requests_into_one_codex_turn(tmp_path: Path):
    project = tmp_path / "gateway-project"
    project.mkdir()
    directives = []
    for index in range(7):
        query = f"official asset {index}"
        directives.append(
            {
                "directive_id": f"directive-{index}",
                "entity": f"Entity {index}",
                "search_query": query,
                "search_required": True,
                "required": False,
                "material_role": "enterprise_logo",
                "max_results": 1,
                "decisions": [{
                    "target": "material.search_evidence",
                    "material_id": search_material_id(query),
                }],
            }
        )
    calls: list[dict[str, object]] = []

    def invoke(_project: Path, **kwargs):
        calls.append(kwargs)
        request_rows = json.loads(str(kwargs["prompt"]).split("SEARCH_REQUESTS: ", 1)[1].splitlines()[0])
        value = {
            "results": [
                {
                    "material_id": item["material_id"],
                    "directive_id": item["directive_id"],
                    "entity": item["entity"],
                    "candidates": [],
                }
                for item in request_rows
            ]
        }
        safe_trace = {
            "runtime": "codex-app-server",
            "role": "visual-material-search",
            "thread_id": f"thread-{len(calls)}",
            "turn_id": f"turn-{len(calls)}",
            "model": "gpt-test-current",
            "model_provider": "openai-test",
            "auth_mode": "chatgpt",
            "plan_type": "plus",
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "image_count": 0,
            "web_search": "live",
        }
        return CodexStructuredResult(
            value=value,
            thread_id=safe_trace["thread_id"],
            turn_id=safe_trace["turn_id"],
            model=safe_trace["model"],
            model_provider=safe_trace["model_provider"],
            auth_mode="chatgpt",
            plan_type="plus",
            usage=safe_trace["usage"],
            safe_trace=safe_trace,
            effort="high",
            duration_seconds=1.0,
            startup_reused=True,
        )

    results = search_visual_materials(
        project,
        directives=directives,
        page_context={
            "page_number": 3,
            "page_title": "Real asset completion",
            "body_text": "Locked page body",
            "key_facts": [],
            "detected_dates": [],
        },
        timeout=30,
        invoke=invoke,
    )

    assert results == [[] for _ in directives]
    assert len(calls) == 1
    candidate_schema = calls[0]["output_schema"]["properties"]["results"]["items"][
        "properties"
    ]["candidates"]["items"]
    assert {"source_authority", "authority_basis"}.issubset(
        set(candidate_schema["required"])
    )
    prompt = str(calls[0]["prompt"]).casefold()
    assert "official source" in prompt
    assert "official brand media page" in prompt
    assert "employer or official event organizer" in prompt
    assert "project site or official news" in prompt
    assert "source_authority" in prompt
    assert "authority_basis" in prompt

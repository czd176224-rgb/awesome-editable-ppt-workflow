from __future__ import annotations

import json
import hashlib
import struct
import subprocess
import sys
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v6_contract import canonical_sha256, new_page, new_project  # noqa: E402
from workflow_v6_image import (  # noqa: E402
    ImageRequest,
    ProviderFailure,
    _run,
    build_image_command,
    build_image_request,
    build_prompt,
    generate_page_body,
    initial_quality,
    _advance_page_to_accepted_receipt,
    seal_page_image_prompt,
)
from workflow_v6_state import create, load, save  # noqa: E402
from workflow_v6_cli import _parser  # noqa: E402
from adaptive_scheduler import PAGE_OWNERSHIP_STATE_FILE, SCHEDULER_STATE_FILE  # noqa: E402
from awesome_page_materials import publish_page_materials  # noqa: E402
from validate_page_image_prompt import _block  # noqa: E402
import workflow_v6_image  # noqa: E402


def _visual_contract() -> dict:
    return {
        "primary_color": "#17365D", "secondary_color": "#C7352B",
        "background_color": "#FFFFFF", "cjk_font": "Microsoft YaHei",
        "latin_font": "Arial", "title_size_pt": 28, "body_size_pt": 12,
        "caption_size_pt": 9, "regional_characteristics": "",
        "visual_description": "Formal editorial presentation.",
    }


def _project(tmp_path: Path, page_count: int = 1) -> Path:
    project = tmp_path / "project"
    state = new_project(
        word_source={"path": "00_source/source.docx"},
        logo_source={"path": "00_source/logo.svg"},
        pages=[new_page(number, title=f"Page {number}") for number in range(1, page_count + 1)],
    )
    for number in range(1, page_count + 1):
        for folder, payload in (
            ("effective_pages", {"artifact_version": "effective-page-v6", "page_number": number, "word_original": f"Body {number}", "comment_directives": []}),
            ("reference_materials", {"artifact_version": "reference-materials-v6", "page_number": number, "references": [], "search_requests": []}),
        ):
            path = project / "02_v6" / folder / f"page_{number:03d}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result = {
        "status": "confirmed", "revision": 1, "confirmed_at": "2026-08-12T00:00:00+08:00",
        "production_profile": "balanced", "global_visual_contract": {"visual_style": "minimal"},
        "confirmed_pages": [{"page_number": number, "effective_body": f"Approved page {number}", "attachment_extracts": [], "chart_facts": [], "image_requirements": [], "degradations": [], "reference_images": [], "reference_decisions": []} for number in range(1, page_count + 1)],
    }
    result_path = project / "confirm_ui" / "result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    state["style_confirmation"] = {
        "status": "confirmed",
        "contract": _visual_contract(),
    }
    state["confirmed_ui_revision"] = 1
    state["confirmed_ui_digest"] = canonical_sha256(result)
    state["page_materials_status"] = "pending"
    create(project, state)
    source_dir = project / "02_v6"
    (source_dir / "paginated_word_source.json").write_text(json.dumps({"pages": [
        {"page_number": number, "fixed_page_title": f"Page {number}", "fixed_page_title_source_block_id": f"title-{number}", "page_comments": [], "blocks": [
            {"type": "paragraph", "text": f"Page {number}", "source_block_id": f"title-{number}", "source_block_index": (number - 1) * 2, "source_order": 1, "relationship_ids": [], "comment_ids": []},
            {"type": "paragraph", "text": f"Body {number}", "source_block_id": f"body-{number}", "source_block_index": (number - 1) * 2 + 1, "source_order": 2, "relationship_ids": [], "comment_ids": []},
        ]} for number in range(1, page_count + 1)
    ]}, ensure_ascii=False), encoding="utf-8")
    (source_dir / "source_assets.json").write_text('{"assets":[]}', encoding="utf-8")
    for number in range(1, page_count + 1):
        publish_page_materials(project, number, project / "02_v6" / "awesome_page_materials" / f"page_{number:03d}.json")
    for number in range(1, page_count + 1):
        _seal_test_prompt(project, number)
    return project


def _seal_test_prompt(project: Path, page_number: int, selected: list[str] | None = None) -> None:
    selected = selected or []
    materials = json.loads((project / f"02_v6/awesome_page_materials/page_{page_number:03d}.json").read_text(encoding="utf-8"))
    word_ids = [item["source_block_id"] for item in materials["complete_word_content"]]
    plan = {
        "composition_archetype": "single-focus", "content_sequence": word_ids,
        "comment_directives": [], "reference_substitutions": [],
        "hierarchy_order": [*word_ids, *selected], "emphasis_ids": [],
        "groups": [{"group_id": "group_1", "member_ids": [*word_ids, *selected]}],
        "reading_direction": "left-to-right", "layout_density": "balanced",
        "whitespace": "balanced", "connector_style": "none",
        "icon_policy": "generic-functional-only",
        "reference_treatments": [{"reference_id": ref, "preserve": "identity-and-content",
                                  "change": "scale-and-place", "crop": "none", "placement": "supporting"}
                                 for ref in selected],
    }
    prompt = (
        "## Task\nGenerate one 1904 x 896 PowerPoint body-region image.\n\n## Original Materials\n"
        + _block("WORD_CONTENT_JSON", materials["complete_word_content"]) + "\n"
        + _block("ORIGINAL_COMMENTS_JSON", materials["original_comments"]) + "\n"
        + _block("SELECTED_REFERENCE_IDS_JSON", selected) + "\n\n## Visual Presentation\n"
        + _block("VISUAL_CONTRACT_JSON", materials["visual_contract"]) + "\n"
        + _block("DESIGN_PLAN_JSON", plan) + "\n\n## Fixed Boundaries\n"
        "Generate only the 17:8 body region at exactly 1904 x 896 and respect the safe area.\n"
        "Do not generate the fixed page title.\nDo not generate the fixed logo.\n"
        "Do not generate the footer.\nDo not generate the page number."
    )
    result = {"schema_version": "page-image-prompt-v1", "page_number": page_number,
              "selected_reference_images": selected, "image_prompt": prompt}
    source = project / f"prompt-{page_number}.json"
    source.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    seal_page_image_prompt(project, page_number, source)


def _confirmed_reference(path: Path, *, status: str = "available", digest: str | None = None, purpose: str = "evidence") -> dict:
    return {
        "reference_id": path.stem,
        "status": status,
        "model_input_path": str(path),
        "purpose": purpose,
        "integrity": {
            "model_input_sha256": digest if digest is not None else hashlib.sha256(path.read_bytes()).hexdigest(),
        },
    }


def _multi_page_project(tmp_path: Path, page_count: int = 3) -> Path:
    return _project(tmp_path, page_count)


def _write_mock_trace(command: list[str]) -> None:
    output = Path(command[command.index("--out") + 1])
    trace = Path(command[command.index("--trace-out") + 1])
    images = [command[index + 1] for index, value in enumerate(command) if value == "--image"]
    roles = [command[index + 1] for index, value in enumerate(command) if value == "--image-role"]
    digests = [command[index + 1] for index, value in enumerate(command) if value == "--image-sha256"]
    trace.write_text(json.dumps({
        "operation": command[2],
        "model": "gpt-image-2",
        "quality": command[command.index("--quality") + 1],
        "size": command[command.index("--size") + 1],
        "input_images": [
            {"role": role, "path": str(Path(path)), "sha256": digest}
            for role, path, digest in zip(roles, images, digests)
        ],
        "outputs": [{
            "path": str(output.resolve()),
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "mime_type": "image/png",
        }],
    }), encoding="utf-8")


def _semantic_failure(
    correction: str, *, score: int = 4, code: str = "global_style_followed",
) -> dict:
    structured_by_code = {
        "global_style_followed": {
            "action": "increase", "target": "contrast_relation",
            "constraint": "contrast_relation",
        },
        "confirmed_content_and_requirements": {
            "action": "restore", "target": "confirmed_body",
            "constraint": "match_confirmed_body",
        },
        "fixed_layers_absent": {
            "action": "remove", "target": "fixed_title",
            "constraint": "remove_fixed_layer",
        },
    }
    fields = structured_by_code[code]
    return {
        "accepted": False,
        "score": score,
        "checks": {
            code: {
                "result": "fail",
                "detail": "The frozen semantic contract is not yet satisfied.",
                "correction": {"check": code, **fields, "correction": correction},
            },
        },
        "issues": [],
    }


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        ({"effective_body": "Short approved copy", "reference_images": [{"purpose": "ordinary photo"}]}, "medium"),
        ({"effective_body": "Short", "reference_images": [{"purpose": "company logo"}]}, "high"),
        ({"effective_body": "Short", "reference_images": [{"purpose": "product screenshot"}]}, "high"),
        ({"effective_body": "Short", "chart_facts": [{"title": "Revenue trend", "unit": "USD m", "series": [{"series": "Revenue", "time": "2025", "value": 20}]}]}, "high"),
        ({"effective_body": "Short", "attachment_extracts": [{"kind": "table", "rows": 12}]}, "high"),
        ({"effective_body": "Short", "attachment_extracts": [{"selector": "selected_rows", "content": [{"Revenue": "20"}, {"Revenue": "40"}]}]}, "high"),
        ({"effective_body": "x" * 1200}, "high"),
        ({"effective_body": "Short", "image_requirements": [{"role": "high-detail evidence"}]}, "high"),
        ({"effective_body": "Short", "image_requirements": [{"kind": "reference_acquisition", "visual": "logo"}]}, "high"),
        ({"effective_body": "Short", "image_requirements": [{"kind": "reference_acquisition", "visual": "screenshot"}]}, "high"),
    ],
)
def test_initial_quality_uses_only_frozen_material_risk(page: dict, expected: str):
    assert initial_quality(page) == expected


def test_subprocess_runner_preserves_typed_provider_status(monkeypatch):
    class Completed:
        returncode = 1
        stdout = ""
        stderr = 'CODEX_IMAGE_ERROR_JSON:{"status_code":429,"network":false,"message":"rate limited"}\n'

    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: Completed())

    with pytest.raises(ProviderFailure) as failure:
        _run(["image-cli"], 10)
    assert failure.value.status_code == 429
    assert failure.value.network is False


@pytest.mark.parametrize("count", [0, 1, 16])
def test_image_request_selects_operation_from_readable_confirmed_images(tmp_path: Path, count: int):
    references = []
    for index in range(count):
        path = tmp_path / f"reference-{index:02d}.png"
        Image.new("RGB", (8, 4), (index, 20, 40)).save(path)
        references.append(_confirmed_reference(path, purpose=f"role-{index:02d}"))

    request = build_image_request(
        confirmed_page={"page_number": 1, "effective_body": "Approved", "reference_images": references},
        visual_contract={"visual_style": "minimal"},
    )

    assert request.operation == ("generate" if count == 0 else "edit")
    assert request.input_images == tuple(Path(item["model_input_path"]).resolve() for item in references)
    assert request.image_roles == tuple(item["purpose"] for item in references)


def test_invalid_confirmed_images_are_excluded_and_all_invalid_falls_back_to_generate(tmp_path: Path):
    valid = tmp_path / "valid.png"
    mismatch = tmp_path / "mismatch.png"
    stale = tmp_path / "stale.png"
    Image.new("RGB", (8, 4), "green").save(valid)
    Image.new("RGB", (8, 4), "red").save(mismatch)
    Image.new("RGB", (8, 4), "blue").save(stale)
    missing = tmp_path / "missing.png"
    unreadable = tmp_path / "directory-not-file"
    unreadable.mkdir()
    invalid = [
        _confirmed_reference(mismatch, digest="0" * 64),
        _confirmed_reference(stale, status="unavailable"),
        {"reference_id": "missing", "status": "available", "model_input_path": str(missing), "purpose": "missing", "integrity": {"model_input_sha256": "1" * 64}},
        {"reference_id": "unreadable", "status": "available", "model_input_path": str(unreadable), "purpose": "unreadable", "integrity": {"model_input_sha256": "2" * 64}},
    ]

    mixed = build_image_request(
        confirmed_page={"page_number": 1, "effective_body": "Approved", "reference_images": [_confirmed_reference(valid), *invalid]},
        visual_contract={"visual_style": "minimal"},
    )
    fallback = build_image_request(
        confirmed_page={"page_number": 1, "effective_body": "Approved", "reference_images": invalid},
        visual_contract={"visual_style": "minimal"},
    )

    assert mixed.operation == "edit"
    assert mixed.input_images == (valid.resolve(),)
    assert fallback.operation == "generate"
    assert fallback.input_images == ()
    assert fallback.image_roles == ()
    assert "mismatch" not in fallback.prompt
    assert "stale" not in fallback.prompt


@pytest.mark.skip(reason="legacy UI reference authority removed by Task 7")
def test_generation_prompt_and_qa_receive_only_resolved_usable_references(tmp_path: Path):
    project = _project(tmp_path)
    invalid = project / "02_v6" / "reference_media" / "invalid" / "model-input.png"
    invalid.parent.mkdir(parents=True)
    Image.new("RGB", (8, 4), "red").save(invalid)
    result_path = project / "confirm_ui" / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["confirmed_pages"][0]["reference_images"] = [{
        **_confirmed_reference(invalid, digest="0" * 64, purpose="must show real evidence"),
        "reference_id": "invalid-real-evidence",
    }]
    result_path.write_text(json.dumps(result), encoding="utf-8")
    state = load(project); state["confirmed_ui_digest"] = canonical_sha256(result); save(project, state)
    observed = {}

    def runner(command, timeout):
        observed["command"] = command
        observed["prompt"] = Path(command[command.index("--prompt-file") + 1]).read_text(encoding="utf-8")
        Image.new("RGB", (1904, 896), "white").save(Path(command[command.index("--out") + 1]))
        _write_mock_trace(command)

    def reviewer(*_args, **kwargs):
        observed["qa_page"] = kwargs["effective_page"]
        return {"accepted": True, "score": 6, "issues": []}

    generate_page_body(project, page_number=1, runner=runner, reviewer=reviewer)

    assert observed["command"][2] == "generate"
    assert "invalid-real-evidence" not in observed["prompt"]
    assert observed["qa_page"]["reference_images"] == []


@pytest.mark.skip(reason="legacy UI page authority removed by Task 7")
def test_generation_prompt_and_qa_receive_only_filtered_frozen_material(tmp_path: Path):
    project = _project(tmp_path)
    result_path = project / "confirm_ui" / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    page = result["confirmed_pages"][0]
    page.update({
        "fixed_page_title": "FIXED_TITLE_PRIVATE",
        "word_original": "RAW_WORD_PRIVATE",
        "raw_comments": ["RAW_COMMENT_PRIVATE"],
        "search_tasks": [{"query": "SEARCH_RECORD_PRIVATE"}],
        "acquisition_receipt": {"status": "ACQUISITION_RECORD_PRIVATE"},
        "backend_conclusions": ["BACKEND_CONCLUSION_PRIVATE"],
        "candidate_path": "04_v6/images/CANDIDATE_PRIVATE.png",
        "confirmed_revision": 1,
        "confirmed_revision_digest": "c" * 64,
    })
    page["attachment_extracts"] = [{
        "content": {
            "path": "USER_ATTACHMENT_PATH",
            "metadata": {"revision": "USER_ATTACHMENT_REVISION"},
            "hash": "USER_ATTACHMENT_HASH",
        },
    }]
    result["global_visual_contract"].update({
        "contract_digest": "d" * 64,
        "local_path": "confirm_ui/PRIVATE_STYLE_PATH.json",
    })
    result_path.write_text(json.dumps(result), encoding="utf-8")
    state = load(project)
    state["confirmed_ui_digest"] = canonical_sha256(result)
    save(project, state)
    observed = {}

    def runner(command, timeout):
        observed["prompt"] = Path(
            command[command.index("--prompt-file") + 1]
        ).read_text(encoding="utf-8")
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    def reviewer(*_args, **kwargs):
        observed["qa_page"] = kwargs["effective_page"]
        observed["qa_style"] = kwargs["style_contract"]
        return {"accepted": True, "score": 6, "issues": []}

    generate_page_body(project, page_number=1, runner=runner, reviewer=reviewer)

    forbidden_fields = {
        "fixed_page_title", "word_original", "raw_comments", "search_tasks",
        "acquisition_receipt", "backend_conclusions", "candidate_path",
        "confirmed_revision", "confirmed_revision_digest",
    }
    assert forbidden_fields.isdisjoint(observed["qa_page"])
    assert observed["qa_page"]["attachment_extracts"] == page["attachment_extracts"]
    assert "contract_digest" not in observed["qa_style"]
    assert "local_path" not in observed["qa_style"]
    assert all(
        sentinel not in observed["prompt"]
        for sentinel in (
            "FIXED_TITLE_PRIVATE", "RAW_WORD_PRIVATE", "RAW_COMMENT_PRIVATE",
            "SEARCH_RECORD_PRIVATE", "ACQUISITION_RECORD_PRIVATE",
            "BACKEND_CONCLUSION_PRIVATE", "CANDIDATE_PRIVATE", "c" * 64,
            "PRIVATE_STYLE_PATH", "d" * 64,
        )
    )
    assert all(
        sentinel in observed["prompt"]
        for sentinel in (
            "USER_ATTACHMENT_PATH", "USER_ATTACHMENT_REVISION", "USER_ATTACHMENT_HASH",
        )
    )


@pytest.mark.parametrize("payload", [b"plain text", b"\x89PNG\r\n\x1a\nnot-decodable"])
def test_digest_matching_non_image_or_corrupt_image_cannot_select_edit(tmp_path: Path, payload: bytes):
    fake = tmp_path / "fake.png"
    fake.write_bytes(payload)

    request = build_image_request(
        confirmed_page={
            "page_number": 1,
            "effective_body": "Approved",
            "reference_images": [_confirmed_reference(fake)],
        },
        visual_contract={"visual_style": "minimal"},
    )

    assert request.operation == "generate"
    assert request.input_images == ()


def test_encoded_image_limit_uses_task4_bounded_reader_without_large_fixture(tmp_path: Path, monkeypatch):
    import workflow_v6_media as media

    image = tmp_path / "small.png"
    Image.new("RGB", (8, 4), "blue").save(image)
    monkeypatch.setattr(media, "MAX_ENCODED_BYTES", len(image.read_bytes()) - 1)
    request = build_image_request(
        confirmed_page={"page_number": 1, "effective_body": "Approved", "reference_images": [_confirmed_reference(image)]},
        visual_contract={"visual_style": "minimal"},
    )
    assert request.operation == "generate"


def test_edge_limit_rejects_decodable_image_without_large_allocation(tmp_path: Path):
    image = tmp_path / "wide.png"
    Image.new("RGB", (16_385, 1), "blue").save(image)
    request = build_image_request(
        confirmed_page={"page_number": 1, "effective_body": "Approved", "reference_images": [_confirmed_reference(image)]},
        visual_contract={"visual_style": "minimal"},
    )
    assert request.operation == "generate"


def test_pixel_limit_rejects_header_before_decoding_large_allocation(tmp_path: Path):
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    image = tmp_path / "huge-header.png"
    image.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 10_000, 9_000, 8, 2, 0, 0, 0))
        + chunk(b"IEND", b"")
    )
    request = build_image_request(
        confirmed_page={"page_number": 1, "effective_body": "Approved", "reference_images": [_confirmed_reference(image)]},
        visual_contract={"visual_style": "minimal"},
    )
    assert request.operation == "generate"


def test_image_command_uses_generate_without_image_inputs(tmp_path: Path):
    request = ImageRequest("generate", "medium", "approved prompt", (), ())
    command = build_image_command(
        request,
        prompt_file=tmp_path / "prompt.txt",
        output=tmp_path / "out.png",
        trace=tmp_path / "trace.json",
    )
    assert command[2] == "generate"
    assert "edit" not in command
    assert "--image" not in command
    assert command[command.index("--size") + 1] == "1904x896"
    assert command[command.index("--quality") + 1] == "medium"


def test_image_command_uses_aligned_edit_inputs_and_roles(tmp_path: Path):
    first, second = tmp_path / "first.png", tmp_path / "second.png"
    Image.new("RGB", (8, 4), "blue").save(first)
    Image.new("RGB", (8, 4), "green").save(second)
    digests = (hashlib.sha256(first.read_bytes()).hexdigest(), hashlib.sha256(second.read_bytes()).hexdigest())
    request = ImageRequest("edit", "high", "approved prompt", (first, second), ("logo", "screenshot"), digests)

    command = build_image_command(request, prompt_file=tmp_path / "prompt.txt", output=tmp_path / "out.png", trace=tmp_path / "trace.json")

    assert command[2] == "edit"
    assert [command[index + 1] for index, value in enumerate(command) if value == "--image"] == [str(first), str(second)]
    assert [command[index + 1] for index, value in enumerate(command) if value == "--image-role"] == ["logo", "screenshot"]
    assert [command[index + 1] for index, value in enumerate(command) if value == "--image-sha256"] == [
        hashlib.sha256(first.read_bytes()).hexdigest(),
        hashlib.sha256(second.read_bytes()).hexdigest(),
    ]
    assert command[command.index("--quality") + 1] == "high"


def test_public_request_keeps_verified_digest_when_file_is_replaced_between_build_calls(tmp_path: Path):
    image = tmp_path / "reference.png"
    Image.new("RGB", (8, 4), "blue").save(image)
    original_digest = hashlib.sha256(image.read_bytes()).hexdigest()
    request = build_image_request(
        confirmed_page={"page_number": 1, "effective_body": "Approved", "reference_images": [_confirmed_reference(image)]},
        visual_contract={"visual_style": "minimal"},
    )
    Image.new("RGB", (8, 4), "red").save(image)

    assert request.input_sha256s == (original_digest,)
    with pytest.raises(ValueError, match="changed|digest"):
        build_image_command(
            request, prompt_file=tmp_path / "prompt.txt",
            output=tmp_path / "out.png", trace=tmp_path / "trace.json",
        )


@pytest.mark.skip(reason="legacy UI snapshot route removed by Task 7")
def test_snapshot_writer_rejects_injected_final_handle_escape_before_payload_write(tmp_path: Path, monkeypatch):
    import workflow_v6_media as media

    project = _project(tmp_path)
    model_input = project / "02_v6" / "reference_media" / "approved" / "model-input.png"
    model_input.parent.mkdir(parents=True)
    Image.new("RGB", (32, 18), "navy").save(model_input)
    result_path = project / "confirm_ui" / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["confirmed_pages"][0]["reference_images"] = [_confirmed_reference(model_input)]
    result["confirmed_pages"][0]["reference_images"][0]["model_input_path"] = model_input.relative_to(project).as_posix()
    result_path.write_text(json.dumps(result), encoding="utf-8")
    state = load(project); state["confirmed_ui_digest"] = canonical_sha256(result); save(project, state)
    outside = tmp_path / "outside" / "snapshot.img"
    original_final_path = media._final_path_for_handle

    def injected_then_real(handle):
        actual = original_final_path(handle)
        return outside if actual.suffix == ".img" else actual

    monkeypatch.setattr(media, "_final_path_for_handle", injected_then_real)
    calls = []

    def runner(command, timeout):
        calls.append(command)
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    generate_page_body(
        project, page_number=1, runner=runner,
        reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
    )

    assert calls[0][2] == "generate"
    assert not outside.exists() or outside.read_bytes() != model_input.read_bytes()


def _project_with_confirmed_reference(tmp_path: Path) -> Path:
    project = _project(tmp_path)
    source = project / "01_source_assets" / "approved" / "model-input.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 18), "navy").save(source)
    materials_path = project / "02_v6/awesome_page_materials/page_001.json"
    materials = json.loads(materials_path.read_text(encoding="utf-8"))
    state_path = project / "workflow_v6.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    materials["word_images"] = [{"asset_id": "approved", "source_order": 1,
                                  "original_filename": "model-input.png", "media_type": "image/png",
                                  "path": source.relative_to(project).as_posix(), "sha256": digest,
                                  "byte_size": source.stat().st_size}]
    payload = (json.dumps(materials, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    materials_path.write_bytes(payload)
    state["pages"][0]["material_receipt"]["digest"] = hashlib.sha256(payload).hexdigest()
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for path in (project / "02_v6/page_image_prompts/page_001.output.json",
                 project / "02_v6/page_image_prompts/page_001.receipt.json"):
        path.unlink(missing_ok=True)
    _seal_test_prompt(project, 1, ["word:approved"])
    return project


def _run_one_accepted_page(project: Path) -> list[list[str]]:
    calls: list[list[str]] = []

    def runner(command, timeout):
        calls.append(command)
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    generate_page_body(
        project, page_number=1, runner=runner,
        reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
    )
    return calls


def test_snapshot_writer_does_not_chmod_path_after_handle_verified_write(tmp_path: Path, monkeypatch):
    project = _project_with_confirmed_reference(tmp_path)
    original_chmod = Path.chmod

    def reject_snapshot_chmod(path: Path, *args, **kwargs):
        if path.suffix == ".img":
            raise AssertionError("snapshot pathname chmod is forbidden")
        return original_chmod(path, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", reject_snapshot_chmod)
    assert _run_one_accepted_page(project)[0][2] == "edit"


def test_snapshot_writer_does_not_resolve_snapshot_path_after_safe_close(tmp_path: Path, monkeypatch):
    project = _project_with_confirmed_reference(tmp_path)
    original_resolve = Path.resolve

    def reject_post_write_resolve(path: Path, *args, **kwargs):
        if path.suffix == ".img" and path.exists():
            raise AssertionError("snapshot pathname resolve after safe close is forbidden")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", reject_post_write_resolve)
    assert _run_one_accepted_page(project)[0][2] == "edit"


@pytest.mark.parametrize("image_request", [
    ImageRequest("edit", "medium", "prompt", (), ()),
    ImageRequest("generate", "medium", "prompt", (Path("reference.png"),), ("evidence",)),
    ImageRequest("edit", "medium", "prompt", (Path("reference.png"),), ()),
    ImageRequest("unsupported", "medium", "prompt", (), ()),  # type: ignore[arg-type]
    ImageRequest("generate", "unsupported", "prompt", (), ()),  # type: ignore[arg-type]
])
def test_image_command_rejects_operation_input_invariants(tmp_path: Path, image_request: ImageRequest):
    with pytest.raises(ValueError, match="image|role|edit|generate|quality|operation|digest"):
        build_image_command(image_request, prompt_file=tmp_path / "prompt.txt", output=tmp_path / "out.png", trace=tmp_path / "trace.json")


def test_qa_no_improvement_stops_without_accepting_first_candidate(tmp_path: Path):
    project = _project(tmp_path)
    calls = []

    def runner(command, timeout):
        calls.append(command)
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    reviews = iter([
        _semantic_failure("Remove the overlap between the body text and chart."),
        _semantic_failure("Remove the remaining overlap between the body text and chart."),
    ])

    with pytest.raises(RuntimeError, match="no independently accepted"):
        generate_page_body(
            project,
            page_number=1,
            runner=runner,
            reviewer=lambda *args, **kwargs: next(reviews),
        )

    assert len(calls) == 2
    assert all(command[2] == "generate" and "--image" not in command for command in calls)
    page = load(project)["pages"][0]
    assert page["selected_candidate"] is None
    assert "qa_no_effective_improvement" in page["degraded_reasons"]
    assert page["state"] == "technical_failed"


def test_qa_unavailable_stops_without_accepting_candidate1(tmp_path: Path):
    project = _project(tmp_path)

    def runner(command, timeout):
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    with pytest.raises(RuntimeError, match="no independently accepted"):
        generate_page_body(
            project, page_number=1, runner=runner,
            reviewer=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("review service unavailable")),
        )
    page = load(project)["pages"][0]
    assert page["state"] == "technical_failed"
    assert page["selected_candidate"] is None
    assert page["degraded_reasons"] == ["qa_unavailable"]


@pytest.mark.skip(reason="legacy UI reference fixture removed; covered by awesome request matrix")
def test_edit_retry_reuses_only_original_confirmed_inputs_never_candidate_one(tmp_path: Path):
    project = _project(tmp_path)
    model_input = project / "02_v6" / "reference_media" / "approved" / "model-input.png"
    model_input.parent.mkdir(parents=True)
    Image.new("RGB", (32, 18), "navy").save(model_input)
    result_path = project / "confirm_ui" / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["confirmed_pages"][0]["reference_images"] = [{
        "reference_id": "approved",
        "source": "attachment",
        "purpose": "approved screenshot",
        "preservation": "preserve",
        "allow_crop": False,
        "allow_restyle": False,
        "status": "available",
        "original_path": "02_v6/reference_media/approved/original.png",
        "model_input_path": model_input.relative_to(project).as_posix(),
        "thumbnail_path": "02_v6/reference_media/approved/thumbnail.png",
        "source_url": None,
        "integrity": {
            "original_sha256": "0" * 64,
            "model_input_sha256": hashlib.sha256(model_input.read_bytes()).hexdigest(),
            "thumbnail_sha256": "1" * 64,
        },
    }]
    result_path.write_text(json.dumps(result), encoding="utf-8")
    state = load(project)
    state["confirmed_ui_digest"] = canonical_sha256(result)
    save(project, state)
    calls = []

    def runner(command, timeout):
        calls.append(command)
        if len(calls) == 1:
            model_input.write_bytes(b"source changed after request resolution")
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    reviews = iter([
        _semantic_failure("Improve composition by aligning the approved screenshot.", score=3),
        {"accepted": True, "score": 6, "issues": []},
    ])
    generate_page_body(
        project, page_number=1, max_candidates=2, runner=runner,
        reviewer=lambda *_args, **_kwargs: next(reviews),
    )

    assert len(calls) == 2
    first_inputs = [Path(calls[0][index + 1]) for index, value in enumerate(calls[0]) if value == "--image"]
    for command in calls:
        assert command[2] == "edit"
        inputs = [Path(command[index + 1]) for index, value in enumerate(command) if value == "--image"]
        assert inputs == first_inputs
        assert inputs[0] != model_input.resolve()
        assert hashlib.sha256(inputs[0].read_bytes()).hexdigest() == command[command.index("--image-sha256") + 1]
        assert all("candidate_" not in str(path) for path in inputs)


@pytest.mark.skip(reason="legacy UI result page is no longer image authority")
def test_generation_rejects_an_altered_live_confirmation_before_runner_or_qa(tmp_path: Path):
    project = _project(tmp_path)
    result_path = project / "confirm_ui" / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["confirmed_pages"][0]["effective_body"] = "altered after sealing"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    with pytest.raises(ValueError, match="identity|digest"):
        generate_page_body(
            project,
            page_number=1,
            runner=lambda *_args, **_kwargs: pytest.fail("runner must not be called"),
            reviewer=lambda *_args, **_kwargs: pytest.fail("reviewer must not be called"),
        )


@pytest.mark.skip(reason="legacy UI result page is no longer image authority")
def test_generation_rejects_revision_two_until_it_is_resealed(tmp_path: Path):
    project = _project(tmp_path)
    result_path = project / "confirm_ui" / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["revision"] = 2
    result_path.write_text(json.dumps(result), encoding="utf-8")

    with pytest.raises(ValueError, match="revision|identity"):
        generate_page_body(
            project,
            page_number=1,
            runner=lambda *_args, **_kwargs: pytest.fail("runner must not be called"),
            reviewer=lambda *_args, **_kwargs: pytest.fail("reviewer must not be called"),
        )


def test_old_generate_only_receipt_is_not_reused_without_current_sealed_request_identity(tmp_path: Path):
    project = _project(tmp_path)
    directory = project / "04_v6" / "images"
    directory.mkdir(parents=True)
    old_output = directory / "page_001.candidate_1.png"
    Image.new("RGB", (1904, 896), "black").save(old_output)
    (directory / "page_001.candidate_1.trace.json").write_text(json.dumps({
        "operation": "generate", "model": "gpt-image-2", "input_images": [],
    }), encoding="utf-8")
    (directory / "page_001.json").write_text(json.dumps({
        "artifact_version": "image2-generate-v6", "page_number": 1,
        "selected": {"attempt": 1, "path": old_output.relative_to(project).as_posix(), "operation": "generate"},
    }), encoding="utf-8")
    calls = []

    def runner(command, timeout):
        calls.append(command)
        output = Path(command[command.index("--out") + 1])
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    generate_page_body(
        project, page_number=1, runner=runner,
        reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
    )

    assert len(calls) == 1


@pytest.mark.skip(reason="Task 8 replaces fallback-first retry semantics")
def test_qa_feedback_over_prompt_limit_stops_without_retry_or_acceptance(tmp_path: Path):
    project = _project(tmp_path)
    result_path = project / "confirm_ui" / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["confirmed_pages"][0]["effective_body"] = "x" * 30_650
    result_path.write_text(json.dumps(result), encoding="utf-8")
    state = load(project)
    state["confirmed_ui_digest"] = canonical_sha256(result)
    save(project, state)
    calls = []
    feedback = "Increase body contrast relative to the approved background: " + "y" * 400

    initial_prompt = build_prompt(
        global_visual_contract=result["global_visual_contract"],
        confirmed_page=result["confirmed_pages"][0],
    )
    retry_prompt = build_prompt(
        global_visual_contract=result["global_visual_contract"],
        confirmed_page=result["confirmed_pages"][0],
        qa_feedback=[feedback],
    )
    assert len(initial_prompt) <= 32_000
    assert len(retry_prompt) > 32_000

    def runner(command, timeout):
        calls.append(command)
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    with pytest.raises(RuntimeError, match="no independently accepted"):
        generate_page_body(
            project,
            page_number=1,
            runner=runner,
            reviewer=lambda *_args, **_kwargs: {
                **_semantic_failure(feedback, score=3),
            },
        )

    first_prompt = Path(calls[0][calls[0].index("--prompt-file") + 1]).read_text(encoding="utf-8")
    assert len(first_prompt) <= 32_000
    assert len(calls) == 1
    page = load(project)["pages"][0]
    assert page["selected_candidate"] is None
    assert page["state"] == "technical_failed"
    assert "qa_feedback_exceeds_prompt_limit" in page["degraded_reasons"]


@pytest.mark.skip(reason="compiler validator now owns prompt validity")
def test_initial_prompt_over_limit_fails_before_runner_or_qa(tmp_path: Path):
    project = _project(tmp_path)
    result_path = project / "confirm_ui" / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["confirmed_pages"][0]["effective_body"] = "x" * 32_000
    result_path.write_text(json.dumps(result), encoding="utf-8")
    state = load(project)
    state["confirmed_ui_digest"] = canonical_sha256(result)
    save(project, state)

    with pytest.raises(ValueError, match="32,000|32000|prompt limit"):
        generate_page_body(
            project,
            page_number=1,
            runner=lambda *_args, **_kwargs: pytest.fail("runner must not be called"),
            reviewer=lambda *_args, **_kwargs: pytest.fail("reviewer must not be called"),
        )


def test_accepted_later_generate_candidate_is_selected(tmp_path: Path):
    project = _project(tmp_path)

    def runner(command, timeout):
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    reviews = iter([
        _semantic_failure("Increase body contrast and align the lower panel.", score=3),
        {"accepted": True, "score": 6, "issues": []},
    ])
    receipt = generate_page_body(
        project,
        page_number=1,
        runner=runner,
        reviewer=lambda *args, **kwargs: next(reviews),
    )
    assert receipt["selected"]["attempt"] == 2
    assert receipt["state"] == "accepted"


def test_light_qa_timeout_is_bounded_independently_from_image_generation(tmp_path: Path):
    project = _project(tmp_path)
    observed = []

    def runner(command, timeout):
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    def reviewer(*args, **kwargs):
        observed.append(kwargs["timeout"])
        return {"accepted": True, "score": 6, "issues": []}

    generate_page_body(project, page_number=1, timeout=900, runner=runner, reviewer=reviewer)

    assert observed == [180]


def test_non_actionable_qa_failure_does_not_spend_second_candidate(tmp_path: Path):
    project = _project(tmp_path)
    calls = []

    def runner(command, timeout):
        calls.append(command)
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    with pytest.raises(RuntimeError, match="no independently accepted"):
        generate_page_body(
            project, page_number=1, max_candidates=99, runner=runner,
            reviewer=lambda *_args, **_kwargs: {"accepted": False, "score": 3, "issues": []},
        )

    assert len(calls) == 1
    page = load(project)["pages"][0]
    assert page["state"] == "technical_failed"
    assert "qa_feedback_not_actionable" in page["degraded_reasons"]


@pytest.mark.parametrize("vague_issue", ["bad", "Looks generally disappointing"])
def test_vague_generic_qa_issue_does_not_spend_second_candidate(
    tmp_path: Path, vague_issue: str,
):
    project = _project(tmp_path)
    calls = []

    def runner(command, timeout):
        calls.append(command)
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    with pytest.raises(RuntimeError, match="no independently accepted"):
        generate_page_body(
            project, page_number=1, runner=runner,
            reviewer=lambda *_args, **_kwargs: {"accepted": False, "score": 3, "issues": [vague_issue]},
        )

    assert len(calls) == 1
    assert "qa_feedback_not_actionable" in load(project)["pages"][0]["degraded_reasons"]


def test_precise_legacy_check_detail_does_not_open_retry_without_correction(tmp_path: Path):
    project = _project(tmp_path)
    calls = []
    reviews = iter([
        {
            "accepted": False, "score": 5, "issues": [],
            "checks": {
                "basic_readability": {
                    "result": "fail",
                    "detail": "Body text overlaps the chart labels in the lower-right panel.",
                },
            },
        },
        {"accepted": True, "score": 6, "issues": []},
    ])

    def runner(command, timeout):
        calls.append(command)
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    with pytest.raises(RuntimeError, match="no independently accepted"):
        generate_page_body(
            project, page_number=1, runner=runner,
            reviewer=lambda *_args, **_kwargs: next(reviews),
        )

    assert len(calls) == 1
    page = load(project)["pages"][0]
    assert page["selected_candidate"] is None
    assert "qa_feedback_not_actionable" in page["degraded_reasons"]


def test_actionable_retry_upgrades_medium_to_high_and_caps_at_two(tmp_path: Path):
    project = _project(tmp_path)
    calls = []
    reviews = iter([
        _semantic_failure("Increase contrast between the body text and panels.", score=3),
        _semantic_failure("Increase contrast further between the body text and panels.", score=3),
    ])

    def runner(command, timeout):
        calls.append(command)
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    with pytest.raises(RuntimeError, match="no independently accepted"):
        generate_page_body(
            project, page_number=1, max_candidates=99, runner=runner,
            reviewer=lambda *_args, **_kwargs: next(reviews),
        )

    assert len(calls) == 2
    assert [command[command.index("--quality") + 1] for command in calls] == ["medium", "high"]
    assert load(project)["pages"][0]["state"] == "technical_failed"


def test_generate_page_retries_typed_429_on_same_candidate_only(tmp_path: Path):
    project = _project(tmp_path)
    calls = []
    delays = []

    def runner(command, timeout):
        calls.append(command)
        if len(calls) == 1:
            raise ProviderFailure("rate limited", status_code=429)
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    receipt = generate_page_body(
        project, page_number=1, runner=runner,
        reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
        retry_sleep=delays.append, retry_jitter=lambda: 0.5,
    )

    assert len(calls) == 2
    assert delays == [1.0]
    assert {Path(command[command.index("--out") + 1]).name for command in calls} == {
        "page_001.candidate_1.png"
    }
    assert len(receipt["candidates"]) == 1


def test_completed_page_with_verified_receipt_is_never_regenerated(tmp_path: Path):
    project = _project(tmp_path)

    def runner(command, timeout):
        output = Path(command[command.index("--out") + 1])
        trace = Path(command[command.index("--trace-out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    first = generate_page_body(
        project, page_number=1, runner=runner,
        reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
    )
    page1_state_before = load(project)["pages"][0]
    receipt_before = (project / "04_v6/images/page_001.json").read_bytes()

    resumed = generate_page_body(
        project, page_number=1,
        runner=lambda *_args, **_kwargs: pytest.fail("completed page must not be regenerated"),
        reviewer=lambda *_args, **_kwargs: pytest.fail("completed page must not be reviewed"),
    )

    assert resumed == first
    assert load(project)["pages"][0] == page1_state_before
    assert (project / "04_v6/images/page_001.json").read_bytes() == receipt_before


@pytest.mark.parametrize("with_reference", [False, True], ids=["generate", "edit"])
def test_adaptive_receipt_resumes_generate_and_edit_without_provider(
    tmp_path: Path, with_reference: bool,
):
    project = (
        _project_with_confirmed_reference(tmp_path)
        if with_reference else _project(tmp_path)
    )
    first = _generate_accepted_receipt(project)

    resumed = generate_page_body(
        project, page_number=1,
        runner=lambda *_args, **_kwargs: pytest.fail("valid adaptive receipt must resume"),
        reviewer=lambda *_args, **_kwargs: pytest.fail("valid adaptive receipt must skip QA"),
    )

    assert resumed == first
    assert resumed["artifact_version"] == "awesome-image2-request-v1"
    assert resumed["request_operation"] == ("edit" if with_reference else "generate")
    assert resumed["request_quality"] in {"medium", "high"}
    assert len(resumed["request_prompt_sha256"]) == 64
    assert len(resumed["request_identity"]) == 64
    assert all(len(item["output_sha256"]) == 64 for item in resumed["candidates"])
    assert all(len(item["trace_sha256"]) == 64 for item in resumed["candidates"])


@pytest.mark.parametrize(
    "damage",
    [
        "receipt_operation",
        "receipt_quality",
        "receipt_prompt",
        "candidate_output",
        "candidate_trace_bytes",
        "candidate_trace_semantics",
        "candidate_trace_wrong_shape",
        "candidate_list",
    ],
)
def test_adaptive_receipt_rejects_changed_request_or_artifact_and_regenerates(
    tmp_path: Path, damage: str,
):
    project = _project(tmp_path)
    original = _generate_accepted_receipt(project)
    receipt_path = project / "04_v6/images/page_001.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    selected_path = project / original["selected"]["path"]
    if damage == "receipt_operation":
        receipt["request_operation"] = "edit"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    elif damage == "receipt_quality":
        receipt["request_quality"] = "high"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    elif damage == "receipt_prompt":
        prompt = selected_path.with_suffix(".prompt.txt")
        prompt.write_text(prompt.read_text(encoding="utf-8") + " changed", encoding="utf-8")
    elif damage == "candidate_output":
        Image.new("RGB", (1904, 896), "red").save(selected_path)
    elif damage == "candidate_trace_bytes":
        trace = selected_path.with_suffix(".trace.json")
        trace.write_bytes(trace.read_bytes() + b" ")
    elif damage == "candidate_trace_semantics":
        trace = selected_path.with_suffix(".trace.json")
        value = json.loads(trace.read_text(encoding="utf-8"))
        value["model"] = "other-model"
        trace.write_text(json.dumps(value), encoding="utf-8")
    elif damage == "candidate_trace_wrong_shape":
        selected_path.with_suffix(".trace.json").write_text("[]", encoding="utf-8")
    else:
        receipt["candidates"] = []
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    calls = 0

    def runner(command, timeout):
        nonlocal calls
        calls += 1
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "blue").save(output)
        _write_mock_trace(command)

    replacement = generate_page_body(
        project, page_number=1, runner=runner,
        reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
    )

    should_regenerate = damage in {
        "receipt_prompt", "candidate_output", "candidate_trace_bytes",
        "candidate_trace_semantics",
        "candidate_trace_wrong_shape",
    }
    assert calls == (1 if should_regenerate else 0)
    assert replacement["artifact_version"] == "awesome-image2-request-v1"
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == replacement


@pytest.mark.parametrize("request_change", ["prompt", "quality"])
@pytest.mark.skip(reason="sealed prompt and request quality replace legacy live recompilation")
def test_adaptive_receipt_invalidates_when_current_prompt_or_quality_changes(
    tmp_path: Path, monkeypatch, request_change: str,
):
    project = _project(tmp_path)
    original = _generate_accepted_receipt(project)
    if request_change == "prompt":
        original_build_prompt = workflow_v6_image.build_prompt

        def changed_prompt(*args, **kwargs):
            return original_build_prompt(*args, **kwargs) + "\nchanged prompt contract"

        monkeypatch.setattr(workflow_v6_image, "build_prompt", changed_prompt)
    else:
        monkeypatch.setattr(workflow_v6_image, "initial_quality", lambda _page: "high")
    calls = []

    def runner(command, timeout):
        calls.append(command)
        output = Path(command[command.index("--out") + 1])
        Image.new("RGB", (1904, 896), "blue").save(output)
        _write_mock_trace(command)

    replacement = generate_page_body(
        project, page_number=1, runner=runner,
        reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
    )

    assert len(calls) == 1
    assert replacement["request_identity"] != original["request_identity"]


@pytest.mark.skip(reason="selected source mutation is now a hard pre-provider error")
def test_edit_receipt_invalidates_when_confirmed_input_bytes_change_at_same_path(
    tmp_path: Path,
):
    project = _project_with_confirmed_reference(tmp_path)
    original = _generate_accepted_receipt(project)
    source = project / "01_source_assets/approved/model-input.png"
    Image.new("RGB", (32, 18), "red").save(source)
    calls = []

    def runner(command, timeout):
        calls.append(command)
        output = Path(command[command.index("--out") + 1])
        Image.new("RGB", (1904, 896), "blue").save(output)
        _write_mock_trace(command)

    replacement = generate_page_body(
        project, page_number=1, runner=runner,
        reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
    )

    assert original["request_operation"] == "edit"
    assert len(calls) == 1 and calls[0][2] == "generate"
    assert replacement["request_operation"] == "generate"


@pytest.mark.skip(reason="sealed capability intentionally transmits local integrity digests to provider process")
def test_receipt_hashes_stay_local_and_input_hash_is_only_an_integrity_argument(
    tmp_path: Path,
):
    project = _project_with_confirmed_reference(tmp_path)
    observed = {}

    def runner(command, timeout):
        observed["command"] = command
        observed["prompt"] = Path(command[command.index("--prompt-file") + 1]).read_text(
            encoding="utf-8",
        )
        output = Path(command[command.index("--out") + 1])
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    receipt = generate_page_body(
        project, page_number=1, runner=runner,
        reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
    )

    local_only = {
        receipt["confirmed_ui_digest"], receipt["request_prompt_sha256"],
        receipt["request_identity"], receipt["candidates_sha256"],
        receipt["selected"]["output_sha256"], receipt["selected"]["trace_sha256"],
        receipt["selected"]["trace_semantics_sha256"],
    }
    assert all(value not in observed["prompt"] for value in local_only)
    assert all(value not in observed["command"] for value in local_only)
    input_digest = receipt["request_input_sha256s"][0]
    positions = [index for index, value in enumerate(observed["command"]) if value == input_digest]
    assert positions == [observed["command"].index("--image-sha256") + 1]


def test_generate_page_does_not_retry_validation_provider_failure(tmp_path: Path):
    project = _project(tmp_path)
    calls = []

    def runner(command, timeout):
        calls.append(command)
        raise ProviderFailure("bad request", status_code=400)

    with pytest.raises(ProviderFailure, match="bad request"):
        generate_page_body(
            project, page_number=1, runner=runner,
            reviewer=lambda *_args, **_kwargs: pytest.fail("QA must not run"),
            retry_sleep=lambda _delay: pytest.fail("400 must not back off"),
        )
    assert len(calls) == 1


def test_second_candidate_missing_output_stops_without_accepting_first(tmp_path: Path):
    project = _project(tmp_path)
    calls = []
    reviews = iter([
        _semantic_failure("Increase contrast between body text and panels."),
    ])

    def runner(command, timeout):
        calls.append(command)
        if len(calls) == 1:
            output = Path(command[command.index("--out") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (1904, 896), "white").save(output)
            _write_mock_trace(command)

    with pytest.raises(RuntimeError, match="no independently accepted"):
        generate_page_body(
            project, page_number=1, runner=runner,
            reviewer=lambda *_args, **_kwargs: next(reviews),
        )

    assert len(calls) == 2
    page = load(project)["pages"][0]
    assert page["selected_candidate"] is None
    assert "later_generation_missing_output" in page["degraded_reasons"]
    assert page["state"] == "technical_failed"


def test_project_429_throttle_limits_subsequent_page_launches_and_preserves_completed(
    tmp_path: Path,
):
    project = _multi_page_project(tmp_path)
    page1_calls = 0

    def write_candidate(command):
        output = Path(command[command.index("--out") + 1])
        trace = Path(command[command.index("--trace-out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    def page1_runner(command, timeout):
        nonlocal page1_calls
        page1_calls += 1
        if page1_calls == 1:
            raise ProviderFailure("rate limited", status_code=429)
        write_candidate(command)

    reviewer = lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []}
    first = generate_page_body(
        project, page_number=1, runner=page1_runner, reviewer=reviewer,
        retry_sleep=lambda _delay: None, retry_jitter=lambda: 0.5,
    )
    scheduler_state = json.loads((project / SCHEDULER_STATE_FILE).read_text(encoding="utf-8"))
    assert scheduler_state["active_limit"] == 1
    assert scheduler_state["leases"] == {}
    page1_state_before = load(project)["pages"][0]
    receipt_before = (project / "04_v6/images/page_001.json").read_bytes()

    page2_entered = threading.Event()
    page2_release = threading.Event()
    page3_entered = threading.Event()

    def page2_runner(command, timeout):
        page2_entered.set()
        assert page2_release.wait(5)
        write_candidate(command)

    def page3_runner(command, timeout):
        page3_entered.set()
        write_candidate(command)

    with ThreadPoolExecutor(max_workers=2) as pool:
        page2 = pool.submit(
            generate_page_body, project, page_number=2, runner=page2_runner, reviewer=reviewer,
        )
        assert page2_entered.wait(5)
        page3 = pool.submit(
            generate_page_body, project, page_number=3, runner=page3_runner, reviewer=reviewer,
        )
        assert not page3_entered.wait(0.25)

        resumed = generate_page_body(
            project, page_number=1,
            runner=lambda *_args, **_kwargs: pytest.fail("completed page must stay untouched"),
            reviewer=lambda *_args, **_kwargs: pytest.fail("completed page must stay untouched"),
        )
        assert resumed == first
        page2_release.set()
        assert page2.result(timeout=10)["state"] == "accepted"
        assert page3.result(timeout=10)["state"] == "accepted"

    assert page3_entered.is_set()
    assert load(project)["pages"][0] == page1_state_before
    assert (project / "04_v6/images/page_001.json").read_bytes() == receipt_before


def test_same_page_waiter_resumes_owner_result_without_duplicate_candidate_or_receipt_write(
    tmp_path: Path,
):
    project = _project(tmp_path)
    owner_entered = threading.Event()
    owner_release = threading.Event()
    runner_calls = 0

    def owner_runner(command, timeout):
        nonlocal runner_calls
        runner_calls += 1
        owner_entered.set()
        assert owner_release.wait(5)
        output = Path(command[command.index("--out") + 1])
        trace = Path(command[command.index("--trace-out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    reviewer = lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []}
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(
            generate_page_body, project, page_number=1,
            runner=owner_runner, reviewer=reviewer,
        )
        assert owner_entered.wait(5)
        waiter = pool.submit(
            generate_page_body, project, page_number=1,
            runner=lambda *_args, **_kwargs: pytest.fail("same-page waiter must not run provider"),
            reviewer=lambda *_args, **_kwargs: pytest.fail("same-page waiter must not run QA"),
        )
        assert not waiter.done()
        owner_release.set()
        owner_result = owner.result(timeout=10)
        receipt_before_waiter_return = (project / "04_v6/images/page_001.json").read_bytes()
        waiter_result = waiter.result(timeout=10)

    assert runner_calls == 1
    assert len(owner_result["candidates"]) == 1
    assert waiter_result == owner_result
    assert (project / "04_v6/images/page_001.json").read_bytes() == receipt_before_waiter_return
    ownership_state = json.loads((project / PAGE_OWNERSHIP_STATE_FILE).read_text(encoding="utf-8"))
    assert ownership_state["owners"] == {}


def test_crash_after_receipt_commit_before_accepted_state_resumes_without_provider_or_qa(
    tmp_path: Path, monkeypatch,
):
    project = _project(tmp_path)

    def runner(command, timeout):
        output = Path(command[command.index("--out") + 1])
        trace = Path(command[command.index("--trace-out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    real_write = workflow_v6_image._atomic_write_json

    def crash_after_write(path, value):
        real_write(path, value)
        raise RuntimeError("injected crash after receipt commit")

    monkeypatch.setattr(workflow_v6_image, "_atomic_write_json", crash_after_write)
    with pytest.raises(RuntimeError, match="injected crash"):
        generate_page_body(
            project, page_number=1, runner=runner,
            reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
        )
    assert load(project)["pages"][0]["state"] == "generating"
    assert (project / "04_v6/images/page_001.json").is_file()

    monkeypatch.setattr(workflow_v6_image, "_atomic_write_json", real_write)
    receipt = generate_page_body(
        project, page_number=1,
        runner=lambda *_args, **_kwargs: pytest.fail("resume must not call provider"),
        reviewer=lambda *_args, **_kwargs: pytest.fail("resume must not call QA"),
    )
    assert receipt["state"] == "accepted"
    assert load(project)["pages"][0]["state"] == "accepted"


@pytest.mark.parametrize("crash_stage", [
    "after_receipt_commit",
    "after_receipt_verification",
    "after_state_commit",
])
def test_every_finalization_boundary_is_resumable_without_duplicate_work(
    tmp_path: Path, monkeypatch, crash_stage: str,
):
    project = _project(tmp_path)
    calls = {"provider": 0, "qa": 0}

    def runner(command, timeout):
        calls["provider"] += 1
        output = Path(command[command.index("--out") + 1])
        trace = Path(command[command.index("--trace-out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    def reviewer(*_args, **_kwargs):
        calls["qa"] += 1
        return {"accepted": True, "score": 6, "issues": []}

    def crash_at(stage):
        if stage == crash_stage:
            raise RuntimeError(f"injected {stage}")

    monkeypatch.setattr(workflow_v6_image, "_finalization_boundary", crash_at)
    with pytest.raises(RuntimeError, match="injected"):
        generate_page_body(project, page_number=1, runner=runner, reviewer=reviewer)
    assert calls == {"provider": 1, "qa": 1}

    monkeypatch.setattr(workflow_v6_image, "_finalization_boundary", lambda _stage: None)
    recovered = generate_page_body(
        project, page_number=1,
        runner=lambda *_args, **_kwargs: pytest.fail("resume must not call provider"),
        reviewer=lambda *_args, **_kwargs: pytest.fail("resume must not call QA"),
    )
    assert recovered["state"] == "accepted"
    assert load(project)["pages"][0]["state"] == "accepted"


@pytest.mark.parametrize("receipt_damage", ["missing", "partial", "forged_identity"])
def test_legacy_accepted_state_recovers_selected_artifact_without_provider_or_qa(
    tmp_path: Path, receipt_damage: str,
):
    project = _project(tmp_path)

    def runner(command, timeout):
        output = Path(command[command.index("--out") + 1])
        trace = Path(command[command.index("--trace-out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    original = generate_page_body(
        project, page_number=1, runner=runner,
        reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
    )
    receipt_path = project / "04_v6/images/page_001.json"
    if receipt_damage == "missing":
        receipt_path.unlink()
    elif receipt_damage == "forged_identity":
        forged = json.loads(json.dumps(original))
        forged["confirmed_ui_digest"] = "0" * 64
        receipt_path.write_text(json.dumps(forged), encoding="utf-8")
    else:
        receipt_path.write_text("{", encoding="utf-8")

    recovered = generate_page_body(
        project, page_number=1,
        runner=lambda *_args, **_kwargs: pytest.fail("recovery must not call provider"),
        reviewer=lambda *_args, **_kwargs: pytest.fail("recovery must not call QA"),
    )
    assert recovered["selected"] == original["selected"]
    assert recovered["state"] == "accepted"
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == recovered


def test_unrecoverable_accepted_artifact_regenerates_without_accepted_to_accepted_transition(
    tmp_path: Path,
):
    project = _project(tmp_path)

    def write_candidate(command, color):
        output = Path(command[command.index("--out") + 1])
        trace = Path(command[command.index("--trace-out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), color).save(output)
        _write_mock_trace(command)

    first = generate_page_body(
        project, page_number=1, runner=lambda command, _timeout: write_candidate(command, "white"),
        reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
    )
    (project / "04_v6/images/page_001.json").unlink()
    (project / first["selected"]["path"]).unlink()
    calls = 0

    def replacement_runner(command, timeout):
        nonlocal calls
        calls += 1
        write_candidate(command, "blue")

    replacement = generate_page_body(
        project, page_number=1, runner=replacement_runner,
        reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
    )
    assert calls == 1
    assert replacement["state"] == "accepted"
    assert load(project)["pages"][0]["state"] == "accepted"


@pytest.mark.parametrize("damage", [
    "corrupt_png",
    "wrong_dimensions",
    "output_path_mismatch",
    "output_digest_mismatch",
])
def test_accepted_recovery_rejects_corrupt_or_mismatched_candidate_and_regenerates_once(
    tmp_path: Path, damage: str,
):
    project = _project(tmp_path)

    def write_candidate(command, color="white"):
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), color).save(output)
        _write_mock_trace(command)

    first = generate_page_body(
        project, page_number=1, runner=lambda command, _timeout: write_candidate(command),
        reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
    )
    image_path = project / first["selected"]["path"]
    trace_path = image_path.with_name(image_path.name.replace(".png", ".trace.json"))
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    if damage == "corrupt_png":
        image_path.write_bytes(b"not a png")
        trace["outputs"][0]["sha256"] = hashlib.sha256(image_path.read_bytes()).hexdigest()
    elif damage == "wrong_dimensions":
        Image.new("RGB", (100, 100), "red").save(image_path)
        trace["outputs"][0]["sha256"] = hashlib.sha256(image_path.read_bytes()).hexdigest()
    elif damage == "output_path_mismatch":
        trace["outputs"][0]["path"] = str((project / "04_v6/images/other.png").resolve())
    else:
        trace["outputs"][0]["sha256"] = "0" * 64
    trace_path.write_text(json.dumps(trace), encoding="utf-8")
    calls = 0

    def replacement_runner(command, timeout):
        nonlocal calls
        calls += 1
        write_candidate(command, "blue")

    recovered = generate_page_body(
        project, page_number=1, runner=replacement_runner,
        reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
    )
    assert calls == 1
    assert recovered["state"] == "accepted"
    assert load(project)["pages"][0]["state"] == "accepted"


def test_accepted_recovery_accepts_valid_decoded_png_and_matching_output_trace(tmp_path: Path):
    project = _project(tmp_path)

    def runner(command, timeout):
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    original = generate_page_body(
        project, page_number=1, runner=runner,
        reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
    )
    (project / "04_v6/images/page_001.json").unlink()
    recovered = generate_page_body(
        project, page_number=1,
        runner=lambda *_args, **_kwargs: pytest.fail("valid recovery must not call provider"),
        reviewer=lambda *_args, **_kwargs: pytest.fail("valid recovery must not call QA"),
    )
    assert recovered["selected"] == original["selected"]


def _generate_accepted_receipt(project: Path) -> dict:
    def runner(command, timeout):
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    return generate_page_body(
        project, page_number=1, runner=runner,
        reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
    )


def test_existing_receipt_rejects_forged_unselected_outside_candidate(tmp_path: Path):
    project = _project(tmp_path)
    original = _generate_accepted_receipt(project)
    receipt_path = project / "04_v6/images/page_001.json"
    forged = json.loads(json.dumps(original))
    forged["candidates"].append({
        "attempt": 2,
        "path": "../../outside.png",
        "operation": forged["request_operation"],
    })
    receipt_path.write_text(json.dumps(forged), encoding="utf-8")

    recovered = generate_page_body(
        project, page_number=1,
        runner=lambda *_args, **_kwargs: pytest.fail("valid selected artifact should recover"),
        reviewer=lambda *_args, **_kwargs: pytest.fail("receipt recovery must not call QA"),
    )

    assert recovered["candidates"] == [original["selected"]]
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == recovered


@pytest.mark.parametrize(
    "candidate_mutation",
    [
        "more_than_two",
        "duplicate_attempt",
        "attempt_zero",
        "attempt_three",
    ],
)
def test_existing_receipt_rejects_invalid_candidate_number_invariants(
    tmp_path: Path, candidate_mutation: str,
):
    project = _project(tmp_path)
    original = _generate_accepted_receipt(project)
    receipt_path = project / "04_v6/images/page_001.json"
    forged = json.loads(json.dumps(original))
    selected = json.loads(json.dumps(forged["selected"]))
    if candidate_mutation == "more_than_two":
        forged["candidates"] = [selected, selected, selected]
    elif candidate_mutation == "duplicate_attempt":
        forged["candidates"] = [selected, selected]
    else:
        forged_candidate = json.loads(json.dumps(selected))
        forged_candidate["attempt"] = 0 if candidate_mutation == "attempt_zero" else 3
        forged["candidates"].append(forged_candidate)
    receipt_path.write_text(json.dumps(forged), encoding="utf-8")

    recovered = generate_page_body(
        project, page_number=1,
        runner=lambda *_args, **_kwargs: pytest.fail("valid selected artifact should recover"),
        reviewer=lambda *_args, **_kwargs: pytest.fail("receipt recovery must not call QA"),
    )

    assert recovered["candidates"] == [original["selected"]]
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == recovered


@pytest.mark.parametrize("mime_damage", ["missing", "wrong"])
def test_accepted_recovery_requires_explicit_png_trace_mime(
    tmp_path: Path, mime_damage: str,
):
    project = _project(tmp_path)
    original = _generate_accepted_receipt(project)
    image_path = project / original["selected"]["path"]
    trace_path = image_path.with_suffix(".trace.json")
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    if mime_damage == "missing":
        trace["outputs"][0].pop("mime_type")
    else:
        trace["outputs"][0]["mime_type"] = "image/jpeg"
    trace_path.write_text(json.dumps(trace), encoding="utf-8")
    calls = 0

    def replacement_runner(command, timeout):
        nonlocal calls
        calls += 1
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "blue").save(output)
        _write_mock_trace(command)

    recovered = generate_page_body(
        project, page_number=1, runner=replacement_runner,
        reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
    )

    assert calls == 1
    assert recovered["state"] == "accepted"


def test_candidate_two_only_recovery_is_a_valid_bounded_receipt(tmp_path: Path):
    project = _project(tmp_path)

    def runner(command, timeout):
        output = Path(command[command.index("--out") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    reviews = iter([
        _semantic_failure("Remove the overlap between body text and chart labels."),
        {"accepted": True, "score": 6, "issues": []},
    ])
    original = generate_page_body(
        project, page_number=1, runner=runner,
        reviewer=lambda *_args, **_kwargs: next(reviews),
    )
    receipt_path = project / "04_v6/images/page_001.json"
    receipt_path.unlink()
    (project / original["candidates"][0]["path"]).unlink()

    recovered = generate_page_body(
        project, page_number=1,
        runner=lambda *_args, **_kwargs: pytest.fail("candidate 2 recovery must not regenerate"),
        reviewer=lambda *_args, **_kwargs: pytest.fail("candidate 2 recovery must not call QA"),
    )

    assert recovered["candidates"] == [original["selected"]]
    assert recovered["selected"] == original["selected"]


def test_candidate_two_only_receipt_does_not_fabricate_first_candidate_provenance() -> None:
    page = new_page(1, title="Page")
    candidate_two = {"attempt": 2, "path": "04_v6/images/page_001.candidate_2.png", "operation": "generate"}
    receipt = {
        "candidates": [candidate_two],
        "selected": candidate_two,
        "state": "accepted",
        "degraded_reasons": [],
    }

    advanced = _advance_page_to_accepted_receipt(page, receipt)

    assert advanced["first_candidate"] is None
    assert advanced["selected_candidate"]["attempt"] == 2


def test_reversed_candidate_attempt_order_is_rejected_and_rebuilt_canonically(tmp_path: Path):
    project = _project(tmp_path)

    def runner(command, timeout):
        output = Path(command[command.index("--out") + 1])
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)

    reviews = iter([
        _semantic_failure("Increase contrast between the body text and panels."),
        {"accepted": True, "score": 6, "issues": []},
    ])
    original = generate_page_body(
        project, page_number=1, runner=runner,
        reviewer=lambda *_args, **_kwargs: next(reviews),
    )
    receipt_path = project / "04_v6/images/page_001.json"
    forged = json.loads(json.dumps(original))
    forged["candidates"].reverse()
    forged["candidates_sha256"] = canonical_sha256(forged["candidates"])
    receipt_path.write_text(json.dumps(forged), encoding="utf-8")

    recovered = generate_page_body(
        project, page_number=1,
        runner=lambda *_args, **_kwargs: pytest.fail("valid page artifacts should rebuild"),
        reviewer=lambda *_args, **_kwargs: pytest.fail("rebuild must skip QA"),
    )

    assert [item["attempt"] for item in recovered["candidates"]] == [1, 2]
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == recovered


@pytest.mark.parametrize("with_reference", [False, True], ids=["generate", "edit"])
@pytest.mark.parametrize("initial", ["medium", "high"])
@pytest.mark.parametrize("damage", ["missing_quality", "wrong_quality", "missing_size", "wrong_size"])
@pytest.mark.skip(reason="Task 7 quality is bound by the sealed immutable ImageRequest")
def test_trace_must_bind_candidate_one_quality_and_requested_size_before_finalization(
    tmp_path: Path, with_reference: bool, initial: str, damage: str, monkeypatch,
):
    project = _project_with_confirmed_reference(tmp_path) if with_reference else _project(tmp_path)
    monkeypatch.setattr(workflow_v6_image, "initial_quality", lambda _page: initial)

    def runner(command, timeout):
        output = Path(command[command.index("--out") + 1])
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)
        trace_path = Path(command[command.index("--trace-out") + 1])
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        field = "quality" if "quality" in damage else "size"
        if damage.startswith("missing"):
            trace.pop(field)
        else:
            trace[field] = "medium" if field == "quality" and initial == "high" else (
                "high" if field == "quality" else "1024x1024"
            )
        trace_path.write_text(json.dumps(trace), encoding="utf-8")

    with pytest.raises(RuntimeError, match="candidate artifact failed validation"):
        generate_page_body(
            project, page_number=1, runner=runner,
            reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
        )


@pytest.mark.parametrize("damage", ["missing_quality", "wrong_quality", "missing_size", "wrong_size"])
def test_candidate_two_trace_requires_medium_to_high_upgrade_semantics(
    tmp_path: Path, damage: str,
):
    project = _project(tmp_path)

    def runner(command, timeout):
        output = Path(command[command.index("--out") + 1])
        Image.new("RGB", (1904, 896), "white").save(output)
        _write_mock_trace(command)
        if output.name.endswith("candidate_2.png"):
            trace_path = Path(command[command.index("--trace-out") + 1])
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            field = "quality" if "quality" in damage else "size"
            if damage.startswith("missing"):
                trace.pop(field)
            else:
                trace[field] = "medium" if field == "quality" else "1024x1024"
            trace_path.write_text(json.dumps(trace), encoding="utf-8")

    semantic_calls = []

    def reviewer(*_args, **_kwargs):
        semantic_calls.append(True)
        return {
            "accepted": False,
            "score": 4,
            "checks": {
                "global_style_followed": {
                    "result": "fail",
                    "detail": "The panel contrast is too low.",
                    "correction": {
                        "check": "global_style_followed",
                        "action": "increase",
                        "target": "contrast_relation",
                        "constraint": "contrast_relation",
                        "correction": "Increase contrast between the body text and panels.",
                    },
                },
            },
            "issues": [],
        }

    with pytest.raises(RuntimeError, match="no independently accepted"):
        generate_page_body(
            project, page_number=1, runner=runner, reviewer=reviewer,
        )

    page = load(project)["pages"][0]
    assert page["state"] == "technical_failed"
    assert page["selected_candidate"] is None
    assert "later_candidate_mechanical_failure" in page["degraded_reasons"]
    assert semantic_calls == [True]
    evidence = page["first_candidate"]["fallback_mechanical_qa"]
    assert evidence["attempt"] == 2
    assert evidence["result"]["accepted"] is False
    assert evidence["result"]["artifact_version"] == "mechanical-qa-v6"
    assert page["first_candidate"]["fallback_mechanical_qa"] == evidence


def test_existing_receipt_requires_selected_to_equal_one_full_candidate(tmp_path: Path):
    project = _project(tmp_path)
    original = _generate_accepted_receipt(project)
    receipt_path = project / "04_v6/images/page_001.json"
    forged = json.loads(json.dumps(original))
    forged["selected"] = {
        field: forged["selected"][field] for field in ("attempt", "path", "operation")
    }
    receipt_path.write_text(json.dumps(forged), encoding="utf-8")

    recovered = generate_page_body(
        project, page_number=1,
        runner=lambda *_args, **_kwargs: pytest.fail("valid page candidate should recover"),
        reviewer=lambda *_args, **_kwargs: pytest.fail("receipt recovery must not call QA"),
    )

    assert recovered["selected"] == original["selected"]
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == recovered


def test_existing_receipt_rejects_valid_candidate_artifact_from_another_page(tmp_path: Path):
    project = _multi_page_project(tmp_path, page_count=2)

    def generate(page_number: int, color: str) -> dict:
        def runner(command, timeout):
            output = Path(command[command.index("--out") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (1904, 896), color).save(output)
            _write_mock_trace(command)

        return generate_page_body(
            project, page_number=page_number, runner=runner,
            reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
        )

    page_one = generate(1, "white")
    page_two = generate(2, "blue")
    page_one_receipt = project / "04_v6/images/page_001.json"
    forged = json.loads(json.dumps(page_one))
    forged["candidates"] = json.loads(json.dumps(page_two["candidates"]))
    forged["selected"] = json.loads(json.dumps(page_two["selected"]))
    page_one_receipt.write_text(json.dumps(forged), encoding="utf-8")

    recovered = generate_page_body(
        project, page_number=1,
        runner=lambda *_args, **_kwargs: pytest.fail("page 1 artifact should recover"),
        reviewer=lambda *_args, **_kwargs: pytest.fail("receipt recovery must not call QA"),
    )

    assert recovered["selected"] == page_one["selected"]
    assert recovered["selected"] != page_two["selected"]


@pytest.mark.parametrize(
    "malformed_attempt",
    [[], {}, "1", True, None, 0, 3],
    ids=["list", "dict", "string", "bool", "null", "zero", "three"],
)
def test_existing_receipt_safely_rejects_malformed_candidate_attempt(
    tmp_path: Path, malformed_attempt,
):
    project = _project(tmp_path)
    original = _generate_accepted_receipt(project)
    receipt_path = project / "04_v6/images/page_001.json"
    forged = json.loads(json.dumps(original))
    forged_candidate = json.loads(json.dumps(original["selected"]))
    forged_candidate["attempt"] = malformed_attempt
    forged["candidates"].append(forged_candidate)
    receipt_path.write_text(json.dumps(forged), encoding="utf-8")

    recovered = generate_page_body(
        project, page_number=1,
        runner=lambda *_args, **_kwargs: pytest.fail("valid page artifact should recover"),
        reviewer=lambda *_args, **_kwargs: pytest.fail("receipt recovery must not call QA"),
    )

    assert recovered["candidates"] == [original["selected"]]
    assert recovered["selected"] == original["selected"]


@pytest.mark.parametrize("malformed_state", [[], {}], ids=["list", "dict"])
def test_existing_receipt_safely_rejects_unhashable_state(
    tmp_path: Path, malformed_state,
):
    project = _project(tmp_path)
    original = _generate_accepted_receipt(project)
    receipt_path = project / "04_v6/images/page_001.json"
    forged = json.loads(json.dumps(original))
    forged["state"] = malformed_state
    receipt_path.write_text(json.dumps(forged), encoding="utf-8")

    recovered = generate_page_body(
        project, page_number=1,
        runner=lambda *_args, **_kwargs: pytest.fail("valid page artifact should recover"),
        reviewer=lambda *_args, **_kwargs: pytest.fail("receipt recovery must not call QA"),
    )

    assert recovered["state"] == "accepted"
    assert recovered["selected"] == original["selected"]


def test_mechanical_failure_never_calls_semantic_reviewer(tmp_path: Path):
    project = _project(tmp_path)
    semantic_calls = []

    def runner(command, timeout):
        output = Path(command[command.index("--out") + 1])
        Image.new("RGB", (1024, 1024), "white").save(output)
        _write_mock_trace(command)

    def reviewer(*args, **kwargs):
        semantic_calls.append((args, kwargs))
        return {"accepted": True, "score": 7, "issues": []}

    with pytest.raises(RuntimeError, match="mechanical"):
        generate_page_body(
            project, page_number=1, runner=runner, reviewer=reviewer,
        )

    assert semantic_calls == []

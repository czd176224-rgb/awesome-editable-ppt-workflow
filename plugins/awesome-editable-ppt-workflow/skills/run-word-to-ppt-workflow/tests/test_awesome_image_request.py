from __future__ import annotations

import hashlib
import json
import sys
import base64
import os
import subprocess
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT.parents[1]
SCRIPTS = ROOT / "scripts"
IMAGE_SCRIPTS = ROOT.parent / "generate-slide-body-image" / "scripts"
for path in (SCRIPTS, IMAGE_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from workflow_v6_contract import canonical_sha256, new_page, new_project  # noqa: E402
from workflow_v6_state import create, load, save  # noqa: E402
from workflow_v6_image import (  # noqa: E402
    build_image_command,
    generate_page_body,
    load_validated_image_request,
    seal_page_image_prompt,
)
import workflow_v6_image  # noqa: E402
import codex_gpt_image  # type: ignore  # noqa: E402
import provider_worker  # type: ignore  # noqa: E402
from validate_page_image_prompt import _block  # noqa: E402


def _visual() -> dict:
    return {
        "primary_color": "#17365D", "secondary_color": "#C7352B",
        "background_color": "#FFFFFF", "cjk_font": "Microsoft YaHei",
        "latin_font": "Arial", "title_size_pt": 28, "body_size_pt": 12,
        "caption_size_pt": 9, "regional_characteristics": "",
        "visual_description": "Formal editorial presentation.",
    }


def _image(path: Path, color: tuple[int, int, int]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 8), color).save(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project(tmp_path: Path, reference_count: int) -> tuple[Path, dict, list[str]]:
    project = tmp_path / "project"
    project.mkdir()
    visual = _visual()
    state = new_project(
        word_source={"path": "00_source/source.docx", "sha256": "1" * 64},
        logo_source={"path": "00_source/logo.svg", "sha256": "2" * 64},
        pages=[new_page(1, title="Page 1")],
    )
    state["style_confirmation"] = {"status": "confirmed", "contract": visual}
    state["confirmed_ui_revision"] = 1
    state["confirmed_ui_digest"] = canonical_sha256(visual)
    state["page_materials_status"] = "confirmed"

    word_images = []
    attachment_inputs = []
    selected: list[str] = []
    for index in range(reference_count):
        if index % 2 == 0:
            asset_id = f"word_{index:02d}"
            relative = f"01_source_assets/word_{index:02d}.png"
            digest = _image(project / relative, (index, 20, 40))
            word_images.append({
                "asset_id": asset_id, "source_order": index + 1,
                "original_filename": f"word_{index:02d}.png", "media_type": "image/png",
                "path": relative, "sha256": digest,
                "byte_size": (project / relative).stat().st_size,
            })
            selected.append(f"word:{asset_id}")
        else:
            asset_id = f"attachment_{index:02d}"
            original = f"01_source_assets/attachment_{index:02d}.pdf"
            original_path = project / original
            original_path.parent.mkdir(parents=True, exist_ok=True)
            original_path.write_bytes(b"stable attachment")
            rendered = f"02_v6/attachment_renders/render_{index:02d}/page_0001.png"
            rendered_digest = _image(project / rendered, (index, 50, 80))
            attachment_inputs.append({
                "asset_id": asset_id, "source_order": index + 1,
                "original_filename": f"attachment_{index:02d}.pdf", "media_type": "application/pdf",
                "path": original, "sha256": hashlib.sha256(original_path.read_bytes()).hexdigest(),
                "byte_size": original_path.stat().st_size,
                "render_receipt": {
                    "schema_version": "awesome-attachment-render-v1", "original_path": original,
                    "original_sha256": hashlib.sha256(original_path.read_bytes()).hexdigest(),
                    "original_byte_size": original_path.stat().st_size, "renderer_identity": "test-renderer",
                    "pages": [{"page_number": 1, "path": rendered, "width": 16, "height": 8,
                               "byte_size": (project / rendered).stat().st_size, "sha256": rendered_digest}],
                    "contact_sheet": {"page_number": 0, "path": rendered, "width": 16, "height": 8,
                                      "byte_size": (project / rendered).stat().st_size, "sha256": rendered_digest},
                },
            })
            selected.append(f"attachment:{asset_id}:page:1")

    materials = {
        "page_number": 1, "fixed_page_title": "Page 1",
        "complete_word_content": [{"type": "paragraph", "text": "Authoritative body",
                                   "source_block_id": "body-1", "source_block_index": 1,
                                   "source_order": 1, "relationship_ids": [], "comment_ids": []}],
        "original_comments": [], "word_images": word_images,
        "attachment_inputs": attachment_inputs, "visual_contract": visual,
        "body_frame": {"geometry_version": "fixed-canvas-cm-v2",
                       "body_bounds_cm": {"x": 0.81, "y": 2.3, "w": 23.78, "h": 11.18},
                       "body_pixels": {"width": 1904, "height": 896},
                       "fixed_layers": ["title", "logo", "footer", "page_number"]},
    }
    material_path = project / "02_v6/awesome_page_materials/page_001.json"
    material_path.parent.mkdir(parents=True)
    material_bytes = (json.dumps(materials, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    material_path.write_bytes(material_bytes)
    state["pages"][0]["material_state"] = "available"
    state["pages"][0]["material_receipt"] = {
        "schema_version": "awesome-page-materials-v1", "page_number": 1,
        "path": "02_v6/awesome_page_materials/page_001.json",
        "digest": hashlib.sha256(material_bytes).hexdigest(),
    }
    create(project, state)
    return project, materials, selected


def _result(materials: dict, selected: list[str]) -> dict:
    word_ids = [item["source_block_id"] for item in materials["complete_word_content"]]
    plan = {
        "composition_archetype": "single-focus", "content_sequence": word_ids,
        "comment_directives": [], "reference_substitutions": [],
        "hierarchy_order": [*word_ids, *selected], "emphasis_ids": [],
        "groups": [{"group_id": "group_1", "member_ids": [*word_ids, *selected]}],
        "reading_direction": "left-to-right", "layout_density": "balanced",
        "whitespace": "balanced", "connector_style": "none",
        "icon_policy": "generic-functional-only",
        "reference_treatments": [
            {"reference_id": ref, "preserve": "identity-and-content",
             "change": "scale-and-place", "crop": "none", "placement": "supporting"}
            for ref in selected
        ],
    }
    prompt = (
        "## Task\nGenerate one 1904 x 896 PowerPoint body-region image.\n\n"
        "## Original Materials\n"
        + _block("WORD_CONTENT_JSON", materials["complete_word_content"]) + "\n"
        + _block("ORIGINAL_COMMENTS_JSON", materials["original_comments"]) + "\n"
        + _block("SELECTED_REFERENCE_IDS_JSON", selected) + "\n\n"
        "## Visual Presentation\n"
        + _block("VISUAL_CONTRACT_JSON", materials["visual_contract"]) + "\n"
        + _block("DESIGN_PLAN_JSON", plan) + "\n\n"
        "## Fixed Boundaries\n"
        "Generate only the 17:8 body region at exactly 1904 x 896 and respect the safe area.\n"
        "Do not generate the fixed page title.\nDo not generate the fixed logo.\n"
        "Do not generate the footer.\nDo not generate the page number."
    )
    return {"schema_version": "page-image-prompt-v1", "page_number": 1,
            "selected_reference_images": selected, "image_prompt": prompt}


@pytest.mark.parametrize("count", [0, 1, 4, 16])
def test_validated_prompt_drives_exact_generate_edit_reference_matrix(tmp_path: Path, count: int):
    project, materials, selected = _project(tmp_path, count)
    source = tmp_path / "compiler-result.json"
    source.write_text(json.dumps(_result(materials, selected), ensure_ascii=False), encoding="utf-8")
    seal_page_image_prompt(project, 1, source)

    request = load_validated_image_request(project, 1)

    assert request.operation == ("generate" if count == 0 else "edit")
    assert request.model == "gpt-image-2"
    assert request.size == "1904x896"
    assert len(request.input_images) == count
    assert request.selected_reference_ids == tuple(selected)
    command = build_image_command(request, prompt_file=tmp_path / "prompt.txt",
                                  output=tmp_path / "out.png", trace=tmp_path / "trace.json")
    assert command[2] == request.operation
    assert [command[i + 1] for i, item in enumerate(command) if item == "--image"] == [str(path) for path in request.input_images]


@pytest.mark.parametrize("count,expected_operation", [(0, "generate"), (1, "edit")])
def test_runtime_e2e_calls_exact_generate_or_edit_and_accepts_receipt(
    tmp_path: Path, count: int, expected_operation: str,
):
    project, materials, selected = _project(tmp_path, count)
    source = tmp_path / "compiler-result.json"
    source.write_text(json.dumps(_result(materials, selected), ensure_ascii=False), encoding="utf-8")
    seal_page_image_prompt(project, 1, source)
    observed: list[list[str]] = []

    def runner(command: list[str], _timeout: int) -> None:
        observed.append(command)
        cap = json.loads(Path(command[command.index("--request-capability") + 1]).read_text(encoding="utf-8"))
        root = Path(command[command.index("--workflow-project") + 1])
        output = root / cap["output_path"]
        trace = root / cap["trace_path"]
        Image.new("RGB", (1904, 896), "white").save(output)
        images = [command[index + 1] for index, value in enumerate(command) if value == "--image"]
        roles = [command[index + 1] for index, value in enumerate(command) if value == "--image-role"]
        digests = [command[index + 1] for index, value in enumerate(command) if value == "--image-sha256"]
        trace.write_text(json.dumps({
            "operation": command[2], "model": "gpt-image-2",
            "quality": command[command.index("--quality") + 1],
            "size": command[command.index("--size") + 1],
            "input_images": [
                {"role": role, "path": str(Path(path)), "sha256": digest}
                for role, path, digest in zip(roles, images, digests)
            ],
            "outputs": [{"path": str(output.resolve()),
                         "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                         "mime_type": "image/png"}],
        }), encoding="utf-8")

    receipt = generate_page_body(
        project, page_number=1, runner=runner,
        reviewer=lambda *_args, **_kwargs: {"accepted": True, "score": 6, "issues": []},
    )

    assert len(observed) == 1
    command = observed[0]
    assert command[2] == expected_operation
    assert [command[i + 1] for i, item in enumerate(command) if item == "--image"] == [
        str(path) for path in load_validated_image_request(project, 1).input_images
    ]
    assert receipt["state"] == "accepted"
    assert receipt["selected"]["operation"] == expected_operation
    assert receipt["selected_reference_ids"] == selected


def test_unselected_page_images_never_enter_request_payload(tmp_path: Path):
    project, materials, owned = _project(tmp_path, 4)
    selected = [owned[2], owned[0]]
    source = tmp_path / "compiler-result.json"
    source.write_text(json.dumps(_result(materials, selected), ensure_ascii=False), encoding="utf-8")
    seal_page_image_prompt(project, 1, source)

    request = load_validated_image_request(project, 1)

    assert request.selected_reference_ids == tuple(selected)
    assert len(request.input_images) == 2
    assert all(reference not in request.prompt for reference in owned if reference not in selected)


@pytest.mark.parametrize("mutation", ["material", "prompt", "selected_path", "ui"])
def test_mutation_or_forgery_is_rejected_before_provider(tmp_path: Path, mutation: str):
    project, materials, selected = _project(tmp_path, 1)
    source = tmp_path / "compiler-result.json"
    source.write_text(json.dumps(_result(materials, selected), ensure_ascii=False), encoding="utf-8")
    artifact = seal_page_image_prompt(project, 1, source)
    if mutation == "material":
        path = project / "02_v6/awesome_page_materials/page_001.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif mutation == "prompt":
        Path(artifact["path"]).write_bytes(Path(artifact["path"]).read_bytes() + b" ")
    elif mutation == "selected_path":
        image = project / materials["word_images"][0]["path"]
        _image(image, (255, 0, 0))
    else:
        state = load(project)
        state["confirmed_ui_revision"] = 2
        save(project, state)

    with pytest.raises((ValueError, RuntimeError)):
        load_validated_image_request(project, 1)


def test_image_generation_has_no_documentation_only_or_legacy_receipt_bypass(tmp_path: Path):
    project, _materials, _selected = _project(tmp_path, 0)
    with pytest.raises(ValueError, match="validated page image prompt"):
        load_validated_image_request(project, 1)
    legacy = project / "02_v6/page_image_prompts/page_001.receipt.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"artifact_version": "image2-adaptive-v6"}), encoding="utf-8")
    with pytest.raises(ValueError, match="validated page image prompt"):
        load_validated_image_request(project, 1)


def test_source_identity_and_capability_mutations_fail_before_network(tmp_path: Path, monkeypatch):
    project, materials, selected = _project(tmp_path, 1)
    source = tmp_path / "compiler-result.json"
    source.write_text(json.dumps(_result(materials, selected), ensure_ascii=False), encoding="utf-8")
    seal_page_image_prompt(project, 1, source)
    request = load_validated_image_request(project, 1)
    capability = workflow_v6_image._issue_capability(request, attempt=1)
    value = json.loads(capability.read_text(encoding="utf-8"))
    value["source_identity"] = "0" * 64
    capability.write_text(json.dumps(value), encoding="utf-8")
    called = []
    monkeypatch.setattr(codex_gpt_image, "_invoke_provider_worker", lambda *_args, **_kwargs: called.append(True))
    parser = codex_gpt_image.build_parser()
    command = build_image_command(request, prompt_file=tmp_path / "prompt.txt",
                                  output=tmp_path / "out.png", trace=tmp_path / "trace.json")
    (tmp_path / "prompt.txt").write_text(request.prompt, encoding="utf-8")
    with pytest.raises(codex_gpt_image.CliError):
        codex_gpt_image.cmd_generate(parser.parse_args(command[2:]))
    assert called == []


def test_quality_uses_page_risk_not_reference_count(tmp_path: Path):
    project, materials, selected = _project(tmp_path, 1)
    source = tmp_path / "compiler-result.json"
    source.write_text(json.dumps(_result(materials, selected), ensure_ascii=False), encoding="utf-8")
    seal_page_image_prompt(project, 1, source)
    assert load_validated_image_request(project, 1).quality == "medium"

    (tmp_path / "dense").mkdir()
    dense_project, dense_materials, _ = _project(tmp_path / "dense", 0)
    dense_path = dense_project / "02_v6/awesome_page_materials/page_001.json"
    dense_materials["complete_word_content"][0]["text"] = "密集数据" * 400
    payload = (json.dumps(dense_materials, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    dense_path.write_bytes(payload)
    state_path = dense_project / "workflow_v6.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["pages"][0]["material_receipt"]["digest"] = hashlib.sha256(payload).hexdigest()
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    dense_source = tmp_path / "dense-result.json"
    dense_source.write_text(json.dumps(_result(dense_materials, []), ensure_ascii=False), encoding="utf-8")
    seal_page_image_prompt(dense_project, 1, dense_source)
    assert load_validated_image_request(dense_project, 1).quality == "high"


@pytest.mark.parametrize("count,operation", [(0, "generate"), (1, "edit")])
def test_actual_codex_cli_to_mock_network_uses_sealed_prompt_and_selected_bytes(
    tmp_path: Path, monkeypatch, count: int, operation: str,
):
    project, materials, selected = _project(tmp_path, count)
    source = tmp_path / "compiler-result.json"
    source.write_text(json.dumps(_result(materials, selected), ensure_ascii=False), encoding="utf-8")
    seal_page_image_prompt(project, 1, source)
    request_value = load_validated_image_request(project, 1)
    request_value = workflow_v6_image.replace(
        request_value, capability_path=workflow_v6_image._issue_capability(request_value, attempt=1),
    )
    prompt_path, output, trace = tmp_path / "prompt.txt", tmp_path / "out.png", tmp_path / "trace.json"
    prompt_path.write_text(request_value.prompt, encoding="utf-8")
    command = build_image_command(request_value, prompt_file=prompt_path, output=output, trace=trace)
    submitted = []
    rendered = BytesIO(); Image.new("RGB", (1904, 896), "white").save(rendered, format="PNG")
    monkeypatch.setattr(codex_gpt_image, "load_or_login_codex_auth",
                        lambda _args: codex_gpt_image.CodexAuth("test"))
    monkeypatch.setattr(codex_gpt_image, "_invoke_provider_worker",
                        lambda _url, _auth, body, _timeout, **_kwargs: submitted.append(body) or {
                            "data": [{"b64_json": base64.b64encode(rendered.getvalue()).decode("ascii")}]
                        })

    assert codex_gpt_image.main(command[2:]) == 0

    assert len(submitted) == 1
    assert submitted[0]["prompt"] == request_value.prompt
    assert ("images" in submitted[0]) == bool(count)
    if count:
        assert base64.b64decode(submitted[0]["images"][0]["image_url"].split(",", 1)[1]) == request_value.input_images[0].read_bytes()
    cap_value = json.loads(request_value.capability_path.read_text(encoding="utf-8"))
    trace = project / cap_value["trace_path"]
    trace_value = json.loads(trace.read_text(encoding="utf-8"))
    assert trace_value["operation"] == operation
    assert trace_value["input_images"] == [
        {"role": role, "path": str(path.resolve()), "sha256": digest}
        for role, path, digest in zip(request_value.image_roles, request_value.input_images, request_value.input_sha256s)
    ]


def test_network_library_boundary_rejects_direct_import_call(monkeypatch):
    assert not hasattr(codex_gpt_image, "post_image_json")
    assert not hasattr(codex_gpt_image, "request")
    with pytest.raises(codex_gpt_image.CliError, match="authority"):
        codex_gpt_image._invoke_provider_worker(
            "https://example.invalid", codex_gpt_image.CodexAuth("token"), {}, 1,
            authority=None,
        )


def test_provider_is_a_one_shot_pipe_worker_without_freeform_cli():
    """The network process must not accept terminal prompt/path arguments."""
    worker = IMAGE_SCRIPTS / "provider_worker.py"
    assert worker.is_file()
    text = worker.read_text(encoding="utf-8")
    assert "AWESOME_PROVIDER_PIPE_FD" in text
    assert "argparse" not in text
    assert "--prompt" not in text
    assert "--image" not in text
    assert "urlopen" in text
    # The public/orchestrator module must not contain a network call site.
    assert "urlopen" not in (IMAGE_SCRIPTS / "codex_gpt_image.py").read_text(encoding="utf-8")


def test_provider_worker_preserves_codex_image_client_headers():
    req = provider_worker._provider_request(
        {
            "url": "https://chatgpt.com/backend-api/codex/images/edits",
            "access_token": "token",
            "account_id": "account",
        },
        b"{}",
    )

    headers = {name.casefold(): value for name, value in req.header_items()}
    assert headers["authorization"] == "Bearer token"
    assert headers["accept"] == "application/json"
    assert headers["content-type"] == "application/json"
    assert headers["originator"] == "generate-slide-body-image"
    assert headers["user-agent"] == "generate-slide-body-image-skill/0.1.0"
    assert headers["chatgpt-account-id"] == "account"


def test_dry_run_does_not_consume_submission_capability(tmp_path: Path):
    project, materials, selected = _project(tmp_path, 0)
    source = tmp_path / "compiler-result.json"
    source.write_text(json.dumps(_result(materials, selected), ensure_ascii=False), encoding="utf-8")
    seal_page_image_prompt(project, 1, source)
    request_value = load_validated_image_request(project, 1)
    capability = workflow_v6_image._issue_capability(request_value, attempt=1)
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text(request_value.prompt, encoding="utf-8")
    command = build_image_command(
        workflow_v6_image.replace(request_value, capability_path=capability),
        prompt_file=prompt_path, output=tmp_path / "out.png", trace=tmp_path / "trace.json",
    )
    command.append("--dry-run")
    assert codex_gpt_image.main(command[2:]) == 0
    journal = project / "04_v6/image_request_capabilities/journal"
    assert not journal.exists() or not list(journal.glob("*.json"))


def test_capability_rejects_duplicate_json_and_excessive_ttl(tmp_path: Path):
    project, materials, selected = _project(tmp_path, 0)
    source = tmp_path / "compiler-result.json"
    source.write_text(json.dumps(_result(materials, selected), ensure_ascii=False), encoding="utf-8")
    seal_page_image_prompt(project, 1, source)
    request_value = load_validated_image_request(project, 1)
    capability = workflow_v6_image._issue_capability(request_value, attempt=1)
    raw = capability.read_text(encoding="utf-8")
    capability.write_text(raw.replace('{"attempt":1', '{"attempt":1,"attempt":1'), encoding="utf-8")
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text(request_value.prompt, encoding="utf-8")
    command = build_image_command(
        workflow_v6_image.replace(request_value, capability_path=capability),
        prompt_file=prompt_path, output=tmp_path / "out.png", trace=tmp_path / "trace.json",
    )
    with pytest.raises((codex_gpt_image.CliError, ValueError)):
        codex_gpt_image.cmd_generate(codex_gpt_image.build_parser().parse_args(command[2:]))


def test_provider_worker_refuses_terminal_and_unsigned_stdin(tmp_path: Path):
    worker = IMAGE_SCRIPTS / "provider_worker.py"
    direct = subprocess.run([sys.executable, str(worker)], input=b"{}", capture_output=True)
    assert direct.returncode != 0
    assert b"inherited" in direct.stderr.lower() or b"pipe" in direct.stderr.lower()


def test_capability_envelope_contains_full_authority_and_inline_image_bytes(tmp_path: Path):
    project, materials, selected = _project(tmp_path, 1)
    source = tmp_path / "compiler-result.json"
    source.write_text(json.dumps(_result(materials, selected), ensure_ascii=False), encoding="utf-8")
    seal_page_image_prompt(project, 1, source)
    request_value = load_validated_image_request(project, 1)
    capability = workflow_v6_image._issue_capability(request_value, attempt=1)
    value = json.loads(capability.read_text(encoding="utf-8"))
    for field in (
        "project_identity", "source_authority", "page_state_authority",
        "material_authority", "prompt_authority", "visual_contract_authority",
        "selected_references", "issued_at", "not_before", "expires_at", "key_id",
    ):
        assert field in value
    assert value["selected_references"][0]["bytes_b64"]
    assert base64.b64decode(value["selected_references"][0]["bytes_b64"]) == request_value.input_images[0].read_bytes()


def test_pre_network_local_validation_failure_reissues_lease(tmp_path: Path, monkeypatch):
    project, materials, selected = _project(tmp_path, 0)
    source = tmp_path / "compiler-result.json"
    source.write_text(json.dumps(_result(materials, selected), ensure_ascii=False), encoding="utf-8")
    seal_page_image_prompt(project, 1, source)
    request_value = load_validated_image_request(project, 1)
    capability = workflow_v6_image._issue_capability(request_value, attempt=1)
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text(request_value.prompt, encoding="utf-8")
    output = tmp_path / "out.png"
    command = build_image_command(
        workflow_v6_image.replace(request_value, capability_path=capability),
        prompt_file=prompt_path, output=output, trace=tmp_path / "trace.json",
    )
    monkeypatch.setattr(
        codex_gpt_image,
        "load_or_login_codex_auth",
        lambda _args: codex_gpt_image.CodexAuth("test"),
    )
    monkeypatch.setattr(codex_gpt_image, "_invoke_provider_worker", lambda *_a, **_k: (_ for _ in ()).throw(codex_gpt_image.CliError("local framing failed")))
    with pytest.raises(codex_gpt_image.CliError, match="local framing"):
        codex_gpt_image.cmd_generate(codex_gpt_image.build_parser().parse_args(command[2:]))
    journal_files = list((project / "04_v6/image_request_capabilities/journal").glob("*.json"))
    assert len(journal_files) == 1
    assert json.loads(journal_files[0].read_text(encoding="utf-8"))["state"] == "issued"


def test_reconstruction_request_is_bound_to_accepted_page_image(tmp_path: Path):
    project, materials, selected = _project(tmp_path, 0)
    source = tmp_path / "compiler-result.json"
    source.write_text(json.dumps(_result(materials, selected), ensure_ascii=False), encoding="utf-8")
    seal_page_image_prompt(project, 1, source)
    request_value = load_validated_image_request(project, 1)
    accepted = project / "04_v6/images/page_001.candidate_1.png"
    _image(accepted, (10, 20, 30))
    accepted_receipt = {
        "page_number": 1,
        "selected": {"attempt": 1, "path": accepted.relative_to(project).as_posix(),
                     "sha256": hashlib.sha256(accepted.read_bytes()).hexdigest()},
        "source_identity": request_value.source_identity,
        "prompt_output_sha256": request_value.prompt_output_sha256,
    }
    receipt_path = project / "04_v6/images/page_001.json"
    receipt_path.write_text(json.dumps(accepted_receipt), encoding="utf-8")
    capability = workflow_v6_image.issue_reconstruction_capability(
        project, page_number=1, accepted_receipt=receipt_path,
        purpose="asset-separation", output_kind="foreground-sheet",
    )
    value = json.loads(capability.read_text(encoding="utf-8"))
    assert value["schema_version"] == "awesome-reconstruction-image-capability-v1"
    assert value["accepted_image_sha256"] == hashlib.sha256(accepted.read_bytes()).hexdigest()
    assert value["input_image_bytes_b64"]


def test_reconstruction_consumer_uses_real_worker_and_sealed_output(tmp_path: Path, monkeypatch):
    project, materials, selected = _project(tmp_path, 0)
    source = tmp_path / "compiler-result.json"
    source.write_text(json.dumps(_result(materials, selected), ensure_ascii=False), encoding="utf-8")
    seal_page_image_prompt(project, 1, source)
    request_value = load_validated_image_request(project, 1)
    accepted = project / "04_v6/images/page_001.candidate_1.png"
    _image(accepted, (30, 40, 50))
    receipt = project / "04_v6/images/page_001.json"
    receipt.write_text(json.dumps({"page_number": 1, "selected": {"attempt": 1,
        "path": accepted.relative_to(project).as_posix(), "sha256": hashlib.sha256(accepted.read_bytes()).hexdigest()},
        "source_identity": request_value.source_identity,
        "prompt_output_sha256": request_value.prompt_output_sha256}), encoding="utf-8")
    capability = workflow_v6_image.issue_reconstruction_capability(
        project, page_number=1, accepted_receipt=receipt,
        purpose="asset-separation", output_kind="foreground-sheet")
    rendered = BytesIO(); Image.new("RGB", (1904, 896), "blue").save(rendered, format="PNG")
    raw = json.dumps({"data": [{"b64_json": base64.b64encode(rendered.getvalue()).decode("ascii")}]}).encode()
    monkeypatch.setattr(codex_gpt_image, "load_or_login_codex_auth", lambda _args: codex_gpt_image.CodexAuth("test"))
    monkeypatch.setenv("AWESOME_PROVIDER_TEST_BUILD", "1")
    monkeypatch.setenv("AWESOME_PROVIDER_TEST_RESPONSE_B64", base64.b64encode(raw).decode("ascii"))
    assert codex_gpt_image.main(["reconstruct-edit", "--request-capability", str(capability),
                                "--workflow-project", str(project)]) == 0
    assert (project / "05_v6/reconstruction_assets/page_001.foreground-sheet.png").is_file()
    assert (project / "05_v6/reconstruction_assets/page_001.foreground-sheet.trace.json").is_file()


def test_editppt_reconstruction_command_routes_to_real_provider_worker(tmp_path: Path, monkeypatch):
    project, materials, selected = _project(tmp_path, 0)
    source = tmp_path / "compiler-result.json"
    source.write_text(json.dumps(_result(materials, selected), ensure_ascii=False), encoding="utf-8")
    seal_page_image_prompt(project, 1, source)
    request_value = load_validated_image_request(project, 1)
    accepted = project / "04_v6/images/page_001.candidate_1.png"; _image(accepted, (1, 2, 3))
    receipt = project / "04_v6/images/page_001.json"
    receipt.write_text(json.dumps({"page_number": 1, "selected": {"attempt": 1,
        "path": accepted.relative_to(project).as_posix(), "sha256": hashlib.sha256(accepted.read_bytes()).hexdigest()},
        "source_identity": request_value.source_identity, "prompt_output_sha256": request_value.prompt_output_sha256}), encoding="utf-8")
    capability = workflow_v6_image.issue_reconstruction_capability(
        project, page_number=1, accepted_receipt=receipt, purpose="asset-separation", output_kind="clean-base")
    rendered = BytesIO(); Image.new("RGB", (1904, 896), "green").save(rendered, format="PNG")
    raw = json.dumps({"data": [{"b64_json": base64.b64encode(rendered.getvalue()).decode()}]}).encode()
    auth = tmp_path / "auth.json"; auth.write_text(json.dumps({"tokens": {"access_token": "test"}}), encoding="utf-8")
    env = {**os.environ, "CODEX_HOME": os.environ.get("CODEX_HOME", str(Path.home() / ".codex")), "CODEX_AUTH_FILE": str(auth),
           "AWESOME_PROVIDER_TEST_BUILD": "1", "AWESOME_PROVIDER_TEST_RESPONSE_B64": base64.b64encode(raw).decode()}
    editppt = PLUGIN_ROOT / "skills/reconstruct-editable-slide/cli/editppt/cli.py"
    completed = subprocess.run([sys.executable, str(editppt), "image", "reconstruct-edit",
        "--request-capability", str(capability), "--workflow-project", str(project)],
        capture_output=True, text=True, env=env, check=False)
    assert completed.returncode == 0, completed.stderr
    assert (project / "05_v6/reconstruction_assets/page_001.clean-base.png").is_file()


def test_reconstruction_skill_docs_default_to_accepted_image_with_only_sealed_exceptions():
    root = PLUGIN_ROOT / "skills/reconstruct-editable-slide"
    required = [root / "SKILL.md", root / "prompts/page-worker.md",
                root / "references/cli-helper.md", root / "references/page-decision-tree.md"]
    for path in required:
        text = path.read_text(encoding="utf-8")
        assert "editppt image edit --image" not in text
    worker = (root / "prompts/page-worker.md").read_text(encoding="utf-8")
    decisions = (root / "references/page-decision-tree.md").read_text(encoding="utf-8")
    for text in (worker, decisions):
        assert "accepted" in text
        assert "visual authority" in text
        assert "page_plan" in text
        assert "core exhibit or reading path" in text
        assert "zero Image2" in text
        assert "explicitly" in text
        assert "editppt image reconstruct-edit" in text
    assert "Word/page_request remains the content authority" not in worker
    assert "Invoke the capability's exact `editppt image reconstruct-edit` command once" not in worker
    assert "Every non-text foreground visual object that requires model separation must" not in decisions


def test_provider_worker_pipe_e2e_uses_inline_authority_bytes(tmp_path: Path, monkeypatch):
    project, materials, selected = _project(tmp_path, 0)
    source = tmp_path / "compiler-result.json"
    source.write_text(json.dumps(_result(materials, selected), ensure_ascii=False), encoding="utf-8")
    seal_page_image_prompt(project, 1, source)
    request_value = load_validated_image_request(project, 1)
    capability = workflow_v6_image._issue_capability(request_value, attempt=1)
    prompt_path, output, trace = tmp_path / "prompt.txt", tmp_path / "out.png", tmp_path / "trace.json"
    prompt_path.write_text(request_value.prompt, encoding="utf-8")
    command = build_image_command(
        workflow_v6_image.replace(request_value, capability_path=capability),
        prompt_file=prompt_path, output=output, trace=trace,
    )
    rendered = BytesIO(); Image.new("RGB", (1904, 896), "white").save(rendered, format="PNG")
    raw = json.dumps({"data": [{"b64_json": base64.b64encode(rendered.getvalue()).decode("ascii")}]}).encode()
    monkeypatch.setattr(codex_gpt_image, "load_or_login_codex_auth", lambda _args: codex_gpt_image.CodexAuth("test"))
    monkeypatch.setenv("AWESOME_PROVIDER_TEST_BUILD", "1")
    monkeypatch.setenv("AWESOME_PROVIDER_TEST_RESPONSE_B64", base64.b64encode(raw).decode("ascii"))
    assert codex_gpt_image.main(command[2:]) == 0
    actual_output = project / json.loads(capability.read_text(encoding="utf-8"))["output_path"]
    assert actual_output.is_file()
    assert Image.open(actual_output).size == (1904, 896)


def test_response_received_journal_recovers_after_real_worker_crash_boundary(tmp_path: Path, monkeypatch):
    project, materials, selected = _project(tmp_path, 0)
    source = tmp_path / "compiler-result.json"
    source.write_text(json.dumps(_result(materials, selected), ensure_ascii=False), encoding="utf-8")
    seal_page_image_prompt(project, 1, source)
    request_value = load_validated_image_request(project, 1)
    capability = workflow_v6_image._issue_capability(request_value, attempt=1)
    capability_value = json.loads(capability.read_text(encoding="utf-8"))
    prompt_path, output, trace = tmp_path / "prompt.txt", tmp_path / "out.png", tmp_path / "trace.json"
    prompt_path.write_text(request_value.prompt, encoding="utf-8")
    command = build_image_command(
        workflow_v6_image.replace(request_value, capability_path=capability),
        prompt_file=prompt_path, output=output, trace=trace,
    )
    rendered = BytesIO(); Image.new("RGB", (1904, 896), "white").save(rendered, format="PNG")
    response = json.dumps({"data": [{"b64_json": base64.b64encode(rendered.getvalue()).decode("ascii")}]}).encode()
    journal = project / "04_v6/image_request_capabilities/journal" / f"{capability_value['nonce']}.json"
    monkeypatch.setattr(codex_gpt_image, "load_or_login_codex_auth", lambda _args: codex_gpt_image.CodexAuth("test"))
    monkeypatch.setenv("AWESOME_PROVIDER_TEST_BUILD", "1")
    monkeypatch.setenv("AWESOME_PROVIDER_TEST_RESPONSE_B64", base64.b64encode(response).decode("ascii"))
    monkeypatch.setattr(codex_gpt_image, "_submission_boundary",
                        lambda stage: (_ for _ in ()).throw(RuntimeError("crash")) if stage == "response_journal_committed" else None)
    with pytest.raises(RuntimeError, match="crash"):
        codex_gpt_image.main(command[2:])
    monkeypatch.setattr(codex_gpt_image, "_submission_boundary", lambda _stage: None)
    monkeypatch.setattr(codex_gpt_image, "_invoke_provider_worker", lambda *_a, **_k: pytest.fail("must not resubmit"))
    assert codex_gpt_image.main(command[2:]) == 0
    assert (project / capability_value["output_path"]).is_file()
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "submitted"


def test_production_parser_has_no_base_url_or_output_path_surface():
    parser = codex_gpt_image.build_parser()
    options = {option for action in parser._subparsers._group_actions[0].choices["generate"]._actions
               for option in action.option_strings}
    assert "--base-url" not in options
    assert "--out" not in options
    assert "--trace-out" not in options


def test_recovery_rejects_unsigned_hand_forged_journal(tmp_path: Path):
    project, materials, selected = _project(tmp_path, 0)
    source = tmp_path / "compiler-result.json"
    source.write_text(json.dumps(_result(materials, selected), ensure_ascii=False), encoding="utf-8")
    seal_page_image_prompt(project, 1, source)
    request_value = load_validated_image_request(project, 1)
    capability = workflow_v6_image._issue_capability(request_value, attempt=1)
    capability_value = json.loads(capability.read_text(encoding="utf-8"))
    prompt_path = tmp_path / "prompt.txt"; prompt_path.write_text(request_value.prompt, encoding="utf-8")
    command = build_image_command(
        workflow_v6_image.replace(request_value, capability_path=capability),
        prompt_file=prompt_path, output=tmp_path / "out.png", trace=tmp_path / "trace.json",
    )
    journal = project / "04_v6/image_request_capabilities/journal" / f"{capability_value['nonce']}.json"
    journal.parent.mkdir()
    journal.write_text(json.dumps({"state": "response_received", "response_bytes_b64": "e30=",
                                   "response_sha256": hashlib.sha256(b"{}").hexdigest()}), encoding="utf-8")
    with pytest.raises(codex_gpt_image.CliError, match="signature|journal"):
        codex_gpt_image.cmd_generate(codex_gpt_image.build_parser().parse_args(command[2:]))


def test_worker_rejects_non_official_endpoint_before_transport():
    text = (IMAGE_SCRIPTS / "provider_worker.py").read_text(encoding="utf-8")
    assert "chatgpt.com" in text
    assert "backend-api/codex/images" in text

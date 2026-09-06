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
    initial_quality,
    seal_page_image_prompt,
)
from workflow_v6_state import create, load, save  # noqa: E402
from workflow_v6_cli import _parser  # noqa: E402
from adaptive_scheduler import PAGE_OWNERSHIP_STATE_FILE, SCHEDULER_STATE_FILE  # noqa: E402
from awesome_page_materials import publish_page_materials  # noqa: E402
from validate_page_image_prompt import _block  # noqa: E402
import workflow_v6_image  # noqa: E402


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


def test_only_candidate_loop_can_generate_page_bodies():
    assert not hasattr(workflow_v6_image, "generate_page_body")
    assert not hasattr(workflow_v6_image, "_generate_page_body_owned")

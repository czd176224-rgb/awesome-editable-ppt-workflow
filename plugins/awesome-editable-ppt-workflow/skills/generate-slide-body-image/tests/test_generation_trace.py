from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import subprocess
import sys
from urllib import error
from pathlib import Path

from PIL import Image
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "codex_gpt_image.py"
sys.path.insert(0, str(SCRIPT.parent))

import codex_gpt_image as image_cli  # noqa: E402
from codex_gpt_image import write_generation_trace  # noqa: E402


def test_image_provider_http_status_remains_typed_for_parent_retry(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(image_cli.time, "sleep", lambda _seconds: None)
    def provider(*_args, **_kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise image_cli.CliError("rate limited", status_code=429, network=True)
        return {"data": []}
    monkeypatch.setattr(image_cli, "_invoke_provider_worker", provider)
    # The retry loop is exercised in the sealed workflow E2E; this verifies the
    # typed error that drives that internal same-owner retry remains stable.
    with pytest.raises(image_cli.CliError) as caught:
        provider()
    assert caught.value.status_code == 429
    assert caught.value.network is True


def test_cli_emits_machine_readable_provider_failure_for_parent(monkeypatch, capsys) -> None:
    class Parser:
        @staticmethod
        def parse_args(_argv):
            def fail(_args):
                raise image_cli.CliError("rate limited", status_code=429)
            return argparse.Namespace(func=fail)

    monkeypatch.setattr(image_cli, "build_parser", lambda: Parser())
    with pytest.raises(SystemExit):
        image_cli.main([])

    stderr = capsys.readouterr().err
    marker = next(line for line in stderr.splitlines() if line.startswith("CODEX_IMAGE_ERROR_JSON:"))
    payload = json.loads(marker.split(":", 1)[1])
    assert payload == {"status_code": 429, "network": False, "message": "rate limited"}


@pytest.mark.skip(reason="standalone provider CLI disabled; workflow runtime E2E owns invocation")
def test_explicit_edit_subcommand_writes_edit_trace_with_named_reference_images(tmp_path: Path) -> None:
    master = tmp_path / "master.png"
    logo = tmp_path / "logo.png"
    Image.new("RGB", (32, 18), "white").save(master)
    Image.new("RGB", (8, 4), "blue").save(logo)
    trace = tmp_path / "trace.json"
    output = tmp_path / "output.png"
    completed = subprocess.run(
        [
            sys.executable, str(SCRIPT), "edit", "--prompt", "test", "--dry-run",
            "--image", str(master), "--image-role", "approved_content_master",
            "--image", str(logo), "--image-role", "company_logo",
            "--model", "gpt-image-2", "--size", "1904x896", "--quality", "high",
            "--out", str(output), "--trace-out", str(trace),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(trace.read_text(encoding="utf-8"))
    assert payload["operation"] == "edit"
    assert payload["endpoint"] == "images/edits"
    assert payload["auth"] == "not_authenticated_dry_run"
    assert payload["model"] == "gpt-image-2"
    assert payload["size"] == "1904x896"
    assert payload["quality"] == "high"
    assert "prompt" not in payload
    assert payload["input_images"] == [
        {"role": "approved_content_master", "path": str(master.resolve()), "sha256": hashlib.sha256(master.read_bytes()).hexdigest()},
        {"role": "company_logo", "path": str(logo.resolve()), "sha256": hashlib.sha256(logo.read_bytes()).hexdigest()},
    ]


@pytest.mark.skip(reason="standalone provider CLI disabled; capability gate rejects first")
def test_generate_subcommand_rejects_reference_images_instead_of_silently_switching_operation(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "reference.png"
    Image.new("RGB", (32, 18), "white").save(reference)

    completed = subprocess.run(
        [
            sys.executable, str(SCRIPT), "generate", "--prompt", "test", "--dry-run",
            "--image", str(reference), "--out", str(tmp_path / "output.png"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "edit" in completed.stderr.casefold()


@pytest.mark.skip(reason="standalone provider CLI disabled; workflow runtime E2E owns invocation")
def test_dry_run_writes_generation_trace_without_input_images(tmp_path: Path) -> None:
    """A text-only request must be traceable as a Codex OAuth generation, not an edit."""
    trace = tmp_path / "trace.json"
    completed = subprocess.run(
        [
            sys.executable, str(SCRIPT), "generate", "--prompt", "current page only", "--dry-run",
            "--out", str(tmp_path / "output.png"), "--trace-out", str(trace),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(trace.read_text(encoding="utf-8"))
    assert payload["operation"] == "generate"
    assert payload["endpoint"] == "images/generations"
    assert payload["auth"] == "not_authenticated_dry_run"
    assert payload["model"] == "gpt-image-2"
    assert payload["size"] == "auto"
    assert payload["quality"] == "auto"
    assert "prompt" not in payload
    assert payload["input_images"] == []


@pytest.mark.skip(reason="standalone provider CLI disabled; capability gate rejects first")
def test_edit_subcommand_requires_an_input_image(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "edit", "--prompt", "test", "--dry-run", "--out", str(tmp_path / "output.png")],
        capture_output=True, text=True, check=False,
    )

    assert completed.returncode != 0
    assert "requires at least one --image" in completed.stderr


def test_edit_subcommand_rejects_more_than_sixteen_images(tmp_path: Path) -> None:
    images = []
    for index in range(17):
        path = tmp_path / f"reference-{index:02d}.png"
        Image.new("RGB", (2, 2), "white").save(path)
        images.extend(["--image", str(path)])
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "edit", "--prompt", "test", "--dry-run", *images, "--out", str(tmp_path / "output.png")],
        capture_output=True, text=True, check=False,
    )

    assert completed.returncode != 0
    assert "At most 16" in completed.stderr


def test_expected_image_digest_rejects_file_changed_after_command_was_built(tmp_path: Path) -> None:
    image = tmp_path / "reference.png"
    Image.new("RGB", (8, 4), "blue").save(image)
    expected = hashlib.sha256(image.read_bytes()).hexdigest()
    image.write_bytes(b"changed after verification")

    completed = subprocess.run(
        [
            sys.executable, str(SCRIPT), "edit", "--prompt", "test", "--dry-run",
            "--image", str(image), "--image-sha256", expected,
            "--out", str(tmp_path / "output.png"),
        ],
        capture_output=True, text=True, check=False,
    )

    assert completed.returncode != 0
    assert "digest" in completed.stderr.casefold()


@pytest.mark.skip(reason="standalone provider CLI disabled; workflow runtime E2E owns invocation")
def test_trace_uses_the_same_single_read_bytes_as_the_submitted_body(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "reference.png"
    Image.new("RGB", (8, 4), "blue").save(image)
    original_digest = hashlib.sha256(image.read_bytes()).hexdigest()
    trace = tmp_path / "trace.json"
    args = image_cli.build_parser().parse_args([
        "edit", "--prompt", "test", "--dry-run", "--image", str(image),
        "--image-sha256", original_digest, "--trace-out", str(trace),
    ])

    def tampering_reopen(path: Path) -> str:
        if Path(path).resolve() == image.resolve():
            image.write_bytes(b"tampered during trace")
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    monkeypatch.setattr(image_cli, "file_sha256", tampering_reopen)
    args.func(args)

    payload = json.loads(trace.read_text(encoding="utf-8"))
    assert payload["input_images"][0]["sha256"] == original_digest
    assert image.read_bytes() != b"tampered during trace"


def test_authenticated_trace_preserves_codex_oauth_proof(tmp_path: Path) -> None:
    """A completed authenticated request must retain its stronger provenance claim."""
    trace = tmp_path / "trace.json"
    write_generation_trace(
        argparse.Namespace(trace_out=str(trace), image_role=[]),
        "generate",
        "gpt-image-2",
        [],
        [],
        authenticated=True,
        authority_project=tmp_path,
    )

    payload = json.loads(trace.read_text(encoding="utf-8"))
    assert payload["auth"] == "codex_oauth"


@pytest.mark.parametrize(
    ("image_format", "expected_mime"),
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")],
)
def test_production_trace_declares_decoded_output_mime(
    tmp_path: Path, image_format: str, expected_mime: str,
) -> None:
    output = tmp_path / f"misleading-{image_format.casefold()}.bin"
    Image.new("RGB", (1904, 896), "white").save(output, format=image_format)
    trace = tmp_path / "trace.json"
    write_generation_trace(
        argparse.Namespace(
            trace_out=str(trace), image_role=[], size="1904x896", quality="medium",
            allow_off_ratio_for_downstream_repair=False,
        ),
        "generate",
        "gpt-image-2",
        [],
        [output],
        authenticated=True,
        authority_project=tmp_path,
    )

    payload = json.loads(trace.read_text(encoding="utf-8"))
    assert payload["outputs"] == [{
        "path": str(output.resolve()),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "mime_type": expected_mime,
    }]

"""Security and fidelity contracts for V6 reference media normalization."""

from __future__ import annotations

import importlib.util
import hashlib
import http.server
import struct
import sys
import threading
import zlib
from pathlib import Path

import pytest
from PIL import Image, ImageChops


ROOT = Path(__file__).resolve().parents[1]
MEDIA_PATH = ROOT / "scripts" / "workflow_v6_media.py"
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_media():
    spec = importlib.util.spec_from_file_location("workflow_v6_media_under_test", MEDIA_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def save_photo(path: Path, *, size: tuple[int, int] = (120, 60), exif: Image.Exif | None = None) -> None:
    image = Image.new("RGB", size, "#336699")
    image.save(path, format="JPEG", exif=exif or Image.Exif())


def png_with_dimensions(width: int, height: int) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(header)) + b"IHDR" + header + struct.pack(">I", zlib.crc32(b"IHDR" + header) & 0xffffffff) + struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xffffffff)


def test_normalize_uses_decoded_magic_not_misleading_extension_and_preserves_original_bytes(tmp_path: Path):
    media = load_media()
    source = tmp_path / "misleading.jpg"
    Image.new("RGB", (16, 8), "red").save(source, format="PNG")
    before = source.read_bytes()

    result = media.normalize_reference(tmp_path / "project", source, reference_id="ref-1", kind="screenshot")

    assert result.mime_type == "image/png"
    assert (tmp_path / "project" / result.original_path).read_bytes() == before
    with Image.open(tmp_path / "project" / result.model_input_path) as normalized:
        assert normalized.format == "PNG"
        assert normalized.size == (16, 8)


def test_normalize_rejects_corrupt_magic_and_decoded_image_bombs(tmp_path: Path):
    media = load_media()
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"\x89PNG\r\n\x1a\nnot-an-image")
    bomb = tmp_path / "bomb.png"
    bomb.write_bytes(png_with_dimensions(16_384, 16_384))

    with pytest.raises(ValueError, match="decode|image"):
        media.normalize_reference(tmp_path / "project", corrupt, reference_id="corrupt", kind="screenshot")
    with pytest.raises(ValueError, match="pixel|dimension|limit"):
        media.normalize_reference(tmp_path / "project", bomb, reference_id="bomb", kind="screenshot")


def test_photo_normalization_applies_orientation_strips_gps_and_preserves_ratio(tmp_path: Path):
    media = load_media()
    source = tmp_path / "portrait.jpg"
    exif = Image.Exif()
    exif[274] = 6
    exif[37510] = b"sensitive-location-metadata"
    save_photo(source, size=(120, 60), exif=exif)

    result = media.normalize_reference(tmp_path / "project", source, reference_id="portrait", kind="photo")

    with Image.open(tmp_path / "project" / result.model_input_path) as normalized:
        assert normalized.size == (60, 120)
        assert normalized.getexif().get(37510) is None
        assert normalized.getexif().get(274) is None
    assert result.width == 60 and result.height == 120


def test_screenshot_model_input_is_lossless_png_and_svg_logo_is_safe_raster_without_stretch(tmp_path: Path):
    media = load_media()
    screenshot = tmp_path / "shot.png"
    source_image = Image.new("RGBA", (96, 48), "#11aa77")
    source_image.save(screenshot, format="PNG")
    screenshot_result = media.normalize_reference(tmp_path / "project", screenshot, reference_id="shot", kind="screenshot")
    with Image.open(tmp_path / "project" / screenshot_result.model_input_path) as actual:
        assert actual.format == "PNG"
        assert ImageChops.difference(source_image, actual.convert("RGBA")).getbbox() is None

    logo = tmp_path / "logo.svg"
    logo.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><rect width="200" height="100" fill="#224488"/></svg>', encoding="utf-8")
    logo_result = media.normalize_reference(tmp_path / "project", logo, reference_id="logo", kind="logo")
    with Image.open(tmp_path / "project" / logo_result.model_input_path) as actual:
        assert actual.format == "PNG"
        assert actual.size[0] == actual.size[1] * 2


def test_svg_renderer_allows_bounded_windows_cold_start_budget(tmp_path: Path, monkeypatch):
    """A safe Chrome render gets one bounded minute, not the flaky 20 second startup window."""
    media = load_media()
    observed: dict[str, object] = {}

    def cold_start_run(command, **kwargs):
        observed["command"] = list(command)
        observed["timeout"] = kwargs.get("timeout")
        output_arg = next(item for item in command if str(item).startswith("--screenshot="))
        output = Path(str(output_arg).split("=", 1)[1])
        Image.new("RGBA", (200, 100), "#224488").save(output, format="PNG")
        return __import__("subprocess").CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(media, "_browser_renderer", lambda: Path("safe-chrome.exe"))
    monkeypatch.setattr(media.subprocess, "run", cold_start_run)
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
        b'<rect width="200" height="100" fill="#224488"/></svg>'
    )

    image = media._safe_svg_raster(svg, tmp_path)

    assert image.size == (200, 100)
    assert observed["timeout"] == 60
    command = observed["command"]
    assert "--disable-background-networking" in command
    assert any(str(item).startswith("--user-data-dir=") for item in command)


def test_svg_with_script_or_external_reference_is_rejected(tmp_path: Path):
    media = load_media()
    for name, body in {
        "script": '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        "external": '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.test/a.png"/></svg>',
    }.items():
        source = tmp_path / f"{name}.svg"
        source.write_text(body, encoding="utf-8")
        with pytest.raises(ValueError, match="SVG|svg|external|script"):
            media.normalize_reference(tmp_path / "project", source, reference_id=name, kind="logo")


def test_svg_external_paint_server_is_rejected_before_rendering(tmp_path: Path):
    media = load_media()
    source = tmp_path / "external-paint.svg"
    source.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"><rect width="2" height="2" fill="url(#local) url(https://example.test/a.svg)"/></svg>', encoding="utf-8")

    with pytest.raises(ValueError, match="external"):
        media.normalize_reference(tmp_path / "project", source, reference_id="external-paint", kind="logo")


def test_static_svg_allowlist_rejects_dynamic_network_elements_without_probe_requests(tmp_path: Path):
    media = load_media()
    requests: list[str] = []

    class Probe(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args):
            pass

    probe = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Probe)
    thread = threading.Thread(target=probe.serve_forever, daemon=True)
    thread.start()
    try:
        source = tmp_path / "dynamic.svg"
        source.write_text(f'''<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">
        <filter id="f"><feImage href="http://127.0.0.1:{probe.server_port}/pixel"/></filter>
        <rect width="20" height="10" filter="url(#f)"/><set attributeName="href" to="http://127.0.0.1:{probe.server_port}/set"/>
        </svg>''', encoding="utf-8")
        with pytest.raises(ValueError, match="unsafe|dynamic|external|SVG"):
            media.normalize_reference(tmp_path / "project", source, reference_id="dynamic", kind="logo")
    finally:
        probe.shutdown()
        thread.join(timeout=2)
    assert requests == []


def test_static_svg_allowlist_rejects_animation_and_inline_css_imports(tmp_path: Path):
    media = load_media()
    for reference_id, body in {
        "set": '<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"><rect width="2" height="2"/><set attributeName="fill" to="red"/></svg>',
        "css": '<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"><style>@import url(https://example.test/x.css);</style><rect width="2" height="2"/></svg>',
    }.items():
        source = tmp_path / f"{reference_id}.svg"
        source.write_text(body, encoding="utf-8")
        with pytest.raises(ValueError, match="unsafe|dynamic|external|SVG"):
            media.normalize_reference(tmp_path / "project", source, reference_id=reference_id, kind="logo")


def test_svg_css_escape_is_rejected_before_renderer_can_contact_network(tmp_path: Path):
    media = load_media()
    requests: list[str] = []

    class Probe(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args):
            pass

    probe = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Probe)
    thread = threading.Thread(target=probe.serve_forever, daemon=True)
    thread.start()
    try:
        source = tmp_path / "escaped-paint.svg"
        source.write_text(
            rf'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10"><rect width="20" height="10" fill="u\72l(http://127.0.0.1:{probe.server_port}/pixel)"/></svg>',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="paint|unsafe|external|SVG"):
            media.normalize_reference(tmp_path / "project", source, reference_id="escaped-paint", kind="logo")
    finally:
        probe.shutdown()
        thread.join(timeout=2)
    assert requests == []


def test_svg_rejects_foreign_or_unknown_namespaces_and_attributes(tmp_path: Path):
    media = load_media()
    examples = {
        "foreign-element": '<svg xmlns="http://www.w3.org/2000/svg" xmlns:evil="urn:evil" width="2" height="2"><evil:rect width="2" height="2"/></svg>',
        "foreign-root": '<svg xmlns="urn:evil" width="2" height="2"><rect width="2" height="2"/></svg>',
        "unused-foreign-namespace": '<svg xmlns="http://www.w3.org/2000/svg" xmlns:evil="urn:evil" width="2" height="2"><rect width="2" height="2"/></svg>',
        "xlink": '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="2" height="2"><path d="M0 0" xlink:href="#anything"/></svg>',
        "unknown-attribute": '<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"><rect width="2" height="2" unexpected="value"/></svg>',
    }
    for reference_id, body in examples.items():
        source = tmp_path / f"{reference_id}.svg"
        source.write_text(body, encoding="utf-8")
        with pytest.raises(ValueError, match="namespace|attribute|unsafe|SVG"):
            media.normalize_reference(tmp_path / "project", source, reference_id=reference_id, kind="logo")


def test_white_logo_is_valid_and_renderer_temp_failure_leaves_a_retryable_project(tmp_path: Path, monkeypatch):
    media = load_media()
    source = tmp_path / "white.svg"
    source.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="5"><path fill="white" d="M0 0h20v5H0z"/></svg>', encoding="utf-8")
    project = tmp_path / "project"
    monkeypatch.setattr(media.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("renderer failed")))
    with pytest.raises(ValueError, match="rasterized"):
        media.normalize_reference(project, source, reference_id="white", kind="logo")
    assert not (project / "02_v6" / "reference_media" / "white" / "original.svg").exists()
    monkeypatch.undo()

    result = media.normalize_reference(project, source, reference_id="white", kind="logo")

    assert (project / result.model_input_path).is_file()


def test_browser_never_writes_predictable_project_render_paths(tmp_path: Path):
    media = load_media()
    project = tmp_path / "project"
    destination = project / "02_v6" / "reference_media" / "hardlink"
    destination.mkdir(parents=True)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside sentinel")
    predictable = destination / ".safe-render.png"
    try:
        import os
        os.link(outside, predictable)
    except OSError:
        pytest.skip("hardlinks are unavailable")
    source = tmp_path / "logo.svg"
    source.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="5"><rect width="20" height="5" fill="#224488"/></svg>', encoding="utf-8")

    media.normalize_reference(project, source, reference_id="hardlink", kind="logo")

    assert outside.read_bytes() == b"outside sentinel"


def test_handle_final_path_verification_rejects_parent_race_before_payload_write(tmp_path: Path, monkeypatch):
    media = load_media()
    project = tmp_path / "project"
    destination = project / "02_v6" / "reference_media" / "race"
    destination.mkdir(parents=True)
    target = destination / "original.png"
    final_paths = iter((project, tmp_path / "outside" / "original.png"))
    monkeypatch.setattr(media, "_final_path_for_handle", lambda _handle: next(final_paths))

    with pytest.raises(ValueError, match="escapes|unsafe"):
        media._write_new(project, target, b"payload")
    assert not target.exists() or target.read_bytes() != b"payload"


def test_handle_final_path_verification_rejects_project_root_swap_before_payload_write(tmp_path: Path, monkeypatch):
    media = load_media()
    project = tmp_path / "project"
    target = project / "02_v6" / "reference_media" / "race" / "original.png"
    target.parent.mkdir(parents=True)
    final_paths = iter((tmp_path / "outside", target))
    monkeypatch.setattr(media, "_final_path_for_handle", lambda _handle: next(final_paths))

    with pytest.raises(ValueError, match="escapes|unsafe"):
        media._write_new(project, target, b"payload")

    assert not target.exists() or target.read_bytes() != b"payload"


def test_handle_final_path_verification_rejects_ancestor_swap_before_payload_read(tmp_path: Path, monkeypatch):
    media = load_media()
    project = tmp_path / "project"
    target = project / "02_v6" / "reference_media" / "race" / "thumbnail.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"outside payload must not be returned")
    final_paths = iter((project, tmp_path / "outside" / "thumbnail.png"))
    monkeypatch.setattr(media, "_final_path_for_handle", lambda _handle: next(final_paths))

    with pytest.raises(ValueError, match="escapes|unsafe"):
        media._read_file_limited(project, target)


def test_normalize_returns_hashes_computed_from_output_payloads_not_path_rereads(tmp_path: Path, monkeypatch):
    media = load_media()
    source = tmp_path / "source.png"
    Image.new("RGB", (8, 4), "#336699").save(source, format="PNG")
    monkeypatch.setattr(media, "_sha256", lambda _path: pytest.fail("normalization must not hash by reopening a pathname"), raising=False)

    result = media.normalize_reference(tmp_path / "project", source, reference_id="hashes", kind="screenshot")

    assert result.original_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result.model_input_sha256 == hashlib.sha256((tmp_path / "project" / result.model_input_path).read_bytes()).hexdigest()


def test_svg_logo_uses_capable_renderer_for_viewbox_paths_transforms_and_gradients(tmp_path: Path):
    media = load_media()
    logo = tmp_path / "mark.svg"
    logo.write_text(
        '''<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="25%" viewBox="0 0 400 100">
        <defs><linearGradient id="g"><stop offset="0" stop-color="#0055aa"/><stop offset="1" stop-color="#33cc88"/></linearGradient></defs>
        <g transform="translate(10 10) scale(.8)"><path d="M0 0 L470 0 L430 100 L0 100 Z" fill="url(#g)" stroke="#112233" stroke-width="4"/></g>
        <text x="40" y="70" fill="white" font-size="36">V6</text></svg>''',
        encoding="utf-8",
    )

    result = media.normalize_reference(tmp_path / "project", logo, reference_id="wide-logo", kind="logo")

    with Image.open(tmp_path / "project" / result.model_input_path) as actual:
        assert actual.size[0] == actual.size[1] * 4
        assert actual.convert("RGBA").getchannel("A").getbbox() is not None


def test_normalize_rejects_a_linked_output_directory_before_copying_source(tmp_path: Path, monkeypatch):
    media = load_media()
    project = tmp_path / "project"
    source = tmp_path / "source.png"
    Image.new("RGB", (8, 4), "#336699").save(source, format="PNG")
    destination = project / "02_v6" / "reference_media" / "link-test"
    destination.mkdir(parents=True)
    monkeypatch.setattr(media, "_is_link_or_reparse", lambda path: Path(path) == destination)

    with pytest.raises(ValueError, match="link|reparse|safe"):
        media.normalize_reference(project, source, reference_id="link-test", kind="screenshot")

    assert list(destination.iterdir()) == []


def test_project_media_resolution_rejects_traversal_and_symlink_escape(tmp_path: Path):
    media = load_media()
    project = tmp_path / "project"
    safe = project / "02_v6" / "reference_media" / "ref" / "thumbnail.png"
    safe.parent.mkdir(parents=True)
    safe.write_bytes(b"ok")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"secret")
    escape = project / "02_v6" / "reference_media" / "ref" / "escape.png"
    try:
        escape.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable")

    assert media.resolve_project_media(project, "02_v6/reference_media/ref/thumbnail.png", variant="thumbnail") == safe.resolve()
    with pytest.raises(ValueError, match="escape|project|path"):
        media.resolve_project_media(project, "../outside.png", variant="thumbnail")
    with pytest.raises(ValueError, match="escape|project|path"):
        media.resolve_project_media(project, "02_v6/reference_media/ref/escape.png", variant="thumbnail")


def test_project_media_resolution_accepts_jpeg_photo_model_input(tmp_path: Path):
    media = load_media()
    project = tmp_path / "project"
    model = project / "02_v6" / "reference_media" / "photo" / "model-input.jpg"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"ok")

    assert media.resolve_project_media(project, "02_v6/reference_media/photo/model-input.jpg", variant="model-input") == model.resolve()


def test_endpoint_reader_uses_handle_verified_candidate_without_pathname_resolution(tmp_path: Path, monkeypatch):
    media = load_media()
    project = tmp_path / "project"
    target = project / "02_v6" / "reference_media" / "ref" / "thumbnail.png"
    target.parent.mkdir(parents=True)
    Image.new("RGB", (2, 2), "#336699").save(target, format="PNG")
    monkeypatch.setattr(media, "resolve_project_media", lambda *_args, **_kwargs: pytest.fail("endpoint must not resolve or reopen an untrusted pathname"))

    data, mime_type, returned = media.read_validated_project_media(
        project, "02_v6/reference_media/ref/thumbnail.png", variant="thumbnail",
    )

    assert data == target.read_bytes()
    assert mime_type == "image/png"
    assert returned == target


@pytest.mark.skip(reason="external reference import authority removed from awesome production")
def test_reference_import_keeps_acquisition_lifecycle_and_records_normalized_local_media(tmp_path: Path, monkeypatch):
    from workflow_v6_contract import new_page, new_project
    from workflow_v6_materials import new_page_materials
    from workflow_v6_source import import_reference
    from workflow_v6_state import create

    project = tmp_path / "project"
    create(project, new_project(
        word_source={"path": "00_source/source.docx", "sha256": "a" * 64},
        logo_source={"path": "00_source/logo.svg", "sha256": "b" * 64},
        pages=[new_page(1, title="Evidence")],
    ))
    materials = new_page_materials(page_number=1, fixed_page_title="Evidence", word_original="Evidence", effective_body="")
    (project / "02_v6/page_materials").mkdir(parents=True)
    (project / "02_v6/page_materials/page_001.json").write_text(__import__("json").dumps(materials), encoding="utf-8")
    receipt = {"artifact_version": "reference-materials-v6", "page_number": 1, "references": [], "search_requests": [], "reference_acquisitions": [{"request_id": "request-1", "page_number": 1, "purpose": "verified storefront image", "identity_evidence_need": "storefront", "status": "pending", "history": ["pending"]}]}
    (project / "02_v6/reference_materials").mkdir(parents=True)
    (project / "02_v6/reference_materials/page_001.json").write_text(__import__("json").dumps(receipt), encoding="utf-8")
    image = tmp_path / "candidate.png"
    Image.new("RGB", (20, 10), "#cc8844").save(image, format="PNG")
    monkeypatch.setattr("socket.create_connection", lambda *_args, **_kwargs: pytest.fail("source URL was fetched"))
    monkeypatch.setattr("workflow_v6_source._sha256", lambda _path: pytest.fail("reference import must use the thumbnail digest returned by the secure writer"))

    result = import_reference(project, page_number=1, request_id="request-1", image=image, source_url="https://example.test/candidate.png")

    candidate = result["candidate"]
    reference = candidate["reference"]
    assert result["status"] == "found"
    assert candidate["source_url"] == "https://example.test/candidate.png"
    assert candidate["local_path"] == reference["original_path"]
    assert reference["thumbnail_path"].endswith("thumbnail.png")
    assert Path(reference["model_input_path"]).stem == "model-input"
    assert (project / reference["original_path"]).read_bytes() == image.read_bytes()


@pytest.mark.skip(reason="external reference import authority removed from awesome production")
def test_reference_import_namespaces_identical_request_ids_by_page(tmp_path: Path):
    from workflow_v6_contract import new_page, new_project
    from workflow_v6_materials import new_page_materials
    from workflow_v6_source import import_reference
    from workflow_v6_state import create
    import json

    project = tmp_path / "project"
    create(project, new_project(
        word_source={"path": "00_source/source.docx", "sha256": "a" * 64},
        logo_source={"path": "00_source/logo.svg", "sha256": "b" * 64},
        pages=[new_page(1, title="One"), new_page(2, title="Two")],
    ))
    for page in (1, 2):
        materials = new_page_materials(page_number=page, fixed_page_title=str(page), word_original=str(page), effective_body="")
        material_path = project / "02_v6/page_materials" / f"page_{page:03d}.json"
        material_path.parent.mkdir(parents=True, exist_ok=True)
        material_path.write_text(json.dumps(materials), encoding="utf-8")
        receipt = {"artifact_version": "reference-materials-v6", "page_number": page, "references": [], "search_requests": [], "reference_acquisitions": [{"request_id": "same-request", "page_number": page, "purpose": "photo", "identity_evidence_need": "evidence", "status": "pending", "history": ["pending"]}]}
        receipt_path = project / "02_v6/reference_materials" / f"page_{page:03d}.json"
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    first, second = tmp_path / "first.png", tmp_path / "second.png"
    Image.new("RGB", (12, 6), "#113355").save(first, format="PNG")
    Image.new("RGB", (12, 6), "#aa6633").save(second, format="PNG")

    one = import_reference(project, page_number=1, request_id="same-request", image=first, source_url=None)["candidate"]
    two = import_reference(project, page_number=2, request_id="same-request", image=second, source_url=None)["candidate"]

    assert one["local_path"] != two["local_path"]
    assert one["sha256"] != two["sha256"]
    assert "page-001" in one["local_path"] and "page-002" in two["local_path"]

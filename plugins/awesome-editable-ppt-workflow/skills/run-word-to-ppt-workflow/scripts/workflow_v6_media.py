"""Bounded, safe local reference-media handling for V6 projects."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Final
from xml.etree import ElementTree

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_ENCODED_BYTES: Final = 25 * 1024 * 1024
MAX_DECODED_PIXELS: Final = 80_000_000
MAX_EDGE: Final = 16_384
MODEL_MAX_EDGE: Final = 2_048
THUMBNAIL_MAX_EDGE: Final = 512
SVG_RENDER_TIMEOUT_SECONDS: Final = 60
MEDIA_ROOT: Final = PurePosixPath("02_v6/reference_media")
_RASTER_FORMATS: Final = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "BMP": "image/bmp"}
_SAFE_VARIANTS: Final = {
    "original": "original",
    "thumbnail": "thumbnail.png",
    "model-input": "model-input.",
}
_REFERENCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")
_SVG_NAMESPACE: Final = "http://www.w3.org/2000/svg"
_SAFE_SVG_TAGS = frozenset({
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "text", "tspan", "defs", "lineargradient", "radialgradient", "stop", "clippath", "mask",
    "title", "desc",
})
_SVG_GLOBAL_ATTRIBUTES: Final = frozenset({
    "id", "transform", "opacity", "fill", "fill-opacity", "fill-rule", "stroke", "stroke-opacity",
    "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "clip-path", "clip-rule",
    "mask", "display", "visibility", "color", "font-family", "font-size", "font-weight", "text-anchor",
    "letter-spacing",
})
_SVG_ATTRIBUTES: Final = {
    "svg": frozenset({"width", "height", "viewBox", "preserveAspectRatio"}),
    "g": frozenset(), "path": frozenset({"d"}),
    "rect": frozenset({"x", "y", "width", "height", "rx", "ry"}),
    "circle": frozenset({"cx", "cy", "r"}), "ellipse": frozenset({"cx", "cy", "rx", "ry"}),
    "line": frozenset({"x1", "y1", "x2", "y2"}), "polyline": frozenset({"points"}),
    "polygon": frozenset({"points"}), "text": frozenset({"x", "y", "dx", "dy"}),
    "tspan": frozenset({"x", "y", "dx", "dy"}), "defs": frozenset(),
    "lineargradient": frozenset({"x1", "y1", "x2", "y2", "gradientUnits", "gradientTransform", "spreadMethod"}),
    "radialgradient": frozenset({"cx", "cy", "r", "fx", "fy", "gradientUnits", "gradientTransform", "spreadMethod"}),
    "stop": frozenset({"offset", "stop-color", "stop-opacity"}),
    "clippath": frozenset({"clipPathUnits"}),
    "mask": frozenset({"x", "y", "width", "height", "maskUnits", "maskContentUnits"}),
    "title": frozenset(), "desc": frozenset(),
}
_PAINT_ATTRIBUTES: Final = frozenset({"fill", "stroke", "color", "stop-color"})
_LOCAL_REFERENCE_ATTRIBUTES: Final = frozenset({"clip-path", "mask"})
_SAFE_PAINT_LITERAL = re.compile(r"(?:none|transparent|currentcolor|inherit|#[0-9a-fA-F]{3,8}|[A-Za-z]+)\Z", re.IGNORECASE)
_LOCAL_FRAGMENT = re.compile(r"url\(#([A-Za-z_][A-Za-z0-9_.-]*)\)\Z")
_BROWSER_PATHS = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
)

# Enforce the V6 boundary consistently for every Pillow decode in this process.
Image.MAX_IMAGE_PIXELS = MAX_DECODED_PIXELS


@dataclass(frozen=True)
class NormalizedReference:
    original_path: str
    thumbnail_path: str
    model_input_path: str
    original_sha256: str
    thumbnail_sha256: str
    model_input_sha256: str
    mime_type: str
    width: int
    height: int


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def _safe_directory(project: Path, reference_id: str) -> Path:
    root = Path(project).resolve()
    for directory in (root, root / "02_v6", root / "02_v6" / "reference_media", root / "02_v6" / "reference_media" / reference_id):
        if directory.exists():
            if not directory.is_dir() or _is_link_or_reparse(directory):
                raise ValueError("reference media output parent is a link, reparse point, or non-directory")
            continue
        directory.mkdir()
        if _is_link_or_reparse(directory):
            raise ValueError("reference media output parent is a link or reparse point")
    return directory


def _final_path_for_handle(handle: int) -> Path:
    """Resolve the operating-system final path for an already-open file handle."""
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        final_name = kernel32.GetFinalPathNameByHandleW
        final_name.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
        final_name.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = final_name(msvcrt.get_osfhandle(handle), buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise OSError(ctypes.get_last_error(), "GetFinalPathNameByHandleW failed")
        value = buffer.value
        return Path(value[4:] if value.startswith("\\\\?\\") else value)
    return Path(os.readlink(f"/proc/self/fd/{handle}"))


def _open_project_root_handle(project: Path) -> int:
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
        create_file.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create_file.restype = wintypes.HANDLE
        handle = create_file(str(project), 0, 0x7, None, 3, 0x02000000, None)
        if handle == wintypes.HANDLE(-1).value:
            raise OSError(ctypes.get_last_error(), "CreateFileW failed for project root")
        try:
            return msvcrt.open_osfhandle(handle, os.O_RDONLY)
        except OSError:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
            raise
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    return os.open(project, flags)


def ensure_secure_directory(project: Path, relative: Path) -> Path:
    """Create a project-local directory chain while every ancestor is held open."""
    root = Path(project).resolve(strict=True)
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("secure directory must be project-relative")
    handles = [_open_project_root_handle(root)]
    current = root
    try:
        for part in relative.parts:
            if part in {"", "."}:
                continue
            current = current / part
            if current.exists():
                if not current.is_dir() or _is_link_or_reparse(current):
                    raise ValueError("secure directory contains a link, reparse point, or non-directory")
            else:
                current.mkdir()
            handle = _open_project_root_handle(current)
            _verify_handle_within(handles[0], handle)
            handles.append(handle)
        return current
    finally:
        for handle in reversed(handles):
            os.close(handle)


def _normalized_final_path(handle: int) -> str:
    return os.path.normcase(os.path.normpath(str(_final_path_for_handle(handle))))


def _verify_handle_within(root_handle: int, file_handle: int) -> None:
    """Compare canonical final names of two still-open handles without path reopening."""
    root = _normalized_final_path(root_handle)
    final_path = _normalized_final_path(file_handle)
    prefix = root if root.endswith(os.sep) else root + os.sep
    if final_path != root and not final_path.startswith(prefix):
        raise ValueError("media handle escapes the project or is unsafe")


def _write_new(project: Path, path: Path, data: bytes) -> str:
    if _is_link_or_reparse(path) or path.exists():
        raise ValueError("reference media output already exists or is unsafe")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    root_descriptor = _open_project_root_handle(project)
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            _verify_handle_within(root_descriptor, handle.fileno())
            handle.write(data)
            return hashlib.sha256(data).hexdigest()
    finally:
        os.close(root_descriptor)


def _image_bytes(image: Image.Image, *, image_format: str, **options: object) -> bytes:
    output = BytesIO()
    image.save(output, format=image_format, **options)
    return output.getvalue()


def _checked_size(width: int, height: int) -> None:
    if width < 1 or height < 1:
        raise ValueError("image dimensions are invalid")
    if width > MAX_EDGE or height > MAX_EDGE or width * height > MAX_DECODED_PIXELS:
        raise ValueError("image dimensions exceed the decoded-image limit")


def _open_raster(data: bytes) -> tuple[Image.Image, str]:
    if not data or len(data) > MAX_ENCODED_BYTES:
        raise ValueError("image encoded size exceeds the limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as inspected:
                image_format = inspected.format or ""
                mime_type = _RASTER_FORMATS.get(image_format)
                if mime_type is None:
                    raise ValueError("unsupported image magic or MIME type")
                _checked_size(*inspected.size)
                inspected.verify()
            with Image.open(BytesIO(data)) as decoded:
                _checked_size(*decoded.size)
                decoded.load()
                return decoded.copy(), mime_type
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("image dimensions exceed the decoded-image limit") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(("unsupported", "image dimensions", "image encoded")):
            raise
        raise ValueError("image magic or decoded content is invalid") from exc


def _contained(project: Path, candidate: Path) -> Path:
    root = Path(project).resolve()
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("media path escapes the project") from exc
    return resolved


def _relative(project: Path, path: Path) -> str:
    return path.relative_to(Path(project).resolve()).as_posix()


def _resized(image: Image.Image, maximum: int) -> Image.Image:
    copy = image.copy()
    copy.thumbnail((maximum, maximum), Image.Resampling.LANCZOS)
    return copy


def _rgb(image: Image.Image) -> Image.Image:
    if image.mode == "RGBA":
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        return background
    return image.convert("RGB")


def _svg_dimension(value: str | None) -> float | None:
    if value is None:
        return None
    match = _NUMBER.match(value.strip())
    return float(match.group(0)) if match else None


def _svg_color(value: str | None) -> str | None:
    if not value or value.lower() in {"none", "transparent"}:
        return None
    if re.fullmatch(r"#[0-9a-fA-F]{3,8}", value):
        if len(value) == 4:
            return "#" + "".join(char * 2 for char in value[1:])
        return value[:7]
    if re.fullmatch(r"[A-Za-z]+", value):
        return value
    return None


def _svg_canvas_size(root: ElementTree.Element) -> tuple[int, int]:
    view_box = root.get("viewBox", "").replace(",", " ").split()
    view_width = _svg_dimension(view_box[2]) if len(view_box) == 4 else None
    view_height = _svg_dimension(view_box[3]) if len(view_box) == 4 else None
    width = _svg_dimension(root.get("width")) if not str(root.get("width", "")).endswith("%") else None
    height = _svg_dimension(root.get("height")) if not str(root.get("height", "")).endswith("%") else None
    width = width or view_width
    height = height or view_height
    if width is None or height is None or width <= 0 or height <= 0:
        raise ValueError("SVG requires numeric dimensions or a valid viewBox")
    _checked_size(round(width), round(height))
    return max(1, round(width)), max(1, round(height))


def _safe_svg_root(data: bytes) -> tuple[ElementTree.Element, int, int]:
    if len(data) > MAX_ENCODED_BYTES:
        raise ValueError("image encoded size exceeds the limit")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("SVG must be UTF-8") from exc
    if re.search(r"<!DOCTYPE|<!ENTITY|<\s*(?:script|style)\b|\bon[a-z]+\s*=", text, re.IGNORECASE):
        raise ValueError("SVG script or external resource is not allowed")
    for namespace in re.finditer(r"\sxmlns(?::[A-Za-z_][A-Za-z0-9_.-]*)?\s*=\s*(['\"])(.*?)\1", text, re.IGNORECASE | re.DOTALL):
        if namespace.group(2) != _SVG_NAMESPACE:
            raise ValueError("SVG contains an unsafe namespace")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ValueError("SVG is malformed") from exc
    if root.tag != f"{{{_SVG_NAMESPACE}}}svg":
        raise ValueError("SVG root is required")
    for element in root.iter():
        if not isinstance(element.tag, str) or not element.tag.startswith(f"{{{_SVG_NAMESPACE}}}"):
            raise ValueError("SVG contains an unsafe namespace")
        tag = element.tag.removeprefix(f"{{{_SVG_NAMESPACE}}}").lower()
        if tag not in _SAFE_SVG_TAGS:
            raise ValueError("SVG contains an unsafe, dynamic, or unsupported element")
        for attribute, value in element.attrib.items():
            if "}" in attribute or attribute not in _SVG_GLOBAL_ATTRIBUTES | _SVG_ATTRIBUTES[tag]:
                raise ValueError("SVG contains an unsafe or unknown attribute")
            if "\\" in value:
                raise ValueError("SVG contains an unsafe CSS escape")
            normalized = value.strip()
            if attribute in _PAINT_ATTRIBUTES:
                if not (_SAFE_PAINT_LITERAL.fullmatch(normalized) or _LOCAL_FRAGMENT.fullmatch(normalized)):
                    if "url(" in normalized.lower():
                        raise ValueError("SVG external resource is not allowed")
                    raise ValueError("SVG contains an unsafe paint value")
            elif attribute in _LOCAL_REFERENCE_ATTRIBUTES and not _LOCAL_FRAGMENT.fullmatch(normalized):
                raise ValueError("SVG contains an unsafe local reference")
            elif "url(" in normalized.lower() or "href" in attribute.lower() or "src" in attribute.lower():
                raise ValueError("SVG external resource is not allowed")
    width, height = _svg_canvas_size(root)
    return root, width, height


def _browser_renderer() -> Path:
    executable = next((path for path in _BROWSER_PATHS if path.is_file()), None)
    if executable is None:
        raise ValueError("a safe SVG renderer is unavailable")
    return executable


def _safe_svg_raster(data: bytes, destination: Path) -> Image.Image:
    root, width, height = _safe_svg_root(data)
    ElementTree.register_namespace("", "http://www.w3.org/2000/svg")
    scale = min(1.0, MODEL_MAX_EDGE / max(width, height))
    output_width, output_height = max(1, round(width * scale)), max(1, round(height * scale))
    root.set("width", str(output_width))
    root.set("height", str(output_height))
    temporary_root = Path(tempfile.mkdtemp(prefix="workflow-v6-svg-"))
    try:
        html = temporary_root / "input.html"
        output = temporary_root / "render.png"
        profile = temporary_root / "profile"
        profile.mkdir()
        markup = (
            "<!doctype html><meta charset=utf-8><style>html,body{margin:0;padding:0;overflow:hidden;}svg{display:block;}</style>"
            + ElementTree.tostring(root, encoding="unicode")
        ).encode("utf-8")
        html.write_bytes(markup)
        completed = subprocess.run(
            [str(_browser_renderer()), "--headless=new", "--disable-gpu", "--disable-extensions", "--disable-background-networking", "--disable-component-update", "--disable-sync", "--hide-scrollbars", "--allow-file-access-from-files", "--run-all-compositor-stages-before-draw", "--force-device-scale-factor=1", f"--user-data-dir={profile}", f"--window-size={output_width},{output_height}", f"--screenshot={output}", html.resolve().as_uri()],
            check=False, capture_output=True, timeout=SVG_RENDER_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0 or not output.is_file() or _is_link_or_reparse(output):
            raise ValueError("SVG could not be safely rasterized")
        image, _mime_type = _open_raster(_read_file_limited(temporary_root, output))
        if image.size != (output_width, output_height):
            raise ValueError("SVG renderer produced invalid dimensions")
        return image
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError("SVG could not be safely rasterized") from exc
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def normalize_reference(project: Path, source: Path, *, reference_id: str, kind: str) -> NormalizedReference:
    """Preserve source bytes while producing bounded, safe derivatives."""
    project = Path(project).resolve()
    source = Path(source)
    if not _REFERENCE_ID.fullmatch(reference_id):
        raise ValueError("reference_id is invalid")
    if kind not in {"photo", "screenshot", "logo"}:
        raise ValueError("reference kind is invalid")
    if not source.is_file() or source.stat().st_size > MAX_ENCODED_BYTES:
        raise ValueError("reference image is unavailable or exceeds the encoded limit")
    data = source.read_bytes()
    is_svg = data.lstrip().startswith(b"<svg") or data.lstrip().startswith(b"<?xml") and b"<svg" in data[:1024]
    destination = _safe_directory(project, reference_id)
    if is_svg:
        if kind != "logo":
            raise ValueError("SVG is only accepted for a logo reference")
        image, mime_type = _safe_svg_raster(data, destination), "image/svg+xml"
        original_suffix = ".svg"
    else:
        image, mime_type = _open_raster(data)
        original_suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/bmp": ".bmp"}[mime_type]
    original = destination / f"original{original_suffix}"
    original_sha256 = _write_new(project, original, data)
    if kind == "photo":
        image = ImageOps.exif_transpose(image)
        model = _resized(_rgb(image), MODEL_MAX_EDGE)
        model_path = destination / "model-input.jpg"
        model_sha256 = _write_new(project, model_path, _image_bytes(model, image_format="JPEG", quality=90, optimize=True))
    else:
        model = _resized(image, MODEL_MAX_EDGE)
        model_path = destination / "model-input.png"
        model_sha256 = _write_new(project, model_path, _image_bytes(model, image_format="PNG", optimize=False, compress_level=9))
    thumbnail = _resized(model, THUMBNAIL_MAX_EDGE)
    thumbnail_path = destination / "thumbnail.png"
    thumbnail_sha256 = _write_new(project, thumbnail_path, _image_bytes(thumbnail, image_format="PNG", optimize=False, compress_level=9))
    return NormalizedReference(
        original_path=_relative(project, original),
        thumbnail_path=_relative(project, thumbnail_path),
        model_input_path=_relative(project, model_path),
        original_sha256=original_sha256,
        thumbnail_sha256=thumbnail_sha256,
        model_input_sha256=model_sha256,
        mime_type=mime_type,
        width=model.width,
        height=model.height,
    )


def _media_candidate(project: Path, relative_path: str, *, variant: str) -> Path:
    """Construct a syntactically constrained candidate; handle verification owns containment."""
    if variant not in _SAFE_VARIANTS or not isinstance(relative_path, str):
        raise ValueError("media variant or path is invalid")
    raw = PurePosixPath(relative_path.replace("\\", "/"))
    if raw.is_absolute() or any(part in {"", ".", ".."} for part in raw.parts) or not raw.is_relative_to(MEDIA_ROOT):
        raise ValueError("media path escapes the project")
    expected = _SAFE_VARIANTS[variant]
    if variant == "original" and not raw.name.startswith(expected + "."):
        raise ValueError("media variant does not match path")
    if variant == "model-input" and not raw.name.startswith(expected):
        raise ValueError("media variant does not match path")
    if variant == "thumbnail" and raw.name != expected:
        raise ValueError("media variant does not match path")
    return Path(project).joinpath(*raw.parts)


def resolve_project_media(project: Path, relative_path: str, *, variant: str) -> Path:
    """Resolve only a declared V6 media derivative under the project root."""
    return _contained(Path(project), _media_candidate(project, relative_path, variant=variant))


def validated_media_mime(path: Path) -> str:
    """Return a safe HTTP MIME only after the stored raster decodes cleanly."""
    _image, mime_type = _open_raster(_read_file_limited(Path(path).parent, Path(path)))
    return mime_type


def validated_raster_bytes(data: bytes) -> tuple[Image.Image, str]:
    """Validate renderer output with the same byte, magic, edge, and pixel guards as references."""
    return _open_raster(data)


def normalized_raster_pixel_seal(data: bytes) -> dict[str, object]:
    """Return a stable digest of decoded RGBA8 pixels, independent of PNG encoding."""
    image, _mime_type = _open_raster(data)
    normalized = image.convert("RGBA")
    prefix = f"RGBA8\0{normalized.width}x{normalized.height}\0".encode("ascii")
    return {
        "pixels": {"width": normalized.width, "height": normalized.height},
        "normalized_pixel_format": "RGBA8",
        "normalized_pixel_sha256": hashlib.sha256(prefix + normalized.tobytes()).hexdigest(),
    }


def _read_file_limited(project: Path, path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    root_descriptor = _open_project_root_handle(project)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            actual = os.fstat(handle.fileno())
            if not stat.S_ISREG(actual.st_mode):
                raise ValueError("media file is unsafe")
            _verify_handle_within(root_descriptor, handle.fileno())
            data = handle.read(MAX_ENCODED_BYTES + 1)
    finally:
        os.close(root_descriptor)
    if len(data) > MAX_ENCODED_BYTES:
        raise ValueError("image encoded size exceeds the limit")
    return data


def read_validated_project_media(project: Path, relative_path: str, *, variant: str) -> tuple[bytes, str, Path]:
    """Read, validate, and return one immutable-in-memory media response buffer."""
    path = _media_candidate(project, relative_path, variant=variant)
    data = _read_file_limited(Path(project), path)
    _image, mime_type = _open_raster(data)
    return data, mime_type, path

from __future__ import annotations

import argparse
import base64
import json
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "codex_gpt_image.py"
sys.path.insert(0, str(SCRIPT.parent))

from codex_gpt_image import CliError, write_generation_trace, write_images  # noqa: E402


def _encoded_png(size: tuple[int, int], *, exterior: str | None = None) -> tuple[str, bytes]:
    buffer = BytesIO()
    image = Image.new("RGB", size, "#1f8f55")
    if exterior == "vertical":
        band = max(1, size[1] // 20)
        image.paste("#c51f3a", (0, 0, size[0], band))
        image.paste("#244fd8", (0, size[1] - band, size[0], size[1]))
    elif exterior == "horizontal":
        band = max(1, size[0] // 20)
        image.paste("#c51f3a", (0, 0, band, size[1]))
        image.paste("#244fd8", (size[0] - band, 0, size[0], size[1]))
    image.save(buffer, format="PNG")
    payload = buffer.getvalue()
    return base64.b64encode(payload).decode("ascii"), payload


def test_default_policy_still_rejects_an_off_ratio_provider_image(tmp_path: Path) -> None:
    encoded, _payload = _encoded_png((1536, 1024))

    with pytest.raises(CliError, match="refusing to distort"):
        write_images([(encoded, None)], str(tmp_path / "page.png"), "png", "1904x896", authority_project=tmp_path)


@pytest.mark.parametrize(
    ("source_size", "exterior", "expected_box", "cropped"),
    [
        ((1536, 1024), "vertical", (0.0, 150.588235, 1536.0, 873.411765), True),
        ((1693, 929), "vertical", (0.0, 66.147059, 1693.0, 862.852941), True),
        ((2400, 896), "horizontal", (248.0, 0.0, 2152.0, 896.0), True),
        ((1904, 896), None, (0.0, 0.0, 1904.0, 896.0), False),
    ],
)
def test_experiment_policy_center_crops_largest_17_8_region_without_padding_or_stretch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    source_size: tuple[int, int],
    exterior: str | None,
    expected_box: tuple[float, float, float, float],
    cropped: bool,
) -> None:
    encoded, original = _encoded_png(source_size, exterior=exterior)
    output = tmp_path / "page.png"

    written = write_images(
        [(encoded, None)],
        str(output),
        "png",
        "1904x896",
        allow_off_ratio_for_downstream_repair=True,
        provider_response_quality="medium",
        authority_project=tmp_path,
    )
    trace = tmp_path / "trace.json"
    write_generation_trace(
        argparse.Namespace(
            trace_out=str(trace), image_role=[], size="1904x896",
            allow_off_ratio_for_downstream_repair=True,
        ),
        "generate",
        "gpt-image-2",
        [],
        written,
        authenticated=True,
        authority_project=tmp_path,
    )

    assert output.read_bytes() != original
    with Image.open(output).convert("RGBA") as image:
        assert image.size == (1904, 896)
        assert image.getpixel((0, 0))[3] == 255
        assert image.getpixel((1903, 0))[3] == 255
        assert image.getpixel((0, 895))[3] == 255
        assert image.getpixel((1903, 895))[3] == 255
        assert image.getpixel((0, 0))[:3] == (31, 143, 85)
        assert image.getpixel((1903, 895))[:3] == (31, 143, 85)
    assert "center" in capsys.readouterr().err.casefold()
    assert json.loads(trace.read_text(encoding="utf-8"))["warnings"] == [{
        "code": "centered_17_8_frame_adaptation",
        "output": str(output.resolve()),
        "requested_size": {"width": 1904, "height": 896},
        "provider_original_size": {"width": source_size[0], "height": source_size[1]},
        "provider_original_quality": "medium",
        "crop_box": {
            "left": expected_box[0], "top": expected_box[1],
            "right": expected_box[2], "bottom": expected_box[3],
        },
        "crop_ratio": {"width": 17, "height": 8, "decimal": 2.125},
        "cropped": cropped,
        "final_size": {"width": 1904, "height": 896},
        "scaling": {"mode": "uniform", "resampling": "lanczos", "stretched": False},
    }]


def test_downstream_repair_policy_still_resizes_same_ratio_output(tmp_path: Path) -> None:
    encoded, original = _encoded_png((952, 448))
    output = tmp_path / "page.png"

    write_images(
        [(encoded, None)],
        str(output),
        "png",
        "1904x896",
        allow_off_ratio_for_downstream_repair=True,
        provider_response_quality="medium",
        authority_project=tmp_path,
    )

    assert output.read_bytes() != original
    with Image.open(output) as image:
        assert image.size == (1904, 896)

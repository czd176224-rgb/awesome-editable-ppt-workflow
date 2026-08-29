from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.chart.data import ChartData
from pptx.enum.chart import XL_CHART_TYPE


RUNTIME = Path(__file__).resolve().parents[1] / "editppt" / "runtime"
sys.path.insert(0, str(RUNTIME))

from build_pptx_from_manifest import px_to_inches, write_deck, write_pptx  # noqa: E402
from fixed_region_runtime import CONTENT_BOX, SLIDE  # noqa: E402
import validate_pptx  # noqa: E402


CHART_TYPES = {
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "bar": XL_CHART_TYPE.BAR_CLUSTERED,
    "line": XL_CHART_TYPE.LINE,
    "scatter": XL_CHART_TYPE.XY_SCATTER,
    "bubble": XL_CHART_TYPE.BUBBLE,
}


def _manifest(chart: dict) -> dict:
    return {
        "workflow_contract_version": "fixed-canvas-cm-v2",
        "reconstruction_contract_version": "editable-image-v3",
        "slide": dict(SLIDE),
        "content_box": dict(CONTENT_BOX),
        "source": {"width_px": 1904, "height_px": 896},
        "text_inventory": [],
        "visual_inventory": [],
        "background_strategy": "native white body background",
        "quality_checks": {
            "font_size_calibrated": True,
            "visual_inventory_matched": True,
            "background_strategy_checked": True,
            "shape_corner_geometry_checked": True,
        },
        "text_boxes": [],
        "tables": [],
        "shapes": [],
        "images": [],
        "charts": [chart],
        "asset_provenance": [],
    }


def _one_dimensional(variant: str = "column") -> dict:
    return {
        "object_id": "chart-1",
        "name": "Revenue chart",
        "box_px": [190, 90, 1142, 620],
        "rendering_primitive": "column_bar" if variant in {"column", "bar"} else "line_point",
        "chart_variant": variant,
        "title": "Revenue",
        "unit": "USD m",
        "period": "FY2025",
        "basis": "same portfolio companies",
        "series": [{"name": "Revenue", "categories": ["A", "B"], "values": [12, 18]}],
    }


def _xy(variant: str) -> dict:
    chart = {
        "object_id": "chart-1",
        "name": "Portfolio chart",
        "box_px": [190, 90, 1142, 620],
        "rendering_primitive": "xy",
        "chart_variant": variant,
        "title": "Portfolio",
        "period": "FY2025",
        "x_label": "Growth",
        "x_unit": "%",
        "x_basis": "FY2025 revenue growth",
        "y_label": "Margin",
        "y_unit": "% pts",
        "y_basis": "FY2025 EBITDA margin",
        "series": [{"name": "Companies", "x_values": [1.2, 2.4], "y_values": [8, 11]}],
    }
    if variant == "bubble":
        chart.update({"size_label": "Revenue", "size_unit": "USD m", "size_basis": "FY2025 revenue"})
        chart["series"][0]["size_values"] = [30, 50]
    return chart


@pytest.mark.parametrize(
    ("chart", "expected_type"),
    [
        (_one_dimensional("column"), XL_CHART_TYPE.COLUMN_CLUSTERED),
        (_one_dimensional("bar"), XL_CHART_TYPE.BAR_CLUSTERED),
        (_one_dimensional("line"), XL_CHART_TYPE.LINE),
        (_xy("scatter"), XL_CHART_TYPE.XY_SCATTER),
        (_xy("bubble"), XL_CHART_TYPE.BUBBLE),
    ],
)
def test_native_variants_preserve_exact_type_data_labels_and_fixed_canvas_box(
    tmp_path: Path, chart: dict, expected_type: XL_CHART_TYPE
) -> None:
    manifest = _manifest(chart)
    out = tmp_path / f"{chart['chart_variant']}.pptx"
    write_pptx(manifest, out, tmp_path / "manifest.json")

    presentation = Presentation(out)
    chart_shape = next(shape for shape in presentation.slides[0].shapes if shape.has_chart)
    expected_box = px_to_inches(manifest, *chart["box_px"])
    metadata = {shape.name: shape.text for shape in presentation.slides[0].shapes if shape.has_text_frame}

    assert chart_shape.chart.chart_type == expected_type
    assert chart_shape.name == chart["name"]
    assert chart_shape.left == pytest.approx(expected_box["left"] * 914400, abs=1)
    assert chart_shape.top == pytest.approx(expected_box["top"] * 914400, abs=1)
    assert chart_shape.width == pytest.approx(expected_box["width"] * 914400, abs=1)
    assert chart_shape.height == pytest.approx(expected_box["height"] * 914400, abs=1)
    assert chart_shape.chart.chart_title.text_frame.text == chart["title"]
    if chart["rendering_primitive"] == "xy":
        assert chart_shape.chart.category_axis.axis_title.text_frame.text == chart["x_label"]
        assert chart_shape.chart.value_axis.axis_title.text_frame.text == chart["y_label"]
    assert metadata[f"{chart['name']} Unit"] == chart.get("unit", "x: % | y: % pts | size: USD m" if chart["chart_variant"] == "bubble" else "x: % | y: % pts")
    assert metadata[f"{chart['name']} Period"] == "FY2025"
    assert validate_pptx.quantitative_chart_readback_violations(out, [manifest]) == []


def test_dot_uses_editable_points_connectors_and_exact_source_labels(tmp_path: Path) -> None:
    chart = _one_dimensional("dot")
    manifest = _manifest(chart)
    out = tmp_path / "dot.pptx"
    write_pptx(manifest, out, tmp_path / "manifest.json")

    slide = Presentation(out).slides[0]
    names = [shape.name for shape in slide.shapes]
    texts = [shape.text for shape in slide.shapes if shape.has_text_frame]

    assert not any(shape.has_chart for shape in slide.shapes)
    assert chart["name"] in names
    assert sum(name.startswith("Revenue chart Point ") for name in names) == 2
    assert sum(name.startswith("Revenue chart Connector ") for name in names) == 2
    assert next(shape.text for shape in slide.shapes if shape.name == "Revenue chart Series 1") == "Revenue"
    assert {"A", "B", "12", "18", "USD m", "FY2025"}.issubset(texts)
    assert validate_pptx.quantitative_chart_readback_violations(out, [manifest]) == []


def test_explicit_target_actual_add_target_line_and_direct_difference_arrow(tmp_path: Path) -> None:
    chart = {**_one_dimensional("column"), "target_value": 20, "actual_value": 18}
    manifest = _manifest(chart)
    out = tmp_path / "target.pptx"
    write_pptx(manifest, out, tmp_path / "manifest.json")

    slide = Presentation(out).slides[0]
    named = {shape.name: shape for shape in slide.shapes}

    assert "Revenue chart Target Line" in named
    assert "Revenue chart Difference Arrow" in named
    assert named["Revenue chart Target"].text == "Target: 20"
    assert named["Revenue chart Actual"].text == "Actual: 18"
    assert named["Revenue chart Difference"].text == "Difference: -2"
    assert validate_pptx.quantitative_chart_readback_violations(out, [manifest]) == []


def test_one_dimensional_chart_accepts_explicit_shared_series_unit_and_basis(tmp_path: Path) -> None:
    chart = _one_dimensional()
    chart["series"][0].update({"unit": chart.pop("unit"), "basis": chart.pop("basis")})
    manifest = _manifest(chart)
    out = tmp_path / "series-metadata.pptx"

    write_pptx(manifest, out, tmp_path / "manifest.json")

    slide = Presentation(out).slides[0]
    assert next(shape.text for shape in slide.shapes if shape.name == "Revenue chart Unit") == "USD m"
    assert validate_pptx.quantitative_chart_readback_violations(out, [manifest]) == []


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda manifest: manifest["charts"][0].pop("chart_variant"), "chart_variant"),
        (lambda manifest: manifest["charts"][0].update({"box_px": [-1, 90, 1142, 620]}), "box_px"),
        (lambda manifest: manifest["charts"][0].update({"anchor": "2cm,2cm,18cm,10cm"}), "anchor"),
        (lambda manifest: manifest["charts"][0].update({"target_value": 20}), "target_value"),
        (lambda manifest: manifest["text_boxes"].append({"object_id": "chart-1", "name": "duplicate", "text": "x", "box_px": [1, 1, 10, 10]}), "unique"),
    ],
)
def test_manifest_rejects_ambiguous_or_unreadable_chart_contract(
    tmp_path: Path, mutate, expected: str
) -> None:
    manifest = _manifest(_one_dimensional())
    mutate(manifest)

    with pytest.raises(ValueError, match=expected):
        write_pptx(manifest, tmp_path / "invalid.pptx", tmp_path / "manifest.json")


def test_readback_detects_changed_native_value(tmp_path: Path) -> None:
    chart = _one_dimensional()
    manifest = _manifest(chart)
    out = tmp_path / "tampered.pptx"
    write_pptx(manifest, out, tmp_path / "manifest.json")
    presentation = Presentation(out)
    native_chart = next(shape.chart for shape in presentation.slides[0].shapes if shape.has_chart)
    changed = ChartData()
    changed.categories = ["A", "B"]
    changed.add_series("Revenue", [12, 99])
    native_chart.replace_data(changed)
    presentation.save(out)

    violations = validate_pptx.quantitative_chart_readback_violations(out, [manifest])

    assert any(item["field"] == "charts[0].series[0].values" for item in violations)


def test_readback_detects_changed_chart_object_id(tmp_path: Path) -> None:
    manifest = _manifest(_one_dimensional())
    out = tmp_path / "tampered-id.pptx"
    write_pptx(manifest, out, tmp_path / "manifest.json")
    presentation = Presentation(out)
    chart_shape = next(shape for shape in presentation.slides[0].shapes if shape.has_chart)
    chart_shape._element.xpath(".//p:cNvPr")[0].set("descr", "object_id:wrong;chart_variant:column")
    presentation.save(out)

    violations = validate_pptx.quantitative_chart_readback_violations(out, [manifest])

    assert any(item["field"] == "charts[0].object_id" for item in violations)


def test_final_deck_rebuild_preserves_quantitative_chart_readback(tmp_path: Path) -> None:
    manifests = [
        _manifest(_xy("bubble")),
        _manifest({**_one_dimensional("column"), "target_value": 20, "actual_value": 18}),
    ]
    out = tmp_path / "final.pptx"
    write_deck(
        {"workflow_contract_version": "fixed-canvas-cm-v2", "slide": dict(SLIDE)},
        [
            {"manifest": deepcopy(manifest), "manifest_path": tmp_path / f"manifest-{index}.json"}
            for index, manifest in enumerate(manifests, start=1)
        ],
        out,
        [],
    )

    assert validate_pptx.quantitative_chart_readback_violations(out, manifests) == []

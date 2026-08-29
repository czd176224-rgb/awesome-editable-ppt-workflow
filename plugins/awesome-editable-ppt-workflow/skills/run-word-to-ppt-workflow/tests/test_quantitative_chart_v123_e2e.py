from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation


TESTS = Path(__file__).resolve().parent
PLUGIN = TESTS.parents[2]
REPO = TESTS.parents[4]
RUNTIME = PLUGIN / "skills/reconstruct-editable-slide/cli/editppt/runtime"
SCRIPTS = PLUGIN / "skills/run-word-to-ppt-workflow/scripts"
sys.path[:0] = [str(RUNTIME), str(SCRIPTS)]

from build_pptx_from_manifest import render_preview, write_deck  # noqa: E402
from fixed_region_runtime import CONTENT_BOX, SLIDE  # noqa: E402
from workflow_v6_materials import select_numeric_authority  # noqa: E402
from workflow_v6_reconstruction import build_reconstruction_request  # noqa: E402
from test_workflow_v6_reconstruction import _project  # noqa: E402


OUTPUT = REPO / "tmp/v1.2.3-acceptance/synthetic"


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


def _qualitative_manifest(relationship: str, fallback: str) -> dict:
    manifest = _manifest({})
    manifest["charts"] = []
    layouts = {
        "driver_bridge": [(0.8, 2.8, 1.8, 0.9), (3.0, 2.1, 1.8, 0.9), (5.2, 2.6, 1.8, 0.9), (7.4, 1.7, 1.8, 0.9)],
        "timeline_roadmap": [(0.8 + index * 2.2, 2.4, 1.8, 0.9) for index in range(4)],
        "qualitative_quadrant_or_comparison_table": [(1.2 + column * 4.2, 1.5 + row * 1.8, 3.2, 1.2) for row in range(2) for column in range(2)],
        "uniform_nodes": [(0.8 + index * 2.2, 2.4, 1.8, 0.9) for index in range(4)],
        "equal_width_hierarchy": [(4.1, 1.2, 1.8, 0.9), *[(1.3 + index * 2.8, 3.0, 1.8, 0.9) for index in range(3)]],
        "roadmap_milestones": [(0.8 + index * 2.2, 2.4, 1.8, 0.9) for index in range(4)],
        "comparison_table": [(1.0, 1.6, 3.7, 2.4), (5.3, 1.6, 3.7, 2.4)],
        "goal_current_gap": [(0.8 + index * 3.0, 2.1, 2.4, 1.4) for index in range(3)],
    }
    boxes = layouts[fallback]
    manifest["shapes"] = [
        {
            "object_id": f"{fallback}-node-{index}",
            "name": f"{fallback} node {index}",
            "type": "roundRect",
            "left": left,
            "top": top,
            "width": width,
            "height": height,
            "fill": "#EAF2F8",
            "stroke": "#6B7A90",
        }
        for index, (left, top, width, height) in enumerate(boxes)
    ]
    manifest["text_boxes"] = [
        {
            "object_id": f"{fallback}-title",
            "name": f"{fallback} title",
            "left": 0.7,
            "top": 0.5,
            "width": 12.0,
            "height": 0.7,
            "text": f"{relationship}: {fallback} (qualitative, non-scaled)",
            "font_size": 22,
        },
        *[
            {
                "object_id": f"{fallback}-label-{index}",
                "name": f"{fallback} label {index}",
                "left": left + 0.15,
                "top": top + 0.25,
                "width": width - 0.3,
                "height": min(0.7, height - 0.3),
                "text": f"Source-backed item {index + 1}",
                "font_size": 14,
                "align": "center",
            }
            for index, (left, top, width, height) in enumerate(boxes)
        ],
    ]
    return manifest


def _one_dimensional(name: str, variant: str, *, target: bool = False) -> dict:
    chart = {
        "object_id": name,
        "name": name,
        "box_px": [190, 90, 1142, 620],
        "rendering_primitive": "column_bar" if variant in {"column", "bar"} else "line_point",
        "chart_variant": variant,
        "title": name,
        "unit": "USD m",
        "period": "FY2025",
        "basis": "same portfolio companies",
        "series": [{"name": "Revenue", "categories": ["A", "B"], "values": [12, 18]}],
    }
    if target:
        chart.update({"target_value": 20, "actual_value": 18})
    return chart


def _xy(name: str, variant: str) -> dict:
    chart = {
        "object_id": name,
        "name": name,
        "box_px": [190, 90, 1142, 620],
        "rendering_primitive": "xy",
        "chart_variant": variant,
        "title": name,
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


def _waterfall() -> dict:
    return {
        "object_id": "waterfall", "name": "waterfall", "box_px": [190, 90, 1142, 620],
        "rendering_primitive": "cumulative_bridge", "title": "waterfall", "unit": "USD m",
        "basis": "FY2025 EBITDA", "period": "FY2025",
        "series": [{"name": "Bridge", "categories": ["Pricing", "Volume"], "start": 100, "changes": [20, -5], "end": 115}],
    }


def _gantt() -> dict:
    return {
        "object_id": "gantt", "name": "gantt", "box_px": [190, 90, 1142, 620],
        "rendering_primitive": "time_interval", "title": "gantt", "period": "September 2026",
        "series": [{"name": "Plan", "categories": ["Diligence", "IC"],
                    "start_dates": ["2026-09-01", "2026-09-11"], "end_dates": ["2026-09-10", "2026-09-15"]}],
    }


def _mekko() -> dict:
    return {
        "object_id": "variable-rectangle", "name": "variable-rectangle", "box_px": [190, 90, 1142, 620],
        "rendering_primitive": "variable_rectangle", "title": "variable rectangle", "period": "FY2025",
        "series": [{"name": "Markets", "categories": ["A", "B"], "width_values": [40, 60],
                    "width_label": "Market size", "width_unit": "USD m", "width_basis": "2025 market",
                    "share_values": [[25, 75], [40, 60]], "share_label": "Share", "share_unit": "%",
                    "share_basis": "2025 composition", "share_denominator": 100}],
    }


QUANTITATIVE_CASES = [
    _one_dimensional("column-bar", "column"),
    _one_dimensional("line", "line"),
    _xy("scatter", "scatter"),
    _xy("bubble", "bubble"),
    _one_dimensional("dot", "dot"),
    _waterfall(),
    _gantt(),
    _mekko(),
    _one_dimensional("target-line", "dot", target=True),
    _one_dimensional("difference-arrow", "dot", target=True),
]


MATRIX = [
    ("drivers", _waterfall(), "driver_bridge"),
    ("time_change", _one_dimensional("time-change", "line"), "timeline_roadmap"),
    ("two_variables", _xy("two-variables", "scatter"), "qualitative_quadrant_or_comparison_table"),
    ("third_variable", _xy("third-variable", "bubble"), "uniform_nodes"),
    ("market_size_share", _mekko(), "equal_width_hierarchy"),
    ("project_stage_time", _gantt(), "roadmap_milestones"),
    ("option_comparison", _one_dimensional("options", "dot"), "comparison_table"),
    ("target_actual_variance", _one_dimensional("target", "dot", target=True), "goal_current_gap"),
]


def test_ten_explicit_quantitative_cases_build_editable_pptx_and_previews() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    entries = []
    for index, chart in enumerate(QUANTITATIVE_CASES, start=1):
        manifest = _manifest(chart)
        manifest_path = OUTPUT / f"quantitative-{index:02d}.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        preview = OUTPUT / f"quantitative-{index:02d}.png"
        render_preview(manifest, manifest_path, preview)
        assert Image.open(preview).size == (1200, 675)
        entries.append({"manifest": manifest, "manifest_path": str(manifest_path)})

    deck_path = OUTPUT / "quantitative-ten-cases.pptx"
    write_deck({"workflow_contract_version": "fixed-canvas-cm-v2", "slide": dict(SLIDE)}, entries, deck_path, [])
    deck = Presentation(deck_path)

    assert len(deck.slides) == 10
    assert all(any(shape.has_chart for shape in deck.slides[index].shapes) for index in range(4))
    assert not any(shape.has_chart for shape in deck.slides[4].shapes)  # dot uses editable shapes
    assert not any(shape.has_chart for index in range(5, 8) for shape in deck.slides[index].shapes)
    for index in (4, 5, 6, 7, 8, 9):
        assert any(shape.shape_type != 13 for shape in deck.slides[index].shapes)
    target_names = {shape.name for shape in deck.slides[8].shapes}
    difference_names = {shape.name for shape in deck.slides[9].shapes}
    assert "target-line Target Line" in target_names
    assert "difference-arrow Difference Arrow" in difference_names


def test_eight_relationships_use_real_selector_reconstruction_and_renderer_contracts(
    tmp_path: Path,
) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    entries = []
    for index, (relationship, quantitative, fallback) in enumerate(MATRIX, start=1):
        quantitative = deepcopy(quantitative)
        quantitative["relationship"] = relationship
        authority = select_numeric_authority([quantitative])
        assert authority is not None
        assert authority["rendering_primitive"] == quantitative["rendering_primitive"]

        qualitative = {
            "title": relationship,
            "relationship": relationship,
            "source_wording": "Source describes the relationship but supplies no complete numeric dimensions.",
            "disabled_primitive": quantitative["rendering_primitive"],
            "fallback": fallback,
            "series": [],
        }
        assert select_numeric_authority([qualitative]) is None
        project = _project(tmp_path / relationship, 1)
        material_path = project / "02_v6/page_materials/page_001.json"
        material_path.parent.mkdir(parents=True, exist_ok=True)
        material_path.write_text(
            json.dumps({"chart_facts": [qualitative]}, ensure_ascii=False), encoding="utf-8",
        )
        request = build_reconstruction_request(project, page_number=1)
        assert "numeric_authority" not in request

        manifest = _qualitative_manifest(relationship, fallback)
        manifest_path = OUTPUT / f"qualitative-{index:02d}.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        preview = OUTPUT / f"qualitative-{index:02d}.png"
        render_preview(manifest, manifest_path, preview)
        assert Image.open(preview).size == (1200, 675)
        entries.append({"manifest": manifest, "manifest_path": str(manifest_path)})

    path = OUTPUT / "qualitative-eight-modes.pptx"
    write_deck({"workflow_contract_version": "fixed-canvas-cm-v2", "slide": dict(SLIDE)}, entries, path, [])

    reopened = Presentation(path)
    assert len(reopened.slides) == 8
    assert all(not any(shape.has_chart for shape in slide.shapes) for slide in reopened.slides)
    expected_counts = (4, 4, 4, 4, 4, 4, 2, 3)
    for slide, (_relationship, quantitative, fallback), expected_count in zip(reopened.slides, MATRIX, expected_counts, strict=True):
        names = {shape.name for shape in slide.shapes}
        assert any(fallback in name for name in names)
        nodes = [shape for shape in slide.shapes if shape.name.startswith(f"{fallback} node")]
        assert len(nodes) == expected_count
        forbidden = {"axis", "scaled", "bubble size", "target line", "difference arrow"}
        assert not any(term in name.casefold() for name in names for term in forbidden)
        assert quantitative["rendering_primitive"] not in " ".join(names)

from __future__ import annotations

import json
import re
import subprocess
import sys
import zipfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE


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
from workflow_v6_reconstruction_worker import (  # noqa: E402
    PageWorkerResult,
    reconstruct_accepted_page,
)
from test_workflow_v6_reconstruction import _project  # noqa: E402


OUTPUT = REPO / "tmp/v1.2.3-acceptance/synthetic"


def _box_px(left: float, top: float, width: float, height: float) -> list[int]:
    return [
        round((left - CONTENT_BOX["left"]) / CONTENT_BOX["width"] * 1904),
        round((top - CONTENT_BOX["top"]) / CONTENT_BOX["height"] * 896),
        round(width / CONTENT_BOX["width"] * 1904),
        round(height / CONTENT_BOX["height"] * 896),
    ]


def _accepted_outcome(project: Path) -> SimpleNamespace:
    receipt = json.loads((project / "04_v6/images/page_001.json").read_text(encoding="utf-8"))
    selected = receipt.get("candidate", receipt.get("selected"))
    candidate = SimpleNamespace(path=project / selected["path"], attempt=selected["attempt"])
    return SimpleNamespace(status="accepted", accepted=SimpleNamespace(candidate=candidate))


def _production_worker(manifest_factory, calls: list[dict]):
    def worker(request):
        page_request = json.loads((request.page_dir / "page_request.json").read_text(encoding="utf-8"))
        accepted_request = json.loads((request.page_dir / "accepted_reconstruction_request.json").read_text(encoding="utf-8"))
        assert page_request.get("numeric_authority") == accepted_request.get("numeric_authority")
        manifest = manifest_factory(page_request)
        manifest_path = request.page_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        dispatch = subprocess.run(
            [sys.executable, str(RUNTIME / "record_page_dispatch.py"), str(request.run_dir), "--page", "page_001", "--agent-id", "deterministic-worker", "--prompt-file", str(request.prompt_file)],
            capture_output=True, text=True, check=False,
        )
        assert dispatch.returncode == 0, dispatch.stderr
        for command in (
            [sys.executable, str(RUNTIME / "main.py"), "page", "build", str(request.page_dir)],
            [sys.executable, str(RUNTIME / "main.py"), "page", "validate", str(request.page_dir), "--report", "validation.json"],
        ):
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            assert completed.returncode == 0, completed.stderr
        validation = json.loads((request.page_dir / "validation.json").read_text(encoding="utf-8"))
        assert validation["passed"] is True
        calls.append({"request": page_request, "prompt": request.prompt_file.read_text(encoding="utf-8"), "manifest": manifest, "page_dir": request.page_dir})
        return PageWorkerResult(status="completed", reconstructed_body=request.page_dir / "page.pptx")

    return worker


def _worker_chart(page_request: dict) -> dict:
    authority = page_request["numeric_authority"]
    return _manifest({"object_id": "sealed-chart", "name": "sealed-chart", "box_px": [190, 90, 1142, 620], **authority})


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
        "driver_bridge": [(0.8 + index * 2.2, 2.4, 1.8, 0.9) for index in range(4)],
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
            "type": "rect",
            "box_px": _box_px(left, top, width, height),
            "fill": "#EAF2F8",
            "stroke": "#6B7A90",
        }
        for index, (left, top, width, height) in enumerate(boxes)
    ]
    manifest["text_boxes"] = [
        {
            "object_id": f"{fallback}-title",
            "name": f"{fallback} title",
            "box_px": _box_px(0.7, 1.0, 8.6, 0.5),
            "text": f"{relationship}: {fallback} (qualitative, non-scaled)",
            "font_size": 22,
        },
        *[
            {
                "object_id": f"{fallback}-label-{index}",
                "name": f"{fallback} label {index}",
                "box_px": _box_px(left + 0.15, top + 0.25, width - 0.3, min(0.7, height - 0.3)),
                "text": f"Source-backed item {chr(65 + index)}",
                "font_size": 14,
                "align": "center",
            }
            for index, (left, top, width, height) in enumerate(boxes)
        ],
    ]
    return manifest


def _one_dimensional(name: str, variant: str, *, comparison_mark: str | None = None) -> dict:
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
    if comparison_mark:
        chart.update({
            "target_value": 20,
            "actual_value": 18,
            "comparison_mark": comparison_mark,
        })
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
    _one_dimensional("target-line", "dot", comparison_mark="target_line"),
    _one_dimensional("difference-arrow", "dot", comparison_mark="difference_arrow"),
]


MATRIX = [
    ("drivers", _waterfall(), "driver_bridge"),
    ("time_change", _one_dimensional("time-change", "line"), "timeline_roadmap"),
    ("two_variables", _xy("two-variables", "scatter"), "qualitative_quadrant_or_comparison_table"),
    ("third_variable", _xy("third-variable", "bubble"), "uniform_nodes"),
    ("market_size_share", _mekko(), "equal_width_hierarchy"),
    ("project_stage_time", _gantt(), "roadmap_milestones"),
    ("option_comparison", _one_dimensional("options", "dot"), "comparison_table"),
    ("target_actual_variance", _one_dimensional("target", "dot", comparison_mark="both"), "goal_current_gap"),
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
    assert "target-line Difference Arrow" not in target_names
    assert "target-line Difference" not in target_names
    assert "difference-arrow Target Line" not in difference_names
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
        quantitative_project = _project(tmp_path / f"{relationship}-quantitative", 1)
        quantitative_materials = quantitative_project / "02_v6/page_materials/page_001.json"
        quantitative_materials.parent.mkdir(parents=True, exist_ok=True)
        quantitative_materials.write_text(json.dumps({"chart_facts": [quantitative]}, ensure_ascii=False), encoding="utf-8")
        quantitative_calls: list[dict] = []
        reconstruct_accepted_page(
            SimpleNamespace(project_copy=quantitative_project, page_number=1),
            _accepted_outcome(quantitative_project),
            page_worker=_production_worker(_worker_chart, quantitative_calls),
        )
        assert quantitative_calls[0]["request"]["numeric_authority"] == authority
        assert "sealed numeric authority owns quantitative mark size" in quantitative_calls[0]["prompt"]
        assert (quantitative_calls[0]["page_dir"] / "page.pptx").is_file()

        qualitative = {
            "title": relationship,
            "relationship": relationship,
            "source_wording": "Source describes the relationship but supplies no complete numeric dimensions.",
            "disabled_primitive": quantitative["rendering_primitive"],
            "fallback": fallback,
            "series": [],
        }
        assert select_numeric_authority([qualitative]) is None
        project = _project(tmp_path / f"{relationship}-qualitative", 1)
        material_path = project / "02_v6/page_materials/page_001.json"
        material_path.parent.mkdir(parents=True, exist_ok=True)
        material_path.write_text(
            json.dumps({"chart_facts": [qualitative]}, ensure_ascii=False), encoding="utf-8",
        )
        request = build_reconstruction_request(project, page_number=1)
        assert "numeric_authority" not in request
        qualitative_calls: list[dict] = []
        reconstruct_accepted_page(
            SimpleNamespace(project_copy=project, page_number=1),
            _accepted_outcome(project),
            page_worker=_production_worker(
                lambda page_request, r=relationship, f=fallback: _qualitative_manifest(r, f),
                qualitative_calls,
            ),
        )
        assert "numeric_authority" not in qualitative_calls[0]["request"]
        assert "No sealed numeric authority is present" in qualitative_calls[0]["prompt"]
        manifest = qualitative_calls[0]["manifest"]
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
    with zipfile.ZipFile(path) as package:
        assert not any(name.startswith("ppt/charts/") for name in package.namelist())
        assert not any(b"<c:chart" in package.read(name) for name in package.namelist() if name.endswith(".xml"))
    expected_counts = (4, 4, 4, 4, 4, 4, 2, 3)
    for slide, (relationship, _quantitative, fallback), expected_count in zip(reopened.slides, MATRIX, expected_counts, strict=True):
        names = {shape.name for shape in slide.shapes}
        assert any(fallback in name for name in names)
        nodes = [shape for shape in slide.shapes if shape.name.startswith(f"{fallback} node")]
        assert len(nodes) == expected_count
        assert len({shape.width for shape in nodes}) == 1
        assert len({shape.height for shape in nodes}) == 1
        assert len({shape.width * shape.height for shape in nodes}) == 1
        assert all(shape.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE and shape.auto_shape_type == MSO_SHAPE.RECTANGLE for shape in nodes)
        assert {shape.name for shape in slide.shapes if shape.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE} == {shape.name for shape in nodes}
        assert all(shape.shape_type not in {MSO_SHAPE_TYPE.LINE, MSO_SHAPE_TYPE.CHART} for shape in slide.shapes)
        slide_text = "\n".join(shape.text for shape in slide.shapes if getattr(shape, "has_text_frame", False))
        assert not re.search(r"\d", slide_text)
        if relationship in {"drivers", "time_change", "third_variable", "project_stage_time", "goal_current_gap"}:
            assert len({shape.top for shape in nodes}) == 1
        if relationship == "two_variables":
            assert len({shape.left for shape in nodes}) == 2 and len({shape.top for shape in nodes}) == 2
        if relationship == "market_size_share":
            assert len({shape.top for shape in nodes}) == 2 and len([shape for shape in nodes if shape.top == min(node.top for node in nodes)]) == 1

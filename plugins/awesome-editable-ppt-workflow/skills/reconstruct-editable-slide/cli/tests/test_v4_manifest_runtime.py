import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation

from editppt.runtime.build_pptx_from_manifest import normalize_manifest, render_preview, write_pptx
from editppt.runtime.fixed_region_runtime import CONTENT_BOX, SLIDE
from editppt.runtime.validate_pptx import (
    ALLOWED_SOURCE_TYPES,
    _connector_endpoints,
    _shape_arrowheads,
    _shape_kind,
    foreground_asset_contract_violations,
)


def _manifest(width=1700, height=800):
    return {
        "workflow_contract_version": "fixed-canvas-cm-v2",
        "reconstruction_contract_version": "editable-image-v3",
        "slide": dict(SLIDE),
        "content_box": dict(CONTENT_BOX),
        "source": {"width_px": width, "height_px": height},
        "text_boxes": [{"object_id": "word-p1", "name": "body-paragraph-1", "text": "权威正文", "box_px": [80, 60, 700, 90]}],
        "tables": [{
            "object_id": "word-t1", "name": "body-table-1", "box_px": [80, 220, 1100, 300],
            "rows": [["项目", "数值"], ["投资额", "50万元"]],
            "font_size": 12, "font_color": "#000000", "cell_fill": "#FFFFFF", "cell_margin_px": 8,
        }],
        "shapes": [{"object_id": "decor-1", "name": "decorative-panel", "type": "rect", "box_px": [40, 30, 1500, 650], "fill": "#f4f4f4"}],
        "images": [],
    }


def _directed_edge_manifest(points_px, source_box=None, target_box=None, width=1700, height=800):
    manifest = _manifest(width, height)
    manifest["shapes"] = [
        {"object_id": "source", "name": "source", "type": "rect", "box_px": source_box or [100, 100, 100, 100], "fill": "#FFFFFF"},
        {"object_id": "target", "name": "target", "type": "rect", "box_px": target_box or [400, 100, 100, 100], "fill": "#FFFFFF"},
        {
            "object_id": "edge:source->target", "name": "edge:source->target", "type": "line",
            "points_px": points_px, "stroke": "#6B7A90",
        },
    ]
    return manifest


def _shape_by_object_id(slide, object_id):
    return next(
        shape for shape in slide.shapes
        if shape._element.xpath(".//p:cNvPr")[0].get("descr") == f"object_id:{object_id}"
    )


def test_authentic_published_source_is_a_first_class_provenance_type():
    assert "authentic-published-source" in ALLOWED_SOURCE_TYPES


@pytest.mark.parametrize("box_px", ([1035, 225, 62, 74], [0, 0, 24, 24]))
def test_v4_accepts_explicit_bounded_pictogram_from_bound_accepted_source(box_px):
    manifest = _manifest(1904, 896)
    manifest["source"]["path"] = "source.png"
    manifest["images"] = [{
        "object_id": "pictogram-0", "path": "assets/pictogram_0.png", "box_px": box_px,
    }]
    manifest["visual_inventory"] = [{
        "description": "Pictogram icon separated as a bounded accepted-image region."
    }]
    manifest["asset_provenance"] = [{
        "path": "assets/pictogram_0.png",
        "source": "source.png",
        "source_type": "user-provided",
        "provenance_note": (
            "Pictogram separated by bounded extraction of accepted source pixels; "
            "original internal geometry preserved."
        ),
    }]

    assert foreground_asset_contract_violations(manifest) == []


def test_v4_bounded_accepted_region_may_truthfully_say_cropped_from_source():
    manifest = _manifest(1904, 896)
    manifest["source"]["path"] = "source.png"
    manifest["images"] = [{
        "object_id": "pictogram-0", "path": "assets/pictogram_0.png",
        "box_px": [1035, 225, 62, 74],
    }]
    manifest["visual_inventory"] = [{
        "description": "Pictogram icon separated as a bounded accepted-image region."
    }]
    manifest["asset_provenance"] = [{
        "path": "assets/pictogram_0.png",
        "source": "source.png",
        "source_type": "user-provided",
        "provenance_note": (
            "Pictogram kept as a bounded accepted-image region cropped from source.png."
        ),
    }]

    assert foreground_asset_contract_violations(manifest) == []


@pytest.mark.parametrize(
    "source,box_px,object_id,note",
    [
        ("other.png", [1035, 225, 62, 74], "pictogram-0", "Pictogram separated by bounded extraction of accepted source pixels."),
        ("source.png", [1890, 880, 40, 40], "pictogram-0", "Pictogram separated by bounded extraction of accepted source pixels."),
        ("source.png", [0, 0, 1904, 896], "pictogram-0", "Pictogram separated by bounded extraction of accepted source pixels."),
        ("source.png", [100, 100, 700, 400], "whole-card", "Whole card screenshot separated by bounded extraction of accepted source pixels."),
        ("source.png", [100, 100, 700, 400], "whole-table", "Whole table screenshot separated by bounded extraction of accepted source pixels."),
        ("source.png", [100, 100, 700, 400], "whole-chart", "Whole chart screenshot separated by bounded extraction of accepted source pixels."),
    ],
)
def test_v4_rejects_unbound_out_of_bounds_full_page_or_structural_bounded_raster(
    source, box_px, object_id, note,
):
    manifest = _manifest(1904, 896)
    manifest["source"]["path"] = "source.png"
    manifest["images"] = [{
        "object_id": object_id, "path": f"assets/{object_id}.png", "box_px": box_px,
    }]
    manifest["visual_inventory"] = [{
        "description": f"{object_id} screenshot separated from the accepted source."
    }]
    manifest["asset_provenance"] = [{
        "path": f"assets/{object_id}.png",
        "source": source,
        "source_type": "user-provided",
        "provenance_note": note,
    }]

    assert foreground_asset_contract_violations(manifest)


def test_v4_rejects_unexpected_16_by_9_instead_of_containing():
    with pytest.raises(ValueError, match="17:8"):
        normalize_manifest(_manifest(1600, 900))


def test_v4_builds_stable_named_text_shape_and_native_table(tmp_path: Path):
    manifest = _manifest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "page.pptx"
    write_pptx(manifest, output, manifest_path)

    deck = Presentation(output)
    names = {shape.name for shape in deck.slides[0].shapes}
    assert {"body-paragraph-1", "body-table-1", "decorative-panel"} <= names
    table = next(shape.table for shape in deck.slides[0].shapes if shape.has_table)
    assert table.cell(1, 1).text == "50万元"
    with zipfile.ZipFile(output) as archive:
        xml = archive.read("ppt/slides/slide1.xml").decode("utf-8")
    assert 'descr="object_id:word-p1"' in xml
    assert 'descr="object_id:word-t1"' in xml
    assert '<a:srgbClr val="000000"' in xml
    assert '<a:srgbClr val="FFFFFF"' in xml
    assert 'marL="' in xml and 'marR="' in xml and 'marT="' in xml and 'marB="' in xml


def test_v4_native_table_rejects_unresolved_default_cell_visuals():
    manifest = _manifest()
    for field in ("font_size", "font_color", "cell_fill", "cell_margin_px"):
        manifest["tables"][0].pop(field)
    with pytest.raises(ValueError, match="table cells require explicit"):
        normalize_manifest(manifest)


@pytest.mark.parametrize("points_px,source_box,target_box", [
    ([150, 150, 399, 150], None, None),  # target endpoint misses its left edge
    ([99, 150, 450, 150], None, None),  # source endpoint misses its left edge
    ([399, 150, 201, 150], [400, 100, 100, 100], [100, 100, 100, 100]),  # flipH
    ([150, 399, 150, 201], [100, 400, 100, 100], [100, 100, 100, 100]),  # flipV
])
def test_v4_sealed_directed_edge_snaps_one_pixel_misses_and_serializes_target_arrow(
    tmp_path: Path, points_px, source_box, target_box,
):
    manifest = _directed_edge_manifest(points_px, source_box, target_box)
    output = tmp_path / "page.pptx"
    write_pptx(manifest, output, tmp_path / "manifest.json")

    slide = Presentation(output).slides[0]
    source = _shape_by_object_id(slide, "source")
    target = _shape_by_object_id(slide, "target")
    edge = _shape_by_object_id(slide, "edge:source->target")
    start_x, start_y, end_x, end_y = _connector_endpoints(edge)
    assert source.left <= start_x <= source.left + source.width
    assert source.top <= start_y <= source.top + source.height
    assert target.left <= end_x <= target.left + target.width
    assert target.top <= end_y <= target.top + target.height
    assert _shape_arrowheads(edge) == {"tailEnd": "triangle"}


def test_v4_sealed_bent_edge_is_one_native_connector_with_exact_direction_and_flips(tmp_path: Path):
    manifest = _directed_edge_manifest(
        [400, 450, 200, 150],
        source_box=[400, 400, 100, 100],
        target_box=[100, 100, 100, 100],
    )
    manifest["shapes"][2]["preset"] = "bentConnector3"
    manifest["shapes"][2]["bend_x_px"] = 350
    output = tmp_path / "page.pptx"
    write_pptx(manifest, output, tmp_path / "manifest.json")

    edge = _shape_by_object_id(Presentation(output).slides[0], "edge:source->target")
    assert edge._element.tag.rsplit("}", 1)[-1] == "cxnSp"
    assert _shape_kind(edge) == "connector"
    assert edge._element.xpath(".//a:prstGeom")[0].get("prst") == "bentConnector3"
    adjustment = edge._element.xpath(".//a:prstGeom/a:avLst/a:gd")
    assert [(item.get("name"), item.get("fmla")) for item in adjustment] == [("adj1", "val 25000")]
    assert _connector_endpoints(edge) == tuple(
        round(value * 914400)
        for value in normalize_manifest(manifest)["shapes"][2]["points"]
    )
    transform = edge._element.xpath(".//a:xfrm")[0]
    assert transform.get("flipH") == "1"
    assert transform.get("flipV") == "1"
    assert _shape_arrowheads(edge) == {"tailEnd": "triangle"}


def test_v4_bent_edge_preview_uses_declared_elbow_dash_and_target_arrow(tmp_path: Path):
    manifest = _directed_edge_manifest(
        [200, 150, 400, 350],
        source_box=[100, 100, 100, 100],
        target_box=[400, 300, 100, 100],
    )
    edge = manifest["shapes"][2]
    edge.update({"preset": "bentConnector3", "bend_x_px": 300, "dash": "dash", "stroke": "#000000"})
    preview = tmp_path / "preview.png"
    render_preview(manifest, tmp_path / "manifest.json", preview)

    normalized = normalize_manifest(manifest)["shapes"][2]
    scale = 120
    start_x, start_y, end_x, end_y = [round(value * scale) for value in normalized["points"]]
    bend_x = round(normalized["bend_x"] * scale)
    with Image.open(preview) as image:
        pixels = image.convert("RGB")

        def is_dark(x, y):
            red, green, blue = pixels.getpixel((x, y))
            return max(red, green, blue) < 80

        def is_dark_near(x, y):
            return any(is_dark(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1))

        horizontal = [is_dark_near(x, start_y) for x in range(start_x + 3, bend_x - 3)]
        vertical = [is_dark_near(bend_x, y) for y in range(start_y + 3, end_y - 3)]
        assert any(horizontal) and not all(horizontal)
        assert any(vertical) and not all(vertical)
        assert not is_dark_near((start_x + bend_x) // 2, (start_y + end_y) // 2)
        assert any(
            is_dark(end_x - offset, end_y + delta)
            for offset in range(2, 9)
            for delta in range(-5, 6)
            if abs(delta) >= 2
        )


@pytest.mark.parametrize("bend_x_px", [199, 401, "not-a-number"])
def test_v4_bent_edge_rejects_bend_outside_its_endpoint_span(tmp_path: Path, bend_x_px):
    manifest = _directed_edge_manifest(
        [200, 150, 400, 350], target_box=[400, 300, 100, 100],
    )
    manifest["shapes"][2].update({"preset": "bentConnector3", "bend_x_px": bend_x_px})

    with pytest.raises(ValueError, match="bend_x_px"):
        write_pptx(manifest, tmp_path / "page.pptx", tmp_path / "manifest.json")


def test_v4_high_resolution_edge_snaps_inside_after_emu_rounding(tmp_path: Path):
    manifest = _directed_edge_manifest(
        [150, 150, 16, 150], target_box=[3, 100, 12, 100], width=34000, height=16000,
    )
    output = tmp_path / "page.pptx"
    write_pptx(manifest, output, tmp_path / "manifest.json")

    slide = Presentation(output).slides[0]
    target = _shape_by_object_id(slide, "target")
    edge = _shape_by_object_id(slide, "edge:source->target")
    _start_x, _start_y, end_x, end_y = _connector_endpoints(edge)
    assert target.left <= end_x <= target.left + target.width
    assert target.top <= end_y <= target.top + target.height


def test_v4_sealed_directed_edge_rejects_two_pixel_target_miss(tmp_path: Path):
    manifest = _directed_edge_manifest([200, 150, 398, 150])

    with pytest.raises(ValueError, match="outside target node"):
        write_pptx(manifest, tmp_path / "page.pptx", tmp_path / "manifest.json")


@pytest.mark.parametrize("defect,error", [
    ("stroke_none", "stroke must be serializable"),
    ("rect", "must be a real line"),
    ("missing_node", "missing or ambiguous"),
    ("duplicate_node", "object_id values must be unique"),
    ("zero_size", "positive dimensions"),
])
def test_v4_sealed_directed_edge_rejects_unserializable_or_unresolved_edges(tmp_path: Path, defect, error):
    manifest = _directed_edge_manifest([150, 150, 400, 150])
    edge = manifest["shapes"][2]
    if defect == "stroke_none":
        edge["stroke"] = "none"
    elif defect == "rect":
        edge["type"] = "rect"
    elif defect == "missing_node":
        manifest["shapes"].pop(1)
    elif defect == "duplicate_node":
        manifest["shapes"].append(dict(manifest["shapes"][1]))
    else:
        manifest["shapes"][1]["box_px"][2] = 0

    with pytest.raises(ValueError, match=error):
        write_pptx(manifest, tmp_path / "page.pptx", tmp_path / "manifest.json")


def test_v4_sealed_directed_edge_rejects_ambiguous_node_ids(tmp_path: Path):
    manifest = _directed_edge_manifest([150, 150, 450, 150])
    manifest["shapes"][0]["object_id"] = "a"
    manifest["shapes"][1]["object_id"] = "b->target"
    manifest["shapes"].extend([
        {"object_id": "a->b", "name": "alternate-source", "type": "rect", "box_px": [100, 300, 100, 100]},
        {"object_id": "target", "name": "alternate-target", "type": "rect", "box_px": [400, 300, 100, 100]},
    ])
    manifest["shapes"][2]["object_id"] = "edge:a->b->target"

    with pytest.raises(ValueError, match="missing or ambiguous"):
        write_pptx(manifest, tmp_path / "page.pptx", tmp_path / "manifest.json")


def test_v4_plain_line_keeps_geometry_and_has_no_arrowhead(tmp_path: Path):
    manifest = _manifest()
    manifest["shapes"] = [{
        "object_id": "plain-line", "name": "plain-line", "type": "line",
        "points_px": [200, 150, 399, 150], "stroke": "#6B7A90",
    }]
    output = tmp_path / "page.pptx"
    write_pptx(manifest, output, tmp_path / "manifest.json")

    edge = _shape_by_object_id(Presentation(output).slides[0], "plain-line")
    assert _connector_endpoints(edge) == tuple(
        round(value * 914400)
        for value in normalize_manifest(manifest)["shapes"][0]["points"]
    )
    assert _shape_arrowheads(edge) == {}

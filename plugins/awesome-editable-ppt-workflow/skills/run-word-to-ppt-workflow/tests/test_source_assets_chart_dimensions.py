from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from source_assets import _chart_record  # noqa: E402


def test_ooxml_xy_chart_binds_top_right_axes_by_id_and_preserves_point_indices():
    xml = b'''<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">
      <c:chart><c:title><c:tx><c:rich><c:p><c:r><c:t>Portfolio map</c:t></c:r></c:p></c:rich></c:tx></c:title>
      <c:plotArea><c:bubbleChart><c:ser>
        <c:tx><c:strRef><c:strCache><c:pt idx="0"><c:v>Companies</c:v></c:pt></c:strCache></c:strRef></c:tx>
        <c:xVal><c:numRef><c:numCache><c:pt idx="0"><c:v>1.2</c:v></c:pt><c:pt idx="1"><c:v>2.4</c:v></c:pt></c:numCache></c:numRef></c:xVal>
        <c:yVal><c:numRef><c:numCache><c:pt idx="0"><c:v>8</c:v></c:pt><c:pt idx="1"><c:v>11</c:v></c:pt></c:numCache></c:numRef></c:yVal>
        <c:bubbleSize><c:numRef><c:numCache><c:pt idx="0"><c:v>30</c:v></c:pt><c:pt idx="1"><c:v>50</c:v></c:pt></c:numCache></c:numRef></c:bubbleSize>
      </c:ser><c:axId val="20"/><c:axId val="10"/></c:bubbleChart>
      <c:valAx><c:axId val="10"/><c:axPos val="r"/><c:title><c:tx><c:rich><c:p><c:r><c:t>Margin (% pts)</c:t></c:r></c:p></c:rich></c:tx></c:title></c:valAx>
      <c:valAx><c:axId val="20"/><c:axPos val="t"/><c:title><c:tx><c:rich><c:p><c:r><c:t>Growth (%)</c:t></c:r></c:p></c:rich></c:tx></c:title></c:valAx>
      </c:plotArea></c:chart></c:chartSpace>'''

    record = _chart_record({"page_numbers": [3], "asset_id": "word_asset_001"}, xml)

    assert record == {
        "page_numbers": [3],
        "source_page": 3,
        "source_asset_id": "word_asset_001",
        "title": "Portfolio map",
        "rendering_primitive": "xy",
        "chart_variant": "bubble",
        "x_label": "Growth",
        "x_unit": "%",
        "y_label": "Margin",
        "y_unit": "% pts",
        "series": [{
            "series": "Companies",
            "x_values": ["1.2", "2.4"],
            "x_indices": [0, 1],
            "y_values": ["8", "11"],
            "y_indices": [0, 1],
            "size_values": ["30", "50"],
            "size_indices": [0, 1],
        }],
    }


def test_ooxml_column_chart_preserves_categories_values_and_explicit_variant():
    xml = b'''<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">
      <c:chart><c:title><c:tx><c:rich><c:p><c:r><c:t>Revenue</c:t></c:r></c:p></c:rich></c:tx></c:title>
      <c:plotArea><c:barChart><c:barDir val="col"/><c:ser>
        <c:tx><c:v>Revenue</c:v></c:tx>
        <c:cat><c:strRef><c:strCache><c:pt idx="0"><c:v>2024</c:v></c:pt><c:pt idx="1"><c:v>2025</c:v></c:pt></c:strCache></c:strRef></c:cat>
        <c:val><c:numRef><c:numCache><c:pt idx="0"><c:v>12</c:v></c:pt><c:pt idx="1"><c:v>18</c:v></c:pt></c:numCache></c:numRef></c:val>
      </c:ser></c:barChart><c:valAx><c:axPos val="l"/><c:title><c:tx><c:rich><c:p><c:r><c:t>Revenue (USD m)</c:t></c:r></c:p></c:rich></c:tx></c:title></c:valAx></c:plotArea>
      </c:chart></c:chartSpace>'''

    record = _chart_record({"page_numbers": [2, 4], "asset_id": "word_asset_002"}, xml)

    assert record["rendering_primitive"] == "column_bar"
    assert record["chart_variant"] == "column"
    assert record["unit"] == "USD m"
    assert record["series"] == [{
        "series": "Revenue",
        "categories": ["2024", "2025"],
        "times": ["2024", "2025"],
        "category_indices": [0, 1],
        "values": ["12", "18"],
        "value_indices": [0, 1],
    }]


def test_ooxml_series_label_uses_cache_without_concatenating_formula():
    xml = b'''<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">
      <c:chart><c:title><c:tx><c:v>Revenue</c:v></c:tx></c:title><c:plotArea><c:barChart><c:barDir val="col"/><c:ser>
      <c:tx><c:strRef><c:f>Sheet1!$B$1</c:f><c:strCache><c:pt idx="0"><c:v>Revenue</c:v></c:pt></c:strCache></c:strRef></c:tx>
      <c:cat><c:strLit><c:pt idx="0"><c:v>2025</c:v></c:pt></c:strLit></c:cat>
      <c:val><c:numLit><c:pt idx="0"><c:v>20</c:v></c:pt></c:numLit></c:val>
      </c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>'''

    record = _chart_record({"page_numbers": [1], "asset_id": "word_asset_008"}, xml)

    assert record["series"][0]["series"] == "Revenue"


def test_ooxml_axis_title_does_not_become_chart_title():
    xml = b'''<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">
      <c:chart><c:plotArea><c:barChart><c:barDir val="col"/><c:ser><c:tx><c:v>Revenue</c:v></c:tx>
      <c:cat><c:strLit><c:pt idx="0"><c:v>2025</c:v></c:pt></c:strLit></c:cat>
      <c:val><c:numLit><c:pt idx="0"><c:v>20</c:v></c:pt></c:numLit></c:val>
      </c:ser></c:barChart><c:valAx><c:title><c:tx><c:v>Revenue (USD m)</c:v></c:tx></c:title></c:valAx>
      </c:plotArea></c:chart></c:chartSpace>'''

    record = _chart_record({"page_numbers": [1], "asset_id": "word_asset_009"}, xml)

    assert record["title"] == "Untitled source chart"
    assert record["disabled_primitive"] == "column_bar"


def test_ooxml_mismatched_xy_point_indices_disable_quantitative_authority():
    xml = b'''<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart>
      <c:title><c:tx><c:v>Risk return</c:v></c:tx></c:title><c:plotArea><c:scatterChart><c:ser>
      <c:tx><c:v>Funds</c:v></c:tx>
      <c:xVal><c:numRef><c:numCache><c:pt idx="0"><c:v>1</c:v></c:pt><c:pt idx="2"><c:v>3</c:v></c:pt></c:numCache></c:numRef></c:xVal>
      <c:yVal><c:numRef><c:numCache><c:pt idx="0"><c:v>4</c:v></c:pt><c:pt idx="1"><c:v>5</c:v></c:pt></c:numCache></c:numRef></c:yVal>
      </c:ser></c:scatterChart></c:plotArea></c:chart></c:chartSpace>'''

    record = _chart_record({"page_numbers": [1], "asset_id": "word_asset_003"}, xml)

    assert record["series"][0]["x_indices"] == [0, 2]
    assert record["series"][0]["y_indices"] == [0, 1]
    assert record["disabled_primitive"] == "xy"
    assert record["fallback"] == "native_table"
    assert "rendering_primitive" not in record


def test_ooxml_mismatched_category_value_indices_disable_quantitative_authority():
    xml = b'''<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart>
      <c:title><c:tx><c:v>Revenue</c:v></c:tx></c:title><c:plotArea><c:barChart><c:barDir val="bar"/><c:ser>
      <c:tx><c:v>Revenue</c:v></c:tx>
      <c:cat><c:strRef><c:strCache><c:pt idx="0"><c:v>A</c:v></c:pt><c:pt idx="2"><c:v>C</c:v></c:pt></c:strCache></c:strRef></c:cat>
      <c:val><c:numRef><c:numCache><c:pt idx="0"><c:v>10</c:v></c:pt><c:pt idx="1"><c:v>20</c:v></c:pt></c:numCache></c:numRef></c:val>
      </c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>'''

    record = _chart_record({"page_numbers": [1], "asset_id": "word_asset_004"}, xml)

    assert record["disabled_primitive"] == "column_bar"
    assert record["series"][0]["category_indices"] == [0, 2]
    assert record["series"][0]["value_indices"] == [0, 1]


def test_ooxml_combination_chart_retains_all_cached_series_as_fallback():
    xml = b'''<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart>
      <c:title><c:tx><c:v>Revenue and margin</c:v></c:tx></c:title><c:plotArea>
      <c:barChart><c:barDir val="col"/><c:ser><c:tx><c:v>Revenue</c:v></c:tx><c:cat><c:strLit><c:pt idx="0"><c:v>2025</c:v></c:pt></c:strLit></c:cat><c:val><c:numLit><c:pt idx="0"><c:v>20</c:v></c:pt></c:numLit></c:val></c:ser></c:barChart>
      <c:lineChart><c:ser><c:tx><c:v>Margin</c:v></c:tx><c:cat><c:strLit><c:pt idx="0"><c:v>2025</c:v></c:pt></c:strLit></c:cat><c:val><c:numLit><c:pt idx="0"><c:v>8</c:v></c:pt></c:numLit></c:val></c:ser></c:lineChart>
      </c:plotArea></c:chart></c:chartSpace>'''

    record = _chart_record({"page_numbers": [1], "asset_id": "word_asset_005"}, xml)

    assert record["disabled_primitive"] == "combination"
    assert record["fallback"] == "native_table"
    assert [series["series"] for series in record["series"]] == ["Revenue", "Margin"]
    assert "rendering_primitive" not in record


def test_ooxml_ser_axis_never_authorizes_bubble_size_metadata():
    xml = b'''<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart>
      <c:title><c:tx><c:v>Portfolio</c:v></c:tx></c:title><c:plotArea><c:bubbleChart><c:ser>
      <c:tx><c:v>Companies</c:v></c:tx><c:xVal><c:numLit><c:pt idx="0"><c:v>1</c:v></c:pt></c:numLit></c:xVal>
      <c:yVal><c:numLit><c:pt idx="0"><c:v>2</c:v></c:pt></c:numLit></c:yVal><c:bubbleSize><c:numLit><c:pt idx="0"><c:v>3</c:v></c:pt></c:numLit></c:bubbleSize>
      </c:ser><c:axId val="1"/><c:axId val="2"/></c:bubbleChart>
      <c:valAx><c:axId val="1"/><c:axPos val="b"/><c:title><c:tx><c:v>Growth (%)</c:v></c:tx></c:title></c:valAx>
      <c:valAx><c:axId val="2"/><c:axPos val="l"/><c:title><c:tx><c:v>Margin (%)</c:v></c:tx></c:title></c:valAx>
      <c:serAx><c:axId val="3"/><c:title><c:tx><c:v>Revenue (USD m)</c:v></c:tx></c:title></c:serAx>
      </c:plotArea></c:chart></c:chartSpace>'''

    record = _chart_record({"page_numbers": [1], "asset_id": "word_asset_006"}, xml)

    assert "size_label" not in record
    assert "size_unit" not in record


def test_ooxml_untitled_chart_retains_cached_values_as_fallback():
    xml = b'''<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart><c:plotArea>
      <c:barChart><c:barDir val="col"/><c:ser><c:tx><c:v>Revenue</c:v></c:tx>
      <c:cat><c:strLit><c:pt idx="0"><c:v>2025</c:v></c:pt></c:strLit></c:cat>
      <c:val><c:numLit><c:pt idx="0"><c:v>20</c:v></c:pt></c:numLit></c:val>
      </c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>'''

    record = _chart_record({"page_numbers": [4], "asset_id": "word_asset_007"}, xml)

    assert record["title"] == "Untitled source chart"
    assert record["disabled_primitive"] == "column_bar"
    assert record["fallback"] == "native_table"
    assert record["series"][0]["categories"] == ["2025"]
    assert record["series"][0]["values"] == ["20"]

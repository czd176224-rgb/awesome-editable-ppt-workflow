from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from source_assets import _chart_record  # noqa: E402


def test_ooxml_xy_chart_preserves_independent_axes_units_and_bubble_size():
    xml = b'''<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">
      <c:chart><c:title><c:tx><c:rich><c:p><c:r><c:t>Portfolio map</c:t></c:r></c:p></c:rich></c:tx></c:title>
      <c:plotArea><c:bubbleChart><c:ser>
        <c:tx><c:strRef><c:strCache><c:pt idx="0"><c:v>Companies</c:v></c:pt></c:strCache></c:strRef></c:tx>
        <c:xVal><c:numRef><c:numCache><c:pt idx="0"><c:v>1.2</c:v></c:pt><c:pt idx="1"><c:v>2.4</c:v></c:pt></c:numCache></c:numRef></c:xVal>
        <c:yVal><c:numRef><c:numCache><c:pt idx="0"><c:v>8</c:v></c:pt><c:pt idx="1"><c:v>11</c:v></c:pt></c:numCache></c:numRef></c:yVal>
        <c:bubbleSize><c:numRef><c:numCache><c:pt idx="0"><c:v>30</c:v></c:pt><c:pt idx="1"><c:v>50</c:v></c:pt></c:numCache></c:numRef></c:bubbleSize>
      </c:ser></c:bubbleChart>
      <c:valAx><c:axPos val="b"/><c:title><c:tx><c:rich><c:p><c:r><c:t>Growth (%)</c:t></c:r></c:p></c:rich></c:tx></c:title></c:valAx>
      <c:valAx><c:axPos val="l"/><c:title><c:tx><c:rich><c:p><c:r><c:t>Margin (% pts)</c:t></c:r></c:p></c:rich></c:tx></c:title></c:valAx>
      <c:serAx><c:title><c:tx><c:rich><c:p><c:r><c:t>Revenue (USD m)</c:t></c:r></c:p></c:rich></c:tx></c:title></c:serAx>
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
        "size_label": "Revenue",
        "size_unit": "USD m",
        "series": [{
            "series": "Companies",
            "x_values": ["1.2", "2.4"],
            "y_values": ["8", "11"],
            "size_values": ["30", "50"],
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
    assert record["series"] == [{"series": "Revenue", "categories": ["2024", "2025"], "values": ["12", "18"]}]
    assert "times" not in record["series"][0]

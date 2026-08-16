from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1] / "editppt" / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from build_pptx_from_manifest import presentation_xml, text_box_xml, theme_xml  # noqa: E402


def test_text_box_maps_manifest_words_to_openxml_enumerations() -> None:
    value = text_box_xml(2, {
        "object_id": "text-1", "text": "测试", "align": "center",
        "valign": "middle", "left": 1, "top": 1, "width": 2, "height": 1,
    })

    assert 'algn="ctr"' in value
    assert 'anchor="ctr"' in value
    assert 'algn="center"' not in value
    assert 'anchor="middle"' not in value


def test_widescreen_presentation_uses_schema_enumeration() -> None:
    value = presentation_xml(1, 9144000, 5143500)

    assert 'type="screen16x9"' in value
    assert 'type="wide"' not in value


def test_theme_contains_required_three_style_entries() -> None:
    root = ET.fromstring(theme_xml())
    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}

    assert len(root.findall(".//a:fillStyleLst/*", ns)) == 3
    assert len(root.findall(".//a:lnStyleLst/*", ns)) == 3
    assert len(root.findall(".//a:effectStyleLst/*", ns)) == 3
    assert len(root.findall(".//a:bgFillStyleLst/*", ns)) == 3

from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def test_native_builder_rejects_invisible_text_overflow_instead_of_saving(tmp_path):
    from native_page_builder import ContentOverflowError, build_native_page

    plan = {
        "page_number": 1, "route": "native", "body_text": "正文" * 10000,
        "semantic_units": [{"coverage_id": "semantic:unit_001", "text": "正文" * 10000}],
        "tables": [], "required_images": [],
        "coverage_contract": {"required_items": [{"coverage_id": "semantic:unit_001", "kind": "semantic_unit", "required": True, "source": {}}]},
    }
    output = tmp_path / "overflow.pptx"
    with pytest.raises(ContentOverflowError, match="content_overflow"):
        build_native_page(plan, {}, output, project_root=tmp_path)
    assert not output.exists()

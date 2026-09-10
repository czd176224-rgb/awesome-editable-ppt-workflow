"""Only the new director's transport and complete-source contracts are supported."""
import copy
import json

import pytest

from complex_page_experiment.director import (
    SCHEMA, _normalize_runtime_director_value, _render_complete_source_block,
    _validate_director_value,
)
from test_director import _compact_material_view, _director_value


def test_optional_numeric_transport_fields_do_not_create_business_authority():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    plan = schema["properties"]["page_plan"]
    assert set(plan["required"]) == set(plan["properties"])
    value = copy.deepcopy(_director_value())
    value["page_plan"].update(quantitative_exhibits=None, numeric_authorities=None)
    normalized = _normalize_runtime_director_value(value)
    assert "quantitative_exhibits" not in normalized["page_plan"]
    assert "numeric_authorities" not in normalized["page_plan"]
    assert _validate_director_value(normalized, _compact_material_view()) == ()


@pytest.mark.parametrize("bad_plan", [None, [], {"primary_relationship": {}}, {"image_prompt": None}])
def test_new_schema_rejects_malformed_or_old_plans(bad_plan):
    value = copy.deepcopy(_director_value())
    value["page_plan"] = bad_plan
    with pytest.raises(ValueError, match="director schema rejected"):
        _validate_director_value(value, _compact_material_view())


@pytest.mark.parametrize("block,expected", [
    ({"type": "paragraph", "text": "完整条件。"}, "完整条件。"),
    ({"type": "list", "text": "Numbered fact", "list_kind": "number", "level": 1}, "  1. Numbered fact"),
    ({"type": "list", "text": "Fact"}, "- Fact"),
    ({"type": "table", "text": "lossy fallback", "rows": [["A", "B"], ["1", "2"]]}, "A | B\n1 | 2"),
])
def test_complete_source_text_and_cells_are_preserved(block, expected):
    assert _render_complete_source_block(block) == expected


@pytest.mark.parametrize("block", [None, {"type": "paragraph"}, {"type": "table", "text": "fallback"},
                                       {"type": "table", "rows": [[1]]}, {"type": "unknown"}])
def test_incomplete_source_has_no_lossy_fallback(block):
    with pytest.raises(ValueError):
        _render_complete_source_block(block)

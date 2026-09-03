import copy
import json
from pathlib import Path

import pytest

from complex_page_experiment.director import _validate_director_value
from test_director import _compact_material_view, _director_value


def test_runtime_schema_represents_optional_edge_label_as_required_nullable() -> None:
    schema = json.loads(
        (Path(__file__).resolve().parents[2] / "schemas" / "consulting_page_director_v3.schema.json")
        .read_text(encoding="utf-8")
    )
    edge = schema["$defs"]["edge"]

    assert edge["required"] == ["from_node", "to_node", "label", "fact_ids"]
    assert edge["properties"]["label"]["type"] == ["string", "null"]


@pytest.mark.parametrize(
    "label",
    [pytest.param(None, id="null"), pytest.param("omitted", id="omitted")],
)
def test_runtime_adapter_accepts_null_or_omitted_optional_edge_label(label: object) -> None:
    value = copy.deepcopy(_director_value())
    edge = value["page_plan"]["primary_relationship"]["edges"][0]
    if label == "omitted":
        edge.pop("label", None)
    else:
        edge["label"] = label

    assert _validate_director_value(value, _compact_material_view()) == ()


def test_runtime_adapter_rejects_blank_non_null_edge_label() -> None:
    value = copy.deepcopy(_director_value())
    value["page_plan"]["primary_relationship"]["edges"][0]["label"] = "   "

    with pytest.raises(ValueError, match="edge label"):
        _validate_director_value(value, _compact_material_view())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"page_plan": None}),
        lambda value: value.update({"page_plan": []}),
        lambda value: value["page_plan"].update({"primary_relationship": None}),
        lambda value: value["page_plan"]["primary_relationship"].update({"edges": None}),
    ],
    ids=("null-page-plan", "list-page-plan", "null-relationship", "null-edges"),
)
def test_runtime_adapter_reports_malformed_structure_as_schema_value_error(mutate) -> None:
    value = copy.deepcopy(_director_value())
    mutate(value)

    with pytest.raises(ValueError, match="director schema rejected"):
        _validate_director_value(value, _compact_material_view())

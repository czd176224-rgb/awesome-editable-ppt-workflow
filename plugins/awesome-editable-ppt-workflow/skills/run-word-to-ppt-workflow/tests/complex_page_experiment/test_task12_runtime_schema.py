import json
from pathlib import Path


def test_runtime_schema_represents_optional_edge_label_as_required_nullable() -> None:
    schema = json.loads(
        (Path(__file__).resolve().parents[2] / "schemas" / "consulting_page_director_v3.schema.json")
        .read_text(encoding="utf-8")
    )
    edge = schema["$defs"]["edge"]

    assert edge["required"] == ["from_node", "to_node", "label", "fact_ids"]
    assert edge["properties"]["label"]["type"] == ["string", "null"]

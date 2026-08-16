"""Deterministically measure the generation complexity of a locked page contract."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping


@dataclass(frozen=True)
class PageComplexity:
    score: int
    band: Literal["simple", "medium", "complex"]
    weight: Literal[1, 2, 3]
    inputs: dict[str, int]


def _list_size(contract: Mapping[str, Any], key: str) -> int:
    value = contract.get(key, [])
    return len(value) if isinstance(value, list) else 0


def _table_cells(tables: object) -> int:
    if not isinstance(tables, list):
        return 0
    cells = 0
    for table in tables:
        if not isinstance(table, str):
            continue
        for line in table.splitlines():
            stripped = line.strip()
            if not stripped or "|" not in stripped:
                continue
            columns = [column.strip() for column in stripped.strip("|").split("|")]
            if columns and all(re.fullmatch(r":?-{3,}:?", column) for column in columns):
                continue
            cells += len(columns)
    return cells


def measure_contract(contract: Mapping[str, Any]) -> dict[str, int]:
    """Return only stable, locked-contract inputs used by :func:`classify_page`."""
    source_text = contract.get("source_text", "")
    proper_nouns = contract.get("proper_nouns", contract.get("named_entities", []))
    return {
        "characters": len(source_text) if isinstance(source_text, str) else 0,
        "semantic_units": _list_size(contract, "semantic_units"),
        "table_cells": _table_cells(contract.get("source_tables", [])),
        "relations": _list_size(contract, "explicit_relations"),
        "assets": _list_size(contract, "asset_bindings"),
        "numbers": _list_size(contract, "detected_numbers") + _list_size(contract, "detected_amounts"),
        "proper_nouns": len(proper_nouns) if isinstance(proper_nouns, list) else 0,
    }


def classify_page(contract: Mapping[str, Any]) -> PageComplexity:
    inputs = measure_contract(contract)
    score = (
        min(inputs["characters"] // 300, 4)
        + min(inputs["semantic_units"] // 5, 4)
        + min(inputs["table_cells"] // 12, 4)
        + min(inputs["relations"] // 3, 3)
        + min(inputs["assets"] * 2, 4)
        + min((inputs["numbers"] + inputs["proper_nouns"]) // 10, 3)
    )
    band, weight = ("simple", 1) if score <= 5 else (("medium", 2) if score <= 12 else ("complex", 3))
    return PageComplexity(score, band, weight, inputs)

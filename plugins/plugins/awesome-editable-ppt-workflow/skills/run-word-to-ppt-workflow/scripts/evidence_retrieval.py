"""Deterministic bounded retrieval over page-local attachment evidence."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Mapping


_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?(?:%|％|万|亿|万元|亿元)?")
_LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{1,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")


def _terms(text: str) -> set[str]:
    compact = "".join(str(text).casefold().split())
    values = set(_NUMBER_RE.findall(compact)) | set(_LATIN_RE.findall(compact))
    for sequence in _CJK_RE.findall(compact):
        if len(sequence) <= 12:
            values.add(sequence)
        values.update(sequence[index:index + 2] for index in range(len(sequence) - 1))
    return {value for value in values if value}


def _comment_text(contract: Mapping[str, Any]) -> str:
    values = []
    for comment in contract.get("page_comments", []):
        text = comment.get("text") if isinstance(comment, Mapping) else None
        if isinstance(text, str):
            values.append(text)
    return "\n".join(values)


def _explicitly_referenced(chunk: Mapping[str, Any], comments: str) -> bool:
    source = chunk.get("source")
    filename = source.get("file") if isinstance(source, Mapping) else None
    if not isinstance(filename, str) or not filename:
        return False
    normalized_comments = "".join(comments.casefold().split())
    filename = PurePosixPath(filename).name.casefold()
    stem = PurePosixPath(filename).stem.casefold()
    return filename in normalized_comments or (len(stem) >= 2 and stem in normalized_comments)


def _score(chunk: Mapping[str, Any], query_terms: set[str], comments: str) -> tuple[int, str]:
    if _explicitly_referenced(chunk, comments):
        return 100_000, "explicit_page_reference"
    text = chunk.get("text", "")
    source = chunk.get("source")
    filename = source.get("file", "") if isinstance(source, Mapping) else ""
    chunk_terms = _terms(f"{filename}\n{text}")
    overlap = query_terms & chunk_terms
    if not overlap:
        return 0, "not_relevant"
    number_overlap = set(_NUMBER_RE.findall(str(text))) & query_terms
    score = sum(min(len(term), 8) for term in overlap) + 20 * len(number_overlap)
    return score, "lexical_page_match"


def retrieve_page_evidence(
    contract: Mapping[str, Any],
    index: Mapping[str, Any],
    *,
    char_budget: int = 5000,
    max_chunks: int = 8,
) -> dict[str, Any]:
    if type(char_budget) is not int or char_budget < 1:
        raise ValueError("char_budget must be positive")
    if type(max_chunks) is not int or max_chunks < 1:
        raise ValueError("max_chunks must be positive")
    chunks = index.get("chunks", [])
    if not isinstance(chunks, list):
        raise ValueError("evidence index chunks must be an array")
    comments = _comment_text(contract)
    query = "\n".join(
        str(contract.get(field, "")) for field in ("page_title", "body_text", "source_text")
    ) + "\n" + comments
    query_terms = _terms(query)
    ranked: list[tuple[int, str, str, Mapping[str, Any]]] = []
    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            continue
        score, reason = _score(chunk, query_terms, comments)
        if score <= 0:
            continue
        ranked.append((-score, str(chunk.get("evidence_id", "")), reason, chunk))
    ranked.sort(key=lambda value: (value[0], value[1]))

    selected: list[dict[str, Any]] = []
    used = 0
    for _negative_score, _identity, reason, chunk in ranked:
        if len(selected) >= max_chunks or used >= char_budget:
            break
        text = str(chunk.get("text", ""))
        remaining = char_budget - used
        item = dict(chunk)
        if len(text) > remaining:
            if remaining < 1:
                break
            item["text"] = text[:remaining]
            item["truncated"] = True
        item["selection_reason"] = reason
        selected.append(item)
        used += len(item.get("text", ""))
    return {
        "schema_version": "1.0",
        "selected_chunks": selected,
        "selected_chars": used,
        "available_chars": sum(len(str(chunk.get("text", ""))) for chunk in chunks if isinstance(chunk, Mapping)),
        "char_budget": char_budget,
        "max_chunks": max_chunks,
    }


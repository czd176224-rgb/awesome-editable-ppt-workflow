from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from workflow_v6_contract import request_identity  # noqa: E402


def test_request_identity_is_canonical_and_order_sensitive() -> None:
    base = {
        "revision_digest": "a" * 64,
        "prompt_sha256": "b" * 64,
        "operation": "edit",
        "quality": "high",
        "input_sha256s": ["c" * 64, "d" * 64],
    }
    first = request_identity(**base)
    second = request_identity(**dict(reversed(list(base.items()))))
    reordered = request_identity(
        **{**base, "input_sha256s": list(reversed(base["input_sha256s"]))}
    )

    assert first == second
    assert len(first) == 64 and first == first.lower()
    assert reordered != first


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revision_digest", "A" * 64),
        ("prompt_sha256", "short"),
        ("operation", "transform"),
        ("quality", "low"),
        ("input_sha256s", ["e" * 64, "BAD"]),
    ],
)
def test_request_identity_rejects_noncanonical_inputs(field: str, value) -> None:
    payload = {
        "revision_digest": "a" * 64,
        "prompt_sha256": "b" * 64,
        "operation": "generate",
        "quality": "medium",
        "input_sha256s": [],
    }
    payload[field] = value

    with pytest.raises(ValueError):
        request_identity(**payload)

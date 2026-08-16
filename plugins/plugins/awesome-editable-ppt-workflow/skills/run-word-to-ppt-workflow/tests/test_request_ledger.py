from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))

from request_ledger import (  # noqa: E402
    claim_request,
    complete_request,
    request_identity,
)
from workflow_v6_contract import request_identity as adaptive_request_identity  # noqa: E402


def test_adaptive_request_identity_is_canonical_and_order_sensitive() -> None:
    base = {
        "revision_digest": "a" * 64,
        "prompt_sha256": "b" * 64,
        "operation": "edit",
        "quality": "high",
        "input_sha256s": ["c" * 64, "d" * 64],
    }

    first = adaptive_request_identity(**base)
    second = adaptive_request_identity(**dict(reversed(list(base.items()))))
    reordered = adaptive_request_identity(**{
        **base, "input_sha256s": list(reversed(base["input_sha256s"])),
    })

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
def test_adaptive_request_identity_rejects_noncanonical_inputs(field: str, value) -> None:
    payload = {
        "revision_digest": "a" * 64,
        "prompt_sha256": "b" * 64,
        "operation": "generate",
        "quality": "medium",
        "input_sha256s": [],
    }
    payload[field] = value

    with pytest.raises(ValueError):
        adaptive_request_identity(**payload)


def test_request_identity_is_stable_across_project_roots_and_rejects_absolute_paths(tmp_path: Path) -> None:
    first = request_identity("image2", {
        "material_sha256": "a" * 64,
        "artifact_path": "04_v4/material/page_001.json",
        "model": "gpt-image-2",
    })
    second = request_identity("image2", {
        "model": "gpt-image-2",
        "artifact_path": "04_v4/material/page_001.json",
        "material_sha256": "a" * 64,
    })

    assert first == second
    with pytest.raises(ValueError, match="relative"):
        request_identity("image2", {"artifact_path": str(tmp_path / "page.json")})


def test_completed_request_is_reused_without_a_second_claim(tmp_path: Path) -> None:
    identity = request_identity("image2", {"material_sha256": "a" * 64})
    first = claim_request(tmp_path, "image2", identity, owner="worker-a")
    assert first["status"] == "claimed"

    complete_request(
        tmp_path, "image2", identity, owner="worker-a",
        receipt={"path": "04_v4/generation/page_001.json", "sha256": "b" * 64},
    )
    resumed = claim_request(tmp_path, "image2", identity, owner="worker-b")

    assert resumed["status"] == "completed"
    assert resumed["receipt"]["sha256"] == "b" * 64


def test_active_claim_prevents_a_duplicate_provider_invocation(tmp_path: Path) -> None:
    identity = request_identity("search", {"material_id": "m-001", "query": "news photo"})
    assert claim_request(tmp_path, "search", identity, owner="worker-a")["status"] == "claimed"

    duplicate = claim_request(tmp_path, "search", identity, owner="worker-b")

    assert duplicate["status"] == "active"
    assert duplicate["owner"] == "worker-a"

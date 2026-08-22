"""Stable JSON hashing shared with editable-page cache validation."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    """Hash a JSON object using stable UTF-8 bytes."""
    try:
        encoded = json.dumps(
            _thaw(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("cache identity inputs must be finite JSON values") from exc
    return hashlib.sha256(encoded).hexdigest()

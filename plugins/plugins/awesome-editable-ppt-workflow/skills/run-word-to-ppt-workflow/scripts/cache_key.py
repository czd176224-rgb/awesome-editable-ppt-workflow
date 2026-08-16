"""Strict page-local cache identity for the current Word-only workflow."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from fixed_region_contract import CONTRACT_VERSION
from workflow_contract import WORKFLOW_VERSION


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


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


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True)
class CacheKeyInputs:
    """The complete and exclusive identity of one reconstructed page."""

    workflow_contract_version: str
    full_source_sha256: str
    style_execution_sha256: str
    page_asset_inputs: Any
    generation_parameters: Mapping[str, Any]
    repair_feedback: Mapping[str, Any]
    reconstruction_version: str
    geometry_version: str
    fixed_layer_version: str
    title_sha256: str
    logo_sha256: str
    page_number: int

    def __post_init__(self) -> None:
        if self.workflow_contract_version != WORKFLOW_VERSION:
            raise ValueError(f"workflow_contract_version must be {WORKFLOW_VERSION}")
        _sha256(self.full_source_sha256, "full_source_sha256")
        _sha256(self.style_execution_sha256, "style_execution_sha256")
        _sha256(self.title_sha256, "title_sha256")
        _sha256(self.logo_sha256, "logo_sha256")
        if not isinstance(self.page_asset_inputs, (list, tuple)):
            raise ValueError("page_asset_inputs must be a JSON array")
        if not isinstance(self.generation_parameters, Mapping) or not self.generation_parameters:
            raise ValueError("generation_parameters must be a non-empty JSON object")
        if not isinstance(self.repair_feedback, Mapping):
            raise ValueError("repair_feedback must be a JSON object")
        if not isinstance(self.reconstruction_version, str) or not self.reconstruction_version.strip():
            raise ValueError("reconstruction_version must be non-empty")
        if self.geometry_version != CONTRACT_VERSION:
            raise ValueError(f"geometry_version must be {CONTRACT_VERSION}")
        if not isinstance(self.fixed_layer_version, str) or not self.fixed_layer_version.strip():
            raise ValueError("fixed_layer_version must be non-empty")
        if type(self.page_number) is not int or self.page_number < 1:
            raise ValueError("page_number must be positive")
        object.__setattr__(self, "generation_parameters", _freeze(self.generation_parameters))
        object.__setattr__(self, "page_asset_inputs", _freeze(self.page_asset_inputs))
        object.__setattr__(self, "repair_feedback", _freeze(self.repair_feedback))

    @property
    def payload(self) -> dict[str, Any]:
        """Return the strict generation, geometry and fixed-layer identity."""
        return {
            "workflow_contract_version": self.workflow_contract_version,
            "full_source_sha256": self.full_source_sha256,
            "style_execution_sha256": self.style_execution_sha256,
            "page_asset_inputs": _thaw(self.page_asset_inputs),
            "generation_parameters": _thaw(self.generation_parameters),
            "repair_feedback": _thaw(self.repair_feedback),
            "reconstruction_version": self.reconstruction_version,
            "geometry_version": self.geometry_version,
            "fixed_layer_version": self.fixed_layer_version,
            "title_sha256": self.title_sha256,
            "logo_sha256": self.logo_sha256,
            "page_number": self.page_number,
        }


def build_page_cache_key(inputs: CacheKeyInputs) -> str:
    """Return the strict identity of a single page pipeline result."""
    if not isinstance(inputs, CacheKeyInputs):
        raise TypeError("inputs must be CacheKeyInputs")
    return canonical_sha256(inputs.payload)

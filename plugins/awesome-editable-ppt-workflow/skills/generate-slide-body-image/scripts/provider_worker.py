#!/usr/bin/env python3
"""Private one-shot Codex Images network worker.

The orchestrator passes one fully materialized, signed request over an inherited
anonymous pipe.  This file deliberately has no importable provider function and
no prompt/path command-line interface.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import sys
import time
from urllib import error, request
from urllib.parse import urlsplit
from provider_keyring import verification_key


MAX_FRAME = 96 * 1024 * 1024
MAX_RESPONSE = 64 * 1024 * 1024
OFFICIAL_HOST = "chatgpt.com"
OFFICIAL_PATHS = {
    "/backend-api/codex/images/generations",
    "/backend-api/codex/images/edits",
}


def _source_test_transport_enabled() -> bool:
    """Test transport exists only in a pytest source checkout, never a release install."""
    if os.environ.pop("AWESOME_PROVIDER_TEST_BUILD", "") != "1":
        return False
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return any((parent / ".git").exists() for parent in Path(__file__).resolve().parents)


def _provider_request(envelope: dict, body_bytes: bytes) -> request.Request:
    """Build the request with the established Codex image client identity."""
    return request.Request(
        envelope["url"],
        data=body_bytes,
        method="POST",
        headers={
            "Authorization": f"Bearer {envelope['access_token']}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "originator": "generate-slide-body-image",
            "User-Agent": "generate-slide-body-image-skill/0.1.0",
            **({"chatgpt-account-id": envelope["account_id"]} if envelope.get("account_id") else {}),
        },
    )


def _pairs_no_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_exact(handle, count):
    data = bytearray()
    while len(data) < count:
        chunk = os.read(handle, count - len(data))
        if not chunk:
            raise ValueError("inherited pipe ended before the framed request")
        data.extend(chunk)
    return bytes(data)


def _verify(envelope, key):
    signature = envelope.pop("hmac_sha256", None)
    expected = hmac.new(key, _canonical(envelope), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise ValueError("signed inherited request is invalid")
    now = int(time.time())
    if not (
        envelope.get("schema_version") == "awesome-provider-envelope-v1"
        and type(envelope.get("issued_at")) is int
        and type(envelope.get("not_before")) is int
        and type(envelope.get("expires_at")) is int
        and envelope["issued_at"] - 10 <= now <= envelope["expires_at"]
        and envelope["not_before"] <= now
        and 0 < envelope["expires_at"] - envelope["issued_at"] <= 300
    ):
        raise ValueError("signed inherited request time authority is invalid")
    body_bytes = base64.b64decode(envelope["body_bytes_b64"], validate=True)
    if hashlib.sha256(body_bytes).hexdigest() != envelope["body_sha256"]:
        raise ValueError("provider body bytes do not match signed authority")
    authority = envelope.get("authority")
    if not isinstance(authority, dict):
        raise ValueError("provider envelope lacks full authority")
    authority_signature = authority.get("hmac_sha256")
    unsigned_authority = dict(authority)
    unsigned_authority.pop("hmac_sha256", None)
    signing_key = verification_key(authority.get("key_id"))
    expected_authority = hmac.new(signing_key, _canonical(unsigned_authority), hashlib.sha256).hexdigest()
    if not isinstance(authority_signature, str) or not hmac.compare_digest(
        authority_signature, expected_authority
    ):
        raise ValueError("provider full authority signature is invalid")
    schema = authority.get("schema_version")
    if not (
        schema in {"awesome-image-request-capability-v3", "awesome-reconstruction-image-capability-v1"}
        and isinstance(authority.get("key_id"), str)
        and authority.get("issued_at") <= now <= authority.get("expires_at")
        and authority.get("not_before") <= now
        and 0 < authority.get("expires_at") - authority.get("issued_at") <= 300
    ):
        raise ValueError("provider full authority lifetime is invalid")
    body = json.loads(body_bytes.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates)
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or hashlib.sha256(prompt.encode("utf-8")).hexdigest() != authority.get("prompt_sha256"):
        raise ValueError("provider prompt differs from full authority")
    selected = authority.get("selected_references")
    if schema == "awesome-reconstruction-image-capability-v1":
        selected = [{"sha256": authority.get("accepted_image_sha256"),
                     "bytes_b64": authority.get("input_image_bytes_b64")}]
    if not isinstance(selected, list):
        raise ValueError("provider selected-reference authority is invalid")
    images = body.get("images", [])
    if len(images) != len(selected):
        raise ValueError("provider image count differs from full authority")
    for image, selected_item in zip(images, selected):
        if not isinstance(image, dict) or not isinstance(selected_item, dict):
            raise ValueError("provider image authority entry is invalid")
        data_url = image.get("image_url")
        if not isinstance(data_url, str) or "," not in data_url:
            raise ValueError("provider image payload is invalid")
        image_bytes = base64.b64decode(data_url.split(",", 1)[1], validate=True)
        if (hashlib.sha256(image_bytes).hexdigest() != selected_item.get("sha256")
                or base64.b64decode(selected_item.get("bytes_b64", ""), validate=True) != image_bytes):
            raise ValueError("provider image bytes differ from full authority")
    expected_operation = "edit" if selected else "generate"
    endpoint = urlsplit(envelope.get("url", ""))
    expected_path = "/backend-api/codex/images/edits" if selected else "/backend-api/codex/images/generations"
    if not (
        endpoint.scheme == "https" and endpoint.hostname == OFFICIAL_HOST
        and endpoint.port is None and endpoint.username is None and endpoint.password is None
        and endpoint.query == "" and endpoint.fragment == "" and endpoint.path == expected_path
        and endpoint.path in OFFICIAL_PATHS and authority.get("operation") == expected_operation
        and authority.get("official_endpoint") == f"https://{OFFICIAL_HOST}{expected_path}"
    ):
        raise ValueError("provider endpoint differs from full authority")
    for field in ("model", "size", "quality"):
        if body.get(field) != authority.get(field):
            raise ValueError(f"provider {field} differs from full authority")
    if schema == "awesome-reconstruction-image-capability-v1":
        if not (
            authority.get("purpose") == "asset-separation"
            and authority.get("output_kind") in {"foreground-sheet", "clean-base"}
            and authority.get("input_sha256s") == [authority.get("accepted_image_sha256")]
            and hashlib.sha256(prompt.encode("utf-8")).hexdigest() == authority.get("prompt_sha256")
            and base64.b64decode(authority.get("accepted_receipt_bytes_b64", ""), validate=True)
            and base64.b64decode(authority.get("ui_bytes_b64", ""), validate=True)
        ):
            raise ValueError("provider reconstruction authority is invalid")
        return body_bytes
    for field in ("project_identity", "source_authority", "page_state_authority",
                  "material_authority", "prompt_authority", "visual_contract_authority"):
        if not isinstance(authority.get(field), dict):
            raise ValueError(f"provider full authority lacks {field}")
    project_identity = authority["project_identity"]
    if project_identity != {
        "plugin_id": "awesome-editable-ppt-workflow", "plugin_version": "1.0.0",
        "workflow_contract": "awesome-word-ppt-workflow-v1",
        "source_identity": authority.get("source_identity"),
    }:
        raise ValueError("provider project identity is invalid")
    material = authority["material_authority"]
    material_bytes = base64.b64decode(material.get("bytes_b64", ""), validate=True)
    if hashlib.sha256(material_bytes).hexdigest() != material.get("sha256"):
        raise ValueError("provider material bytes differ from authority")
    material_value = json.loads(material_bytes.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates)
    if material_value.get("page_number") != authority.get("page_number"):
        raise ValueError("provider material page differs from authority")
    prompt_authority = authority["prompt_authority"]
    prompt_artifact = base64.b64decode(prompt_authority.get("prompt_bytes_b64", ""), validate=True)
    prompt_receipt_bytes = base64.b64decode(prompt_authority.get("receipt_bytes_b64", ""), validate=True)
    if hashlib.sha256(prompt_artifact).hexdigest() != prompt_authority.get("prompt_sha256"):
        raise ValueError("provider prompt artifact differs from authority")
    prompt_receipt = json.loads(prompt_receipt_bytes.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates)
    if not (
        prompt_receipt.get("prompt_output_sha256") == prompt_authority.get("prompt_sha256")
        and prompt_receipt.get("page_material_digest") == material.get("sha256")
        and prompt_receipt.get("selected_reference_ids") == authority.get("selected_reference_ids")
        and prompt_receipt.get("ui_digest") == authority["visual_contract_authority"].get("digest")
        and prompt_receipt.get("source_identity") == authority.get("source_identity")
    ):
        raise ValueError("provider prompt receipt does not close over full authority")
    return body_bytes


def _main():
    raw_fd = os.environ.pop("AWESOME_PROVIDER_PIPE_FD", "")
    raw_handle = os.environ.pop("AWESOME_PROVIDER_PIPE_HANDLE", "")
    raw_key = os.environ.pop("AWESOME_PROVIDER_PIPE_KEY", "")
    if not raw_fd or not raw_key:
        raise ValueError("provider worker requires an inherited anonymous pipe")
    if os.name == "nt" and raw_handle:
        import msvcrt
        fd = msvcrt.open_osfhandle(int(raw_handle), os.O_RDONLY | getattr(os, "O_BINARY", 0))
    else:
        fd = int(raw_fd)
    key = base64.b64decode(raw_key, validate=True)
    header = _read_exact(fd, 8)
    size = int.from_bytes(header, "big")
    if not 1 <= size <= MAX_FRAME:
        raise ValueError("inherited pipe frame length is invalid")
    envelope = json.loads(_read_exact(fd, size).decode("utf-8"), object_pairs_hook=_pairs_no_duplicates)
    body_bytes = _verify(envelope, key)
    req = _provider_request(envelope, body_bytes)
    try:
        test_response = os.environ.pop("AWESOME_PROVIDER_TEST_RESPONSE_B64", "")
        if test_response:
            if not _source_test_transport_enabled():
                raise ValueError("provider test transport is unavailable in release execution")
            class _TestResponse:
                def __enter__(self): return self
                def __exit__(self, *_args): return False
                def read(self, _limit): return base64.b64decode(test_response, validate=True)
            response_context = _TestResponse()
        else:
            response_context = request.urlopen(req, timeout=int(envelope["timeout"]))
        with response_context as response:
            response_bytes = response.read(MAX_RESPONSE + 1)
    except error.HTTPError as exc:
        response_bytes = exc.read(4096)
        result = {"ok": False, "status_code": exc.code,
                  "network_started": True, "response_bytes_b64": base64.b64encode(response_bytes).decode("ascii")}
    except error.URLError as exc:
        result = {"ok": False, "status_code": None, "network_started": True,
                  "network_error": str(exc.reason)}
    else:
        if len(response_bytes) > MAX_RESPONSE:
            raise ValueError("provider response exceeded limit")
        result = {"ok": True, "status_code": 200, "network_started": True,
                  "response_bytes_b64": base64.b64encode(response_bytes).decode("ascii")}
    sys.stdout.buffer.write(_canonical(result))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main())
    except Exception as exc:
        print(f"provider worker pipe error: {exc}", file=sys.stderr)
        raise SystemExit(2)

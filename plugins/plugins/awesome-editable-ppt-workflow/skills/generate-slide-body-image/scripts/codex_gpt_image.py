#!/usr/bin/env python3
"""Generate GPT Image outputs through Codex OAuth.

This CLI uses the local Codex
OAuth session, then calls the Codex Images backend used by Codex-integrated
agents.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import subprocess
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import json
import mimetypes
import os
import stat
from pathlib import Path
import re
import sys
import time
from typing import Any
import webbrowser

from PIL import Image, PngImagePlugin
from provider_keyring import signing_key, verification_key

WORKFLOW_SCRIPTS = Path(__file__).resolve().parents[2] / "run-word-to-ppt-workflow" / "scripts"
if str(WORKFLOW_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(WORKFLOW_SCRIPTS))
import workflow_v6_secure_io as secure_io  # noqa: E402


DEFAULT_CODEX_AUTH_FILE = "~/.codex/auth.json"
DEFAULT_CODEX_IMAGES_BASE_URL = "https://chatgpt.com/backend-api/codex"
OPENAI_AUTH_BASE_URL = "https://auth.openai.com"
# Public OAuth client id used by official Codex login; override with
# CODEX_APP_SERVER_LOGIN_CLIENT_ID or --client-id for staging/private clients.
DEFAULT_OPENAI_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_CLIENT_ID_ENV_VAR = "CODEX_APP_SERVER_LOGIN_CLIENT_ID"
OPENAI_CODEX_DEVICE_CALLBACK_URL = f"{OPENAI_AUTH_BASE_URL}/deviceauth/callback"
DEFAULT_IMAGE_MODEL = "gpt-image-2"
DEFAULT_SIZE = "auto"
DEFAULT_QUALITY = "auto"
DEFAULT_OUTPUT_FORMAT = "png"
DEFAULT_OUTPUT_COMPRESSION = 100
DEFAULT_BACKGROUND = "auto"
DEFAULT_MODERATION = "auto"
DEFAULT_TIMEOUT = 600
DEFAULT_COUNT = 1
DEVICE_CODE_TIMEOUT = 15 * 60
DEVICE_CODE_DEFAULT_INTERVAL = 5
DEVICE_CODE_MIN_INTERVAL = 1
MAX_COUNT = 10
MAX_INPUT_IMAGES = 16
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_BASE64_CHARS = 64 * 1024 * 1024
MAX_IMAGE_DATA_URL_CHARS = 20_971_520
SUPPORTED_QUALITIES = {"low", "medium", "high", "auto"}
SUPPORTED_OUTPUT_FORMATS = {"png", "jpeg", "jpg", "webp"}
SUPPORTED_BACKGROUNDS = {"opaque", "auto"}
SUPPORTED_MODERATIONS = {"low", "auto"}
CHATGPT_AUTH_CLAIM = "https://api.openai.com/auth"
CHATGPT_ACCOUNT_ID_CLAIM = "chatgpt_account_id"
GPT_IMAGE_2_MIN_PIXELS = 655_360
GPT_IMAGE_2_MAX_PIXELS = 8_294_400
GPT_IMAGE_2_MAX_EDGE = 3840
GPT_IMAGE_2_MAX_RATIO = 3.0


class CliError(RuntimeError):
    def __init__(
        self, message: str, *, status_code: int | None = None, network: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.network = network


@dataclass
class DeviceCode:
    device_auth_id: str
    user_code: str
    verification_url: str
    interval: int


@dataclass
class DeviceAuthorization:
    authorization_code: str
    code_verifier: str


@dataclass
class CodexAuth:
    access_token: str
    account_id: str | None = None
    last_refresh: str | None = None


@dataclass(frozen=True)
class LoadedImage:
    path: Path
    data: bytes
    sha256: str


@dataclass(frozen=True)
class AuthorityContext:
    capability: dict[str, Any]
    journal: Path
    project: Path
    output: Path
    trace: Path


def _authority_bytes(authority: AuthorityContext, path: Path, *, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    try:
        relative = path.relative_to(authority.project)
    except ValueError as exc:
        raise CliError("authority artifact is outside the project") from exc
    try:
        return secure_io.read_bytes(authority.project, relative, max_bytes=max_bytes)
    except (OSError, ValueError) as exc:
        raise CliError("authority artifact is unavailable or unsafe") from exc


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def die(message: str, code: int = 1) -> None:
    eprint(f"Error: {message}")
    raise SystemExit(code)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CliError(f"File not found: {path}") from exc
    except OSError as exc:
        raise CliError(f"Cannot read {path}: {exc}") from exc


def codex_auth_file() -> Path:
    configured = os.getenv("CODEX_AUTH_FILE")
    if configured:
        return Path(configured).expanduser()
    codex_home = os.getenv("CODEX_HOME")
    if codex_home:
        candidate = Path(codex_home).expanduser() / "auth.json"
        if candidate.is_file():
            return candidate
    return Path(DEFAULT_CODEX_AUTH_FILE).expanduser()


def _secure_read(path: Path, *, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    if path.is_symlink():
        raise CliError(f"Refusing linked authority file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
                raise CliError(f"Authority file is not a bounded regular file: {path}")
            data = handle.read(max_bytes + 1)
    except CliError:
        raise
    except OSError as exc:
        raise CliError(f"Cannot securely read authority file: {path}") from exc
    if len(data) > max_bytes:
        raise CliError(f"Authority file exceeds limit: {path}")
    return data


def openai_codex_client_id(args: argparse.Namespace) -> str:
    raw = (
        getattr(args, "client_id", None)
        or os.getenv(CODEX_CLIENT_ID_ENV_VAR)
        or DEFAULT_OPENAI_CODEX_CLIENT_ID
    )
    client_id = str(raw).strip()
    if not client_id:
        raise CliError("Codex OAuth client id is empty.")
    return client_id


def auth_headers(content_type: str) -> dict[str, str]:
    return {"Content-Type": content_type}


def post_json(*_args, **_kwargs):
    raise CliError("OAuth login networking is not exposed by this plugin; use the Codex login flow.")


def post_form(*_args, **_kwargs):
    raise CliError("OAuth login networking is not exposed by this plugin; use the Codex login flow.")


def decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def infer_account_id_from_tokens(tokens: dict[str, Any]) -> str | None:
    account_id = tokens.get("account_id")
    if isinstance(account_id, str) and account_id.strip():
        return account_id.strip()

    id_token = tokens.get("id_token")
    if not isinstance(id_token, str) or not id_token.strip():
        return None

    auth_claim = decode_jwt_payload(id_token).get(CHATGPT_AUTH_CLAIM)
    if not isinstance(auth_claim, dict):
        return None
    chatgpt_account_id = auth_claim.get(CHATGPT_ACCOUNT_ID_CLAIM)
    if isinstance(chatgpt_account_id, str) and chatgpt_account_id.strip():
        return chatgpt_account_id.strip()
    return None


def request_device_code(timeout: int, client_id: str) -> DeviceCode:
    data = post_json(
        f"{OPENAI_AUTH_BASE_URL}/api/accounts/deviceauth/usercode",
        {"client_id": client_id},
        timeout,
    )
    device_auth_id = str(data.get("device_auth_id") or "").strip()
    user_code = str(data.get("user_code") or data.get("usercode") or "").strip()
    interval_raw = data.get("interval")
    try:
        interval = int(interval_raw)
    except (TypeError, ValueError):
        interval = DEVICE_CODE_DEFAULT_INTERVAL
    if not device_auth_id or not user_code:
        raise CliError("Device-code response did not include device_auth_id and user_code.")
    return DeviceCode(
        device_auth_id=device_auth_id,
        user_code=user_code,
        verification_url=f"{OPENAI_AUTH_BASE_URL}/codex/device",
        interval=max(DEVICE_CODE_MIN_INTERVAL, interval),
    )


def poll_device_code(device_code: DeviceCode, timeout: int) -> DeviceAuthorization:
    deadline = time.time() + DEVICE_CODE_TIMEOUT
    while time.time() < deadline:
        try:
            data = post_json(
                f"{OPENAI_AUTH_BASE_URL}/api/accounts/deviceauth/token",
                {
                    "device_auth_id": device_code.device_auth_id,
                    "user_code": device_code.user_code,
                },
                timeout,
            )
        except CliError as exc:
            text = str(exc)
            if "HTTP 403" in text or "HTTP 404" in text:
                time.sleep(min(device_code.interval, max(DEVICE_CODE_MIN_INTERVAL, int(deadline - time.time()))))
                continue
            raise
        authorization_code = str(data.get("authorization_code") or "").strip()
        code_verifier = str(data.get("code_verifier") or "").strip()
        if not authorization_code or not code_verifier:
            raise CliError("Device authorization response did not include exchange code fields.")
        return DeviceAuthorization(
            authorization_code=authorization_code,
            code_verifier=code_verifier,
        )
    raise CliError("OpenAI Codex device authorization timed out after 15 minutes.")


def exchange_device_code(authz: DeviceAuthorization, timeout: int, client_id: str) -> dict[str, Any]:
    data = post_form(
        f"{OPENAI_AUTH_BASE_URL}/oauth/token",
        {
            "grant_type": "authorization_code",
            "code": authz.authorization_code,
            "redirect_uri": OPENAI_CODEX_DEVICE_CALLBACK_URL,
            "client_id": client_id,
            "code_verifier": authz.code_verifier,
        },
        timeout,
    )
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    if not isinstance(access, str) or not access.strip():
        raise CliError("Token exchange succeeded but did not return access_token.")
    if not isinstance(refresh, str) or not refresh.strip():
        raise CliError("Token exchange succeeded but did not return refresh_token.")
    return data


def write_codex_auth(tokens: dict[str, Any]) -> Path:
    path = codex_auth_file()
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(read_text(path))
            if isinstance(loaded, dict):
                existing = loaded
        except Exception:
            existing = {}
    access = str(tokens["access_token"]).strip()
    refresh = str(tokens["refresh_token"]).strip()
    account_id = infer_account_id_from_tokens(tokens)
    existing["auth_mode"] = "chatgpt"
    existing["last_refresh"] = datetime.now(timezone.utc).isoformat()
    existing["tokens"] = {
        "access_token": access,
        "refresh_token": refresh,
        **({"id_token": tokens["id_token"]} if isinstance(tokens.get("id_token"), str) else {}),
        **({"account_id": account_id} if account_id else {}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def login_device_code(args: argparse.Namespace) -> CodexAuth:
    eprint("Requesting Codex device code...")
    client_id = openai_codex_client_id(args)
    device_code = request_device_code(args.timeout, client_id)
    print()
    print("Open this URL in your browser and enter the code:")
    print(f"URL:  {device_code.verification_url}")
    print(f"Code: {device_code.user_code}")
    print("The code expires in about 15 minutes. Never share it.")
    print()
    if args.open_browser:
        try:
            webbrowser.open(device_code.verification_url)
        except Exception:
            eprint(f"Could not open browser automatically. Open manually: {device_code.verification_url}")
    eprint("Waiting for browser authorization...")
    authz = poll_device_code(device_code, args.timeout)
    eprint("Exchanging device code for Codex OAuth tokens...")
    tokens = exchange_device_code(authz, args.timeout, client_id)
    path = write_codex_auth(tokens)
    eprint(f"Codex OAuth saved to {path}.")
    return load_codex_auth()


def load_or_login_codex_auth(args: argparse.Namespace) -> CodexAuth:
    try:
        return load_codex_auth()
    except CliError:
        if getattr(args, "login_if_missing", False):
            return login_device_code(args)
        raise


def load_codex_auth() -> CodexAuth:
    path = codex_auth_file()
    if not path.exists():
        raise CliError(
            f"Codex auth file not found: {path}. Run `codex login` or sign in with Codex first."
        )
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        raise CliError(f"Codex auth file is not valid JSON: {path}") from exc

    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        raise CliError(f"Codex auth file has no tokens object: {path}")
    token = tokens.get("access_token")
    if not isinstance(token, str) or not token.strip():
        raise CliError(
            f"Codex access token is missing in {path}. Run `codex login` again."
        )
    account_id = tokens.get("account_id")
    return CodexAuth(
        access_token=token.strip(),
        account_id=account_id if isinstance(account_id, str) else None,
        last_refresh=data.get("last_refresh") if isinstance(data.get("last_refresh"), str) else None,
    )


def redact(value: str | None) -> str | None:
    if not value:
        return value
    if len(value) <= 8:
        return "<redacted>"
    return f"{value[:4]}...{value[-4:]}"


def canonicalize_codex_base_url(base_url: str | None) -> str:
    raw = (
        base_url
        or os.getenv("CODEX_IMAGES_BASE_URL")
        or DEFAULT_CODEX_IMAGES_BASE_URL
    ).strip()
    if not raw:
        return DEFAULT_CODEX_IMAGES_BASE_URL
    if re.fullmatch(r"https?://chatgpt\.com/backend-api(?:/codex)?(?:/v1)?/?", raw, re.I):
        return DEFAULT_CODEX_IMAGES_BASE_URL
    return raw.rstrip("/")


def image_endpoint_url(base_url: str | None, operation: str) -> str:
    endpoint = image_endpoint(operation)
    return f"{canonicalize_codex_base_url(base_url)}/{endpoint}"


def image_endpoint(operation: str) -> str:
    """Map the two supported image operations to their traceable API paths."""
    if operation == "edit":
        return "images/edits"
    if operation == "generate":
        return "images/generations"
    raise CliError("image operation must be generate or edit.")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_generation_trace(
    args: argparse.Namespace,
    operation: str,
    model: str,
    image_paths: list[str],
    outputs: list[Path],
    authenticated: bool,
    authority_project: Path,
    loaded_images: list[LoadedImage] | None = None,
) -> None:
    if not args.trace_out:
        return
    roles = args.image_role or []
    if roles and len(roles) != len(image_paths):
        raise CliError("--image-role count must match --image count when writing a trace.")
    if not roles:
        roles = [f"reference_{index}" for index in range(1, len(image_paths) + 1)]
    warnings: list[dict[str, Any]] = []
    requested_size = parse_size(str(getattr(args, "size", "auto")))
    if (
        getattr(args, "allow_off_ratio_for_downstream_repair", False)
        and requested_size is not None
    ):
        for path in outputs:
            with Image.open(path) as source:
                actual_size = source.size
                original_size = source.info.get("awesome_provider_original_size")
                original_quality = source.info.get("awesome_provider_original_quality")
                crop_box_json = source.info.get("awesome_center_17_8_crop_box")
            if (
                isinstance(original_size, str)
                and re.fullmatch(r"\d+x\d+", original_size)
                and isinstance(original_quality, str)
                and isinstance(crop_box_json, str)
            ):
                source_width, source_height = (int(value) for value in original_size.split("x", 1))
                try:
                    crop_box = json.loads(crop_box_json, object_pairs_hook=_pairs_no_duplicates)
                except Exception as exc:
                    raise CliError("Generated frame-adaptation metadata is invalid.") from exc
                if set(crop_box) != {"left", "top", "right", "bottom"}:
                    raise CliError("Generated frame-adaptation crop box is invalid.")
                warnings.append({
                    "code": "centered_17_8_frame_adaptation",
                    "output": str(path.resolve()),
                    "requested_size": {
                        "width": requested_size[0], "height": requested_size[1],
                    },
                    "provider_original_size": {"width": source_width, "height": source_height},
                    "provider_original_quality": original_quality,
                    "crop_box": crop_box,
                    "crop_ratio": {"width": 17, "height": 8, "decimal": 2.125},
                    "cropped": crop_box != {
                        "left": 0.0, "top": 0.0,
                        "right": float(source_width), "bottom": float(source_height),
                    },
                    "final_size": {"width": actual_size[0], "height": actual_size[1]},
                    "scaling": {"mode": "uniform", "resampling": "lanczos", "stretched": False},
                })
            elif _ratio_delta(actual_size, requested_size) > 0.005:
                warnings.append({
                    "code": "off_ratio_preserved_for_downstream_repair",
                    "output": str(path.resolve()),
                    "actual_size": {"width": actual_size[0], "height": actual_size[1]},
                    "requested_size": {
                        "width": requested_size[0], "height": requested_size[1],
                    },
                })
    payload = {
        "operation": operation,
        "endpoint": image_endpoint(operation),
        "model": model,
        "size": str(getattr(args, "size", "auto")),
        "quality": str(getattr(args, "quality", "auto")),
        "auth": "codex_oauth" if authenticated else "not_authenticated_dry_run",
        "input_images": [
            {"role": role, "path": str(loaded.path.resolve()), "sha256": loaded.sha256}
            for role, loaded in zip(roles, loaded_images)
        ] if loaded_images is not None else [
            {"role": role, "path": str(Path(raw).resolve()), "sha256": file_sha256(Path(raw))}
            for role, raw in zip(roles, image_paths)
        ],
        "outputs": [
            {
                "path": str(path.resolve()),
                "sha256": file_sha256(path),
                "mime_type": decoded_output_mime(path),
            }
            for path in outputs
        ],
    }
    if warnings:
        payload["warnings"] = warnings
    destination = Path(args.trace_out)
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    secure_io.atomic_write_bytes(
        authority_project, destination.relative_to(authority_project), data,
    )


def decoded_output_mime(path: Path) -> str:
    try:
        with Image.open(path) as image:
            image_format = image.format
            image.verify()
    except (OSError, ValueError) as exc:
        raise CliError(f"Generated output is not a valid supported image: {path}") from exc
    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(
        str(image_format).upper(),
    )
    if mime is None:
        raise CliError(f"Generated output must be PNG, JPEG, or WebP: {path}")
    return mime


def read_prompt(prompt: str | None, prompt_file: str | None) -> str:
    if prompt and prompt_file:
        raise CliError("Use --prompt or --prompt-file, not both.")
    if prompt_file:
        text = read_text(Path(prompt_file)).strip()
    elif prompt:
        text = prompt.strip()
    else:
        raise CliError("Missing prompt. Use --prompt or --prompt-file.")
    if not text:
        raise CliError("Prompt is empty.")
    return text


def require_workflow_capability(args: argparse.Namespace, prompt: str, loaded_images: list[LoadedImage]) -> AuthorityContext:
    raw = getattr(args, "request_capability", None)
    if not raw:
        raise CliError("Image provider is private to the sealed Awesome workflow request gate.")
    path = Path(raw)
    project_raw = getattr(args, "workflow_project", None)
    page_number = getattr(args, "workflow_page", None)
    if not project_raw or type(page_number) is not int or page_number < 1:
        raise CliError("Image request capability is missing its workflow project/page binding.")
    secure_io.reject_reparse_chain(Path(project_raw))
    project = Path(project_raw).resolve(strict=True)
    try:
        path.resolve(strict=True).relative_to(project / "04_v6" / "image_request_capabilities")
    except (OSError, ValueError) as exc:
        raise CliError("Image request capability is outside the workflow authority.") from exc
    try:
        capability = json.loads(
            _secure_read(path, max_bytes=96 * 1024 * 1024).decode("utf-8"),
            object_pairs_hook=_pairs_no_duplicates,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CliError("Image request capability is unavailable or invalid.") from exc
    if capability.get("schema_version") != "awesome-image-request-capability-v3":
        raise CliError("Image request capability version is invalid.")
    signature = capability.pop("hmac_sha256", None)
    try:
        secret = verification_key(capability.get("key_id"))
    except (OSError, ValueError) as exc:
        raise CliError("Image request signing authority is unavailable.") from exc
    unsigned = json.dumps(capability, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    expected_signature = hmac.new(secret, unsigned, hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected_signature):
        raise CliError("Image request capability signature is invalid.")
    # Preserve the original signed canonical capability through the worker boundary.
    capability["hmac_sha256"] = signature
    now = int(time.time())
    if not (
        type(capability.get("issued_at")) is int
        and type(capability.get("not_before")) is int
        and type(capability.get("expires_at")) is int
        and capability["not_before"] <= now <= capability["expires_at"]
        and 0 < capability["expires_at"] - capability["issued_at"] <= 300
    ):
        raise CliError("Image request capability has expired.")
    binding = hashlib.sha256(str(project).encode("utf-8")).hexdigest()
    if capability.get("project_binding") != binding:
        raise CliError("Image request capability belongs to another project.")
    nonce = capability.get("nonce")
    if not isinstance(nonce, str) or not nonce:
        raise CliError("Image request capability nonce is invalid.")
    expected = getattr(args, "prompt_sha256", None)
    actual = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if not expected or expected != actual or capability.get("prompt_sha256") != actual:
        raise CliError("Prompt bytes do not match the sealed image request capability.")
    try:
        state = json.loads(secure_io.read_bytes(project, Path("workflow_v6.json")).decode("utf-8"), object_pairs_hook=_pairs_no_duplicates)
        prompt_receipt = json.loads(secure_io.read_bytes(
            project, Path("02_v6") / "page_image_prompts" / f"page_{page_number:03d}.receipt.json"
        ).decode("utf-8"), object_pairs_hook=_pairs_no_duplicates)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CliError("Workflow authority for the image request is unavailable.") from exc
    source_identity = hashlib.sha256(json.dumps({
        "logo_source": state.get("logo_source"), "word_source": state.get("word_source"),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if state.get("source_identity") != source_identity or capability.get("source_identity") != source_identity:
        raise CliError("Image request source identity is invalid.")
    if capability.get("page_number") != page_number:
        raise CliError("Image request page identity is invalid.")
    for key in ("plugin_id", "plugin_version", "workflow_contract", "source_identity",
                "ui_revision", "ui_digest", "page_material_digest", "prompt_output_sha256"):
        expected_value = prompt_receipt.get(key)
        if capability.get(key) != expected_value:
            raise CliError(f"Image request capability does not match sealed {key} authority.")
    if capability.get("operation") != args.declared_operation or capability.get("model") != args.model:
        raise CliError("Image request operation or model does not match its capability.")
    if capability.get("size") != args.size or capability.get("quality") != args.quality:
        raise CliError("Image request size or quality does not match its capability.")
    if capability.get("input_sha256s") != [image.sha256 for image in loaded_images]:
        raise CliError("Image request inputs do not match their capability.")
    if capability.get("image_roles") != (args.image_role or []):
        raise CliError("Image request roles do not match their capability.")
    return AuthorityContext(
        capability=capability,
        journal=project / "04_v6" / "image_request_capabilities" / "journal" / f"{nonce}.json",
        project=project,
        output=project / capability["output_path"],
        trace=project / capability["trace_path"],
    )


def require_reconstruction_capability(args: argparse.Namespace) -> tuple[AuthorityContext, str, LoadedImage]:
    """Validate the sole reconstruction edit authority and its accepted image bytes."""
    secure_io.reject_reparse_chain(Path(args.workflow_project))
    project = Path(args.workflow_project).resolve(strict=True)
    path = Path(args.request_capability).resolve(strict=True)
    try:
        path.relative_to(project / "04_v6" / "reconstruction_capabilities")
    except ValueError as exc:
        raise CliError("Reconstruction capability is outside project authority.") from exc
    try:
        capability = json.loads(_secure_read(path, max_bytes=96 * 1024 * 1024), object_pairs_hook=_pairs_no_duplicates)
    except Exception as exc:
        raise CliError("Reconstruction capability is invalid.") from exc
    signature = capability.pop("hmac_sha256", None)
    expected = hmac.new(verification_key(capability.get("key_id")), json.dumps(
        capability, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise CliError("Reconstruction capability signature is invalid.")
    capability["hmac_sha256"] = signature
    now = int(time.time())
    if not (capability.get("schema_version") == "awesome-reconstruction-image-capability-v1"
            and capability.get("operation") == "edit" and capability.get("purpose") == "asset-separation"
            and isinstance(capability.get("key_id"), str)
            and capability.get("not_before") <= now <= capability.get("expires_at")
            and 0 < capability.get("expires_at") - capability.get("issued_at") <= 300):
        raise CliError("Reconstruction capability authority is invalid or expired.")
    if capability.get("project_binding") != hashlib.sha256(str(project).encode("utf-8")).hexdigest():
        raise CliError("Reconstruction capability belongs to another project.")
    state_bytes = secure_io.read_bytes(project, Path("workflow_v6.json"))
    state = json.loads(state_bytes, object_pairs_hook=_pairs_no_duplicates)
    ui_bytes = json.dumps(state["style_confirmation"]["contract"], ensure_ascii=False,
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
    if (state.get("source_identity") != capability.get("source_identity")
            or state.get("confirmed_ui_revision") != capability.get("ui_revision")
            or state.get("confirmed_ui_digest") != capability.get("ui_digest")
            or base64.b64decode(capability.get("ui_bytes_b64", ""), validate=True) != ui_bytes):
        raise CliError("Reconstruction project/UI authority changed.")
    receipt = project / "04_v6" / "images" / f"page_{capability['page_number']:03d}.json"
    receipt_bytes = secure_io.read_bytes(project, receipt.relative_to(project))
    if (hashlib.sha256(receipt_bytes).hexdigest() != capability.get("accepted_receipt_sha256")
            or base64.b64decode(capability.get("accepted_receipt_bytes_b64", ""), validate=True) != receipt_bytes):
        raise CliError("Accepted page receipt changed before reconstruction.")
    image_path = project / capability["accepted_image_path"]
    image_bytes = secure_io.read_bytes(project, image_path.relative_to(project))
    digest = hashlib.sha256(image_bytes).hexdigest()
    if (digest != capability.get("accepted_image_sha256")
            or base64.b64decode(capability.get("input_image_bytes_b64", ""), validate=True) != image_bytes):
        raise CliError("Accepted page image changed before reconstruction.")
    expected_output = Path("05_v6") / "reconstruction_assets" / f"page_{capability['page_number']:03d}.{capability['output_kind']}.png"
    expected_trace = expected_output.with_suffix(".trace.json")
    if capability.get("output_path") != expected_output.as_posix() or capability.get("trace_path") != expected_trace.as_posix():
        raise CliError("Reconstruction output authority is invalid.")
    authority = AuthorityContext(capability, project / "04_v6" / "image_request_capabilities" / "journal" / f"{capability['nonce']}.json",
                                 project, project / expected_output, project / expected_trace)
    return authority, capability["prompt"], LoadedImage(image_path, image_bytes, digest)


def _pairs_no_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _atomic_journal(path: Path, value: dict[str, Any]) -> None:
    payload = dict(value)
    capability_digest = payload.get("capability_sha256")
    if not isinstance(capability_digest, str):
        raise CliError("submission journal lacks capability binding")
    key_id, key = signing_key()
    payload["key_id"] = key_id
    payload.pop("journal_hmac_sha256", None)
    unsigned = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["journal_hmac_sha256"] = hmac.new(key, unsigned, hashlib.sha256).hexdigest()
    relative = path.relative_to(_journal_project(path))
    secure_io.atomic_write_bytes(
        _journal_project(path), relative,
        (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
        replace=path.exists(),
    )


def _journal_project(path: Path) -> Path:
    parts = path.parts
    try:
        index = parts.index("04_v6")
    except ValueError as exc:
        raise CliError("submission journal is outside canonical project storage") from exc
    return Path(*parts[:index])


def _verified_journal(authority: AuthorityContext) -> dict[str, Any]:
    try:
        prior = json.loads(
            secure_io.read_bytes(authority.project, authority.journal.relative_to(authority.project)).decode("utf-8"),
            object_pairs_hook=_pairs_no_duplicates,
        )
    except Exception as exc:
        raise CliError("image submission journal is corrupt") from exc
    signature = prior.pop("journal_hmac_sha256", None)
    capability_digest = hashlib.sha256(json.dumps(
        authority.capability, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    if prior.get("capability_sha256") != capability_digest:
        raise CliError("image submission journal capability binding is invalid")
    expected_fields = {
        "nonce": authority.capability.get("nonce"),
        "attempt": authority.capability.get("attempt"),
        "operation": authority.capability.get("operation"),
        "input_sha256s": authority.capability.get("input_sha256s"),
    }
    if any(prior.get(key) != value for key, value in expected_fields.items()):
        raise CliError("image submission journal request binding is invalid")
    try:
        journal_key = verification_key(prior.get("key_id"))
    except (OSError, ValueError) as exc:
        raise CliError("image submission journal signing key is revoked or unknown") from exc
    expected = hmac.new(journal_key, json.dumps(
        prior, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise CliError("image submission journal signature is invalid")
    prior["journal_hmac_sha256"] = signature
    return prior


def _claim_submission(authority: AuthorityContext) -> dict[str, Any]:
    """Claim a capability exactly once using create-new journal semantics."""
    path = authority.journal
    if path.exists():
        try:
            prior = _verified_journal(authority)
        except Exception as exc:
            raise CliError("image submission journal is corrupt") from exc
        if prior.get("state") != "issued":
            raise CliError("Image request capability is already submitting or was consumed.")
        generation = int(prior.get("generation", 0)) + 1
        owner = f"{os.getpid()}-{time.time_ns()}"
        claimed = {**prior, "generation": generation, "owner": owner,
                   "state": "submitting", "network_started": False}
        # Cross-process replacement remains serialized by the workflow page lease.
        _atomic_journal(path, claimed)
        return claimed
    owner = f"{os.getpid()}-{time.time_ns()}"
    capability_digest = hashlib.sha256(json.dumps(authority.capability, ensure_ascii=False,
                                                   sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    claimed = {"schema_version": "awesome-image-submission-v2",
               "nonce": authority.capability["nonce"], "attempt": authority.capability["attempt"],
               "operation": authority.capability["operation"],
               "input_sha256s": authority.capability["input_sha256s"],
               "capability_sha256": capability_digest,
               "generation": 1, "owner": owner, "state": "submitting", "network_started": False}
    payload = dict(claimed)
    key_id, journal_key = signing_key()
    payload["key_id"] = key_id
    payload["journal_hmac_sha256"] = hmac.new(journal_key, json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"), hashlib.sha256).hexdigest()
    data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        secure_io.atomic_write_bytes(authority.project, path.relative_to(authority.project), data)
    except FileExistsError as exc:
        raise CliError("Image request capability is already submitting or was consumed.") from exc
    return claimed


def normalize_output_format(value: str | None) -> str:
    fmt = (value or DEFAULT_OUTPUT_FORMAT).lower()
    if fmt not in SUPPORTED_OUTPUT_FORMATS:
        raise CliError("output format must be png, jpeg, jpg, or webp.")
    return "jpeg" if fmt == "jpg" else fmt


def validate_quality(value: str) -> None:
    if value not in SUPPORTED_QUALITIES:
        raise CliError("quality must be low, medium, high, or auto.")


def validate_background(value: str | None) -> None:
    if value is not None and value not in SUPPORTED_BACKGROUNDS:
        raise CliError("background must be auto or opaque.")


def validate_moderation(value: str | None) -> None:
    if value is not None and value not in SUPPORTED_MODERATIONS:
        raise CliError("moderation must be low or auto.")


def validate_output_compression(value: int | None, output_format: str) -> None:
    if value is None:
        return
    if value < 0 or value > 100:
        raise CliError("output-compression must be between 0 and 100.")
    if output_format not in {"jpeg", "webp"}:
        raise CliError("output-compression is only supported for jpeg or webp output.")


def default_output_compression(output_format: str) -> int | None:
    if output_format in {"jpeg", "webp"}:
        return DEFAULT_OUTPUT_COMPRESSION
    return None


def parse_size(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", value)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _ratio_delta(actual_size: tuple[int, int], expected_size: tuple[int, int]) -> float:
    actual_ratio = actual_size[0] / actual_size[1]
    expected_ratio = expected_size[0] / expected_size[1]
    return abs(actual_ratio - expected_ratio) / expected_ratio


def _centered_17_8_crop_box(size: tuple[int, int]) -> tuple[float, float, float, float]:
    """Return the largest centered exact 17:8 box, retaining width or height."""
    width, height = size
    if width * 8 == height * 17:
        return 0.0, 0.0, float(width), float(height)
    if width * 8 < height * 17:
        crop_height = width * 8 / 17
        top = (height - crop_height) / 2
        return 0.0, top, float(width), top + crop_height
    crop_width = height * 17 / 8
    left = (width - crop_width) / 2
    return left, 0.0, left + crop_width, float(height)


def _rounded_crop_box(box: tuple[float, float, float, float]) -> dict[str, float]:
    return {
        name: round(value, 6)
        for name, value in zip(("left", "top", "right", "bottom"), box)
    }


def is_gpt_image_2(model: str) -> bool:
    return "gpt-image-2" in model


def validate_size(size: str, model: str) -> None:
    if size == "auto":
        return
    parsed = parse_size(size)
    if parsed is None:
        raise CliError("size must be auto or WIDTHxHEIGHT, for example 1024x1024.")
    width, height = parsed
    if not is_gpt_image_2(model):
        if size not in {"1024x1024", "1536x1024", "1024x1536"}:
            raise CliError("this image model only supports 1024x1024, 1536x1024, 1024x1536, or auto.")
        return
    max_edge = max(width, height)
    min_edge = min(width, height)
    pixels = width * height
    if max_edge > GPT_IMAGE_2_MAX_EDGE:
        raise CliError("gpt-image-2 max edge must be <= 3840.")
    if width % 16 != 0 or height % 16 != 0:
        raise CliError("gpt-image-2 width and height must be multiples of 16.")
    if max_edge / min_edge > GPT_IMAGE_2_MAX_RATIO:
        raise CliError("gpt-image-2 long-to-short ratio must be <= 3:1.")
    if pixels < GPT_IMAGE_2_MIN_PIXELS or pixels > GPT_IMAGE_2_MAX_PIXELS:
        raise CliError("gpt-image-2 total pixels must be between 655,360 and 8,294,400.")


def guess_mime(path: Path, data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    mime, _ = mimetypes.guess_type(str(path))
    if mime in {"image/png", "image/jpeg", "image/webp"}:
        return mime
    raise CliError(f"Input image must be PNG, JPEG, or WebP: {path}")


def image_to_data_url(path: Path) -> str:
    if not path.exists():
        raise CliError(f"Input image not found: {path}")
    data = path.read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    data_url = f"data:{guess_mime(path, data)};base64,{encoded}"
    if len(data_url) > MAX_IMAGE_DATA_URL_CHARS:
        raise CliError(f"Input image exceeds data URL limit: {path}")
    return data_url


def load_input_images(image_paths: list[str], expected_sha256s: list[str]) -> list[LoadedImage]:
    if expected_sha256s and len(expected_sha256s) != len(image_paths):
        raise CliError("--image-sha256 count must match --image count.")
    loaded: list[LoadedImage] = []
    for index, raw in enumerate(image_paths):
        path = Path(raw)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise CliError(f"Cannot read input image: {path}") from exc
        digest = hashlib.sha256(data).hexdigest()
        if expected_sha256s and digest != expected_sha256s[index]:
            raise CliError(f"Input image digest does not match the confirmed digest: {path}")
        loaded.append(LoadedImage(path=path.resolve(), data=data, sha256=digest))
    return loaded


def loaded_image_reference(image: LoadedImage) -> dict[str, str]:
    encoded = base64.b64encode(image.data).decode("ascii")
    data_url = f"data:{guess_mime(image.path, image.data)};base64,{encoded}"
    if len(data_url) > MAX_IMAGE_DATA_URL_CHARS:
        raise CliError(f"Input image exceeds data URL limit: {image.path}")
    return {"image_url": data_url}


def image_reference(path: Path) -> dict[str, str]:
    return {"image_url": image_to_data_url(path)}


def build_image_body(
    args: argparse.Namespace,
    prompt: str,
    image_paths: list[str],
    *,
    operation: str,
    loaded_images: list[LoadedImage] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    if len(image_paths) > MAX_INPUT_IMAGES:
        raise CliError(f"At most {MAX_INPUT_IMAGES} input images are supported.")
    if loaded_images is not None and len(loaded_images) != len(image_paths):
        raise CliError("Loaded image count must match --image count.")
    if args.mask and not image_paths:
        raise CliError("--mask can only be used with at least one --image input.")

    image_model = args.model
    output_format = normalize_output_format(args.output_format)
    output_compression = (
        args.output_compression
        if args.output_compression is not None
        else default_output_compression(output_format)
    )

    validate_quality(args.quality)
    validate_background(args.background)
    validate_moderation(args.moderation)
    validate_output_compression(output_compression, output_format)
    validate_size(args.size, image_model)

    body: dict[str, Any] = {
        "prompt": prompt,
        "model": image_model,
        "n": args.count,
        "size": args.size,
        "quality": args.quality,
        "output_format": output_format,
    }
    if args.background:
        body["background"] = args.background
    if args.moderation:
        body["moderation"] = args.moderation
    if output_compression is not None:
        body["output_compression"] = output_compression
    if args.user:
        body["user"] = args.user

    if operation == "generate" and image_paths:
        raise CliError("Reference images require the explicit edit subcommand.")
    if operation == "edit" and not image_paths:
        raise CliError("The edit subcommand requires at least one --image input.")
    if operation not in {"generate", "edit"}:
        raise CliError("image operation must be generate or edit.")
    if image_paths:
        body["images"] = (
            [loaded_image_reference(image) for image in loaded_images]
            if loaded_images is not None
            else [image_reference(Path(raw)) for raw in image_paths]
        )
        if args.mask:
            body["mask"] = image_reference(Path(args.mask))

    return operation, image_model, body


def codex_image_headers(auth: CodexAuth) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {auth.access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "originator": "generate-slide-body-image",
        "User-Agent": "generate-slide-body-image-skill/0.1.0",
    }
    if auth.account_id:
        headers["ChatGPT-Account-ID"] = auth.account_id
    return headers


def _invoke_provider_worker(
    url: str, auth: CodexAuth, body: dict[str, Any], timeout: int,
    *, authority: AuthorityContext,
) -> dict[str, Any]:
    """Invoke the only network-capable process over an inherited one-shot pipe."""
    if not isinstance(authority, AuthorityContext):
        raise CliError("Provider worker requires fully validated workflow authority.")
    body_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    now = int(time.time())
    envelope = {
        "schema_version": "awesome-provider-envelope-v1",
        "issued_at": now, "not_before": now - 5, "expires_at": now + 300,
        "request_nonce": authority.capability["nonce"],
        "authority": authority.capability,
        "capability_sha256": hashlib.sha256(
            json.dumps(authority.capability, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "url": url, "timeout": min(max(int(timeout), 1), 900),
        "access_token": auth.access_token, "account_id": auth.account_id,
        "body_sha256": hashlib.sha256(body_bytes).hexdigest(),
        "body_bytes_b64": base64.b64encode(body_bytes).decode("ascii"),
    }
    one_shot_key = secrets.token_bytes(32)
    unsigned = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    envelope["hmac_sha256"] = hmac.new(one_shot_key, unsigned, hashlib.sha256).hexdigest()
    frame = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    read_fd, write_fd = os.pipe()
    os.set_inheritable(read_fd, True)
    os.set_inheritable(write_fd, False)
    env = dict(os.environ)
    env["AWESOME_PROVIDER_PIPE_FD"] = str(read_fd)
    env["AWESOME_PROVIDER_PIPE_KEY"] = base64.b64encode(one_shot_key).decode("ascii")
    worker = Path(__file__).with_name("provider_worker.py")
    network_started = False
    try:
        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE, "env": env, "close_fds": False,
        }
        if os.name == "nt":
            import msvcrt
            from subprocess import STARTUPINFO
            native_handle = msvcrt.get_osfhandle(read_fd)
            startup = STARTUPINFO()
            startup.lpAttributeList = {"handle_list": [native_handle]}
            env["AWESOME_PROVIDER_PIPE_HANDLE"] = str(native_handle)
            popen_kwargs["startupinfo"] = startup
            popen_kwargs["close_fds"] = True
        process = subprocess.Popen([sys.executable, str(worker)], **popen_kwargs)
        os.close(read_fd)
        read_fd = -1
        os.write(write_fd, len(frame).to_bytes(8, "big") + frame)
        os.close(write_fd)
        write_fd = -1
        network_started = True
        stdout, stderr = process.communicate(timeout=timeout + 30)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise CliError("provider worker timed out after request handoff", network=network_started) from exc
    except OSError as exc:
        raise CliError(f"provider worker local pipe failed: {exc}", network=network_started) from exc
    finally:
        if read_fd >= 0:
            os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)
    if process.returncode != 0:
        raise CliError(stderr.decode("utf-8", errors="replace")[:1000] or "provider worker failed",
                       network=network_started)
    try:
        result = json.loads(stdout.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates)
    except Exception as exc:
        raise CliError("provider worker returned invalid framed response", network=True) from exc
    if not result.get("ok"):
        status = result.get("status_code")
        detail = base64.b64decode(result.get("response_bytes_b64", "")).decode("utf-8", errors="replace")
        raise CliError(detail or result.get("network_error") or "provider request failed",
                       status_code=status if type(status) is int else None, network=True)
    try:
        raw_response = base64.b64decode(result["response_bytes_b64"])
        response = json.loads(raw_response.decode("utf-8"),
                              object_pairs_hook=_pairs_no_duplicates)
    except Exception as exc:
        raise CliError("provider response was not duplicate-safe JSON", network=True) from exc
    if not isinstance(response, dict):
        raise CliError("provider response must be a JSON object", network=True)
    response["__awesome_raw_response_b64"] = base64.b64encode(raw_response).decode("ascii")
    return response


def extract_image_payloads(response: dict[str, Any]) -> list[tuple[str, str | None]]:
    error_obj = response.get("error")
    if isinstance(error_obj, dict):
        message = error_obj.get("message") or error_obj.get("code")
        raise CliError(str(message or "OpenAI Codex image generation failed."))

    data = response.get("data")
    if not isinstance(data, list):
        raise CliError("Image response did not include a data array.")
    payloads: list[tuple[str, str | None]] = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("b64_json"), str):
            revised_prompt = item.get("revised_prompt")
            payloads.append(
                (
                    item["b64_json"],
                    revised_prompt if isinstance(revised_prompt, str) else None,
                )
            )
    return payloads


def write_images(
    payloads: list[tuple[str, str | None]],
    out: str,
    output_format: str,
    requested_size: str = "auto",
    *,
    allow_off_ratio_for_downstream_repair: bool = False,
    provider_response_quality: str | None = None,
    authority_project: Path,
) -> list[Path]:
    if not payloads:
        raise CliError("No image payload found in Codex response.")
    target = Path(out)
    ext = "jpg" if output_format == "jpeg" else output_format
    multi = len(payloads) > 1
    written: list[Path] = []
    for index, (payload, _revised_prompt) in enumerate(payloads, start=1):
        if len(payload) > MAX_BASE64_CHARS:
            raise CliError("Image payload exceeded size limit.")
        data = base64.b64decode(payload)
        expected_size = parse_size(requested_size)
        if expected_size is not None:
            try:
                with Image.open(BytesIO(data)) as source:
                    actual_size = source.size
                    if allow_off_ratio_for_downstream_repair:
                        if expected_size != (1904, 896):
                            raise CliError("Complex-page frame adaptation requires a 1904x896 request.")
                        if output_format != "png":
                            raise CliError("Complex-page frame adaptation requires PNG output.")
                        crop_box = _centered_17_8_crop_box(actual_size)
                        adapted = source.convert("RGB").resize(
                            expected_size,
                            Image.Resampling.LANCZOS,
                            box=crop_box,
                        )
                        metadata = PngImagePlugin.PngInfo()
                        metadata.add_text(
                            "awesome_provider_original_size",
                            f"{actual_size[0]}x{actual_size[1]}",
                        )
                        metadata.add_text(
                            "awesome_provider_original_quality",
                            provider_response_quality or "unknown",
                        )
                        metadata.add_text(
                            "awesome_center_17_8_crop_box",
                            json.dumps(_rounded_crop_box(crop_box), sort_keys=True, separators=(",", ":")),
                        )
                        buffer = BytesIO()
                        adapted.save(buffer, format="PNG", pnginfo=metadata)
                        data = buffer.getvalue()
                        eprint(
                            f"Center-cropped provider image {actual_size[0]}x{actual_size[1]} to its largest "
                            f"17:8 content region and uniformly scaled it to {expected_size[0]}x{expected_size[1]}."
                        )
                    elif actual_size != expected_size:
                        ratio_delta = _ratio_delta(actual_size, expected_size)
                        if ratio_delta > 0.005:
                            raise CliError(
                                f"Generated image aspect ratio {actual_size[0]}x{actual_size[1]} does not match "
                                f"requested size {expected_size[0]}x{expected_size[1]}; refusing to distort it."
                            )
                        else:
                            normalized = source.convert("RGBA" if output_format == "png" else "RGB").resize(
                                expected_size, Image.Resampling.LANCZOS
                            )
                            buffer = BytesIO()
                            save_format = "JPEG" if output_format in {"jpeg", "jpg"} else output_format.upper()
                            save_kwargs: dict[str, Any] = {"format": save_format}
                            if save_format == "JPEG":
                                save_kwargs["quality"] = 100
                            elif save_format == "WEBP":
                                save_kwargs["quality"] = 100
                            normalized.save(buffer, **save_kwargs)
                            data = buffer.getvalue()
                            eprint(
                                f"Normalized generated image from {actual_size[0]}x{actual_size[1]} "
                                f"to requested {expected_size[0]}x{expected_size[1]}."
                            )
            except CliError:
                raise
            except Exception as exc:
                raise CliError(f"Cannot validate generated image dimensions: {exc}") from exc
        path = target
        if multi:
            stem = target.stem or "image"
            suffix = target.suffix or f".{ext}"
            path = target.with_name(f"{stem}-{index:02d}{suffix}")
        secure_io.atomic_write_bytes(authority_project, path.relative_to(authority_project), data)
        written.append(path)
    return written


def _authority_project_for_target(path: Path) -> Path:
    parts = path.parts
    for marker in ("04_v6", "05_v6"):
        if marker in parts:
            return Path(*parts[:parts.index(marker)])
    raise CliError("provider output is outside canonical project storage")


def cmd_auth_status(args: argparse.Namespace) -> int:
    auth = load_or_login_codex_auth(args)
    payload = {
        "auth_file": str(codex_auth_file()),
        "has_access_token": True,
        "account_id": redact(auth.account_id),
        "last_refresh": auth.last_refresh,
        "base_url": canonicalize_codex_base_url(args.base_url),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("Codex OAuth is available.")
        print(f"Auth file: {payload['auth_file']}")
        if payload["account_id"]:
            print(f"Account: {payload['account_id']}")
        if payload["last_refresh"]:
            print(f"Last refresh: {payload['last_refresh']}")
        print(f"Images base URL: {payload['base_url']}")
    return 0


def _recover_response_received(
    authority: AuthorityContext, args: argparse.Namespace, operation: str,
    image_model: str, image_paths: list[str], loaded_images: list[LoadedImage],
    output_format: str,
) -> list[Path] | None:
    if not authority.journal.exists():
        return None
    try:
        prior = _verified_journal(authority)
    except Exception as exc:
        raise CliError("image submission journal is corrupt") from exc
    if prior.get("state") != "response_received":
        return None
    try:
        response_bytes = base64.b64decode(prior["response_bytes_b64"], validate=True)
        if hashlib.sha256(response_bytes).hexdigest() != prior["response_sha256"]:
            raise ValueError("response digest mismatch")
        response = json.loads(response_bytes.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates)
    except Exception as exc:
        raise CliError("recoverable provider response journal is invalid") from exc
    written = write_images(extract_image_payloads(response), str(authority.output), output_format, args.size,
                           allow_off_ratio_for_downstream_repair=args.allow_off_ratio_for_downstream_repair,
                           provider_response_quality=(
                               str(response["quality"]) if isinstance(response.get("quality"), str) else None
                           ),
                           authority_project=authority.project)
    write_generation_trace(args, operation, image_model, image_paths, written, authenticated=True,
                           loaded_images=loaded_images, authority_project=authority.project)
    completed = {**prior, "state": "submitted",
                 "outputs": [hashlib.sha256(_authority_bytes(authority, path)).hexdigest() for path in written]}
    _atomic_journal(authority.journal, completed)
    return written


def _submission_boundary(_stage: str) -> None:
    """Fault-injection seam for crash-boundary tests; production is intentionally inert."""


def cmd_generate(args: argparse.Namespace) -> int:
    prompt = read_prompt(args.prompt, args.prompt_file)
    image_paths = args.image or []
    if len(image_paths) > MAX_INPUT_IMAGES:
        raise CliError(f"At most {MAX_INPUT_IMAGES} input images are supported.")
    loaded_images = load_input_images(image_paths, getattr(args, "image_sha256", None) or [])
    if args.count < 1 or args.count > MAX_COUNT:
        raise CliError(f"--count must be between 1 and {MAX_COUNT}.")
    output_format = normalize_output_format(args.output_format)
    operation = str(args.declared_operation)
    operation, image_model, body = build_image_body(
        args, prompt, image_paths, operation=operation, loaded_images=loaded_images,
    )
    url = None

    if args.dry_run:
        authority = require_workflow_capability(args, prompt, loaded_images)
        url = authority.capability["official_endpoint"]
        args.trace_out = None
        summary = {
            "url": url,
            "auth": "Codex OAuth access token (not loaded during dry-run)",
            "operation": operation,
            "image_model": image_model,
            "size": body["size"],
            "quality": body.get("quality"),
            "output_format": output_format,
            "background": body.get("background"),
            "moderation": body.get("moderation"),
            "output_compression": body.get("output_compression"),
            "input_images": len(image_paths),
            "mask": bool(args.mask),
            "count": args.count,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        write_generation_trace(
            args, operation, image_model, image_paths, [], authenticated=False,
            loaded_images=loaded_images, authority_project=authority.project,
        )
        return 0

    start = time.time()
    authority = require_workflow_capability(args, prompt, loaded_images)
    url = authority.capability["official_endpoint"]
    args.out = str(authority.output)
    args.trace_out = str(authority.trace)
    recovered = _recover_response_received(
        authority, args, operation, image_model, image_paths, loaded_images, output_format,
    )
    if recovered is not None:
        for path in recovered:
            print(path)
        return 0
    claimed = _claim_submission(authority)
    base_journal = {key: claimed[key] for key in (
        "schema_version", "nonce", "attempt", "operation", "input_sha256s",
        "capability_sha256", "generation", "owner",
    )}
    try:
        auth = load_or_login_codex_auth(args)
        # OAuth is local-file validation; the network worker is still the sole request callsite.
        response = None
        for provider_attempt in range(1, 4):
            try:
                response = _invoke_provider_worker(url, auth, body, args.timeout, authority=authority)
                break
            except CliError as exc:
                if exc.status_code != 429 or provider_attempt == 3:
                    raise
                time.sleep(min(2 ** (provider_attempt - 1), 4))
        if response is None:
            raise CliError("provider request did not produce a response", network=True)
    except CliError as exc:
        target_state = "outcome_unknown" if exc.network else "issued"
        _atomic_journal(authority.journal, {**base_journal, "state": target_state,
                                           "network_started": bool(exc.network),
                                           "error": str(exc)[:800]})
        raise
    raw_response_b64 = response.pop("__awesome_raw_response_b64", None)
    response_bytes = (
        base64.b64decode(raw_response_b64)
        if isinstance(raw_response_b64, str)
        else json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    _atomic_journal(authority.journal, {**base_journal, "state": "response_received", "network_started": True,
                                       "response_sha256": hashlib.sha256(response_bytes).hexdigest(),
                                       "response_bytes_b64": base64.b64encode(response_bytes).decode("ascii")})
    _submission_boundary("response_journal_committed")
    written = write_images(extract_image_payloads(response), str(authority.output), output_format, args.size,
                           allow_off_ratio_for_downstream_repair=args.allow_off_ratio_for_downstream_repair,
                           provider_response_quality=(
                               str(response["quality"]) if isinstance(response.get("quality"), str) else None
                           ),
                           authority_project=authority.project)
    write_generation_trace(args, operation, image_model, image_paths, written, authenticated=True,
                           loaded_images=loaded_images, authority_project=authority.project)
    submitted = {**base_journal, "state": "submitted", "network_started": True,
                 "response_sha256": hashlib.sha256(response_bytes).hexdigest(),
                 "response_bytes_b64": base64.b64encode(response_bytes).decode("ascii"),
                 "outputs": [hashlib.sha256(_authority_bytes(authority, path)).hexdigest() for path in written]}
    _atomic_journal(authority.journal, submitted)
    for path in written:
        print(path)
    eprint(f"Generated {len(written)} image(s) via Codex OAuth in {time.time() - start:.1f}s.")
    return 0


def cmd_reconstruct_edit(args: argparse.Namespace) -> int:
    """One exact sealed edit used only by accepted-page editable reconstruction."""
    authority, prompt, loaded = require_reconstruction_capability(args)
    capability = authority.capability
    body = {
        "prompt": prompt, "model": capability["model"], "n": 1,
        "size": capability["size"], "quality": capability["quality"],
        "images": [{"image_url": "data:image/png;base64," + base64.b64encode(loaded.data).decode("ascii")}],
    }
    claimed = _claim_submission(authority)
    base_journal = {key: claimed[key] for key in (
        "schema_version", "nonce", "attempt", "operation", "input_sha256s",
        "capability_sha256", "generation", "owner",
    )}
    try:
        auth = load_or_login_codex_auth(args)
        response = _invoke_provider_worker(capability["official_endpoint"], auth, body, args.timeout,
                                           authority=authority)
    except CliError as exc:
        _atomic_journal(authority.journal, {**base_journal,
            "state": "outcome_unknown" if exc.network else "issued",
            "network_started": bool(exc.network), "error": str(exc)[:800]})
        raise
    raw_b64 = response.pop("__awesome_raw_response_b64", None)
    raw = base64.b64decode(raw_b64) if isinstance(raw_b64, str) else json.dumps(
        response, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    _atomic_journal(authority.journal, {**base_journal, "state": "response_received",
        "network_started": True, "response_sha256": hashlib.sha256(raw).hexdigest(),
        "response_bytes_b64": base64.b64encode(raw).decode("ascii")})
    written = write_images(extract_image_payloads(response), str(authority.output), "png", capability["size"],
                           authority_project=authority.project)
    trace = {
        "schema_version": "awesome-reconstruction-image-trace-v1",
        "capability_sha256": base_journal["capability_sha256"], "nonce": capability["nonce"],
        "accepted_image_sha256": loaded.sha256, "output_sha256": hashlib.sha256(_authority_bytes(authority, written[0])).hexdigest(),
    }
    secure_io.atomic_write_bytes(
        authority.project, authority.trace.relative_to(authority.project),
        (json.dumps(trace, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
    )
    _atomic_journal(authority.journal, {**base_journal, "state": "submitted", "network_started": True,
        "response_sha256": hashlib.sha256(raw).hexdigest(), "response_bytes_b64": base64.b64encode(raw).decode("ascii"),
        "outputs": [trace["output_sha256"]]})
    print(written[0])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate GPT Image outputs through the installed Codex OAuth capability."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    auth = sub.add_parser("auth-status", help="Check whether local Codex OAuth auth is readable.")
    auth.add_argument("--base-url", default=None)
    auth.add_argument("--login-if-missing", action="store_true")
    auth.add_argument("--open-browser", action="store_true")
    auth.add_argument("--client-id", help="Override the Codex OAuth client id for device-code login.")
    auth.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    auth.add_argument("--json", action="store_true")
    auth.set_defaults(func=cmd_auth_status)

    login = sub.add_parser("login", help="Run OpenAI Codex device-code login and save ~/.codex/auth.json.")
    login.add_argument("--open-browser", action="store_true")
    login.add_argument("--client-id", help="Override the Codex OAuth client id for device-code login.")
    login.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    login.set_defaults(func=lambda args: 0 if login_device_code(args) else 1)

    def add_image_arguments(command: argparse.ArgumentParser, operation: str) -> None:
        command.add_argument("--prompt", "-p")
        command.add_argument("--prompt-file")
        command.add_argument("--prompt-sha256")
        command.add_argument("--request-capability")
        command.add_argument("--workflow-project")
        command.add_argument("--workflow-page", type=int)
        command.add_argument("--image", "-i", action="append", help="Reference/edit image path. Repeatable.")
        command.add_argument("--image-sha256", action="append", help="Expected SHA-256 for each --image, in the same order. Repeatable.")
        command.add_argument("--image-role", action="append", help="Semantic role for each --image, in the same order. Repeatable.")
        command.add_argument("--mask", help="Mask image path for edits. Requires at least one --image.")
        command.add_argument(
            "--allow-off-ratio-for-downstream-repair",
            action="store_true",
            help="Preserve an off-ratio provider image for a caller-owned downstream repair step.",
        )
        command.add_argument("--model", default=os.getenv("CODEX_GPT_IMAGE_MODEL", DEFAULT_IMAGE_MODEL))
        command.add_argument("--size", default=DEFAULT_SIZE)
        command.add_argument("--quality", default=DEFAULT_QUALITY, choices=sorted(SUPPORTED_QUALITIES))
        command.add_argument("--output-format", default=DEFAULT_OUTPUT_FORMAT, choices=sorted(SUPPORTED_OUTPUT_FORMATS))
        command.add_argument("--background", default=DEFAULT_BACKGROUND, choices=sorted(SUPPORTED_BACKGROUNDS))
        command.add_argument("--moderation", default=DEFAULT_MODERATION, choices=sorted(SUPPORTED_MODERATIONS))
        command.add_argument("--output-compression", type=int, help="Compression level 0-100. Only valid for jpeg/webp.")
        command.add_argument("--user")
        command.add_argument("--count", type=int, default=DEFAULT_COUNT, help="Number of images, 1-10.")
        command.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
        command.add_argument("--login-if-missing", action="store_true")
        command.add_argument("--open-browser", action="store_true")
        command.add_argument("--client-id", help="Override the Codex OAuth client id when --login-if-missing runs.")
        command.add_argument("--dry-run", action="store_true")
        command.set_defaults(func=cmd_generate, declared_operation=operation)

    gen = sub.add_parser("generate", help="Generate an image without reference inputs.")
    add_image_arguments(gen, "generate")
    edit = sub.add_parser("edit", help="Edit or compose an image from explicit reference inputs.")
    add_image_arguments(edit, "edit")
    reconstruct = sub.add_parser("reconstruct-edit", help="Run the sealed accepted-page reconstruction edit.")
    reconstruct.add_argument("--request-capability", required=True)
    reconstruct.add_argument("--workflow-project", required=True)
    reconstruct.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    reconstruct.add_argument("--login-if-missing", action="store_true")
    reconstruct.add_argument("--open-browser", action="store_true")
    reconstruct.add_argument("--client-id")
    reconstruct.set_defaults(func=cmd_reconstruct_edit)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(argv) if argv is not None else list(sys.argv[1:])
    # These values are emitted only by the sealed workflow runner adapter and
    # are ignored here; authority supplies and validates the actual paths.
    for internal in ("--out", "--trace-out"):
        if internal in argv:
            index = argv.index(internal)
            del argv[index:index + 2]
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except CliError as exc:
        eprint("CODEX_IMAGE_ERROR_JSON:" + json.dumps({
            "status_code": exc.status_code,
            "network": exc.network,
            "message": str(exc),
        }, ensure_ascii=False, separators=(",", ":")))
        die(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

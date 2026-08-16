"""Generate and select V6 page bodies using authoritative gpt-image-2 requests."""

from __future__ import annotations

import copy
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

from workflow_v6_contract import canonical_sha256, request_identity, transition_page
from workflow_v6_contract import PLUGIN_ID, PLUGIN_VERSION, WORKFLOW_VERSION, validate_material_receipts
from workflow_v6_qa import (
    actionable_retry_feedback,
    improved,
    mechanical_review,
    review_candidate,
)
from workflow_v6_state import load, update_page
from workflow_v6_prompt_contract import (
    compile_confirmed_page_prompt,
    filter_confirmed_page_for_prompt,
    filter_global_visual_contract,
)
from adaptive_scheduler import (
    AdaptiveScheduler,
    PageOwnershipLease,
    ProjectGenerationGate,
    ProjectPageOwnership,
)
import workflow_v6_media as v6_media
import workflow_v6_secure_io as secure_io
IMAGE_PROVIDER_SCRIPTS = Path(__file__).resolve().parents[2] / "generate-slide-body-image" / "scripts"
if str(IMAGE_PROVIDER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(IMAGE_PROVIDER_SCRIPTS))
from provider_keyring import signing_key  # noqa: E402


from validate_page_image_prompt import (  # noqa: E402
    _embedded_json as _prompt_embedded_json,
    _sections as _prompt_sections,
    load_json_no_duplicates,
    validate_page_image_prompt,
)


IMAGE_CLI = (
    Path(__file__).resolve().parents[2]
    / "generate-slide-body-image" / "scripts" / "codex_gpt_image.py"
)
QA_TIMEOUT_SECONDS = 180
_PROMPT_LIMIT = 32_000
_MAX_CANDIDATES = 2
_SMALL_TEXT_RISK_CHARS = 1_000
_HIGH_DETAIL_TERMS = re.compile(
    r"(?:logo|logotype|screenshot|screen\s*shot|high[-_ ]?detail|fine[-_ ]?detail|"
    r"small[-_ ]?text|dense[-_ ]?data|徽标|标志|截图|高细节|小字|密集数据)",
    re.IGNORECASE,
)
_PROVIDER_ERROR_PREFIX = "CODEX_IMAGE_ERROR_JSON:"


def _image_request_identity(request: ImageRequest, *, revision_digest: str, prompt_sha256: str,
                            quality: str | None = None) -> str:
    return request_identity(
        revision_digest=revision_digest, prompt_sha256=prompt_sha256,
        operation=request.operation, quality=quality or request.quality,
        input_sha256s=request.input_sha256s, plugin_id=request.plugin_id,
        plugin_version=request.plugin_version, workflow_contract=request.workflow_contract,
        page_material_digest=request.page_material_digest,
        prompt_output_sha256=request.prompt_output_sha256,
        selected_reference_ids=request.selected_reference_ids,
        model=request.model, size=request.size, source_identity=request.source_identity,
    )


@dataclass(frozen=True)
class ImageRequest:
    operation: Literal["generate", "edit"]
    quality: Literal["medium", "high"]
    prompt: str
    input_images: tuple[Path, ...]
    image_roles: tuple[str, ...]
    input_sha256s: tuple[str, ...] = ()
    model: str = "gpt-image-2"
    size: str = "1904x896"
    selected_reference_ids: tuple[str, ...] = ()
    plugin_id: str = PLUGIN_ID
    plugin_version: str = PLUGIN_VERSION
    workflow_contract: str = WORKFLOW_VERSION
    ui_revision: int | None = None
    ui_digest: str = ""
    page_material_digest: str = ""
    prompt_output_sha256: str = ""
    source_identity: str = ""
    capability_path: Path | None = None
    project_root: Path | None = None
    page_number: int | None = None


def _capability_secret() -> bytes:
    return signing_key()[1]
    # Legacy single-key initialization below remains migration-only unreachable.
    base = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()
    directory = base / "plugin-secrets"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if v6_media._is_link_or_reparse(directory):
        raise ValueError("plugin capability secret directory is unsafe")
    path = directory / "awesome-editable-ppt-workflow.image-request.key"
    if not path.exists():
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(path, flags, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(secrets.token_bytes(32))
        except FileExistsError:
            pass
    if v6_media._is_link_or_reparse(path):
        raise ValueError("plugin capability secret is unsafe")
    if os.name == "nt":
        import subprocess as _sp
        sid_result = _sp.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=False)
        sid_match = re.search(r"S-1-5-(?:\d+-)+\d+", sid_result.stdout)
        if sid_result.returncode != 0 or sid_match is None:
            raise ValueError("plugin capability secret owner SID cannot be verified")
        owner_sid = sid_match.group(0)
        if not path.exists():
            raise ValueError("plugin capability secret disappeared before ACL verification")
        _sp.run(["icacls", str(path), "/inheritance:r", "/grant:r",
                 f"*{owner_sid}:(F)", "*S-1-5-18:(F)", "*S-1-5-32-544:(F)"],
                capture_output=True, text=True, check=True)
        acl_script = (
            "& { param($p) $ErrorActionPreference='Stop'; (Get-Acl -LiteralPath $p).Access | ForEach-Object {"
            "$sid=$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value;"
            "Write-Output ($sid+'|'+$_.AccessControlType+'|'+$_.FileSystemRights+'|'+$_.IsInherited)} }"
        )
        acl = _sp.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", acl_script, str(path)],
                      capture_output=True, text=True, check=False)
        if acl.returncode != 0 or not acl.stdout.strip():
            raise ValueError("plugin capability secret ACL cannot be verified")
        acl_sids = set()
        for line in acl.stdout.splitlines():
            fields = line.strip().split("|")
            if len(fields) != 4 or fields[1] != "Allow" or fields[3] != "False":
                raise ValueError("plugin capability secret ACL is inherited or denied")
            acl_sids.add(fields[0])
        allowed = {owner_sid, "S-1-5-18", "S-1-5-32-544"}
        if not allowed.issubset(acl_sids) or not acl_sids.issubset(allowed):
            raise ValueError("plugin capability secret ACL contains broad or unknown trustees")
    else:
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            raise ValueError("plugin capability secret must be owner-only")
    data = path.read_bytes()
    if len(data) != 32:
        raise ValueError("plugin capability secret is invalid")
    return data


def _issue_capability(
    request: ImageRequest, *, attempt: int,
    output: Path | None = None, trace: Path | None = None,
) -> Path:
    if request.project_root is None or request.page_number is None:
        raise ValueError("sealed image request lacks project/page authority")
    secure_io.reject_reparse_chain(request.project_root)
    root = request.project_root.resolve(strict=True)
    output = output or root / "04_v6" / "images" / f"page_{request.page_number:03d}.candidate_{attempt}.png"
    trace = trace or root / "04_v6" / "images" / f"page_{request.page_number:03d}.candidate_{attempt}.trace.json"
    for label, path in (("output", output), ("trace", trace)):
        try:
            path.resolve(strict=False).relative_to(root / "04_v6" / "images")
        except (OSError, ValueError) as exc:
            raise ValueError(f"provider {label} must remain in canonical project staging") from exc
    state = load(root)
    materials, material_bytes, material_digest = _material_authority(
        root, state, request.page_number,
    )
    prompt_path, prompt_receipt_path = _canonical_prompt_paths(root, request.page_number)
    prompt_bytes = v6_media._read_file_limited(root, prompt_path)
    prompt_receipt_bytes = v6_media._read_file_limited(root, prompt_receipt_path)
    now = int(time.time())
    selected_references = []
    for reference_id, role, digest, path in zip(
        request.selected_reference_ids, request.image_roles,
        request.input_sha256s, request.input_images,
    ):
        data = v6_media._read_file_limited(root, path)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("selected reference bytes changed before capability issue")
        selected_references.append({
            "reference_id": reference_id, "role": role, "sha256": digest,
            "bytes_b64": base64.b64encode(data).decode("ascii"),
        })
    payload = {
        "schema_version": "awesome-image-request-capability-v3",
        "plugin_id": request.plugin_id, "plugin_version": request.plugin_version,
        "workflow_contract": request.workflow_contract, "source_identity": request.source_identity,
        "project_binding": hashlib.sha256(str(root).encode("utf-8")).hexdigest(),
        "page_number": request.page_number, "ui_revision": request.ui_revision,
        "ui_digest": request.ui_digest, "page_material_digest": request.page_material_digest,
        "prompt_sha256": hashlib.sha256(request.prompt.encode("utf-8")).hexdigest(),
        "prompt_output_sha256": request.prompt_output_sha256,
        "operation": request.operation, "model": request.model, "size": request.size,
        "official_endpoint": (
            "https://chatgpt.com/backend-api/codex/images/edits"
            if request.operation == "edit"
            else "https://chatgpt.com/backend-api/codex/images/generations"
        ),
        "output_path": output.relative_to(root).as_posix(),
        "trace_path": trace.relative_to(root).as_posix(),
        "quality": request.quality, "selected_reference_ids": list(request.selected_reference_ids),
        "input_sha256s": list(request.input_sha256s), "image_roles": list(request.image_roles),
        "attempt": attempt, "nonce": secrets.token_hex(16),
        "issued_at": now, "not_before": now - 5, "expires_at": now + 300,
        "key_id": signing_key()[0],
        "project_identity": {
            "plugin_id": state.get("plugin_id"), "plugin_version": state.get("plugin_version"),
            "workflow_contract": state.get("workflow_contract"),
            "source_identity": state.get("source_identity"),
        },
        "source_authority": {
            "word_source": state.get("word_source"), "logo_source": state.get("logo_source"),
        },
        "page_state_authority": copy.deepcopy(state["pages"][request.page_number - 1]),
        "material_authority": {
            "sha256": material_digest, "bytes_b64": base64.b64encode(material_bytes).decode("ascii"),
        },
        "prompt_authority": {
            "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
            "prompt_bytes_b64": base64.b64encode(prompt_bytes).decode("ascii"),
            "receipt_bytes_b64": base64.b64encode(prompt_receipt_bytes).decode("ascii"),
        },
        "visual_contract_authority": {
            "revision": state.get("confirmed_ui_revision"),
            "digest": state.get("confirmed_ui_digest"),
            "contract": state.get("style_confirmation", {}).get("contract"),
        },
        "selected_references": selected_references,
    }
    unsigned = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    value = {**payload, "hmac_sha256": hmac.new(_capability_secret(), unsigned, hashlib.sha256).hexdigest()}
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    relative_path = Path("04_v6") / "image_request_capabilities" / f"page_{request.page_number:03d}.attempt_{attempt}.{payload['nonce']}.json"
    return secure_io.atomic_write_bytes(root, relative_path, data)


def issue_reconstruction_capability(
    project: Path, *, page_number: int, accepted_receipt: Path,
    purpose: str, output_kind: str,
) -> Path:
    """Bind a reconstruction-only image edit to one accepted body image."""
    if purpose != "asset-separation" or output_kind not in {"foreground-sheet", "clean-base"}:
        raise ValueError("unsupported reconstruction image purpose")
    secure_io.reject_reparse_chain(Path(project))
    root = Path(project).resolve(strict=True)
    receipt_path = Path(accepted_receipt).resolve(strict=True)
    try:
        receipt_path.relative_to(root / "04_v6" / "images")
    except ValueError as exc:
        raise ValueError("accepted image receipt must be project-owned") from exc
    receipt_relative = receipt_path.relative_to(root)
    receipt_bytes = secure_io.read_bytes(root, receipt_relative)
    receipt = load_json_no_duplicates(receipt_bytes.decode("utf-8"))
    if not isinstance(receipt, Mapping) or receipt.get("page_number") != page_number:
        raise ValueError("accepted page receipt identity is invalid")
    selected = receipt.get("selected")
    if not isinstance(selected, Mapping):
        raise ValueError("accepted page receipt has no selected image")
    relative = selected.get("path")
    digest = selected.get("sha256")
    if not isinstance(relative, str) or not isinstance(digest, str):
        raise ValueError("accepted page receipt image authority is incomplete")
    image_path = root / relative
    image_bytes = v6_media._read_file_limited(root, image_path)
    if hashlib.sha256(image_bytes).hexdigest() != digest:
        raise ValueError("accepted page image changed before reconstruction request")
    state = load(root)
    now = int(time.time())
    ui_bytes = json.dumps(state["style_confirmation"]["contract"], ensure_ascii=False,
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
    output_relative = Path("05_v6") / "reconstruction_assets" / f"page_{page_number:03d}.{output_kind}.png"
    trace_relative = output_relative.with_suffix(".trace.json")
    payload = {
        "schema_version": "awesome-reconstruction-image-capability-v1",
        "plugin_id": PLUGIN_ID, "plugin_version": PLUGIN_VERSION,
        "workflow_contract": WORKFLOW_VERSION, "source_identity": state["source_identity"],
        "page_number": page_number, "purpose": purpose, "output_kind": output_kind,
        "accepted_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "accepted_receipt_bytes_b64": base64.b64encode(receipt_bytes).decode("ascii"),
        "accepted_image_sha256": digest,
        "accepted_image_path": Path(relative).as_posix(),
        "input_image_bytes_b64": base64.b64encode(image_bytes).decode("ascii"),
        "prompt": (
            "Separate only the visible non-text foreground assets from this accepted slide-body image. "
            "Preserve exact visible asset appearance; do not add facts, labels, logos, people, or content. "
            f"Return a {output_kind} for editable reconstruction."
        ),
        "prompt_sha256": "", "operation": "edit", "attempt": 1,
        "model": "gpt-image-2", "size": "1904x896", "quality": "high",
        "input_sha256s": [digest], "image_roles": ["accepted-slide-body"],
        "official_endpoint": "https://chatgpt.com/backend-api/codex/images/edits",
        "project_binding": hashlib.sha256(str(root).encode("utf-8")).hexdigest(),
        "ui_revision": state["confirmed_ui_revision"], "ui_digest": state["confirmed_ui_digest"],
        "ui_bytes_b64": base64.b64encode(ui_bytes).decode("ascii"),
        "output_path": output_relative.as_posix(), "trace_path": trace_relative.as_posix(),
        "issued_at": now, "not_before": now - 5, "expires_at": now + 300,
        "key_id": signing_key()[0], "nonce": secrets.token_hex(16),
    }
    payload["prompt_sha256"] = hashlib.sha256(payload["prompt"].encode("utf-8")).hexdigest()
    unsigned = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sealed = {**payload, "hmac_sha256": hmac.new(_capability_secret(), unsigned, hashlib.sha256).hexdigest()}
    relative_path = Path("04_v6") / "reconstruction_capabilities" / f"page_{page_number:03d}.{purpose}.{sealed['nonce']}.json"
    return secure_io.atomic_write_bytes(root, relative_path, (json.dumps(
        sealed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n").encode("utf-8"))


class ProviderFailure(RuntimeError):
    def __init__(
        self, message: str, *, status_code: int | None = None, network: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.network = network


def _material_role_text(page: Mapping[str, Any]) -> str:
    values: list[str] = []
    for field in ("reference_images", "image_requirements"):
        for item in page.get(field, []):
            if not isinstance(item, Mapping):
                continue
            for key in ("kind", "purpose", "role", "visual", "subject", "search_query"):
                value = item.get(key)
                if isinstance(value, str):
                    values.append(value)
    return " ".join(values)


def initial_quality(page: Mapping[str, Any]) -> Literal["medium", "high"]:
    """Classify only the material package frozen by the single confirmation UI."""
    body = str(page.get("effective_body", ""))
    charts = page.get("chart_facts", [])
    attachment_types = {
        str(item.get("kind", item.get("type", ""))).strip().casefold()
        for item in page.get("attachment_extracts", [])
        if isinstance(item, Mapping)
    }
    structured_attachment = any(
        isinstance(item, Mapping)
        and item.get("selector") in {"selected_rows", "selected_fields"}
        and isinstance(item.get("content"), (list, dict))
        and bool(item.get("content"))
        for item in page.get("attachment_extracts", [])
    )
    dense_data = (
        isinstance(charts, list) and bool(charts)
        or bool(attachment_types & {"table", "chart", "spreadsheet", "data_table"})
        or structured_attachment
    )
    return "high" if (
        len(body) >= _SMALL_TEXT_RISK_CHARS
        or dense_data
        or bool(_HIGH_DETAIL_TERMS.search(_material_role_text(page)))
    ) else "medium"


def _read_json(path: Path) -> dict[str, Any]:
    parts = path.parts
    project_markers = [marker for marker in ("02_v6", "04_v6", "05_v6", "06_v6", "08_final") if marker in parts]
    if project_markers:
        marker = project_markers[0]
        root = Path(*parts[:parts.index(marker)])
        data = secure_io.read_bytes(root, path.relative_to(root))
    else:
        data = path.read_bytes()
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _canonical_prompt_paths(root: Path, page_number: int) -> tuple[Path, Path]:
    directory = root / "02_v6" / "page_image_prompts"
    return (
        directory / f"page_{page_number:03d}.output.json",
        directory / f"page_{page_number:03d}.receipt.json",
    )


def _material_authority(root: Path, state: Mapping[str, Any], page_number: int) -> tuple[dict[str, Any], bytes, str]:
    validate_material_receipts(root, state)
    if state.get("page_materials_status") != "confirmed":
        raise ValueError("validated page image prompt requires confirmed page materials")
    try:
        page = state["pages"][page_number - 1]
        receipt = page["material_receipt"]
    except (IndexError, KeyError, TypeError) as exc:
        raise ValueError("validated page image prompt has no page-owned material receipt") from exc
    if not isinstance(receipt, Mapping):
        raise ValueError("validated page image prompt has no page-owned material receipt")
    path = root / str(receipt["path"])
    data = secure_io.read_bytes(root, path.relative_to(root))
    digest = hashlib.sha256(data).hexdigest()
    if digest != receipt.get("digest"):
        raise ValueError("page material artifact changed after publication")
    try:
        value = load_json_no_duplicates(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("page material artifact is not valid canonical JSON") from exc
    if not isinstance(value, dict) or value.get("page_number") != page_number:
        raise ValueError("page material artifact is not owned by this page")
    return value, data, digest


def seal_page_image_prompt(project: Path, page_number: int, compiler_result: Path) -> dict[str, Any]:
    """Validate and immutably bind one current-Codex compiler output to current authority."""
    secure_io.reject_reparse_chain(Path(project))
    root = Path(project).resolve(strict=True)
    if type(page_number) is not int or page_number < 1:
        raise ValueError("page number must be positive")
    source_bytes = Path(compiler_result).read_bytes()
    try:
        result = load_json_no_duplicates(source_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("compiler result is not duplicate-key-safe UTF-8 JSON") from exc
    if not isinstance(result, dict):
        raise ValueError("compiler result must be a JSON object")
    from workflow_v6_state import mutation_lock
    with mutation_lock(root):
        state = load(root)
        materials, _material_bytes, material_digest = _material_authority(root, state, page_number)
        validate_page_image_prompt(materials, result)
        output_path, receipt_path = _canonical_prompt_paths(root, page_number)
        v6_media.ensure_secure_directory(root, output_path.parent.relative_to(root))
        existing = None
        if output_path.is_file() and receipt_path.is_file():
            try:
                existing = _read_json(receipt_path)
            except (OSError, ValueError):
                existing = None
        prompt_digest = hashlib.sha256(source_bytes).hexdigest()
        receipt = {
            "schema_version": "awesome-page-image-prompt-receipt-v1",
            "plugin_id": PLUGIN_ID,
            "plugin_version": PLUGIN_VERSION,
            "workflow_contract": WORKFLOW_VERSION,
            "source_identity": state["source_identity"],
            "page_number": page_number,
            "ui_revision": state["confirmed_ui_revision"],
            "ui_digest": state["confirmed_ui_digest"],
            "page_material_digest": material_digest,
            "prompt_output_path": output_path.relative_to(root).as_posix(),
            "prompt_output_sha256": prompt_digest,
            "selected_reference_ids": list(result["selected_reference_images"]),
        }
        if existing is not None:
            existing_bytes = v6_media._read_file_limited(root, output_path)
            if existing != receipt or existing_bytes != source_bytes:
                raise ValueError("validated page image prompt is already sealed with different bytes")
        else:
            if receipt_path.exists():
                raise ValueError("validated page image prompt has an invalid prior receipt")
            if output_path.exists():
                orphan_bytes = v6_media._read_file_limited(root, output_path)
                if orphan_bytes != source_bytes:
                    raise ValueError("validated page image prompt orphan conflicts with current compiler bytes")
            else:
                v6_media._write_new(root, output_path, source_bytes)
            try:
                v6_media._write_new(
                    root, receipt_path,
                    (json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
                )
            except Exception:
                # The verified output is a recoverable pending publication; receipt-last is authority.
                raise
    return {**receipt, "path": str(output_path)}


def _reference_records(materials: Mapping[str, Any]) -> dict[str, tuple[Path, str]]:
    records: dict[str, tuple[Path, str]] = {}
    for item in materials.get("word_images", []):
        if isinstance(item, Mapping):
            records[f"word:{item['asset_id']}"] = (Path(str(item["path"])), str(item["sha256"]))
    for item in materials.get("attachment_inputs", []):
        if not isinstance(item, Mapping):
            continue
        asset_id = str(item["asset_id"])
        if str(item.get("media_type", "")).startswith("image/"):
            records[f"attachment:{asset_id}:original"] = (Path(str(item["path"])), str(item["sha256"]))
        receipt = item.get("render_receipt")
        if isinstance(receipt, Mapping):
            for page in receipt.get("pages", []):
                if isinstance(page, Mapping):
                    records[f"attachment:{asset_id}:page:{page['page_number']}"] = (
                        Path(str(page["path"])), str(page["sha256"]),
                    )
    return records


def load_validated_image_request(project: Path, page_number: int) -> ImageRequest:
    """Load only a current, sealed, byte-verified prompt and its selected page-owned images."""
    secure_io.reject_reparse_chain(Path(project))
    root = Path(project).resolve(strict=True)
    state = load(root)
    materials, _material_bytes, material_digest = _material_authority(root, state, page_number)
    output_path, receipt_path = _canonical_prompt_paths(root, page_number)
    if not output_path.is_file() or not receipt_path.is_file():
        raise ValueError("image generation requires a current validated page image prompt artifact")
    output_bytes = v6_media._read_file_limited(root, output_path)
    receipt_bytes = v6_media._read_file_limited(root, receipt_path)
    try:
        result = load_json_no_duplicates(output_bytes.decode("utf-8"))
        receipt = load_json_no_duplicates(receipt_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("validated page image prompt artifact is corrupt") from exc
    if not isinstance(result, dict) or not isinstance(receipt, dict):
        raise ValueError("validated page image prompt artifact is invalid")
    expected = {
        "schema_version": "awesome-page-image-prompt-receipt-v1",
        "plugin_id": PLUGIN_ID, "plugin_version": PLUGIN_VERSION,
        "workflow_contract": WORKFLOW_VERSION, "page_number": page_number,
        "source_identity": state["source_identity"],
        "ui_revision": state["confirmed_ui_revision"], "ui_digest": state["confirmed_ui_digest"],
        "page_material_digest": material_digest,
        "prompt_output_path": output_path.relative_to(root).as_posix(),
        "prompt_output_sha256": hashlib.sha256(output_bytes).hexdigest(),
        "selected_reference_ids": list(result.get("selected_reference_images", [])),
    }
    if receipt != expected:
        raise ValueError("validated page image prompt receipt does not match current project authority")
    validate_page_image_prompt(materials, result)
    selected = tuple(result["selected_reference_images"])
    if len(selected) > 16:
        raise ValueError("Image2 accepts at most 16 selected references")
    records = _reference_records(materials)
    sections = _prompt_sections(result["image_prompt"])
    plan = _prompt_embedded_json(sections["Visual Presentation"], "DESIGN_PLAN_JSON")
    treatments = {item["reference_id"]: item for item in plan["reference_treatments"]}
    images: list[Path] = []
    roles: list[str] = []
    digests: list[str] = []
    snapshot_dir = v6_media.ensure_secure_directory(
        root, Path("04_v6") / "request_inputs" / expected["prompt_output_sha256"] / f"page_{page_number:03d}",
    )
    for index, reference_id in enumerate(selected, start=1):
        if reference_id not in records:
            raise ValueError(f"selected reference is not page-owned: {reference_id}")
        relative, digest = records[reference_id]
        original = root / relative
        data = _verified_image_bytes(original, digest, project_root=root)
        if data is None:
            raise ValueError(f"selected reference changed before provider invocation: {reference_id}")
        snapshot = snapshot_dir / f"{index:02d}.{digest}.img"
        if snapshot.exists():
            if v6_media._read_file_limited(root, snapshot) != data:
                raise ValueError("selected reference snapshot conflicts with sealed request")
        else:
            v6_media._write_new(root, snapshot, data)
        treatment = treatments[reference_id]
        roles.append(
            f"{reference_id};preserve={treatment['preserve']};change={treatment['change']};"
            f"crop={treatment['crop']};placement={treatment['placement']}"
        )
        images.append(snapshot)
        digests.append(digest)
    quality = initial_quality({
        "effective_body": "\n".join(
            str(item.get("text", "")) for item in materials.get("complete_word_content", [])
            if isinstance(item, Mapping)
        ),
        "chart_facts": [item for item in materials.get("attachment_inputs", [])
                        if isinstance(item, Mapping) and "chart" in str(item.get("media_type", "")).casefold()],
        "attachment_extracts": materials.get("attachment_inputs", []),
        "image_requirements": [
            {"visual": treatment.get("preserve", ""), "purpose": reference_id}
            for reference_id, treatment in treatments.items()
        ],
    })
    return ImageRequest(
        operation="edit" if selected else "generate", quality=quality,
        prompt=result["image_prompt"], input_images=tuple(images), image_roles=tuple(roles),
        input_sha256s=tuple(digests), selected_reference_ids=selected,
        ui_revision=int(state["confirmed_ui_revision"]), ui_digest=str(state["confirmed_ui_digest"]),
        page_material_digest=material_digest,
        prompt_output_sha256=expected["prompt_output_sha256"],
        source_identity=str(state["source_identity"]),
        project_root=root, page_number=page_number,
    )


def build_prompt(
    *,
    global_visual_contract: Mapping[str, Any],
    confirmed_page: Mapping[str, Any],
    qa_feedback: list[str] | None = None,
) -> str:
    """Compile only a frozen V6 result page; there is no raw-material fallback."""
    return compile_confirmed_page_prompt(
        global_visual_contract, confirmed_page, qa_feedback or (),
    )


def _absolute_without_resolving(path: Path, *, base: Path | None = None) -> Path:
    if path.is_absolute():
        return path
    return (base if base is not None else Path.cwd()) / path


def _verified_image_bytes(
    path: Path, expected_sha256: str, *, project_root: Path | None = None,
) -> bytes | None:
    """Return one bounded, handle-contained, fully decoded image buffer."""
    candidate = _absolute_without_resolving(path, base=project_root)
    stable_root = project_root if project_root is not None else candidate.parent
    try:
        data = v6_media._read_file_limited(Path(stable_root), candidate)
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            return None
        decoded, _mime_type = v6_media._open_raster(data)
        decoded.close()
        return data
    except (OSError, ValueError):
        return None


def _resolved_confirmed_page(
    confirmed_page: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[Path, ...], tuple[str, ...], tuple[str, ...]]:
    resolved_page = copy.deepcopy(dict(confirmed_page))
    valid_references: list[dict[str, Any]] = []
    images: list[Path] = []
    roles: list[str] = []
    digests: list[str] = []
    for reference in resolved_page.get("reference_images", []):
        if not isinstance(reference, Mapping) or reference.get("status") != "available":
            continue
        raw_path = reference.get("model_input_path")
        integrity = reference.get("integrity")
        expected = integrity.get("model_input_sha256") if isinstance(integrity, Mapping) else None
        role = reference.get("purpose")
        if (
            not isinstance(raw_path, str)
            or not isinstance(expected, str)
            or not isinstance(role, str)
            or not role.strip()
        ):
            continue
        path = _absolute_without_resolving(Path(raw_path))
        if _verified_image_bytes(path, expected) is None:
            continue
        valid_references.append(copy.deepcopy(dict(reference)))
        images.append(path)
        roles.append(role.strip())
        digests.append(expected)
    resolved_page["reference_images"] = valid_references
    return resolved_page, tuple(images), tuple(roles), tuple(digests)


def build_image_request(
    *,
    confirmed_page: Mapping[str, Any],
    visual_contract: Mapping[str, Any],
    qa_feedback: Sequence[str] = (),
) -> ImageRequest:
    """Resolve usable frozen references, then select the only valid operation."""
    resolved_page, images, roles, digests = _resolved_confirmed_page(confirmed_page)
    if len(images) > 16:
        raise ValueError("Image2 accepts at most 16 compiler-selected reference images")
    prompt = build_prompt(
        global_visual_contract=visual_contract,
        confirmed_page=resolved_page,
        qa_feedback=list(qa_feedback),
    )
    if len(prompt) > _PROMPT_LIMIT:
        raise ValueError("V6 fully compiled prompt exceeds the 32,000-character prompt limit")
    return ImageRequest(
        operation="edit" if images else "generate",
        quality=initial_quality(resolved_page),
        prompt=prompt,
        input_images=images,
        image_roles=roles,
        input_sha256s=digests,
    )


def build_image_command(
    request: ImageRequest, *, prompt_file: Path, output: Path, trace: Path,
) -> list[str]:
    if request.operation not in {"generate", "edit"}:
        raise ValueError("Image2 operation must be generate or edit")
    if request.quality not in {"medium", "high"}:
        raise ValueError("Image2 quality must be medium or high")
    if len(request.input_images) != len(request.image_roles):
        raise ValueError("Image2 image roles must align with image inputs")
    if len(request.input_images) != len(request.input_sha256s):
        raise ValueError("Image2 input digests must align with image inputs")
    if len(request.input_images) > 16:
        raise ValueError("Image2 accepts at most 16 image inputs")
    if request.operation == "edit" and not request.input_images:
        raise ValueError("Image2 edit requires at least one image input")
    if request.operation == "generate" and request.input_images:
        raise ValueError("Image2 generate cannot carry image inputs")
    command = [
        sys.executable,
        str(IMAGE_CLI),
        request.operation,
        "--prompt-file",
        str(prompt_file),
        "--model",
        request.model,
        "--size",
        request.size,
        "--quality",
        request.quality,
        "--request-capability",
        str(request.capability_path) if request.capability_path else "",
        "--prompt-sha256",
        hashlib.sha256(request.prompt.encode("utf-8")).hexdigest(),
        "--workflow-project",
        str(request.project_root) if request.project_root else "",
        "--workflow-page",
        str(request.page_number or 0),
    ]
    if request.capability_path:
        try:
            capability = load_json_no_duplicates(secure_io.read_bytes(
                request.project_root, request.capability_path.relative_to(request.project_root)
            ).decode("utf-8"))
            expected_output = request.project_root / capability["output_path"]
            expected_trace = request.project_root / capability["trace_path"]
        except (OSError, ValueError, KeyError, TypeError):
            capability = None
        if capability is not None and output.resolve(strict=False) == expected_output.resolve(strict=False) and trace.resolve(strict=False) == expected_trace.resolve(strict=False):
            command.extend(["--out", str(output), "--trace-out", str(trace)])
    for path, role, expected_digest in zip(
        request.input_images, request.image_roles, request.input_sha256s,
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
            raise ValueError("Image2 input digest is invalid")
        if _verified_image_bytes(path, expected_digest) is None:
            raise ValueError(f"Image2 request input changed after confirmation: {path}")
        command.extend([
            "--image", str(path),
            "--image-role", role,
            "--image-sha256", expected_digest,
        ])
    return command


def _request_input_records(request: ImageRequest) -> list[dict[str, str]]:
    records = []
    if len(request.input_images) != len(request.input_sha256s):
        raise ValueError("Image2 request input digests are not aligned")
    for path, role, expected in zip(
        request.input_images, request.image_roles, request.input_sha256s,
    ):
        if _verified_image_bytes(path, expected) is None:
            raise ValueError(f"Image2 request input changed after confirmation: {path}")
        records.append({"role": role, "path": str(path), "sha256": expected})
    return records


def _run(command: list[str], timeout: int) -> None:
    command = list(command)
    for internal in ("--out", "--trace-out"):
        if internal in command:
            index = command.index(internal)
            del command[index:index + 2]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, errors="replace", timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise ProviderFailure("Image2 provider request timed out", network=True) from exc
    if completed.returncode != 0:
        message = completed.stderr or completed.stdout or "Image2 generation failed"
        for line in message.splitlines():
            if not line.startswith(_PROVIDER_ERROR_PREFIX):
                continue
            try:
                value = json.loads(line[len(_PROVIDER_ERROR_PREFIX):])
            except json.JSONDecodeError:
                break
            if isinstance(value, Mapping):
                status = value.get("status_code")
                raise ProviderFailure(
                    str(value.get("message") or "Image2 provider request failed"),
                    status_code=status if type(status) is int else None,
                    network=value.get("network") is True,
                )
        raise ProviderFailure(message)


def _with_project_reference_paths(
    root: Path, page: Mapping[str, Any], *, sealed_digest: str, page_number: int,
) -> dict[str, Any]:
    """Resolve and snapshot frozen model inputs without changing prompt semantics."""
    resolved_page = copy.deepcopy(dict(page))
    snapshot_dir = root / "04_v6" / "request_inputs" / sealed_digest / f"page_{page_number:03d}"
    for index, reference in enumerate(resolved_page.get("reference_images", []), start=1):
        if not isinstance(reference, dict):
            continue
        raw = reference.get("model_input_path")
        if not isinstance(raw, str):
            continue
        candidate = _absolute_without_resolving(Path(raw), base=root)
        integrity = reference.get("integrity")
        expected = integrity.get("model_input_sha256") if isinstance(integrity, Mapping) else None
        if not isinstance(expected, str):
            reference["status"] = "unavailable"
            continue
        data = _verified_image_bytes(candidate, expected, project_root=root)
        if data is None:
            reference["status"] = "unavailable"
            continue
        snapshot = snapshot_dir / f"{index:02d}.{expected}.img"
        try:
            if snapshot.exists():
                if secure_io.read_bytes(root, snapshot.relative_to(root)) != data:
                    reference["status"] = "unavailable"
                    continue
            else:
                secure_io.atomic_write_bytes(root, snapshot.relative_to(root), data)
                written_digest = hashlib.sha256(data).hexdigest()
                if written_digest != expected:
                    reference["status"] = "unavailable"
                    continue
        except (OSError, ValueError):
            reference["status"] = "unavailable"
            continue
        reference["model_input_path"] = str(snapshot)
    return resolved_page


def _verified_existing_receipt(
    root: Path,
    page_number: int,
    *,
    confirmed_revision: int,
    confirmed_digest: str,
    request: ImageRequest,
) -> dict[str, Any] | None:
    receipt_path = root / "04_v6" / "images" / f"page_{page_number:03d}.json"
    if not receipt_path.is_file():
        return None
    try:
        receipt = _read_json(receipt_path)
        selected = receipt["selected"]
    except (KeyError, OSError, ValueError):
        return None
    try:
        expected_inputs = _request_input_records(request)
    except (OSError, ValueError):
        return None
    prompt_sha256 = hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()
    expected_identity = _image_request_identity(
        request, revision_digest=confirmed_digest, prompt_sha256=prompt_sha256,
    )
    if (
        receipt.get("artifact_version") != "awesome-image2-request-v1"
        or receipt.get("plugin_id") != request.plugin_id
        or receipt.get("plugin_version") != request.plugin_version
        or receipt.get("workflow_contract") != request.workflow_contract
        or receipt.get("page_number") != page_number
        or receipt.get("confirmed_ui_revision") != confirmed_revision
        or receipt.get("confirmed_ui_digest") != confirmed_digest
        or receipt.get("request_operation") != request.operation
        or receipt.get("request_quality") != request.quality
        or receipt.get("request_prompt_sha256") != prompt_sha256
        or receipt.get("page_material_digest") != request.page_material_digest
        or receipt.get("prompt_output_sha256") != request.prompt_output_sha256
        or receipt.get("source_identity") != request.source_identity
        or receipt.get("selected_reference_ids") != list(request.selected_reference_ids)
        or receipt.get("request_model") != request.model
        or receipt.get("request_size") != request.size
        or receipt.get("request_input_sha256s") != list(request.input_sha256s)
        or receipt.get("request_input_images") != expected_inputs
        or receipt.get("request_identity") != expected_identity
        or not isinstance(receipt.get("state"), str)
        or receipt.get("state") != "accepted"
        or not isinstance(receipt.get("degraded_reasons"), list)
        or not _receipt_candidates_are_valid(
            root,
            receipt.get("candidates"),
            page_number=page_number,
            selected=selected,
            request=request,
            revision_digest=confirmed_digest,
        )
        or receipt.get("candidates_sha256") != canonical_sha256(receipt.get("candidates"))
    ):
        return None
    return receipt


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Commit JSON as one replace so a crash cannot expose a partial receipt."""
    parts = path.parts
    if "04_v6" not in parts:
        raise ValueError("image receipt must remain in canonical project storage")
    root = Path(*parts[:parts.index("04_v6")])
    secure_io.atomic_write_bytes(
        root, path.relative_to(root),
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        replace=path.exists(),
    )


def _finalization_boundary(_stage: str) -> None:
    """Fault-injection seam for verifying recoverable finalization boundaries."""


def _committed_receipt_matches(
    root: Path, receipt_path: Path, expected: Mapping[str, Any], *, request: ImageRequest,
) -> bool:
    """Verify the atomic receipt bytes and all candidate outputs before state commit."""
    try:
        committed = _read_json(receipt_path)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False
    if committed != dict(expected):
        return False
    return _receipt_candidates_are_valid(
        root,
        committed.get("candidates"),
        page_number=committed.get("page_number"),
        selected=committed.get("selected"),
        request=request,
        revision_digest=str(committed.get("confirmed_ui_digest", "")),
    )


def _receipt_candidates_are_valid(
    root: Path,
    candidates: Any,
    *,
    page_number: Any,
    selected: Any,
    request: ImageRequest,
    revision_digest: str,
) -> bool:
    if (
        type(page_number) is not int
        or page_number < 1
        or not isinstance(candidates, list)
        or not 1 <= len(candidates) <= 2
    ):
        return False
    attempts = [
        candidate.get("attempt")
        for candidate in candidates
        if isinstance(candidate, Mapping)
    ]
    if (
        len(attempts) != len(candidates)
        or any(type(attempt) is not int or attempt not in {1, 2} for attempt in attempts)
        or attempts not in ([1], [2], [1, 2])
        or sum(candidate == selected for candidate in candidates) != 1
    ):
        return False
    return all(
        _candidate_artifact_is_valid(
            root, candidate, page_number=page_number, request=request,
        )
        and _candidate_receipt_integrity_is_valid(
            root, candidate, request=request, revision_digest=revision_digest,
        )
        for candidate in candidates
    )


def _candidate_receipt_integrity(
    root: Path,
    candidate: Mapping[str, Any],
    *,
    request: ImageRequest,
    revision_digest: str,
) -> dict[str, str] | None:
    """Compute receipt-only identity from stable candidate, prompt, and trace bytes."""
    attempt = candidate.get("attempt")
    relative = candidate.get("path")
    if type(attempt) is not int or attempt not in {1, 2} or not isinstance(relative, str):
        return None
    image = root / relative
    prompt = image.with_suffix(".prompt.txt")
    trace = image.with_suffix(".trace.json")
    try:
        image_data = v6_media._read_file_limited(root, image)
        prompt_data = v6_media._read_file_limited(root, prompt)
        trace_data = v6_media._read_file_limited(root, trace)
        prompt_text = prompt_data.decode("utf-8")
        trace_value = json.loads(trace_data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None
    if attempt == 1 and prompt_text != request.prompt:
        return None
    quality = _candidate_quality(request, attempt)
    prompt_sha256 = hashlib.sha256(prompt_data).hexdigest()
    return {
        "quality": quality,
        "prompt_sha256": prompt_sha256,
        "request_identity": _image_request_identity(
            request, revision_digest=revision_digest, prompt_sha256=prompt_sha256,
            quality=quality,
        ),
        "output_sha256": hashlib.sha256(image_data).hexdigest(),
        "trace_sha256": hashlib.sha256(trace_data).hexdigest(),
        "trace_semantics_sha256": canonical_sha256(trace_value),
    }


def _candidate_receipt_integrity_is_valid(
    root: Path,
    candidate: Mapping[str, Any],
    *,
    request: ImageRequest,
    revision_digest: str,
) -> bool:
    expected = _candidate_receipt_integrity(
        root, candidate, request=request, revision_digest=revision_digest,
    )
    return expected is not None and all(
        candidate.get(key) == value for key, value in expected.items()
    )


def _enrich_candidate_receipt(
    root: Path,
    candidate: Mapping[str, Any],
    *,
    request: ImageRequest,
    revision_digest: str,
) -> dict[str, Any] | None:
    integrity = _candidate_receipt_integrity(
        root, candidate, request=request, revision_digest=revision_digest,
    )
    if integrity is None:
        return None
    integrity_fields = set(integrity)
    if integrity_fields.intersection(candidate) and any(
        candidate.get(key) != value for key, value in integrity.items()
    ):
        return None
    enriched = copy.deepcopy(dict(candidate))
    enriched.update(integrity)
    return enriched


def _candidate_artifact_is_valid(
    root: Path,
    candidate: Any,
    *,
    page_number: int,
    request: ImageRequest,
) -> bool:
    if (
        not isinstance(candidate, Mapping)
        or type(candidate.get("attempt")) is not int
        or candidate["attempt"] not in {1, 2}
        or candidate.get("operation") != request.operation
        or not isinstance(candidate.get("path"), str)
    ):
        return False
    relative = Path(candidate["path"])
    if relative.is_absolute() or ".." in relative.parts or relative.suffix.casefold() != ".png":
        return False
    image = root / relative
    trace = image.with_suffix(".trace.json")
    try:
        data = v6_media._read_file_limited(root, image)
        digest = hashlib.sha256(data).hexdigest()
        decoded, mime_type = v6_media._open_raster(data)
        try:
            dimensions = decoded.size
        finally:
            decoded.close()
        trace_data = v6_media._read_file_limited(root, trace)
        trace_value = json.loads(trace_data.decode("utf-8"))
        canonical_image = image.resolve(strict=True)
        canonical_relative = canonical_image.relative_to(root.resolve(strict=True)).as_posix()
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False
    if (
        mime_type != "image/png"
        or canonical_relative != candidate["path"]
        or re.fullmatch(
            rf"04_v6/images/page_{page_number:03d}"
            rf"(?:\.generation_[1-9][0-9]*)?\.candidate_{candidate['attempt']}\.png",
            canonical_relative,
        ) is None
        or dimensions != (1904, 896)
        or not isinstance(trace_value, Mapping)
        or trace_value.get("operation") != request.operation
        or trace_value.get("model") != "gpt-image-2"
        or trace_value.get("quality") != _candidate_quality(request, candidate["attempt"])
        or trace_value.get("size") != "1904x896"
        or trace_value.get("input_images") != _request_input_records(request)
        or not isinstance(trace_value.get("outputs"), list)
    ):
        return False
    canonical_text = str(canonical_image)
    for output in trace_value["outputs"]:
        if not isinstance(output, Mapping) or output.get("path") != canonical_text:
            continue
        if output.get("sha256") != digest:
            return False
        if output.get("mime_type") != "image/png":
            return False
        traced_mimes = [output[key] for key in ("mime", "content_type") if key in output]
        return all(value == "image/png" for value in traced_mimes)
    return False


def _candidate_quality(request: ImageRequest, attempt: int) -> str:
    return "high" if attempt == 2 and request.quality == "medium" else request.quality


def _adaptive_receipt(
    *,
    page_number: int,
    confirmed_revision: int,
    confirmed_digest: str,
    request: ImageRequest,
    candidates: Sequence[Mapping[str, Any]],
    selected: Mapping[str, Any],
    state: str,
    degraded_reasons: Sequence[str],
) -> dict[str, Any]:
    prompt_sha256 = hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()
    candidate_values = sorted(
        (copy.deepcopy(dict(candidate)) for candidate in candidates),
        key=lambda candidate: candidate["attempt"],
    )
    selected_value = copy.deepcopy(dict(selected))
    return {
        "artifact_version": "awesome-image2-request-v1",
        "plugin_id": request.plugin_id,
        "plugin_version": request.plugin_version,
        "workflow_contract": request.workflow_contract,
        "page_number": page_number,
        "confirmed_ui_revision": confirmed_revision,
        "confirmed_ui_digest": confirmed_digest,
        "request_prompt_sha256": prompt_sha256,
        "page_material_digest": request.page_material_digest,
        "prompt_output_sha256": request.prompt_output_sha256,
        "source_identity": request.source_identity,
        "selected_reference_ids": list(request.selected_reference_ids),
        "request_model": request.model,
        "request_size": request.size,
        "request_operation": request.operation,
        "request_quality": request.quality,
        "request_input_sha256s": list(request.input_sha256s),
        "request_input_images": _request_input_records(request),
        "request_identity": _image_request_identity(
            request, revision_digest=confirmed_digest, prompt_sha256=prompt_sha256,
        ),
        "candidates": candidate_values,
        "candidates_sha256": canonical_sha256(candidate_values),
        "selected": selected_value,
        "state": state,
        "degraded_reasons": list(degraded_reasons),
    }


def _receipt_from_accepted_page(
    root: Path,
    page_number: int,
    *,
    page: Mapping[str, Any],
    confirmed: Mapping[str, Any],
    confirmed_digest: str,
    request: ImageRequest,
) -> dict[str, Any] | None:
    """Recover the old accepted-before-receipt crash window from fenced page state."""
    if page.get("state") not in {
        "accepted", "reconstructing", "page_complete",
    }:
        return None
    selected = page.get("selected_candidate")
    if not _candidate_artifact_is_valid(
        root, selected, page_number=page_number, request=request,
    ):
        return None
    candidates: list[dict[str, Any]] = []
    first = page.get("first_candidate")
    same_artifact = isinstance(first, Mapping) and all(
        first.get(field) == selected.get(field) for field in ("attempt", "path", "operation")
    )
    if (
        isinstance(first, Mapping)
        and first.get("attempt") == 1
        and not same_artifact
        and _candidate_artifact_is_valid(
            root, first, page_number=page_number, request=request,
        )
    ):
        first_enriched = _enrich_candidate_receipt(
            root, first, request=request, revision_digest=confirmed_digest,
        )
        if first_enriched is None:
            return None
        candidates.append(first_enriched)
    selected_copy = _enrich_candidate_receipt(
        root, selected, request=request, revision_digest=confirmed_digest,
    )
    if selected_copy is None:
        return None
    if selected_copy not in candidates:
        candidates.append(selected_copy)
    return _adaptive_receipt(
        page_number=page_number,
        confirmed_revision=int(confirmed["revision"]),
        confirmed_digest=confirmed_digest,
        request=request,
        candidates=candidates,
        selected=selected_copy,
        state="accepted",
        degraded_reasons=list(page.get("degraded_reasons", [])),
    )


def _advance_page_to_accepted_receipt(
    page: Mapping[str, Any], receipt: Mapping[str, Any],
) -> dict[str, Any]:
    updated = copy.deepcopy(dict(page))
    target = str(receipt["state"])
    if updated["state"] in {"accepted", "reconstructing", "page_complete"}:
        return updated
    if updated["state"] == "technical_failed":
        updated = transition_page(updated, "generating")
    if updated["state"] == "prepared":
        updated = transition_page(updated, "generating")
    if updated["state"] == "generating":
        updated = transition_page(updated, "qa_review")
    if updated["state"] != "qa_review":
        raise ValueError("V6 page cannot resume an accepted receipt from its current state")
    first_candidate = next(
        (candidate for candidate in receipt["candidates"] if candidate.get("attempt") == 1),
        None,
    )
    updated["first_candidate"] = copy.deepcopy(first_candidate)
    updated["selected_candidate"] = copy.deepcopy(receipt["selected"])
    updated["degraded_reasons"] = list(receipt["degraded_reasons"])
    return transition_page(updated, target)


def generate_page_body(
    project: Path,
    *,
    page_number: int,
    timeout: int = 900,
    max_candidates: int = 2,
    runner: Callable[[list[str], int], None] = _run,
    reviewer: Callable[..., dict[str, Any]] = review_candidate,
    retry_sleep: Callable[[float], None] | None = None,
    retry_jitter: Callable[[], float] | None = None,
) -> dict[str, Any]:
    secure_io.reject_reparse_chain(Path(project))
    root = Path(project).resolve()
    ownership_ttl = min(
        max(float(timeout) * 6.0 + float(QA_TIMEOUT_SECONDS) * 2.0 + 300.0, 900.0),
        86_400.0,
    )
    ownership = ProjectPageOwnership(root, stale_after=ownership_ttl)
    with ownership.own(page_number=page_number, wait_timeout=ownership_ttl) as lease:
        return _generate_page_body_owned(
            root,
            page_number=page_number,
            timeout=timeout,
            max_candidates=max_candidates,
            runner=runner,
            reviewer=reviewer,
            retry_sleep=retry_sleep,
            retry_jitter=retry_jitter,
            page_ownership=ownership,
            ownership_lease=lease,
        )


def _generate_page_body_owned(
    project: Path,
    *,
    page_number: int,
    timeout: int,
    max_candidates: int,
    runner: Callable[[list[str], int], None],
    reviewer: Callable[..., dict[str, Any]],
    retry_sleep: Callable[[float], None] | None,
    retry_jitter: Callable[[], float] | None,
    page_ownership: ProjectPageOwnership,
    ownership_lease: PageOwnershipLease,
) -> dict[str, Any]:
    if max_candidates < 1:
        raise ValueError("max_candidates must be positive")
    max_candidates = min(max_candidates, _MAX_CANDIDATES)
    secure_io.reject_reparse_chain(Path(project))
    root = Path(project).resolve()
    state = load(root)
    if state["style_confirmation"]["status"] != "confirmed":
        raise ValueError("V6 style must be confirmed before Image2 generation")
    if state["page_materials_status"] != "confirmed":
        raise ValueError(
            "V6 page materials are not prepared; run prepare-page-materials before Image2 generation"
        )
    page_index = page_number - 1
    if page_index < 0 or page_index >= len(state["pages"]):
        raise ValueError("V6 page number is out of range")
    page = state["pages"][page_index]
    validated_request = load_validated_image_request(root, page_number)
    global_contract = dict(state["style_confirmation"]["contract"])
    material_path = root / state["pages"][page_index]["material_receipt"]["path"]
    resolved_request_page = _read_json(material_path)
    confirmed = {"revision": state["confirmed_ui_revision"], "production_profile": "balanced"}
    initial_request = validated_request
    profile = str(confirmed.get("production_profile") or "balanced")
    provider_scheduler = AdaptiveScheduler.for_profile(profile)
    lease_ttl = min(max(float(timeout) * 3.0 + 120.0, 300.0), 86_400.0)
    project_gate = ProjectGenerationGate(
        root, profile=profile, stale_after=lease_ttl,
    )

    def require_current_owner() -> None:
        page_ownership.assert_current(ownership_lease)

    existing = _verified_existing_receipt(
        root,
        page_number,
        confirmed_revision=int(confirmed["revision"]),
        confirmed_digest=str(state["confirmed_ui_digest"]),
        request=initial_request,
    )
    if existing is not None and page["state"] in {
        "accepted", "reconstructing", "page_complete",
    }:
        return existing
    if existing is not None and page["state"] in {"prepared", "generating", "qa_review", "technical_failed"}:
        page = _advance_page_to_accepted_receipt(page, existing)
        require_current_owner()
        update_page(root, page_number, page)
        return existing
    recovered = _receipt_from_accepted_page(
        root,
        page_number,
        page=page,
        confirmed=confirmed,
        confirmed_digest=str(state["confirmed_ui_digest"]),
        request=initial_request,
    )
    if recovered is not None:
        receipt_path = root / "04_v6" / "images" / f"page_{page_number:03d}.json"
        page_ownership.commit_if_current(
            ownership_lease, lambda: _atomic_write_json(receipt_path, recovered),
        )
        require_current_owner()
        verified_recovery = _verified_existing_receipt(
            root,
            page_number,
            confirmed_revision=int(confirmed["revision"]),
            confirmed_digest=str(state["confirmed_ui_digest"]),
            request=initial_request,
        )
        if verified_recovery is None:
            raise RuntimeError("V6 recovered Image2 receipt failed verification")
        return verified_recovery
    if page["state"] == "accepted":
        page["technical_failure"] = {
            "stage": "accepted_artifact_recovery",
            "reason": "missing_or_invalid_receipt_or_candidate",
        }
        page["degraded_reasons"] = list(dict.fromkeys([
            *page["degraded_reasons"], "accepted_artifact_recovery_failed",
        ]))
        page = transition_page(page, "technical_failed")
    elif page["state"] in {"reconstructing", "page_complete"}:
        raise RuntimeError("V6 completed page has no recoverable Image2 receipt")
    logo_name = Path(state["logo_source"]["path"]).stem
    directory = v6_media.ensure_secure_directory(root, Path("04_v6") / "images")

    if page["state"] in {"prepared", "technical_failed"}:
        page = transition_page(page, "generating")
    require_current_owner()
    update_page(root, page_number, page)

    candidates = []
    first_qa = None
    selected = None
    degraded_reason = None
    feedback: list[str] | None = None
    for attempt in range(1, max_candidates + 1):
        generation_marker = (
            "" if ownership_lease.generation == 1
            else f".generation_{ownership_lease.generation}"
        )
        output = directory / f"page_{page_number:03d}{generation_marker}.candidate_{attempt}.png"
        prompt_file = directory / f"page_{page_number:03d}{generation_marker}.candidate_{attempt}.prompt.txt"
        trace = directory / f"page_{page_number:03d}{generation_marker}.candidate_{attempt}.trace.json"
        request = ImageRequest(
            operation=initial_request.operation,
            quality=(
                "high"
                if attempt == 2 and initial_request.quality == "medium"
                else initial_request.quality
            ),
            prompt=initial_request.prompt,
            input_images=initial_request.input_images,
            image_roles=initial_request.image_roles,
            input_sha256s=initial_request.input_sha256s,
            model=initial_request.model,
            size=initial_request.size,
            selected_reference_ids=initial_request.selected_reference_ids,
            plugin_id=initial_request.plugin_id,
            plugin_version=initial_request.plugin_version,
            workflow_contract=initial_request.workflow_contract,
            ui_revision=initial_request.ui_revision,
            ui_digest=initial_request.ui_digest,
            page_material_digest=initial_request.page_material_digest,
            prompt_output_sha256=initial_request.prompt_output_sha256,
            source_identity=initial_request.source_identity,
            capability_path=initial_request.capability_path,
            project_root=initial_request.project_root,
            page_number=initial_request.page_number,
        )
        request = replace(request, capability_path=_issue_capability(
            request, attempt=attempt, output=output, trace=trace,
        ))
        secure_io.atomic_write_bytes(
            root, prompt_file.relative_to(root), request.prompt.encode("utf-8"),
        )
        try:
            command = build_image_command(
                request, prompt_file=prompt_file, output=output, trace=trace,
            )
            def invoke_provider() -> None:
                try:
                    runner(command, timeout)
                except Exception as exc:
                    if getattr(exc, "status_code", None) == 429:
                        project_gate.throttle_on_429()
                    raise

            with project_gate.lease(
                page_number=page_number,
                wait_timeout=lease_ttl,
            ):
                provider_scheduler.run_transient(
                    invoke_provider,
                    max_attempts=3,
                    sleep=retry_sleep,
                    jitter=retry_jitter,
                )
            require_current_owner()
        except Exception:
            if attempt == 1:
                page["technical_failure"] = {"stage": "image2_generate", "attempt": attempt}
                page = transition_page(page, "technical_failed")
                require_current_owner()
                update_page(root, page_number, page)
                raise
            degraded_reason = "later_generation_failed"
            break
        if not output.is_file():
            if attempt == 1:
                page["technical_failure"] = {
                    "stage": "image2_generate", "attempt": attempt,
                    "reason": "missing_output",
                }
                page = transition_page(page, "technical_failed")
                require_current_owner()
                update_page(root, page_number, page)
                raise RuntimeError("Image2 generate command produced no output")
            degraded_reason = "later_generation_missing_output"
            break
        mechanical = mechanical_review(
            request=request,
            output=output,
            receipt_inputs={
                "trace_path": trace,
                "visual_contract": global_contract,
            },
        )
        if not mechanical["accepted"]:
            if attempt == 1:
                page["technical_failure"] = {
                    "stage": "mechanical_qa",
                    "attempt": attempt,
                    "result": mechanical,
                }
                page = transition_page(page, "technical_failed")
                require_current_owner()
                update_page(root, page_number, page)
                raise RuntimeError(
                    "V6 candidate artifact failed validation during mechanical review"
                )
            fallback_evidence = {
                "attempt": attempt,
                "result": copy.deepcopy(mechanical),
            }
            candidates[0]["fallback_mechanical_qa"] = fallback_evidence
            page["first_candidate"] = copy.deepcopy(candidates[0])
            degraded_reason = "later_candidate_mechanical_failure"
            break
        candidate = {
            "attempt": attempt,
            "path": output.relative_to(root).as_posix(),
            "operation": request.operation,
            "mechanical_qa": mechanical,
        }
        candidates.append(candidate)
        if attempt == 1:
            page["first_candidate"] = copy.deepcopy(candidate)
        if page["state"] == "generating":
            page = transition_page(page, "qa_review")
        try:
            qa = reviewer(
                root,
                image=output,
                effective_page=filter_confirmed_page_for_prompt(resolved_request_page),
                style_contract=dict(global_contract),
                fixed_logo_name=logo_name,
                timeout=min(timeout, QA_TIMEOUT_SECONDS),
            )
        except Exception:
            degraded_reason = "qa_unavailable"
            break
        require_current_owner()
        candidate["qa"] = qa
        page["qa_attempts"] = attempt
        if attempt == 1:
            first_qa = qa
        if qa["accepted"]:
            selected = candidate
            break
        if attempt > 1 and first_qa is not None and not improved(first_qa, qa):
            degraded_reason = "qa_no_effective_improvement"
            break
        feedback = actionable_retry_feedback(
            qa,
            first_qa if attempt > 1 else None,
        )
        if not feedback:
            degraded_reason = "qa_feedback_not_actionable"
            break
        if page["state"] == "qa_review":
            page = transition_page(page, "generating")

    if selected is None:
        degraded_reason = degraded_reason or "qa_candidate_limit_reached"
        page["selected_candidate"] = None
        page["degraded_reasons"] = list(dict.fromkeys([
            *page["degraded_reasons"], degraded_reason,
        ]))
        page["technical_failure"] = {
            "stage": "independent_visual_review",
            "reason": degraded_reason,
        }
        page = transition_page(page, "technical_failed")
        require_current_owner()
        update_page(root, page_number, page)
        raise RuntimeError("V6 page has no independently accepted Image2 candidate")
    page["selected_candidate"] = copy.deepcopy(selected)
    page = transition_page(page, "accepted")
    if not _candidate_artifact_is_valid(
        root,
        page["selected_candidate"],
        page_number=page_number,
        request=initial_request,
    ):
        page["technical_failure"] = {
            "stage": "candidate_artifact_validation",
            "reason": "invalid_png_or_generation_trace",
        }
        page = transition_page(page, "technical_failed")
        require_current_owner()
        update_page(root, page_number, page)
        raise RuntimeError("V6 selected Image2 candidate artifact failed validation")
    enriched_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        enriched = _enrich_candidate_receipt(
            root,
            candidate,
            request=initial_request,
            revision_digest=str(state["confirmed_ui_digest"]),
        )
        if enriched is None:
            raise RuntimeError("V6 candidate receipt integrity could not be computed")
        enriched_candidates.append(enriched)
    selected_candidate = next(
        (
            candidate for candidate in enriched_candidates
            if candidate.get("attempt") == page["selected_candidate"].get("attempt")
            and candidate.get("path") == page["selected_candidate"].get("path")
        ),
        None,
    )
    if selected_candidate is None:
        raise RuntimeError("V6 selected candidate is absent from the bounded candidate list")
    first_candidate = next(
        (candidate for candidate in enriched_candidates if candidate.get("attempt") == 1),
        None,
    )
    page["first_candidate"] = copy.deepcopy(first_candidate)
    page["selected_candidate"] = copy.deepcopy(selected_candidate)
    receipt = _adaptive_receipt(
        page_number=page_number,
        confirmed_revision=int(confirmed["revision"]),
        confirmed_digest=str(state["confirmed_ui_digest"]),
        request=initial_request,
        candidates=enriched_candidates,
        selected=selected_candidate,
        state=str(page["state"]),
        degraded_reasons=page["degraded_reasons"],
    )
    receipt_path = directory / f"page_{page_number:03d}.json"
    page_ownership.commit_if_current(
        ownership_lease, lambda: _atomic_write_json(receipt_path, receipt),
    )
    _finalization_boundary("after_receipt_commit")
    require_current_owner()
    if not _committed_receipt_matches(root, receipt_path, receipt, request=initial_request):
        raise RuntimeError("V6 Image2 receipt failed verification before accepted state")
    _finalization_boundary("after_receipt_verification")
    require_current_owner()
    update_page(root, page_number, page)
    _finalization_boundary("after_state_commit")
    return receipt

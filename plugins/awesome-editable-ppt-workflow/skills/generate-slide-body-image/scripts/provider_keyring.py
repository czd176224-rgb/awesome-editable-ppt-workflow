"""Private capability signing keyring with one bounded previous key."""
from __future__ import annotations

import hashlib, hmac, json, os, secrets, stat, subprocess, sys, time
from pathlib import Path

WORKFLOW_SCRIPTS = Path(__file__).resolve().parents[2] / "run-word-to-ppt-workflow" / "scripts"
if str(WORKFLOW_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(WORKFLOW_SCRIPTS))
import workflow_v6_secure_io as secure_io

NAME = "awesome-editable-ppt-workflow.image-provider-keyring"
PREVIOUS_TTL = 300
_VERIFIED_ACL_PATHS: set[str] = set()


def _root() -> Path:
    base = Path(os.path.abspath(Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()))
    secure_io.reject_reparse_chain(base)
    root = base / "plugin-secrets" / NAME
    with secure_io.hold_parent(base, Path("plugin-secrets") / NAME / ".authority", create=True):
        pass
    secure_io.reject_reparse_chain(root)
    if os.name == "nt":
        for protected in (base / "plugin-secrets", root):
            identity = os.path.normcase(str(protected))
            if identity not in _VERIFIED_ACL_PATHS:
                _harden_acl(protected)
                _VERIFIED_ACL_PATHS.add(identity)
    return root


def _harden_acl(path: Path) -> None:
    who = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True)
    import re
    match = re.search(r"S-1-5-(?:\d+-)+\d+", who.stdout)
    if match is None:
        raise ValueError("provider keyring owner SID unavailable")
    subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"*{match.group(0)}:(F)",
                    "*S-1-5-18:(F)", "*S-1-5-32-544:(F)"], capture_output=True, text=True, check=True)
    script = ("& { param($p) $ErrorActionPreference='Stop'; "
              "Import-Module (Join-Path $PSHOME 'Modules\\Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1') -Force; "
              "(Get-Acl -LiteralPath $p).Access | % {"
              "$sid=$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value;"
              "Write-Output ($sid+'|'+$_.AccessControlType+'|'+$_.IsInherited)} }")
    acl = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script, str(path)],
                         capture_output=True, text=True, check=True)
    allowed = {match.group(0), "S-1-5-18", "S-1-5-32-544"}
    observed = set()
    for line in acl.stdout.splitlines():
        fields = line.strip().split("|")
        if len(fields) != 3 or fields[1:] != ["Allow", "False"]:
            raise ValueError("provider keyring ACL is inherited or denied")
        sid = fields[0]
        if sid.startswith("S-1-5-5-"):
            continue  # Windows injects the current logon-session identity; it is no broader than the owner.
        observed.add(sid)
    if not allowed.issubset(observed) or not observed.issubset(allowed | {"S-1-3-4"}):
        raise ValueError("provider keyring ACL contains broad or missing trustees")


def _read_regular(path: Path, limit: int = 4096) -> bytes:
    root = _root()
    return secure_io.read_bytes(root, path.relative_to(root), max_bytes=limit)


def _write_new(path: Path, data: bytes) -> None:
    root = _root()
    secure_io.atomic_write_bytes(root, path.relative_to(root), data)
    if os.name == "nt":
        _harden_acl(path)
        _VERIFIED_ACL_PATHS.add(os.path.normcase(str(path)))


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _initialize(root: Path) -> None:
    key_id = "k-" + secrets.token_hex(12); key = secrets.token_bytes(32)
    key_path = root / f"{key_id}.key"
    _write_new(key_path, key)
    value = {"schema_version":"awesome-provider-keyring-v1", "current":key_id,
             "previous":None, "previous_expires_at":None, "generation":1}
    value["manifest_hmac_sha256"] = hmac.new(key, _canonical(value), hashlib.sha256).hexdigest()
    _write_new(root / "manifest.json", _canonical(value) + b"\n")


def _load() -> tuple[dict, dict[str, bytes]]:
    root = _root(); manifest = root / "manifest.json"
    if not manifest.exists():
        try: _initialize(root)
        except FileExistsError: pass
    value = json.loads(_read_regular(manifest), object_pairs_hook=lambda pairs: _no_dupes(pairs))
    signature = value.pop("manifest_hmac_sha256", None)
    ids = [value.get("current"), value.get("previous")]
    keys = {key_id: _read_regular(root / f"{key_id}.key", 32) for key_id in ids if isinstance(key_id, str)}
    current = keys.get(value.get("current"))
    if len(current or b"") != 32 or not isinstance(signature, str) or not hmac.compare_digest(
        signature, hmac.new(current, _canonical(value), hashlib.sha256).hexdigest()):
        raise ValueError("provider keyring manifest authentication failed")
    value["manifest_hmac_sha256"] = signature
    return value, keys


def _no_dupes(pairs):
    out = {}
    for key, value in pairs:
        if key in out: raise ValueError("duplicate keyring JSON key")
        out[key] = value
    return out


def signing_key() -> tuple[str, bytes]:
    value, keys = _load(); return value["current"], keys[value["current"]]


def verification_key(key_id: str) -> bytes:
    value, keys = _load()
    if key_id == value["current"]: return keys[key_id]
    if key_id == value.get("previous") and int(value.get("previous_expires_at") or 0) >= int(time.time()):
        return keys[key_id]
    raise ValueError("provider capability key is revoked or unknown")


def rotate() -> str:
    root = _root(); lock = root / "rotate.lock"
    for _ in range(100):
        try: _write_new(lock, str(os.getpid()).encode()); break
        except FileExistsError: time.sleep(.01)
    else: raise ValueError("provider keyring rotation is busy")
    try:
        value, keys = _load(); old_previous = value.get("previous")
        new_id = "k-" + secrets.token_hex(12); new_key = secrets.token_bytes(32)
        _write_new(root / f"{new_id}.key", new_key)
        updated = {"schema_version":"awesome-provider-keyring-v1", "current":new_id,
                   "previous":value["current"], "previous_expires_at":int(time.time()) + PREVIOUS_TTL,
                   "generation":int(value["generation"]) + 1}
        updated["manifest_hmac_sha256"] = hmac.new(new_key, _canonical(updated), hashlib.sha256).hexdigest()
        secure_io.atomic_write_bytes(root, Path("manifest.json"), _canonical(updated) + b"\n", replace=True)
        if isinstance(old_previous, str): (root / f"{old_previous}.key").unlink(missing_ok=True)
        return new_id
    finally: lock.unlink(missing_ok=True)

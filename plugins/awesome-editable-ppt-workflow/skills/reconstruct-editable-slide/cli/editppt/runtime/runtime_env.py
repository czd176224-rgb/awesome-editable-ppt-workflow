#!/usr/bin/env python3
"""Local reconstruct-editable-slide environment and optional Paddle token."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path


DEFAULT_CONFIG_HOME = "~/.editppt"
DEFAULT_CODEX_AUTH_FILE = "~/.codex/auth.json"
ENV_FIELDS = ("PADDLE_OCR_TOKEN",)
PADDLE_TOKEN_APPLY_URL = "https://aistudio.baidu.com/account/accessToken"


def cli_reinstall_hint() -> str:
    return "`pipx install --force --editable <path-to-reconstruct-editable-slide>/cli`"


def runtime_home() -> Path:
    return Path(os.getenv("EDITPPT_CONFIG_HOME", DEFAULT_CONFIG_HOME)).expanduser()


def config_path(home: Path | None = None) -> Path:
    return (home or runtime_home()) / "config.yaml"


def read_config_file(path: Path) -> dict:
    path = Path(path).expanduser()
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit(f"PyYAML is required. Reinstall with {cli_reinstall_hint()}.") from exc
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise SystemExit(f"Invalid config file: {path}")
    return value


def write_config_file(path: Path, values: dict) -> None:
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit(f"PyYAML is required. Reinstall with {cli_reinstall_hint()}.") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        yaml.safe_dump(
            {"PADDLE_OCR_TOKEN": values["PADDLE_OCR_TOKEN"]}
            if values.get("PADDLE_OCR_TOKEN") else {},
            handle,
            allow_unicode=True,
            sort_keys=True,
        )


def mask_secret(value: str) -> str:
    if not value:
        return ""
    return "****" if len(value) <= 8 else f"{value[:4]}...{value[-4:]}"


def codex_auth_file() -> Path:
    return Path(os.getenv("CODEX_AUTH_FILE", DEFAULT_CODEX_AUTH_FILE)).expanduser()


def codex_oauth_ready() -> bool:
    path = codex_auth_file()
    if not path.exists():
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    tokens = value.get("tokens") if isinstance(value, dict) else None
    return isinstance(tokens, dict) and bool(str(tokens.get("access_token") or "").strip())


def config(args: argparse.Namespace) -> int:
    path = config_path()
    values = read_config_file(path)
    before = dict(values)
    if args.paddle_ocr_token:
        values["PADDLE_OCR_TOKEN"] = args.paddle_ocr_token.strip()
    if before != values or not path.exists():
        write_config_file(path, values)
    print(f"config={'updated' if before != values else 'unchanged'} path={path}")
    print(f"PADDLE_OCR_TOKEN={mask_secret(str(values.get('PADDLE_OCR_TOKEN', ''))) or '<unset>'}")
    return 0


def collect_status() -> dict:
    values = read_config_file(config_path())
    if os.getenv("PADDLE_OCR_TOKEN"):
        values["PADDLE_OCR_TOKEN"] = os.environ["PADDLE_OCR_TOKEN"]
    dependencies = {
        module: importlib.util.find_spec(module) is not None
        for module in ("pypdf", "pypdfium2", "PIL", "yaml", "numpy", "requests")
    }
    codex_ready = codex_oauth_ready()
    return {
        "ok": all(dependencies.values()) and codex_ready,
        "config_home": str(runtime_home()),
        "config_file": str(config_path()),
        "cli_python": sys.executable,
        "dependencies": dependencies,
        "codex_oauth": {"ready": codex_ready, "auth_file": str(codex_auth_file())},
        "paddle": {
            "token": "set" if values.get("PADDLE_OCR_TOKEN") else "unset",
            "policy": "only after the Codex page worker reports unreadable text",
            "apply_url": PADDLE_TOKEN_APPLY_URL,
        },
        "next": "no action needed" if codex_ready else "run `codex login`",
    }


def doctor(args: argparse.Namespace) -> int:
    status = collect_status()
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print(f"cli python: {status['cli_python']}")
        print(f"Codex OAuth={'ready' if status['codex_oauth']['ready'] else 'missing'}")
        print(f"Paddle token={status['paddle']['token']} (conditional only)")
        for module, ready in status["dependencies"].items():
            print(f"python import {module}: {'ok' if ready else 'missing'}")
        print(f"next: {status['next']}")
    return 0 if status["ok"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="editppt", description=__doc__)
    sub = parser.add_subparsers(required=True)
    doc = sub.add_parser("doctor", help="Check dependencies and Codex OAuth.")
    doc.add_argument("--json", action="store_true")
    doc.add_argument("--timeout", type=int, default=30)
    doc.set_defaults(func=doctor)
    cfg = sub.add_parser("config", help="Store the optional Paddle token.")
    cfg.add_argument("--paddle-ocr-token", help=f"Apply at {PADDLE_TOKEN_APPLY_URL}.")
    cfg.set_defaults(func=config)
    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()

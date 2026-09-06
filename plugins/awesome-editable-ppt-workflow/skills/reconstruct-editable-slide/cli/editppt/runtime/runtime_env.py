#!/usr/bin/env python3
"""Local reconstruct-editable-slide dependencies and Codex authentication."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path


DEFAULT_CODEX_AUTH_FILE = "~/.codex/auth.json"


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


def collect_status() -> dict:
    dependencies = {
        module: importlib.util.find_spec(module) is not None
        for module in ("pypdf", "pypdfium2", "PIL", "yaml", "numpy", "requests")
    }
    codex_ready = codex_oauth_ready()
    return {
        "ok": all(dependencies.values()) and codex_ready,
        "cli_python": sys.executable,
        "dependencies": dependencies,
        "codex_oauth": {"ready": codex_ready, "auth_file": str(codex_auth_file())},
        "next": "no action needed" if codex_ready else "run `codex login`",
    }


def doctor(args: argparse.Namespace) -> int:
    status = collect_status()
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print(f"cli python: {status['cli_python']}")
        print(f"Codex OAuth={'ready' if status['codex_oauth']['ready'] else 'missing'}")
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
    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()

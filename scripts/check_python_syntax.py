#!/usr/bin/env python3
"""Compile every shipped Python source in memory without creating pyc paths."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    failures: list[str] = []
    for base_name in ("plugins", "scripts"):
        for path in sorted((root / base_name).rglob("*.py")):
            try:
                source = path.read_text(encoding="utf-8-sig")
                compile(source, path.relative_to(root).as_posix(), "exec")
            except (OSError, SyntaxError, UnicodeError) as exc:
                failures.append(f"{path.relative_to(root).as_posix()}: {exc}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("python-source-syntax=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

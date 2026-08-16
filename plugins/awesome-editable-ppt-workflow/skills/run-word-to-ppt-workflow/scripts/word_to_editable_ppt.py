"""Awesome Word-to-PPT production entry and explicit diagnostic dispatcher."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


TOOLS = {
    "confirm-ui": "confirm_ui/server.py",
    "doctor": "doctor.py",
    "v6": "workflow_v6_cli.py",
}


def main() -> int:
    # Dispatch a recognized command before argparse sees delegated flags such
    # as ``confirm-ui --help``. Otherwise the top-level parser consumes them
    # and hides the selected tool's own lifecycle interface.
    if len(sys.argv) >= 2 and sys.argv[1] in TOOLS:
        target = Path(__file__).resolve().parent / TOOLS[sys.argv[1]]
        return subprocess.call([sys.executable, str(target), *sys.argv[2:]])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=sorted(TOOLS))
    args, remainder = parser.parse_known_args()
    target = Path(__file__).resolve().parent / TOOLS[args.command]
    return subprocess.call([sys.executable, str(target), *remainder])


if __name__ == "__main__":
    raise SystemExit(main())

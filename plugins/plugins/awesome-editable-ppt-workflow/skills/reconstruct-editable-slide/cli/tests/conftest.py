"""Make the repository-local CLI package importable from the repository root."""

from __future__ import annotations

import sys
from pathlib import Path


CLI_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = CLI_ROOT / "editppt" / "runtime"
WORKFLOW_SCRIPTS = Path(__file__).resolve().parents[3] / "run-word-to-ppt-workflow" / "scripts"
for path in (CLI_ROOT, RUNTIME_ROOT, WORKFLOW_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

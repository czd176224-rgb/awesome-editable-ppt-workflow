from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from test_accepted_image_worker_reconstruction import (  # noqa: E402
    _accepted_outcome,
    _successful_worker,
    _workspace,
)
from test_workflow_v6_reconstruction import _project, _write_signed_receipt  # noqa: E402
from workflow_v6_reconstruction_worker import reconstruct_accepted_page  # noqa: E402


@pytest.mark.parametrize("defect", ["stale_binding", "replaced", "deleted"])
def test_completed_recovery_rejects_stale_replaced_or_deleted_acceptance_receipt(
    tmp_path: Path, defect: str,
) -> None:
    project = _project(tmp_path, 1)
    reconstruct_accepted_page(
        _workspace(project),
        _accepted_outcome(project),
        page_worker=_successful_worker([]),
    )
    receipt_path = project / "04_v6/images/page_001.json"
    if defect == "deleted":
        receipt_path.unlink()
    elif defect == "replaced":
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["page_plan"]["page_purpose"] = "Replaced after reconstruction."
        _write_signed_receipt(project, 1, receipt)
    else:
        reconstruction_path = project / "05_v6/reconstruction_runs/page_001/reconstruction.json"
        reconstruction = json.loads(reconstruction_path.read_text(encoding="utf-8"))
        reconstruction["accepted_receipt"]["sha256"] = "0" * 64
        reconstruction_path.write_text(json.dumps(reconstruction), encoding="utf-8")

    outcome = _accepted_outcome(project) if receipt_path.is_file() else SimpleNamespace(
        status="accepted", accepted=SimpleNamespace(candidate=SimpleNamespace(path="unused")),
    )
    with pytest.raises(RuntimeError, match="acceptance receipt"):
        reconstruct_accepted_page(
            _workspace(project),
            outcome,
            page_worker=lambda request: pytest.fail("invalid recovery called the page worker"),
        )

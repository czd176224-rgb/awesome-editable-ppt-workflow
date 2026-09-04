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

from test_accepted_image_worker_reconstruction import _workspace  # noqa: E402
from test_quantitative_chart_v123_e2e import (  # noqa: E402
    _accepted_outcome,
    _production_worker,
    _relationship_manifest,
    _relationship_project,
)
from test_workflow_v6_reconstruction import _body, _project, _write_signed_receipt  # noqa: E402
from workflow_v6_reconstruction import finalize_reconstructed_page  # noqa: E402
from workflow_v6_reconstruction_worker import PageWorkerResult, reconstruct_accepted_page  # noqa: E402
from workflow_v6_state import load, save  # noqa: E402
from workflow_v6_contract import canonical_sha256  # noqa: E402


def test_worker_adapter_cannot_downgrade_missing_sealed_authority(tmp_path: Path) -> None:
    project = _project(tmp_path, 1)

    def incomplete_worker(request):
        body = request.page_dir / "incomplete-worker.pptx"
        _body(body, "No sealed nodes, edges, or manifest")
        return PageWorkerResult(status="completed", reconstructed_body=body)

    with pytest.raises(ValueError, match="sealed reconstruction manifest is missing"):
        reconstruct_accepted_page(
            _workspace(project), _accepted_outcome(project), page_worker=incomplete_worker,
        )


def test_explicit_native_direct_requires_no_candidate_or_receipt(tmp_path: Path) -> None:
    project = _project(tmp_path, 1)
    body = tmp_path / "native-direct.pptx"
    _body(body, "Native direct")
    (project / "04_v6/images/page_001.json").unlink()

    with pytest.raises(ValueError, match="formal Word"):
        finalize_reconstructed_page(
            project,
            page_number=1,
            reconstructed_body=body,
            authority_mode="native_direct",
        )

    state = load(project)
    state["word_source"]["authority_mode"] = "legacy_non_word"
    state["source_identity"] = canonical_sha256({
        "word_source": state["word_source"], "logo_source": state["logo_source"],
    })
    save(project, state)

    with pytest.raises(ValueError, match="selected candidate"):
        finalize_reconstructed_page(
            project,
            page_number=1,
            reconstructed_body=body,
            authority_mode="native_direct",
        )

    state = load(project)
    state["pages"][0]["selected_candidate"] = None
    save(project, state)

    report = finalize_reconstructed_page(
        project,
        page_number=1,
        reconstructed_body=body,
        authority_mode="native_direct",
    )

    assert report["fixed_frame"]["passed"] is True


@pytest.mark.parametrize("defect", ["stale_binding", "pixel_binding", "replaced", "deleted"])
def test_completed_recovery_rejects_stale_replaced_or_deleted_acceptance_receipt(
    tmp_path: Path, defect: str,
) -> None:
    project, _receipt = _relationship_project(tmp_path)
    reconstruct_accepted_page(
        _workspace(project),
        _accepted_outcome(project),
        page_worker=_production_worker(_relationship_manifest, []),
    )
    receipt_path = project / "04_v6/images/page_001.json"
    if defect == "deleted":
        receipt_path.unlink()
    elif defect == "replaced":
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["page_plan"]["page_purpose"] = "Replaced after reconstruction."
        _write_signed_receipt(project, 1, receipt)
    elif defect == "stale_binding":
        reconstruction_path = project / "05_v6/reconstruction_runs/page_001/reconstruction.json"
        reconstruction = json.loads(reconstruction_path.read_text(encoding="utf-8"))
        reconstruction["accepted_receipt"]["sha256"] = "0" * 64
        reconstruction_path.write_text(json.dumps(reconstruction), encoding="utf-8")
    else:
        reconstruction_path = project / "05_v6/reconstruction_runs/page_001/reconstruction.json"
        reconstruction = json.loads(reconstruction_path.read_text(encoding="utf-8"))
        reconstruction["accepted_source_body"]["normalized_pixel_sha256"] = "0" * 64
        reconstruction_path.write_text(json.dumps(reconstruction), encoding="utf-8")

    outcome = _accepted_outcome(project) if receipt_path.is_file() else SimpleNamespace(
        status="accepted", accepted=SimpleNamespace(candidate=SimpleNamespace(path="unused")),
    )
    with pytest.raises(RuntimeError, match="acceptance receipt|accepted image pixels"):
        reconstruct_accepted_page(
            _workspace(project),
            outcome,
            page_worker=lambda request: pytest.fail("invalid recovery called the page worker"),
        )

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PLUGIN = SCRIPTS.parents[2]
EDITPPT_CLI = PLUGIN / "skills" / "reconstruct-editable-slide" / "cli"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


from test_awesome_pipeline import _project  # noqa: E402
from test_workflow_v6_special_pages import (  # noqa: E402
    _reordered_toc_project,
    ten_block_cover_project,
)
from workflow_v6_state import load, save  # noqa: E402


def _publish_receipts(project: Path, *page_numbers: int) -> None:
    state = load(project)
    for page_number in page_numbers:
        payload = (json.dumps({"page_number": page_number}) + "\n").encode()
        relative = Path(f"02_v6/awesome_page_materials/page_{page_number:03d}.json")
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        state["pages"][page_number - 1]["material_state"] = "available"
        state["pages"][page_number - 1]["material_receipt"] = {
            "schema_version": "awesome-page-materials-v1",
            "page_number": page_number,
            "path": relative.as_posix(),
            "digest": hashlib.sha256(payload).hexdigest(),
        }
    state["style_confirmation"] = {"status": "confirmed", "contract": {}}
    state["confirmed_ui_revision"] = 1
    state["confirmed_ui_digest"] = "c" * 64
    state["page_materials_status"] = "confirmed"
    save(project, state)


def _runtime_payload(*, worker: Path | None = None, editppt: Path | None = None) -> dict:
    runtime = EDITPPT_CLI / "editppt" / "runtime"
    return {
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": "3.test",
        "plugin_id": "awesome-editable-ppt-workflow",
        "plugin_version": "1.2.3",
        "modules": {
            "workflow_v6_contract": str(SCRIPTS / "workflow_v6_contract.py"),
            "workflow_v6_reconstruction_worker": str(worker or SCRIPTS / "workflow_v6_reconstruction_worker.py"),
            "editppt": str(editppt or EDITPPT_CLI / "editppt" / "__init__.py"),
            "editppt.runtime.validate_pptx": str(runtime / "validate_pptx.py"),
            "editppt.runtime.build_pptx_from_manifest": str(runtime / "build_pptx_from_manifest.py"),
            "pptx": "C:/runtime/site-packages/pptx/__init__.py",
            "PIL": "C:/runtime/site-packages/PIL/__init__.py",
            "numpy": "C:/runtime/site-packages/numpy/__init__.py",
        },
    }


def test_partial_material_publication_stops_before_any_paid_stage(tmp_path: Path) -> None:
    from workflow_v6_pipeline import PipelineConfiguration, PipelineDependencies, run_pages
    from workflow_v6_preflight import preflight_project

    project = _project(tmp_path, pages=2)
    _publish_receipts(project, 1)
    state_path = project / "workflow_v6.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["page_materials_status"] = "pending"
    save(project, state)
    calls = []
    report = run_pages(
        project, [1],
        dependencies=PipelineDependencies(
            preflight=lambda root, pages: preflight_project(root, pages, check_runtime=False),
            open_workspace=lambda *_args: calls.append("workspace"),
            director_invoke=lambda *_args, **_kwargs: calls.append("director"),
            provider_runner=lambda *_args, **_kwargs: calls.append("provider"),
        ),
        configuration=PipelineConfiguration(),
    )
    assert calls == []
    assert report.failed_pages
    assert [issue["code"] for issue in report.preflight["issues"]] == ["page_materials_not_confirmed"]
    _publish_receipts(project, 2)
    assert preflight_project(project, [1], check_runtime=False)["passed"] is True


def test_overflowing_special_page_stops_pipeline_before_any_generation(tmp_path: Path) -> None:
    # Break caught: a later special page overflows after an earlier content page starts.
    from workflow_v6_pipeline import PipelineConfiguration, PipelineDependencies, run_pages
    from workflow_v6_preflight import preflight_project

    project = _reordered_toc_project(tmp_path)
    source_path = project / "02_v6/paginated_word_source.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["pages"][0]["blocks"].extend({
        "type": "paragraph", "text": f"PART {index}", "source_block_id": f"toc-{index}",
    } for index in range(2, 14))
    source_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    composition_path = project / "02_v6/page_composition.json"
    composition = json.loads(composition_path.read_text(encoding="utf-8"))
    composition["pages"][1]["material_source_block_ids"] = ["toc-title", *(f"toc-{index}" for index in range(1, 14))]
    composition_path.write_text(json.dumps(composition, ensure_ascii=False), encoding="utf-8")
    _publish_receipts(project, 1, 2)
    calls: list[str] = []
    report = run_pages(
        project,
        [1, 2],
        dependencies=PipelineDependencies(
            preflight=lambda root, pages: preflight_project(root, pages, check_runtime=False),
            open_workspace=lambda *_args: calls.append("workspace"),
            director_invoke=lambda *_args, **_kwargs: calls.append("director"),
            provider_runner=lambda *_args, **_kwargs: calls.append("provider"),
        ),
        configuration=PipelineConfiguration(
            page_workers=1, initial_page_concurrency=1, maximum_page_concurrency=1,
        ),
    )

    assert calls == []
    assert report.completed_pages == ()
    assert report.failed_pages
    assert report.preflight["issues"] == [{
        "code": "special_page_overflow",
        "message": "V6 toc page 2 overflow source block IDs: toc-13",
        "page_number": 2,
    }]


def test_preflight_accepts_ten_block_cover_when_native_layout_can_fit(
    tmp_path: Path,
) -> None:
    # Break caught: project preflight rejects a readable cover before native rendering can begin.
    from workflow_v6_preflight import preflight_project

    project = ten_block_cover_project(tmp_path)
    _publish_receipts(project, 1)

    report = preflight_project(project, [1], check_runtime=False)

    assert report["passed"] is True
    assert report["issues"] == []


def test_missing_material_receipt_fails_before_any_selected_page_begins(tmp_path: Path) -> None:
    # Break caught: one missing durable receipt is discovered only after another page starts.
    from workflow_v6_preflight import preflight_project

    report = preflight_project(_project(tmp_path, pages=2), [1, 2], check_runtime=False)

    assert report["passed"] is False
    assert [issue["page_number"] for issue in report["issues"] if "page_number" in issue] == [1, 2]
    assert {issue["code"] for issue in report["issues"]} == {"material_receipt_invalid", "page_materials_not_confirmed"}


def test_out_of_range_issue_keeps_the_requested_page_number(tmp_path: Path) -> None:
    # Break caught: operators cannot identify which requested page is outside the project.
    from workflow_v6_preflight import preflight_project

    report = preflight_project(_project(tmp_path, pages=1), [2], check_runtime=False)

    issue = next(item for item in report["issues"] if item["code"] == "page_out_of_range")
    assert issue["page_number"] == 2


def test_runtime_probe_rejects_mixed_worker_module_path(
    tmp_path: Path, monkeypatch,
) -> None:
    # Break caught: a copied venv reports 1.2.3 while importing its worker from 1.2.1.
    import workflow_v6_preflight as preflight

    project = _project(tmp_path, pages=1)
    _publish_receipts(project, 1)
    old_worker = tmp_path / "workflow-1.2.1" / "workflow_v6_reconstruction_worker.py"
    payload = _runtime_payload(worker=old_worker)
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, json.dumps(payload), ""),
    )

    report = preflight.preflight_project(project, [1])

    assert report["passed"] is False
    assert any(issue["code"] == "runtime_module_mismatch" for issue in report["issues"])
    assert report["runtime"]["modules"]["workflow_v6_reconstruction_worker"] == str(old_worker)


def test_special_overflow_code_does_not_depend_on_exception_wording(
    tmp_path: Path, monkeypatch,
) -> None:
    # Break caught: editing the actionable message silently changes the stable issue code.
    import workflow_v6_preflight as preflight
    from workflow_v6_special_pages import SpecialPageOverflowError

    project = _reordered_toc_project(tmp_path)
    _publish_receipts(project, 1, 2)
    monkeypatch.setattr(
        preflight,
        "check_special_page",
        lambda *_args: (_ for _ in ()).throw(SpecialPageOverflowError("capacity changed")),
    )

    report = preflight.preflight_project(project, [2], check_runtime=False)

    issue = next(item for item in report["issues"] if item["page_number"] == 2)
    assert issue["code"] == "special_page_overflow"
    assert issue["message"] == "capacity changed"


def test_runtime_probe_uses_worker_interpreter_checkout_modules_and_no_user_site(
    tmp_path: Path, monkeypatch,
) -> None:
    # Break caught: the probe passes in pytest's environment but the worker resolves old site modules.
    import workflow_v6_preflight as preflight

    project = _project(tmp_path, pages=1)
    _publish_receipts(project, 1)
    seen: dict[str, object] = {}

    def run(command, **kwargs):
        seen.update(command=command, kwargs=kwargs)
        return subprocess.CompletedProcess(command, 0, json.dumps(_runtime_payload()), "")

    monkeypatch.setattr(preflight.subprocess, "run", run)

    report = preflight.preflight_project(project, [1])

    assert report["passed"] is True
    assert Path(seen["command"][0]).resolve() == Path(sys.executable).resolve()
    assert seen["command"][1] == "-s"
    environment = seen["kwargs"]["env"]
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert Path(environment["PYTHONPATH"].split(os.pathsep)[0]).resolve() == EDITPPT_CLI.resolve()
    assert Path(report["runtime"]["modules"]["editppt"]).resolve().is_relative_to(EDITPPT_CLI.resolve())


def test_cli_preflight_defaults_to_every_page() -> None:
    # Break caught: operators must enumerate pages and can accidentally skip a bad cover.
    import workflow_v6_cli

    all_pages = workflow_v6_cli._parser().parse_args(["preflight", "--project", "project"])
    selected = workflow_v6_cli._parser().parse_args([
        "preflight", "--project", "project", "--pages", "2", "4",
    ])

    assert all_pages.pages is None
    assert selected.pages == [2, 4]


def test_production_dependency_factory_defers_worker_import_until_after_preflight(
    monkeypatch,
) -> None:
    # Break caught: a stale editppt import crashes dependency construction before preflight can report it.
    import workflow_v6_pipeline as pipeline

    monkeypatch.setitem(sys.modules, "workflow_v6_reconstruction_worker", None)

    dependencies = pipeline.production_pipeline_dependencies()

    assert callable(dependencies.preflight)
    assert callable(dependencies.reconstruct_page)

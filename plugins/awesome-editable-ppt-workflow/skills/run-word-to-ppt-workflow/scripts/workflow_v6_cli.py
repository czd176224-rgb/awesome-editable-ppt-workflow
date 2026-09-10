"""Production command line for the Awesome Word-to-PPT workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from awesome_page_materials import publish_page_materials
from complex_page_experiment.real_asset_completion import complete_project_real_assets
from workflow_v6_source import initialize_v6_project
from workflow_v6_state import load
from workflow_v6_pipeline import (
    PipelineConfiguration,
    recover_failed_pages,
    run_pages,
)


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _emit_pipeline_result(value: dict[str, Any]) -> int:
    _emit(value)
    return int(bool(value.get("failed_pages"))
               or (value.get("preflight") or {}).get("passed") is False
               or (value.get("assembly") or {}).get("status") in {"failed", "validation_incomplete"})


def _status(project: Path) -> dict[str, Any]:
    state = load(project)
    pages = [
        {"page_number": page["page_number"], "state": page["state"]}
        for page in state["pages"]
    ]
    if state["style_confirmation"]["status"] != "confirmed":
        next_action = "confirm_global_style"
    elif state["page_materials_status"] != "confirmed":
        next_action = "prepare_page_materials"
    elif any(page["state"] in {"prepared", "generating", "qa_review", "technical_failed"} for page in state["pages"]):
        next_action = "generate_page_bodies"
    elif any(page["state"] in {"accepted", "reconstructing"} for page in state["pages"]):
        next_action = "reconstruct_pages"
    elif all(page["state"] == "page_complete" for page in state["pages"]):
        next_action = "assemble_deck"
    else:
        next_action = "inspect_state"
    return {
        "workflow_contract_version": state["workflow_contract_version"],
        "image_policy": state["image_policy"],
        "style_status": state["style_confirmation"]["status"],
        "page_materials_status": state["page_materials_status"],
        "pages": pages,
        "next_action": next_action,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="create or resume the complete workflow with one confirmation session")
    run.add_argument("--project", type=Path, required=True)
    run.add_argument("--word", type=Path)
    run.add_argument("--logo", type=Path)
    run.add_argument("--preserve-source-layout", action="store_true")
    run.add_argument("--pages", type=int, nargs="+")
    run.add_argument("--recovery-round", type=int, choices=range(1, 1000), metavar="1..999")
    run.add_argument("--page-workers", type=int, default=12)
    run.add_argument("--page-concurrency", type=int, default=2)
    run.add_argument("--timeout", type=int, default=900)
    run.add_argument("--confirmation-timeout", type=int, default=590)
    init = sub.add_parser("init", help="lock Word and SVG Logo and create a fresh Awesome project")
    init.add_argument("--word", type=Path, required=True)
    init.add_argument("--logo", type=Path, required=True)
    init.add_argument("--project", type=Path, required=True)
    init.add_argument("--preserve-source-layout", action="store_true", help="keep existing page roles without proposing additional structure pages")
    status = sub.add_parser("status", help="show the authoritative Awesome project state")
    status.add_argument("--project", type=Path, required=True)
    materials = sub.add_parser("prepare-page-materials", help="publish one lossless page-material artifact")
    materials.add_argument("--project", type=Path, required=True)
    materials.add_argument("--page", type=int, required=True)
    materials.add_argument("--out", type=Path, required=True)
    pages = sub.add_parser("run-pages", help="authoritatively run bounded creative generation for multiple pages")
    pages.add_argument("--project", type=Path, required=True)
    pages.add_argument("--pages", type=int, nargs="+", required=True)
    pages.add_argument("--page-workers", type=int, default=12)
    pages.add_argument("--page-concurrency", type=int, default=2)
    pages.add_argument("--timeout", type=int, default=900)
    recovery = sub.add_parser(
        "recover-failed-pages",
        help="explicitly run one numbered recovery round for sealed failed pages",
    )
    recovery.add_argument("--project", type=Path, required=True)
    recovery.add_argument("--pages", type=int, nargs="+", required=True)
    recovery.add_argument("--recovery-round", type=int, required=True)
    recovery.add_argument("--page-workers", type=int, default=12)
    recovery.add_argument("--page-concurrency", type=int, default=2)
    recovery.add_argument("--timeout", type=int, default=900)
    preflight = sub.add_parser("preflight", help="validate every selected page and the worker runtime without generation")
    preflight.add_argument("--project", type=Path, required=True)
    preflight.add_argument("--pages", type=int, nargs="+")
    request = sub.add_parser("reconstruction-request", help="write one editable reconstruction request")
    request.add_argument("--project", type=Path, required=True)
    request.add_argument("--page", type=int, required=True)
    finalize = sub.add_parser("finalize-page", help="add fixed layers to one reconstructed body")
    finalize.add_argument("--project", type=Path, required=True)
    finalize.add_argument("--page", type=int, required=True)
    finalize.add_argument("--body-pptx", type=Path, required=True)
    assemble = sub.add_parser("assemble", help="mechanically assemble all completed pages")
    assemble.add_argument("--project", type=Path, required=True)
    return parser


def _require_valid_project(project: Path) -> None:
    """Reject invalid state before a command can create a mutation lock."""
    load(project)


def _run_project(args: argparse.Namespace) -> int:
    configuration = PipelineConfiguration(
        page_workers=args.page_workers,
        initial_page_concurrency=args.page_concurrency,
        maximum_page_concurrency=args.page_concurrency,
        timeout=args.timeout,
    )
    if args.confirmation_timeout < 1:
        raise ValueError("confirmation-timeout must be positive")
    if args.pages and any(number < 1 for number in args.pages):
        raise ValueError("page numbers must be positive")
    if args.recovery_round is not None and not args.pages:
        raise ValueError("an explicit recovery round requires --pages")
    project = args.project.resolve()
    if (project / "workflow_v6.json").exists():
        state = load(project)
        for supplied, key in ((args.word, "word_source"), (args.logo, "logo_source")):
            if supplied is not None and hashlib.sha256(supplied.read_bytes()).hexdigest() != state[key]["sha256"]:
                raise ValueError(f"{key} differs from the locked project source; use a new project")
    else:
        if args.word is None or args.logo is None:
            raise ValueError("a new project requires --word and --logo")
        if args.recovery_round is not None:
            raise ValueError("recovery requires an existing project")
        state = initialize_v6_project(
            args.word, args.logo, project, complete_structure=not args.preserve_source_layout,
        )
    if state["style_confirmation"]["status"] != "confirmed":
        from confirm_ui import server

        if server._confirmed_stage(project / server.CONFIRM_DIR / server.RESULT) < 4:
            code = server._start(project, server.DEFAULT_PORT, False, 900)
            if code:
                return code
        code = server._wait(project, "final", args.confirmation_timeout)
        if code:
            return code
        state = load(project)
    numbers = args.pages or [page["page_number"] for page in state["pages"]]
    if not set(numbers).issubset({page["page_number"] for page in state["pages"]}):
        raise ValueError("selected page does not exist in the confirmed plan")
    missing = [page["page_number"] for page in state["pages"] if page["material_receipt"] is None]
    if missing:
        complete_project_real_assets(project, timeout=args.timeout)
        for number in missing:
            publish_page_materials(project, number, project / "02_v6" / "awesome_page_materials" / f"page_{number:03d}.json")
        state = load(project)
    if args.recovery_round is not None:
        result = recover_failed_pages(
            project, numbers, recovery_round=args.recovery_round, configuration=configuration,
        ).to_dict()
    else:
        pending = [page["page_number"] for page in state["pages"]
                   if page["page_number"] in numbers and page["state"] != "page_complete"]
        if pending:
            result = run_pages(project, pending, configuration=configuration).to_dict()
        else:
            from workflow_v6_reconstruction_worker import assemble_reconstructed_project

            result = {"assembly": assemble_reconstructed_project(project, {})}
    failed = _emit_pipeline_result(result)
    assembly = result.get("assembly") or {}
    return int(bool(failed) or assembly.get("status") != "complete"
               or assembly.get("release_ready") is not True or not assembly.get("final_output"))


def main() -> int:
    args = _parser().parse_args()
    if args.command == "run":
        return _run_project(args)
    elif args.command == "init":
        initialize_v6_project(args.word, args.logo, args.project, complete_structure=not args.preserve_source_layout)
        _emit(_status(args.project))
    elif args.command == "status":
        _emit(_status(args.project))
    elif args.command == "prepare-page-materials":
        _require_valid_project(args.project)
        complete_project_real_assets(args.project, timeout=900)
        _emit(publish_page_materials(args.project, args.page, args.out))
    elif args.command == "preflight":
        from workflow_v6_preflight import preflight_project

        result = preflight_project(args.project, args.pages or ())
        _emit(result)
        return int(result.get("passed") is not True)
    elif args.command == "run-pages":
        _require_valid_project(args.project)
        return _emit_pipeline_result(run_pages(
            args.project,
            args.pages,
            configuration=PipelineConfiguration(
                page_workers=args.page_workers,
                initial_page_concurrency=args.page_concurrency,
                maximum_page_concurrency=args.page_concurrency,
                timeout=args.timeout,
            ),
        ).to_dict())
    elif args.command == "recover-failed-pages":
        _require_valid_project(args.project)
        return _emit_pipeline_result(recover_failed_pages(
            args.project,
            args.pages,
            recovery_round=args.recovery_round,
            configuration=PipelineConfiguration(
                page_workers=args.page_workers,
                initial_page_concurrency=args.page_concurrency,
                maximum_page_concurrency=args.page_concurrency,
                timeout=args.timeout,
            ),
        ).to_dict())
    elif args.command == "reconstruction-request":
        from workflow_v6_reconstruction import build_reconstruction_request

        _require_valid_project(args.project)
        _emit(build_reconstruction_request(args.project, page_number=args.page))
    elif args.command == "finalize-page":
        from workflow_v6_reconstruction import finalize_reconstructed_page

        _require_valid_project(args.project)
        _emit(finalize_reconstructed_page(
            args.project,
            page_number=args.page,
            reconstructed_body=args.body_pptx,
            authority_mode="sealed_reconstruction",
        ))
    elif args.command == "assemble":
        from workflow_v6_reconstruction import assemble_v6_deck

        _require_valid_project(args.project)
        result = assemble_v6_deck(args.project)
        _emit(result)
        return int(result.get("status") != "complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

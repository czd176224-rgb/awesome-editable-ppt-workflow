"""Production command line for the Awesome Word-to-PPT workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from awesome_page_materials import publish_page_materials
from complex_page_experiment.real_asset_completion import complete_project_real_assets
from workflow_v6_reconstruction import (
    assemble_v6_deck,
    build_reconstruction_request,
    finalize_reconstructed_page,
)
from workflow_v6_source import initialize_v6_project
from workflow_v6_state import load
from workflow_v6_pipeline import (
    PipelineConfiguration,
    run_pages,
)


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


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
    init = sub.add_parser("init", help="lock Word and SVG Logo and create a fresh Awesome project")
    init.add_argument("--word", type=Path, required=True)
    init.add_argument("--logo", type=Path, required=True)
    init.add_argument("--project", type=Path, required=True)
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


def main() -> int:
    args = _parser().parse_args()
    if args.command == "init":
        initialize_v6_project(args.word, args.logo, args.project)
        _emit(_status(args.project))
    elif args.command == "status":
        _emit(_status(args.project))
    elif args.command == "prepare-page-materials":
        _require_valid_project(args.project)
        complete_project_real_assets(args.project, timeout=900)
        _emit(publish_page_materials(args.project, args.page, args.out))
    elif args.command == "run-pages":
        _require_valid_project(args.project)
        _emit(run_pages(
            args.project,
            args.pages,
            configuration=PipelineConfiguration(
                page_workers=args.page_workers,
                initial_page_concurrency=args.page_concurrency,
                maximum_page_concurrency=args.page_concurrency,
                timeout=args.timeout,
            ),
        ).to_dict())
    elif args.command == "reconstruction-request":
        _require_valid_project(args.project)
        _emit(build_reconstruction_request(args.project, page_number=args.page))
    elif args.command == "finalize-page":
        _require_valid_project(args.project)
        _emit(finalize_reconstructed_page(args.project, page_number=args.page, reconstructed_body=args.body_pptx))
    elif args.command == "assemble":
        _require_valid_project(args.project)
        _emit(assemble_v6_deck(args.project))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

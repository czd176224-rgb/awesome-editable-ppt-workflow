#!/usr/bin/env python3
"""Replace a pending page's visual authority and update its measured source geometry."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from PIL import Image

from deck_run_state import (
    find_page, load_jobs, page_dir_for, read_json, run_dir_from_target, write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--page", required=True)
    parser.add_argument("--image", type=Path, required=True)
    args = parser.parse_args()
    run = run_dir_from_target(args.run)
    jobs = load_jobs(run)
    page = find_page(jobs, args.page)
    if page.get("status") != "pending":
        parser.error("page source may be replaced only after the page is reset to pending")
    source = args.image.resolve()
    with Image.open(source) as image:
        image.verify()
    with Image.open(source) as image:
        width, height = image.size
    if (width, height) != (1904, 896):
        parser.error("word workflow replacement source must be exactly 1904x896")
    page_dir = page_dir_for(run, page)
    destination = page_dir / "source.png"
    shutil.copy2(source, destination)
    request_path = page_dir / "page_request.json"
    request = read_json(request_path)
    request["source_size_px"] = {"width": width, "height": height}
    write_json(request_path, request)
    print(json.dumps({
        "page_id": page["page_id"], "status": "pending",
        "source_image": str(destination), "source_size_px": [width, height],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

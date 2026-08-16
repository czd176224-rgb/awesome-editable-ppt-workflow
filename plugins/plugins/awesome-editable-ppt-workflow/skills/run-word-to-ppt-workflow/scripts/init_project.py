"""Create a clean PPT project from the portable template."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date
from pathlib import Path


def initialize(template: Path, destination: Path, name: str, pages: int) -> Path:
    if pages < 1:
        raise ValueError("--pages must be at least 1")
    if not (template / "project.json").is_file():
        raise FileNotFoundError(f"Template is missing project.json: {template}")
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"Destination is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template, destination, dirs_exist_ok=True)
    config_path = destination / "project.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update(
        {
            "project_name": name,
            "expected_page_count": pages,
            "created_date": date.today().isoformat(),
            "source_mode": "paginated_word",
        }
    )
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--pages", type=int, required=True)
    args = parser.parse_args()
    path = initialize(args.template.resolve(), args.destination.resolve(), args.name.strip(), args.pages)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

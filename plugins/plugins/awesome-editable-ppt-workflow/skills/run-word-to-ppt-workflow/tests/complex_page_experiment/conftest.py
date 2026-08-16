from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v6_contract import canonical_sha256, new_page, new_project  # noqa: E402
from workflow_v6_state import create  # noqa: E402


def _source_record(project: Path, relative: str) -> dict[str, object]:
    path = project / relative
    payload = path.read_bytes()
    return {
        "path": relative,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "byte_size": len(payload),
    }


@pytest.fixture
def awesome_four_page_project(tmp_path: Path) -> Path:
    project = tmp_path / "awesome-source"
    (project / "00_source").mkdir(parents=True)
    (project / "00_source" / "source.docx").write_bytes(b"four-page-awesome-word-source")
    (project / "00_source" / "logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="4"/></svg>',
        encoding="utf-8",
    )
    (project / "01_source_assets").mkdir()
    (project / "01_source_assets" / "appendix.pdf").write_bytes(b"original-attachment")
    cache = project / "02_v6" / "attachment_renders" / "appendix" / "page_0001.png"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"stable-render-cache-bytes")

    pages = [new_page(number, title=f"Awesome page {number}") for number in range(1, 5)]
    visual = {
        "primary_color": "#17365D",
        "secondary_color": "#C7352B",
        "background_color": "#FFFFFF",
        "cjk_font": "Microsoft YaHei",
        "latin_font": "Arial",
        "title_size_pt": 28,
        "body_size_pt": 12,
        "caption_size_pt": 9,
        "regional_characteristics": "",
        "visual_description": "Confirmed Awesome baseline",
    }
    state = new_project(
        word_source=_source_record(project, "00_source/source.docx"),
        logo_source=_source_record(project, "00_source/logo.svg"),
        pages=pages,
    )
    state["style_confirmation"] = {"status": "confirmed", "contract": visual}
    state["confirmed_ui_revision"] = 3
    state["confirmed_ui_digest"] = canonical_sha256(visual)
    state["page_materials_status"] = "confirmed"

    for page in state["pages"]:
        number = page["page_number"]
        attachment = _source_record(project, "01_source_assets/appendix.pdf")
        render = _source_record(
            project, "02_v6/attachment_renders/appendix/page_0001.png"
        )
        material = {
            "page_number": number,
            "fixed_page_title": f"Awesome page {number}",
            "complete_word_content": [
                {
                    "type": "paragraph",
                    "text": f"Authoritative body {number}",
                    "source_block_id": f"body-{number}",
                    "source_block_index": number,
                    "source_order": 1,
                    "relationship_ids": [],
                    "comment_ids": [],
                }
            ],
            "original_comments": [],
            "word_images": [],
            "attachment_inputs": [
                {
                    "asset_id": "appendix",
                    "source_order": 1,
                    "original_filename": "appendix.pdf",
                    "media_type": "application/pdf",
                    **attachment,
                    "render_receipt": {
                        "schema_version": "awesome-attachment-render-v1",
                        "original_path": attachment["path"],
                        "original_sha256": attachment["sha256"],
                        "original_byte_size": attachment["byte_size"],
                        "renderer_identity": "fixture-renderer",
                        "pages": [
                            {
                                "page_number": 1,
                                "width": 16,
                                "height": 8,
                                **render,
                            }
                        ],
                        "contact_sheet": {
                            "page_number": 0,
                            "width": 16,
                            "height": 8,
                            **render,
                        },
                    },
                }
            ],
            "visual_contract": visual,
            "body_frame": {
                "geometry_version": "fixed-canvas-cm-v2",
                "body_bounds_cm": {"x": 0.81, "y": 2.3, "w": 23.78, "h": 11.18},
                "body_pixels": {"width": 1904, "height": 896},
                "fixed_layers": ["title", "logo", "footer", "page_number"],
            },
        }
        material_path = (
            project / "02_v6" / "awesome_page_materials" / f"page_{number:03d}.json"
        )
        material_path.parent.mkdir(parents=True, exist_ok=True)
        material_bytes = (
            json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        material_path.write_bytes(material_bytes)
        page["material_state"] = "available"
        page["material_receipt"] = {
            "schema_version": "awesome-page-materials-v1",
            "page_number": number,
            "path": material_path.relative_to(project).as_posix(),
            "digest": hashlib.sha256(material_bytes).hexdigest(),
        }

    create(project, state)
    return project

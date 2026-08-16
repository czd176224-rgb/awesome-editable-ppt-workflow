from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from complex_page_experiment import create_experiment_copy
from complex_page_experiment.materials import (
    build_complete_page_material_view,
    validate_complete_page_material_view,
)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _record(project: Path, relative: str) -> dict[str, object]:
    data = (project / relative).read_bytes()
    return {
        "path": relative,
        "sha256": hashlib.sha256(data).hexdigest(),
        "byte_size": len(data),
    }


def _prepare_complete_page_one(project: Path) -> dict[str, object]:
    word_image = project / "01_source_assets" / "photo.png"
    word_image.write_bytes(b"original-word-image")
    duplicate_word_image = project / "01_source_assets" / "photo-copy.png"
    duplicate_word_image.write_bytes(word_image.read_bytes())
    second_render = (
        project / "02_v6" / "attachment_renders" / "appendix" / "page_0002.png"
    )
    second_render.write_bytes(b"second-render-page")
    xlsx = project / "01_source_assets" / "facts.xlsx"
    xlsx.write_bytes(b"original-xlsx-authority")
    xlsx_render = (
        project / "02_v6" / "attachment_renders" / "facts" / "page_0001.png"
    )
    xlsx_render.parent.mkdir(parents=True)
    xlsx_render.write_bytes(b"xlsx-render-page")

    word_record = _record(project, "01_source_assets/photo.png")
    duplicate_word_record = _record(project, "01_source_assets/photo-copy.png")
    pdf_record = _record(project, "01_source_assets/appendix.pdf")
    xlsx_record = _record(project, "01_source_assets/facts.xlsx")
    render_one = _record(
        project, "02_v6/attachment_renders/appendix/page_0001.png"
    )
    render_two = _record(
        project, "02_v6/attachment_renders/appendix/page_0002.png"
    )
    xlsx_page = _record(
        project, "02_v6/attachment_renders/facts/page_0001.png"
    )

    pages = []
    for number in range(1, 5):
        pages.append(
            {
                "page_number": number,
                "fixed_page_title": f"Awesome page {number}",
                "fixed_page_title_source_block_id": f"title-{number}",
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": f"Awesome page {number}",
                        "source_block_id": f"title-{number}",
                        "source_block_index": 0,
                        "source_order": 1,
                        "relationship_ids": [],
                        "comment_ids": [],
                    },
                    {
                        "type": "paragraph",
                        "text": f"Authoritative body {number}",
                        "source_block_id": f"body-{number}",
                        "source_block_index": number,
                        "source_order": 2,
                        "relationship_ids": ["rId-photo"] if number == 1 else [],
                        "comment_ids": ["comment-1"] if number == 1 else [],
                    },
                ],
                "page_comments": (
                    [
                        {
                            "comment_id": "comment-1",
                            "text": "Keep this original direction exactly.  ",
                            "author": "User",
                            "timestamp": None,
                        }
                    ]
                    if number == 1
                    else []
                ),
            }
        )
    paginated = {
        "schema_version": "1.0",
        "source_file": "source.docx",
        "page_count": 4,
        "pages": pages,
    }
    paginated_path = project / "02_v6" / "paginated_word_source.json"
    paginated_path.write_bytes(_canonical(paginated))

    assets = {
        "schema_version": "1.0",
        "assets": [
            {
                "asset_id": "word-photo",
                "media_type": "image/png",
                "page_numbers": [1],
                "relative_path": "photo.png",
                "original_filename": "photo.png",
                **{key: word_record[key] for key in ("sha256", "byte_size")},
            },
            {
                "asset_id": "word-photo-copy",
                "media_type": "image/png",
                "page_numbers": [1],
                "relative_path": "photo-copy.png",
                "original_filename": "photo-copy.png",
                **{
                    key: duplicate_word_record[key]
                    for key in ("sha256", "byte_size")
                },
            },
            {
                "asset_id": "appendix-pdf",
                "media_type": "application/pdf",
                "page_numbers": [1],
                "relative_path": "appendix.pdf",
                "original_filename": "appendix.pdf",
                **{key: pdf_record[key] for key in ("sha256", "byte_size")},
            },
            {
                "asset_id": "facts-xlsx",
                "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "page_numbers": [1],
                "relative_path": "facts.xlsx",
                "original_filename": "facts.xlsx",
                **{key: xlsx_record[key] for key in ("sha256", "byte_size")},
            },
        ],
    }
    (project / "02_v6" / "source_assets.json").write_bytes(_canonical(assets))

    material_path = project / "02_v6" / "awesome_page_materials" / "page_001.json"
    material = json.loads(material_path.read_text(encoding="utf-8"))
    material["complete_word_content"] = [copy.deepcopy(pages[0]["blocks"][1])]
    material["original_comments"] = [
        {
            "comment_id": "comment-1",
            "source_order": 1,
            "text": "Keep this original direction exactly.  ",
        }
    ]
    material["word_images"] = [
        {
            "asset_id": "word-photo",
            "source_order": 1,
            "original_filename": "photo.png",
            "media_type": "image/png",
            **word_record,
        },
        {
            "asset_id": "word-photo-copy",
            "source_order": 2,
            "original_filename": "photo-copy.png",
            "media_type": "image/png",
            **duplicate_word_record,
        },
    ]
    material["attachment_inputs"] = [
        {
            "asset_id": "appendix-pdf",
            "source_order": 3,
            "original_filename": "appendix.pdf",
            "media_type": "application/pdf",
            **pdf_record,
            "render_receipt": {
                "schema_version": "awesome-attachment-render-v1",
                "original_path": pdf_record["path"],
                "original_sha256": pdf_record["sha256"],
                "original_byte_size": pdf_record["byte_size"],
                "renderer_identity": "fixture-pdf-renderer",
                "pages": [
                    {"page_number": 1, "width": 16, "height": 8, **render_one},
                    {"page_number": 2, "width": 16, "height": 8, **render_two},
                ],
                # Exact same owned bytes as page 1: this derivative must be recorded
                # but omitted from the multimodal image tuple.
                "contact_sheet": {
                    "page_number": 0,
                    "width": 16,
                    "height": 8,
                    **render_one,
                },
            },
        },
        {
            "asset_id": "facts-xlsx",
            "source_order": 4,
            "original_filename": "facts.xlsx",
            "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            **xlsx_record,
            "render_receipt": {
                "schema_version": "awesome-attachment-render-v1",
                "original_path": xlsx_record["path"],
                "original_sha256": xlsx_record["sha256"],
                "original_byte_size": xlsx_record["byte_size"],
                "renderer_identity": "fixture-xlsx-renderer",
                "pages": [
                    {"page_number": 1, "width": 16, "height": 8, **xlsx_page}
                ],
                "contact_sheet": {
                    "page_number": 0,
                    "width": 16,
                    "height": 8,
                    **xlsx_page,
                },
            },
        },
    ]
    payload = _canonical(material)
    material_path.write_bytes(payload)
    state_path = project / "workflow_v6.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["pages"][0]["material_receipt"]["digest"] = hashlib.sha256(payload).hexdigest()
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return material


def test_build_preserves_complete_authorities_and_reuses_existing_render_cache(
    awesome_four_page_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    published = _prepare_complete_page_one(awesome_four_page_project)
    workspace = create_experiment_copy(
        awesome_four_page_project,
        tmp_path / "experiment",
        experiment_id="complex-page-001",
    )

    import awesome_attachment_render

    def forbidden_renderer(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("material view must never rerender an attachment")

    monkeypatch.setattr(awesome_attachment_render, "render_attachment", forbidden_renderer)
    result = build_complete_page_material_view(workspace)

    assert result.value["schema_version"] == "awesome-complete-page-material-view-v1"
    assert result.value["experiment_id"] == "complex-page-001"
    assert result.value["page_number"] == 1
    assert result.value["fixed_page_title"] == published["fixed_page_title"]
    assert result.value["complete_word_content"] == published["complete_word_content"]
    assert result.value["original_comments"] == published["original_comments"]
    assert result.value["visual_contract"] == published["visual_contract"]


    assert len(result.value["visual_contract"]) == 10
    assert result.value["body_frame"] == published["body_frame"]
    assert result.value["body_frame"]["body_pixels"] == {
        "width": 1904,
        "height": 896,
    }

    materials = result.value["materials"]
    assert [item["material_id"] for item in materials] == list(result.material_ids)
    assert [item["kind"] for item in materials] == [
        "word_block",
        "word_comment",
        "word_image",
        "word_image",
        "attachment_original",
        "attachment_render_page",
        "attachment_render_page",
        "attachment_contact_sheet",
        "attachment_original",
        "attachment_render_page",
        "attachment_contact_sheet",
    ]
    assert materials[0]["original"] == published["complete_word_content"][0]
    assert materials[1]["original"] == published["original_comments"][0]
    assert materials[2]["original_filename"] == "photo.png"
    assert materials[3]["original_filename"] == "photo-copy.png"
    assert materials[4]["original"] == published["attachment_inputs"][0]
    assert materials[5]["page_number"] == 1
    assert materials[6]["page_number"] == 2
    assert materials[8]["original"] == published["attachment_inputs"][1]
    assert [item["source_order"] for item in materials[:5]] == [2, 1, 1, 2, 3]
    assert [item["source_order"] for item in materials[5:8]] == [1, 2, 3]
    assert [item["source_order"] for item in materials[8:]] == [4, 1, 2]
    assert all(
        {
            "material_id",
            "kind",
            "source_order",
            "authority_path",
            "sha256",
            "media_type",
            "viewable_image",
        }.issubset(record)
        for record in materials
    )

    assert result.value["deduplicated_derivatives"] == [
        {
            "material_id": materials[7]["material_id"],
            "duplicate_of": materials[5]["material_id"],
            "sha256": materials[7]["sha256"],
        },
        {
            "material_id": materials[10]["material_id"],
            "duplicate_of": materials[9]["material_id"],
            "sha256": materials[10]["sha256"],
        },
    ]
    assert result.multimodal_images == tuple(
        workspace.project_copy / relative
        for relative in (
            "01_source_assets/photo.png",
            "01_source_assets/photo-copy.png",
            "02_v6/attachment_renders/appendix/page_0001.png",
            "02_v6/attachment_renders/appendix/page_0002.png",
            "02_v6/attachment_renders/facts/page_0001.png",
        )
    )
    assert len(result.sha256) == 64

    output = (
        workspace.project_copy
        / "02_v6"
        / "experiments"
        / "complex-page-001"
        / "complete_page_material_view.json"
    )
    assert output.read_bytes() == _canonical(result.value)
    assert hashlib.sha256(output.read_bytes()).hexdigest() == result.sha256


def test_build_preserves_unrenderable_nonblocking_attachment_as_original_authority(
    awesome_four_page_project: Path, tmp_path: Path
):
    _prepare_complete_page_one(awesome_four_page_project)
    opaque = awesome_four_page_project / "01_source_assets" / "oleObject1.bin"
    opaque.write_bytes(b"opaque OLE authority")
    opaque_record = _record(awesome_four_page_project, "01_source_assets/oleObject1.bin")

    manifest_path = awesome_four_page_project / "02_v6/source_assets.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["assets"].append(
        {
            "asset_id": "opaque-ole",
            "media_type": "application/vnd.openxmlformats-officedocument.oleObject",
            "page_numbers": [1],
            "relative_path": "oleObject1.bin",
            "original_filename": "oleObject1.bin",
            "asset_role": "unsupported",
            "processing": "unavailable",
            "blocking": False,
            **{key: opaque_record[key] for key in ("sha256", "byte_size")},
        }
    )
    manifest_path.write_bytes(_canonical(manifest))

    material_path = awesome_four_page_project / "02_v6/awesome_page_materials/page_001.json"
    material = json.loads(material_path.read_text(encoding="utf-8"))
    material["attachment_inputs"].append(
        {
            "asset_id": "opaque-ole",
            "source_order": 5,
            "original_filename": "oleObject1.bin",
            "media_type": "application/vnd.openxmlformats-officedocument.oleObject",
            **opaque_record,
        }
    )
    payload = _canonical(material)
    material_path.write_bytes(payload)
    state_path = awesome_four_page_project / "workflow_v6.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["pages"][0]["material_receipt"]["digest"] = hashlib.sha256(payload).hexdigest()
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    workspace = create_experiment_copy(
        awesome_four_page_project,
        tmp_path / "experiment-unrenderable",
        experiment_id="complex-page-unrenderable",
    )
    result = build_complete_page_material_view(workspace)

    opaque_material = next(
        item for item in result.value["materials"] if item["material_id"] == "attachment:opaque-ole"
    )
    assert opaque_material["kind"] == "attachment_original"
    assert opaque_material["original_filename"] == "oleObject1.bin"
    assert "render_receipt" not in opaque_material["original"]
    assert all("opaque-ole" not in str(path) for path in result.multimodal_images)


def test_build_rejects_a_published_receipt_that_differs_from_current_authority(
    awesome_four_page_project: Path, tmp_path: Path
):
    _prepare_complete_page_one(awesome_four_page_project)
    workspace = create_experiment_copy(
        awesome_four_page_project,
        tmp_path / "experiment",
        experiment_id="complex-page-001",
    )
    manifest_path = workspace.project_copy / "02_v6" / "paginated_word_source.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["pages"][0]["blocks"][1]["text"] = "unreceipted mutation"
    manifest_path.write_bytes(_canonical(manifest))

    with pytest.raises(ValueError, match="durable.*receipt|current.*authority"):
        build_complete_page_material_view(workspace)


def test_build_is_idempotent_only_for_identical_canonical_bytes(
    awesome_four_page_project: Path, tmp_path: Path
):
    _prepare_complete_page_one(awesome_four_page_project)
    workspace = create_experiment_copy(
        awesome_four_page_project,
        tmp_path / "experiment",
        experiment_id="complex-page-001",
    )

    first = build_complete_page_material_view(workspace)
    second = build_complete_page_material_view(workspace)

    assert second == first


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update({"summary": "forbidden"}), "Additional properties|summary"),
        (
            lambda value: value["materials"][2].update(
                {"authority_path": "../outside.png"}
            ),
            "path|authority",
        ),
        (
            lambda value: value["materials"][0].update({"sha256": "0" * 64}),
            "digest|sha256",
        ),
        (
            lambda value: value["materials"][2]["original"].update(
                {"sha256": "0" * 64}
            ),
            "digest|sha256|original",
        ),
        (
            lambda value: value["materials"][0]["original"].update(
                {"summary": "forbidden nested rewrite"}
            ),
            "summary|Additional properties|page materials|Word content",
        ),
        (
            lambda value: value["materials"][5]["original"].update(
                {"path": "02_v6/attachment_renders/appendix/other.png"}
            ),
            "path|original",
        ),
        (
            lambda value: value["source_receipts"]["paginated_word_source"].update(
                {"path": "02_v6/other.json"}
            ),
            "paginated|source receipt|path",
        ),
        (
            lambda value: value["materials"][5].update({"width": 99}),
            "render|receipt|width|authority",
        ),
        (
            lambda value: value["complete_word_content"][0].update(
                {"source_order": 99}
            ),
            "source order|Word content",
        ),
    ],
)
def test_validation_rejects_nonclosed_or_internally_inconsistent_views(
    awesome_four_page_project: Path,
    tmp_path: Path,
    mutation,
    message: str,
):
    _prepare_complete_page_one(awesome_four_page_project)
    workspace = create_experiment_copy(
        awesome_four_page_project,
        tmp_path / "experiment",
        experiment_id="complex-page-001",
    )
    value = copy.deepcopy(build_complete_page_material_view(workspace).value)
    mutation(value)

    with pytest.raises(ValueError, match=message):
        validate_complete_page_material_view(value)


def test_build_rejects_an_experiment_id_that_could_escape_its_output_directory(
    awesome_four_page_project: Path, tmp_path: Path
):
    _prepare_complete_page_one(awesome_four_page_project)
    workspace = create_experiment_copy(
        awesome_four_page_project,
        tmp_path / "experiment",
        experiment_id="../escape",
    )

    with pytest.raises(ValueError, match="experiment.*ID|experiment_id"):
        build_complete_page_material_view(workspace)
    assert not (workspace.project_copy / "02_v6" / "escape").exists()

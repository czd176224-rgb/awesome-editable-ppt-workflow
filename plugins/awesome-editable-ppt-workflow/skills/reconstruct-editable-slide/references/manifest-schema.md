# Manifest Schema

This document describes the responsibilities, owners, and current field contracts for `editppt` run/page JSON files. All key state is advanced by `editppt` commands; page reconstructors write only page-local files.

## `deck_manifest.json`

Owner: created by `editppt prepare`; `editppt run backend` may update the image backend; `editppt run finalize` reads it and writes completion time.

Purpose:

- Input type.
- Page order.
- Page manifest paths.
- Notes manifest path.
- Final output path.
- Run-level image backend contract.
- Original user request.

Key fields:

```json
{
  "schema_version": 1,
  "run_id": "job-id",
  "input_type": "image|images|pdf|pptx",
  "max_concurrent_pages": 6,
  "image_backend": {},
  "pages": [],
  "notes_manifest": "notes_manifest.json",
  "output": "final/origin_edited.pptx"
}
```

`image_backend` is written with defaults by `editppt prepare` and may be overwritten by `editppt run backend` when needed.

## `page_jobs.json`

Owner: created by `editppt prepare`, updated by `editppt run` commands.

Purpose:

- Source of truth for page state.
- Dispatch records.
- Result records.

Structure:

```json
{
  "schema_version": 1,
  "run_id": "job-id",
  "max_concurrent_pages": 6,
  "pages": [
    {
      "page_id": "page_001",
      "status": "pending",
      "page_dir": "pages/page_001",
      "page_request": "pages/page_001/page_request.json",
      "source": "pages/page_001/source.png",
      "dispatch": null,
      "result": null
    }
  ]
}
```

`dispatch` is written by `editppt run dispatch`. It includes `execution_mode`: `"worker"` for normal page-worker dispatch and `"local"` for the parent agent's single-page local claim; older dispatch records without this field are treated as `"worker"`. A page with status `dispatched` is an active execution lease until explicit completion, failure, cancellation, or lost-worker verification; elapsed time alone does not make it lost. `result` is written by `editppt run record`. `accepted` is written by `editppt run finalize`.

## `page_request.json`

Owner: `editppt prepare`.

Purpose: task boundary for the page worker.

Includes:

- workflow contract version (`fixed-canvas-cm-v2` only)
- page id
- page directory
- source image
- slide size
- content box
- max concurrent pages
- allowed write scope
- required outputs
- user constraints
- image backend contract

Must not include:

- page type prediction
- `imagegen_required` prediction
- object-level decisions

If the run uses an image backend, `page_request.json` must contain the same `image_backend`.

`workflow_contract_version`, `slide`, and `content_box` are written by `editppt prepare` from the immutable `fixed-canvas-cm-v2` authority. The source may have any positive integer pixel width and height; `prepare` records the actual dimensions. The slide is 25.4x14.288 cm and the content box is x=0.81 cm, y=2.3 cm, w=23.78 cm, h=11.18 cm, with right remainder 0.81 cm and bottom remainder 0.808 cm. The agent must copy the authority fields into page `manifest.json` unchanged. A reconstructed page package may use a neutral scaffold background. Final Word-workflow assembly applies the UI-confirmed background color as the native whole-slide background; source/design body fills remain positioned inside the content box. Standalone legacy conversion may center and proportionally contain an unexpected ratio. `word-ppt-workflow-v4` and `word-ppt-workflow-v5` instead require relative aspect error from 17:8 of at most 1%; otherwise they must repair or block and never contain-pass. They never stretch, crop or post-position the body. Final geometry may deviate by at most 0.1%.

## `page_result.json`

Owner: created by the page reconstructor, validated by `editppt run record`.

Includes:

- manifest path
- imagegen jobs path
- page pptx path
- preview path
- contact sheet path
- validation path
- page-local output hashes, which may be supplemented by `editppt run record`

Minimal required shape (paths are relative to the page directory):

```json
{
  "page_manifest": "manifest.json",
  "imagegen_jobs": "imagegen-jobs.json",
  "page_pptx": "page.pptx",
  "preview": "preview.png",
  "contact_sheet": "split_assets_contact.png",
  "validation": "validation.json",
  "page_result": "page_result.json"
}
```

The `manifest` artifact is the authoritative page source for final assembly. `editppt run finalize` rebuilds the final deck from recorded page manifests in page order. The `page_pptx` artifact remains a page-level deliverability artifact and is validated by `editppt run record`, but it is not the final assembly input.

## `pages/page_NNN/validation.json`

Owner: created by the page reconstructor, read by `editppt run record`.

Purpose: page-level deliverability conclusion.

Must contain at top level:

```json
{
  "passed": true
}
```

`passed` must be a boolean. `editppt run record` only reads top-level `passed` to decide whether the page can enter final assembly. `status: "pass"`, `runtime_validation.passed`, or other nested fields may remain as supplemental information, but they cannot replace top-level `passed`.

## `pages/page_NNN/manifest.json`

Owner: page reconstructor.

Purpose: source of truth for page-level PPTX construction.

The manifest is not a summary of a separately authored `page.pptx`. It is the build contract for both page-level validation and final deck assembly. A page may not pass validation if the page PPTX can only be reproduced by custom page-local code while the manifest lacks object positions.

Must contain:

- `workflow_contract_version` equal to `fixed-canvas-cm-v2`
- `slide`
- `content_box`
- `source`
- `text_inventory`
- `visual_inventory`
- `background_strategy`
- `quality_checks`
- `text_boxes`
- `shapes`
- `images`
- `asset_provenance`
- page strategy

`workflow_contract_version`, `slide`, `content_box`, and `source.width_px/source.height_px` must come from `page_request.json`. `source.width_px` and `source.height_px` are the measured positive dimensions of the current `source.png`, not contract constants. All `box_px`, `points_px`, and `polygon_px` values use that actual source coordinate system. Standalone legacy conversion may use its centered proportional contain box. `word-ppt-workflow-v4` and `word-ppt-workflow-v5` map only a 17:8 source within 1% relative aspect error into the full fixed `content_box`; out-of-tolerance Word-workflow input repairs or blocks. Missing or altered geometry is rejected. Coordinate layouts:

- `box_px: [x, y, width, height]`
- `points_px: [x1, y1, x2, y2]`

Positioned build object requirements:

- Every `text_boxes[]` item must have `box_px`. Text in `text_inventory` does not create a positioned text box.
- Every `images[]` item must have `box_px`.
- Every non-line `shapes[]` item must have `box_px`.
- Every line shape must have `points_px`.
- Every optional `charts[]` item must have `object_id`, `name`, and an in-bounds `box_px`. Its `object_id` shares the same uniqueness scope as text boxes, tables, images, and shapes. Legacy centimeter `anchor` and inferred `chart_type` fields are rejected.

### Optional quantitative `charts[]`

`charts[]` consumes the sealed quantitative authority without choosing or inferring a chart form. Each item requires `rendering_primitive`, explicit `chart_variant`, `title`, `period`, and non-empty `series`. Supported standard pairs are exactly:

- `column_bar` with `column` or `bar`
- `line_point` with `line` or `dot`
- `xy` with `scatter` or `bubble`

`column`, `bar`, `line`, `scatter`, and `bubble` become native PowerPoint chart objects. `dot` becomes editable title, point, connector, category, and value shapes because PowerPoint has no stable dedicated dot-plot chart type.

One-dimensional charts require one shared explicit `unit` and `basis`, either at chart level or repeated identically on every series. Every series requires renderer-ready `name`, string `categories`, and an equally sized numeric `values` list; all series in one categorical chart must use identical categories. XY charts require explicit `x_label`/`x_unit`/`x_basis` and `y_label`/`y_unit`/`y_basis`, plus aligned numeric `x_values` and `y_values` per named series. Bubble charts additionally require `size_label`/`size_unit`/`size_basis` and aligned non-negative `size_values`. The runtime renders every applicable basis as a named editable text object and requires exact basis readback; missing or changed basis text blocks validation.

Example:

```json
{
  "object_id": "chart-revenue",
  "name": "Revenue chart",
  "box_px": [190, 90, 1142, 620],
  "rendering_primitive": "column_bar",
  "chart_variant": "column",
  "title": "Revenue",
  "unit": "USD m",
  "period": "FY2025",
  "basis": "same portfolio companies",
  "series": [
    {"name": "Revenue", "categories": ["A", "B"], "values": [12, 18]}
  ]
}
```

`target_value` and `actual_value` are optional only as one explicit numeric pair on the shape-based `dot` variant. When both exist, the builder adds an editable target line, actual/target labels, and a difference arrow whose displayed value is the decimal-formatted direct subtraction `actual_value - target_value`. Native `column`, `bar`, `line`, `scatter`, and `bubble` charts with either mark are rejected because fixed chart-box percentages do not prove alignment with PowerPoint's plot area and axes. A lone value, inferred target, inferred actual, or additional derived metric is rejected.

Core validation reopens both page and final PPTX files and checks the exact chart/object type, fixed-canvas box, object identity, title, labels, units, bases, period, series dimensions, target, actual, and displayed direct difference. Dot points/connectors plus target and difference marks have deterministic object descriptions; validation checks their expected ellipse/connector type and exact integer-EMU bounds or endpoints. The difference arrow must retain triangle arrowheads at both ends. OfficeCLI may add optional evidence but is not required for this readback.

For `editable-image-v3`, every `tables[]` item is a native table and must also provide `rows`, `font_size`, `font_color`, `cell_fill`, and `cell_margin_px`. Both colors are explicit `#RRGGBB`; inheritance from an unresolved table style or theme is not accepted. The runtime writes the selected font size/color, fill, and margins into every cell so the verifier can calculate per-cell contrast and capacity from actual DrawingML rather than defaults.

`text_inventory` and `visual_inventory` are only inventories; they do not substitute for positioned `text_boxes`, `images`, and `shapes`. The manifest must be sufficient to rebuild the page without reading any custom page script.

Missing coordinates are page-contract violations. The runtime must reject them during `editppt run record` and deck validation because otherwise missing values fall back to default positions such as the top-left corner.

Text-size fitting:

- `text_boxes[].font_size` is treated as the requested font size. The deterministic builder may clamp it downward during normalization when the requested size is too large for the resolved source-pixel box.
- Keep default fitting enabled for first drafts. Set `fit_text: false` only when the page author has manually calibrated the box and font size.
- `text_boxes[].box_px` should describe the source text bounds plus modest padding. Do not use an entire card, chart, table cell group, or unrelated container as the text box, because the fitter can only infer size from the box it receives.
- Optional tuning fields are `min_font_size`, `max_font_size`, `text_fit_safety`, and `line_height`.

`text_inventory` may be a list of strings or a list of structured objects. In structured objects, the fields used for exact text validation are `text`, `required_text`, `items`, or `texts`; fields such as `id`, `decision`, `description`, and `note` are only records and are not used for exact text matching. Example:

```json
[
  {"id": "title", "text": "Market Overview", "decision": "native-text"},
  {"id": "metrics", "required_text": ["Annual recurring revenue", "42.8M"]}
]
```

`quality_checks` must include at least:

```json
{
  "font_size_calibrated": true,
  "visual_inventory_matched": true,
  "background_strategy_checked": true,
  "shape_corner_geometry_checked": true
}
```

`background_strategy` must explain at least:

- `mode`: `native-or-script`, `source-preserving-local-cleanup`, `imagegen-full-clean-base`, or similar.
- `source_consistency_contract`: which composition, perspective, object positions, colors, lighting, and key details are preserved.
- `removed_foreground`: which foreground objects were removed from the background and rebuilt later.
- `comparison_note`: the background consistency conclusion after comparing the preview against the source.

`asset_provenance` requirements — every path referenced in `images[]` must have a matching entry:

- `path`: the image path as referenced in `images[]`.
- `source`: the file the asset was produced from (for separated assets and clean bases this is typically `source.png` or the recorded asset sheet; for formulas the `.tex` file). The referenced file must exist.
- `source_type`: exactly one of `asset-sheet-separated`, `authentic-published-source`, `imagegen`, `latex-rendered-formula`, `user-provided`, `user-approved-rasterization`. `authentic-published-source` means the exact bytes of a publicly sourced authentic image are embedded with a source-page URL and custody hash; it is not an Image2 likeness or a user-provided file. No other value passes validation.
- `provenance_note`: a non-empty explanation of how the asset was produced.

Validation keyword-scans the free text of `visual_inventory` and `asset_provenance` entries:

- An item whose description names a foreground object (icon, photo, logo, screenshot, badge, 图标, 照片, ...) must state its separation method in its text — include a term like "asset-sheet separated" / "image edit" / "分离" — unless the text marks it as background, formula, or native structure. Matching is substring-level, so words like "benchmark" or "trademark" also trigger the foreground check ("mark"); give native structural items an explicit "native structural" / "结构" marker in their description to exempt them.
- Terms naming forbidden fallbacks — "crop", "approximation", "fallback", "emoji", "裁剪", "近似", "降级", and similar — fail validation wherever they appear in these texts, even inside negations such as "no crop". Describe what was done ("asset-sheet separated from source"), not what was avoided.

`roundRect` shapes must record `source_corner_radius_px`; they may also record `corner_reason`. If the source is a straight-corner rectangle, use `rect`.

Recommended record:

```json
{
  "type": "roundRect",
  "box_px": [64, 169, 472, 187],
  "source_corner_radius_px": 12,
  "corner_category": "small-radius",
  "corner_reason": "source card corners are lightly rounded"
}
```

Allowed `corner_category` values: `straight`, `small-radius`, `large-radius`, `pill`. `straight` should not use `roundRect`.

`latex-rendered-formula` formula assets must record:

```json
{
  "images": [
    {
      "id": "formula_c2_1",
      "path": "assets/formula_c2_1.svg",
      "box_px": [105, 392, 390, 90],
      "alt": "LaTeX rendered formula formula_c2_1",
      "z_index": 220
    }
  ],
  "asset_provenance": [
    {
      "path": "assets/formula_c2_1.svg",
      "source": "assets/formula_c2_1.tex",
      "source_type": "latex-rendered-formula",
      "provenance_note": "Rendered from LaTeX by editppt formula render-latex; visual fidelity is prioritized over formula editability."
    }
  ],
  "formula_inventory": [
    {
      "id": "formula_c2_1",
      "decision": "latex-rendered-image",
      "editable": false,
      "image": "assets/formula_c2_1.svg",
      "tex_source": "assets/formula_c2_1.tex"
    }
  ]
}
```

Formula images must be generated by `editppt formula render-latex`. Do not use source-image formula snippets, and do not assemble complex formulas from hand-written native text boxes.

## `pages/page_NNN/imagegen-jobs.json`

Owner: created by `editppt prepare`, updated by `editppt image import` and `editppt image process-sheet` (`generate`/`edit` do not write it — importing the selected output is what records the job).

Purpose: record the generation and processing process for clean bases, asset sheets, and selected bitmap assets.

State and provenance record rules are described in the State Principles section of `SKILL.md` and in the asset processing examples in `cli-helper.md`.

## `notes_manifest.json`

Owner: created by `editppt prepare`, read by `editppt run finalize`.

Purpose:

- Original PPT/PPTX speaker notes.
- Notes hashes.
- Page mapping.

Notes are not handed to page workers, translated, summarized, or rewritten.

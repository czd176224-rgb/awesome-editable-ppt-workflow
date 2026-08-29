# PR1 Final Review Fix Report

## Status

Complete. All final whole-PR Critical/Important findings and the two requested Minor findings are fixed inside Tasks 1-3 only.

## Fixes

- Canonicalized numeric authority once before sealing: source numeric strings become numbers, categorical labels become strings, source `series` becomes renderer `name`, legacy `times` is removed from sealed authority, and period/shared-category/renderer compatibility is enforced.
- Added a real OOXML extraction -> authority -> manifest -> PPTX build -> exact readback regression.
- Read cached OOXML series labels without formula concatenation and restricted chart titles to the chart-level `<c:title>`.
- Restricted target lines and difference arrows to the editable shape-based `dot` renderer. Native column/bar/line/scatter/bubble manifests and authority candidates with these marks refuse.
- Formatted target, actual, and direct difference labels through `Decimal`, including exact `0.3 - 0.2 -> 0.1` display.
- Made the OfficeCLI-free preview carry unit, period, basis, dot rows/connectors, target/actual/difference labels and marks; corrected horizontal negative bars to share the zero baseline.
- Preserved legacy `times` beside extracted `<c:cat>` values while keeping `categories` as the only numeric-authority category field.
- Refused variable-rectangle authorities whose widths sum to zero.
- Skipped quantitative PPTX readback opening when no manifest contains charts.
- Updated the existing manifest schema; no sidecar, dependency, Agent/model call, renderer hierarchy, or Task 4-6 work was added.

## RED Evidence

The corrected regression files were overlaid onto an isolated detached worktree at fix base `17d8650` and run against the old production code:

```text
python -m pytest \
  plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_materials.py \
  plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_source_assets_chart_dimensions.py \
  plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_quantitative_charts.py \
  -q -k "selector_canonicalizes or selector_requires_period or native_chart_variants_refuse or variable_rectangle_refuses or series_label_uses or axis_title_does or explicit_target_actual or native_variants_refuse_shape or preview_dot or preview_negative or chart_readback_skips or extracted_chart"
```

Result: exit code 1, `20 failed, 113 deselected in 4.59s`.

The failures directly showed uncanonicalized source strings/series labels, missing period and category-equality checks, native target-mark acceptance, zero-width variable rectangles, formula-title concatenation, axis-title leakage, float display artifacts, incomplete preview semantics, the negative-bar baseline defect, unnecessary chart readback opening, and failure of the extraction-to-readback path.

## GREEN Evidence

Focused changed-boundary suite:

```text
python -m pytest \
  plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_materials.py \
  plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_source_assets_chart_dimensions.py \
  plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_quantitative_charts.py -q
```

Result: `133 passed in 4.37s`.

Full PR1 cross-task gate (the controller's original 177-test command plus 19 new regressions):

```text
python -m pytest \
  plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_materials.py \
  plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_source_assets_chart_dimensions.py \
  plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_source.py \
  plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_reconstruction.py \
  plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests -q
```

Result: `196 passed in 17.20s`.

Full reconstruction CLI suite:

```text
python -m pytest plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests -q
```

Result: `64 passed in 7.82s`.

## Files

- `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_materials.py`
- `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/source_assets.py`
- `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/build_pptx_from_manifest.py`
- `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/validate_pptx.py`
- `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/references/manifest-schema.md`
- `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_materials.py`
- `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_source_assets_chart_dimensions.py`
- `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_reconstruction.py`
- `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_quantitative_charts.py`
- this report

## Self-Review

- Authority normalization is one direct copy-and-canonicalize boundary; fallback chart facts remain untouched and retain legacy `times`.
- Standard authority emitted by the selector now satisfies the renderer's data contract before geometry is added.
- Native charts cannot reach fixed-percentage target geometry through either selector or manifest validation.
- Dot construction, preview, and readback use the same horizontal value semantics and exact decimal labels.
- Preview assertions inspect emitted labels/line structure and bar rectangles, so the pre-fix preview fails them.
- Readback still validates every chart-bearing page/final deck; only the no-chart path avoids reopening.
- No unrelated worktree changes, dependency additions, Tasks 4-6 code, or release metadata changes were made.

## Concerns

None blocking. Native target/difference marks remain intentionally unavailable until PowerPoint plot-area and axis coordinates can be proven; callers must use the editable `dot` form for those marks in PR1.

# Quantitative Chart Discipline v1.2.3 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add source-backed professional quantitative charts and financial-analysis discipline to v1.2.3 while preserving the v1.2.2 Word+SVG input, model-call count, editability, ordinary-page behavior, and rollback path.

**Architecture:** Word remains the only factual source. Complete chart facts become one sealed `numeric_authority`. Standard charts are built with the already-installed `python-pptx`; special charts use existing editable shapes and text. OfficeCLI/PowerPoint validation is optional enhancement only and must never block the core build, preview, validation, or assembly path.

**Tech Stack:** Python 3, python-docx, python-pptx, Pillow, JSON, pytest. No new dependency.

**Spec:** `docs/plans/2026-08-29-quantitative-chart-discipline-v1.2.3-design.md`

**Starting point:** branch `spike/chart-feasibility-v1.2.3`, commit `47848f3`. Phase 0 proved the manifest lifecycle, Word dimension extraction, incomplete-data fallback, and `numeric_authority` transport. Task 1 replaces its spike-only OfficeCLI construction dependency before production work continues.

## Global Constraints

- User inputs remain exactly one paginated `.docx` and one `.svg` logo.
- Word is factual authority; Image2 never authorizes numbers or calculations.
- Supported primitives remain exactly `column_bar`, `line_point`, `xy`, `cumulative_bridge`, `time_interval`, and `variable_rectangle`.
- Standard primitives carry an explicit source-backed `chart_variant`: `column`, `bar`, `line`, `scatter`, or `bubble`.
- Missing, ambiguous, or dimensionally inconsistent data falls back to the recoverable source chart or a native table.
- No new Agent, model call, reviewer, retry, service, Excel input, formula engine, renderer hierarchy, or dependency.
- Standard charts are native PowerPoint chart objects; special charts are editable shapes and text, never whole-chart screenshots.
- OfficeCLI is optional. Its absence or failure cannot block core generation, preview, validation, or final assembly.
- Every task is test-first and stops if its focused tests or ordinary-page regressions remain red.

---

### Task 1: Replace the Spike-Only OfficeCLI Construction Path

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/build_pptx_from_manifest.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/finalize_manifest_deck_run.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_manifest_run_record_finalize.py`

- [ ] Add a failing lifecycle test that makes `officecli_executable()` raise and still expects chart build, preview, exact readback, validation, and final assembly to succeed.
- [ ] Build the Phase 0 standard chart with `python-pptx` `Presentation` and `ChartData` after the existing deterministic manifest build.
- [ ] Generate the required deterministic chart preview from manifest data with Pillow; keep native object correctness separate and prove it through `python-pptx` readback.
- [ ] Leave OfficeCLI/PowerPoint checks behind an optional branch only.
- [ ] Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_manifest_run_record_finalize.py -q`
- [ ] Commit: `fix: remove required OfficeCLI chart path`

### Task 2: Seal Numeric Authority and Explicit Variants

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_materials.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_source.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_reconstruction.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_materials.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_source.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_reconstruction.py`

- [ ] Write failing cases for the six primitives and five standard variants.
- [ ] Preserve only explicit source dimensions: categories, values, x/y/size, start/changes/end, start/end dates, widths, and shares.
- [ ] Implement one direct ordered selector. It returns authority only when exactly one chart is complete and unambiguous.
- [ ] Require `chart_variant` for `column_bar`, `line_point`, and `xy`; do not infer line versus dot, column versus bar, or scatter versus bubble downstream.
- [ ] Add refusal tests for mismatched lengths, missing units, missing bubble sizes, missing endpoints, inconsistent totals, multiple eligible charts, and qualitative priority text.
- [ ] Keep incomplete records available for native-table fallback.
- [ ] Run the three focused workflow tests and commit: `feat: seal quantitative authority variants`.

### Task 3: Render Native Standard Charts

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/references/manifest-schema.md`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/build_pptx_from_manifest.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/validate_pptx.py`
- Create: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_quantitative_charts.py`

- [ ] Add failing manifest and readback tests for unique IDs, in-bounds `box_px`, exact labels/values, units, periods, and explicit variants.
- [ ] Map variants directly to `XL_CHART_TYPE.COLUMN_CLUSTERED`, `BAR_CLUSTERED`, `LINE`, `XY_SCATTER`, and `BUBBLE`.
- [ ] Use the existing fixed-canvas `box_px` mapping; remove the spike's independent free-form anchor path.
- [ ] Validate exact chart type, categories, series values, title, unit, and period from the built and final PPTX.
- [ ] Document optional `charts[]` inside the existing manifest; do not create a sidecar schema.
- [ ] Run chart plus manifest lifecycle tests and commit: `feat: render native quantitative charts`.

### Task 4: Render the Three Special Primitives with Existing Shapes

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/build_pptx_from_manifest.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/validate_pptx.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_quantitative_charts.py`

- [ ] Write failing exact-geometry and invalid-input tests for waterfall, Gantt, and variable rectangles.
- [ ] Calculate only values consumed by these renderers: cumulative levels, date durations/positions, normalized widths, and segment shares.
- [ ] Expand them into the existing rectangle, line, and text-box records with deterministic object IDs.
- [ ] Validate editable objects, coordinates, labels, and source totals before recording and after final assembly.
- [ ] Do not add `presentation_annotations`, CAGR, percentage-change, mean/min/max helpers, a formula registry, a generic geometry engine, or a renderer class hierarchy.
- [ ] Run chart plus fixed-region tests and commit: `feat: render editable special charts`.

### Task 5: Connect the Existing Worker, Director, and Reviewer

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_reconstruction_worker.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/prompts/page-worker.md`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/scripts/build-page-worker-prompt.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/consulting_prompt.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/review.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_accepted_image_worker_reconstruction.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_current_editable_page_package.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_consulting_prompt.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_review.py`

- [ ] First prove the renderers accept the sealed authority; only then add prompt contracts describing supported behavior.
- [ ] Copy `numeric_authority` into the existing page request and include it in the existing whole-request hash. No sidecar.
- [ ] State that the accepted image owns composition while numeric authority owns quantitative marks and labels; workers cannot change the primitive, variant, or calculate new metrics.
- [ ] Add conditional think-cell-style chart grammar and TTS-style financial discipline inside the existing six prompt sections only.
- [ ] Extend only existing reviewer categories and the existing reviewer call; add no score, category, retry, or model invocation.
- [ ] Verify ordinary qualitative pages receive no quantitative encoding instruction.
- [ ] Run the existing worker/package/prompt/reviewer tests and commit: `feat: connect quantitative chart discipline`.

### Task 6: Run the Seven-Page Gate and Update v1.2.3 Metadata

**Files:**
- Create: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_quantitative_chart_v123_e2e.py`
- Modify: plugin version metadata found by `rg -n '1\.2\.2' plugins/awesome-editable-ppt-workflow`
- Modify: `docs/plans/2026-08-29-quantitative-chart-discipline-v1.2.3-design.md`

- [ ] Build six quantitative Word fixtures plus one qualitative control: column/bar, line, scatter/bubble, waterfall, Gantt, variable rectangle, and qualitative-only.
- [ ] Verify page count remains seven; standard pages contain native chart objects; special pages contain editable shapes; the control page contains no invented quantitative encoding.
- [ ] Run the full workflow and reconstruction test suites without OfficeCLI available.
- [ ] Only after the core gate passes, run optional installed OfficeCLI/PowerPoint validation and record its result separately.
- [ ] Update v1.2.3 metadata and evidence while preserving the v1.2.2 tag and verified rollback cache.
- [ ] Commit: `feat: complete quantitative chart discipline v1.2.3`.

## Execution Handoff

Execute sequentially in the current isolated worktree with `superpowers:executing-plans`. Production files overlap, so one implementation owner with per-task test checkpoints is the smallest stable path.

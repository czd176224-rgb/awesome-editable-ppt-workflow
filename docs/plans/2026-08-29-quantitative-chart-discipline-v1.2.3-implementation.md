# Quantitative Chart Discipline v1.2.3 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add source-backed professional quantitative charts and financial-analysis discipline to v1.2.3 while preserving the v1.2.2 Word+SVG input, model-call count, editability, and ordinary-page behavior.

**Architecture:** Keep one factual path: Word extraction produces conservative `chart_facts`; eligible facts become one sealed `numeric_authority`; the existing director and reviewer receive conditional clauses; the existing reconstruction manifest owns either native PowerPoint charts or deterministic editable shapes. Reuse the Phase 0 `charts` field and OfficeCLI lifecycle proof, and add no parallel manifest, Agent, service, or dependency.

**Tech Stack:** Python 3, python-docx, python-pptx, existing OfficeCLI/PowerPoint runtime, JSON, pytest.

**Spec:** `docs/plans/2026-08-29-quantitative-chart-discipline-v1.2.3-design.md`

**Starting point:** branch `spike/chart-feasibility-v1.2.3`, commit `47848f3`. Phase 0 is already complete: the optional manifest `charts` field, native PowerPoint preview path, final-assembly survival, real Word time-interval extraction, incomplete-data table fallback, and `numeric_authority` transport are proved. Production work hardens and generalizes that spike; it must not recreate it in a parallel path.

## Global Constraints

- User inputs remain exactly one paginated `.docx` and one `.svg` logo.
- The Word source is the factual authority; Image2 never authorizes a number.
- `rendering_primitive` is one of `column_bar`, `line_point`, `xy`, `cumulative_bridge`, `time_interval`, or `variable_rectangle`.
- Missing or ambiguous dimensions retain the recoverable source chart or fall back to a native table.
- No new Agent, model call, reviewer, retry, service, schema system, Excel input, or dependency installation.
- Standard charts are native PowerPoint chart objects; special charts are editable shapes and text, never whole-chart screenshots.
- Every production change follows red-green-refactor and keeps the published v1.2.2 tag unchanged.
- Stop after any task whose focused tests or ordinary-page regression tests remain red. Do not hide a failure with a screenshot, extra model call, or permissive fallback.

---

### Task 1: Seal the Numeric Authority Contract

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_materials.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_source.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_reconstruction.py`
- Test: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_materials.py`
- Test: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_source.py`
- Test: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_reconstruction.py`

**Interfaces:**
- Consumes: extracted Word chart records and explicit Word table rows.
- Produces: `numeric_authority_from_chart_facts(chart_facts: list[dict]) -> dict | None` and a sealed `reconstruction_request.numeric_authority`.

- [ ] **Step 1: Write failing tests for all six eligibility mappings**

```python
@pytest.mark.parametrize(("facts", "expected"), [
    ({"unit": "USD m", "categories": ["A", "B"], "series": [{"name": "Revenue", "values": [1, 2]}]}, "column_bar"),
    ({"unit": "USD m", "series": [{"name": "Revenue", "times": ["2025", "2026"], "values": [1, 2]}]}, "line_point"),
    ({"unit": "%", "series": [{"name": "Assets", "x_values": [1, 2], "y_values": [3, 4]}]}, "xy"),
    ({"unit": "USD m", "start_value": 10, "changes": [2, -1], "end_value": 11}, "cumulative_bridge"),
    ({"series": [{"name": "Plan", "times": ["A"], "start_dates": ["2026-09-01"], "end_dates": ["2026-09-10"]}]}, "time_interval"),
    ({"unit": "%", "width_values": [60, 40], "share_values": [[70, 30], [20, 80]]}, "variable_rectangle"),
])
def test_numeric_authority_selects_only_complete_primitive(facts, expected):
    authority = numeric_authority_from_chart_facts([facts])
    assert authority["rendering_primitive"] == expected
```

- [ ] **Step 2: Run the new tests and confirm they fail because the selector is absent**

Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_materials.py -q`

- [ ] **Step 3: Implement the minimum conservative selector and explicit-dimension preservation**

```python
RENDERING_PRIMITIVES = {
    "column_bar", "line_point", "xy", "cumulative_bridge", "time_interval", "variable_rectangle",
}

def numeric_authority_from_chart_facts(chart_facts):
    eligible = []
    for item in chart_facts:
        primitive = conservative_rendering_primitive(item)
        if primitive not in RENDERING_PRIMITIVES:
            continue
        series = item.get("series", [])
        if primitive == "column_bar" and item.get("categories") and series:
            eligible.append({**copy.deepcopy(item), "rendering_primitive": primitive})
        elif primitive == "line_point" and series and all(s.get("times") for s in series):
            eligible.append({**copy.deepcopy(item), "rendering_primitive": primitive})
        elif primitive == "xy" and series and all(s.get("x_values") and s.get("y_values") for s in series):
            eligible.append({**copy.deepcopy(item), "rendering_primitive": primitive})
        elif primitive == "cumulative_bridge" and all(key in item for key in ("start_value", "changes", "end_value")):
            eligible.append({**copy.deepcopy(item), "rendering_primitive": primitive})
        elif primitive == "time_interval" and series and all(s.get("start_dates") and s.get("end_dates") for s in series):
            eligible.append({**copy.deepcopy(item), "rendering_primitive": primitive})
        elif primitive == "variable_rectangle" and all(key in item for key in ("width_values", "share_values")):
            eligible.append({**copy.deepcopy(item), "rendering_primitive": primitive})
    return eligible[0] if len(eligible) == 1 else None
```

`conservative_rendering_primitive()` is one direct ordered `if` chain. It honors a valid source-backed primitive from Phase 0, otherwise maps only complete explicit dimensions. Keep both functions in the existing materials module; do not add a registry or generic rule engine.

- [ ] **Step 4: Add refusal tests**

Cover missing units, mismatched label/value lengths, missing bubble size, missing Gantt endpoints, incomplete Mekko composition, ambiguous multiple eligible charts, and qualitative priority text. Each must return no quantitative authority and preserve a native-table fallback record.

- [ ] **Step 5: Make reconstruction use the selector instead of selecting the first string-valued primitive**

```python
authority = numeric_authority_from_chart_facts(materials.get("chart_facts", []))
if authority is not None:
    request["numeric_authority"] = authority
```

- [ ] **Step 6: Run the three targeted test files**

Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_materials.py plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_source.py plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_reconstruction.py -q`

- [ ] **Step 7: Commit**

```bash
git add plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_materials.py plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_source.py plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_reconstruction.py plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_materials.py plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_source.py plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_reconstruction.py
git commit -m "feat: seal quantitative authority"
```

### Task 2: Add Only Permitted Presentation Calculations

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_materials.py`
- Test: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_materials.py`

**Interfaces:**
- Consumes: complete numeric inputs with compatible units and periods.
- Produces: `presentation_annotations(facts: Mapping[str, Any]) -> list[dict[str, Any]]` with recorded inputs and displayed results.

- [ ] **Step 1: Write failing exact-arithmetic and refusal tests**

Test `difference`, `share`, `percentage_change`, `mean`, `min`, `max`, `cagr`, waterfall positions, and duration. Test zero CAGR base, missing denominator, incompatible units, missing period count, and invalid dates as refusals.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_materials.py -k presentation -q`

- [ ] **Step 3: Implement direct helpers with `Decimal` and `date.fromisoformat`**

```python
def difference(left: Decimal, right: Decimal) -> Decimal:
    return right - left

def duration(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days
```

Each annotation stores `kind`, `inputs`, `displayed_result`, `unit`, `period`, and `source_page`. Do not implement IRR, DCF, valuation multiples, forecasts, scenarios, or a formula registry.

- [ ] **Step 4: Run focused and full materials tests**

Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_materials.py -q`

- [ ] **Step 5: Commit**

```bash
git add plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_materials.py plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_materials.py
git commit -m "feat: add source-backed chart annotations"
```

### Task 3: Add Conditional Director and Reviewer Contracts

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/consulting_prompt.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/review.py`
- Test: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_consulting_prompt.py`
- Test: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_review.py`

**Interfaces:**
- Consumes: existing confirmed `chart_facts` and financial page content.
- Produces: two conditional clauses inside the existing six-part prompt and expanded definitions for existing review error categories.

- [ ] **Step 1: Write failing prompt tests**

Assert that quantitative pages contain the exact rules “match the verified data relationship” and “never turn qualitative information into quantitative visual encoding”; financial pages contain subject, period, unit, basis, actual/forecast, assumption, and total/component preservation. Assert ordinary qualitative pages do not receive chart-family instructions.

- [ ] **Step 2: Run the prompt tests and confirm failure**

Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_consulting_prompt.py -q`

- [ ] **Step 3: Append the two clauses inside existing compiler-owned sections**

Place chart grammar in `consulting_information_architecture` and financial discipline in `factual_and_brand_constraints`. Keep exactly six sections and do not alter director schema or model-call flow.

- [ ] **Step 4: Write failing reviewer prompt tests**

Assert existing `misleading_fabrication`, `consulting_argument_failure`, and `unusable_17_8_composition` definitions reject changed unit/period/entity/status, qualitative-to-quantitative encoding, unsupported metrics, and chart-displacing decoration.

- [ ] **Step 5: Extend only the existing reviewer wording**

Do not add an error category, reviewer call, score, or retry.

- [ ] **Step 6: Run prompt and reviewer tests**

Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_consulting_prompt.py plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_review.py -q`

- [ ] **Step 7: Commit**

```bash
git add plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/consulting_prompt.py plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/review.py plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_consulting_prompt.py plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_review.py
git commit -m "feat: add quantitative and financial prompt discipline"
```

### Task 4: Complete the Existing Manifest Chart Contract

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/references/manifest-schema.md`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/build_pptx_from_manifest.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/validate_pptx.py`
- Test: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_manifest_run_record_finalize.py`
- Create: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_quantitative_charts.py`

**Interfaces:**
- Consumes: `manifest.charts[]` entries with `object_id`, `rendering_primitive`, `box_px`, categories/series/dimensions, labels, unit, and source digest.
- Produces: native PowerPoint charts for `column_bar`, `line_point`, and `xy`; exact chart-object readback validation.

- [ ] **Step 1: Write failing manifest validation tests**

Require unique `object_id`, valid primitive, positive in-bounds `box_px`, equal category/value lengths, numeric XY arrays, non-negative bubble sizes, and explicit unit/period labels. Reject unknown fields that could smuggle unsupported calculations.

- [ ] **Step 2: Run the new file and confirm failure**

Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_quantitative_charts.py -q`

- [ ] **Step 3: Replace Phase 0 string assembly with validated native chart props**

Map `column_bar` to column/bar, `line_point` to line, and `xy` to scatter/bubble using existing OfficeCLI properties. Convert `box_px` through the existing fixed-canvas mapping instead of accepting an independent `anchor` string.

- [ ] **Step 4: Add exact post-build and final-deck readback**

Read chart categories, series values, title/unit, and chart type from the generated PPTX. Any mismatch adds a page contract violation and blocks recording/finalization.

- [ ] **Step 5: Document `charts[]` in the existing manifest schema reference**

State that `charts[]` is optional, manifest-authoritative, and never a second sidecar.

- [ ] **Step 6: Run reconstruction tests**

Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_manifest_run_record_finalize.py plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_quantitative_charts.py -q`

- [ ] **Step 7: Commit**

```bash
git add plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/references/manifest-schema.md plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/build_pptx_from_manifest.py plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/validate_pptx.py plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_manifest_run_record_finalize.py plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_quantitative_charts.py
git commit -m "feat: validate native chart manifests"
```

### Task 5: Render the Three Special Primitives as Editable Shapes

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/build_pptx_from_manifest.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/validate_pptx.py`
- Test: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_quantitative_charts.py`

**Interfaces:**
- Consumes: validated `cumulative_bridge`, `time_interval`, and `variable_rectangle` chart entries.
- Produces: ordinary manifest `shapes[]` and `text_boxes[]` with deterministic `object_id` values and quantitative geometry records.

- [ ] **Step 1: Write failing geometry tests**

For a fixed `box_px`, assert exact waterfall baselines/tops, Gantt date-to-x mapping, and Mekko width/share rectangles. Assert zero-span dates, inconsistent waterfall endpoint, negative widths, and incomplete shares reject or fall back.

- [ ] **Step 2: Run the geometry tests and confirm failure**

Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_quantitative_charts.py -k special -q`

- [ ] **Step 3: Implement three direct expansion functions**

```python
levels = [Decimal(str(chart["start_value"]))]
for change in chart["changes"]:
    levels.append(levels[-1] + Decimal(str(change)))
if levels[-1] != Decimal(str(chart["end_value"])):
    raise ValueError("waterfall endpoint does not equal start plus changes")

span_days = (date.fromisoformat(max(chart["end_dates"])) - date.fromisoformat(min(chart["start_dates"]))).days
if span_days <= 0:
    raise ValueError("time interval requires a positive date span")

width_total = sum(Decimal(str(value)) for value in chart["width_values"])
if width_total <= 0:
    raise ValueError("variable rectangle widths must sum to a positive value")
```

Keep the three short expansion functions beside the existing manifest normalization code. Use the existing rectangle, line, and text manifest fields. Extract a separate module only if this file would otherwise exceed the repository's current maintainability threshold during review; do not add a chart class hierarchy, renderer interface, or generic geometry engine.

- [ ] **Step 4: Expand special charts during manifest normalization**

Preserve the original `charts[]` record for validation, append generated native objects to existing arrays, and prohibit a whole-chart image.

- [ ] **Step 5: Validate object editability and exact geometry**

Compare the generated shape coordinates and displayed labels to the source chart record before page recording and after final assembly.

- [ ] **Step 6: Run the chart and fixed-region suites**

Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_quantitative_charts.py plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_fixed_region_reconstruction.py -q`

- [ ] **Step 7: Commit**

```bash
git add plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/build_pptx_from_manifest.py plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/validate_pptx.py plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_quantitative_charts.py
git commit -m "feat: render editable special charts"
```

### Task 6: Give the Existing Reconstruction Worker the Sealed Exception

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_reconstruction_worker.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/prompts/page-worker.md`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/scripts/build-page-worker-prompt.py`
- Test: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_accepted_image_worker_reconstruction.py`
- Test: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_current_editable_page_package.py`

**Interfaces:**
- Consumes: accepted V6 reconstruction request containing optional `numeric_authority`.
- Produces: editppt `page_request.json.numeric_authority` and a worker prompt that treats it as authority only for quantitative marks and labels.

- [ ] **Step 1: Write failing request-copy and prompt tests**

Assert byte-for-byte semantic equality of `numeric_authority`, inclusion in the sealed request hash, and explicit worker rules: source image owns composition; numeric authority owns marks/labels; the worker cannot change primitive or calculate.

- [ ] **Step 2: Run the two test files and confirm failure**

Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_accepted_image_worker_reconstruction.py plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_current_editable_page_package.py -q`

- [ ] **Step 3: Copy the optional authority into the existing page request before dispatch**

Do not create a sidecar. The existing whole-request SHA-256 remains the only dispatch seal.

- [ ] **Step 4: Update the worker prompt and manifest instructions**

Require native charts/special shapes from `numeric_authority`, prohibit chart screenshots, and retain native-table fallback.

- [ ] **Step 5: Run the request, prompt, and dispatch tests**

Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_accepted_image_worker_reconstruction.py plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_current_editable_page_package.py plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_manifest_run_record_finalize.py -q`

- [ ] **Step 6: Commit**

```bash
git add plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_reconstruction_worker.py plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/prompts/page-worker.md plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/scripts/build-page-worker-prompt.py plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_accepted_image_worker_reconstruction.py plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_current_editable_page_package.py
git commit -m "feat: seal chart authority into reconstruction"
```

### Task 7: Run the Seven-Page Release Gate and Publish v1.2.3 Metadata

**Files:**
- Modify: plugin version metadata files identified by `rg -n '1\.2\.2' plugins/awesome-editable-ppt-workflow`
- Modify: `docs/plans/2026-08-29-quantitative-chart-discipline-v1.2.3-design.md`
- Create: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_quantitative_chart_v123_e2e.py`

**Interfaces:**
- Consumes: six complete Word fixtures and one qualitative control fixture.
- Produces: validated PPTX outputs, a release-gate report, and v1.2.3 metadata while preserving v1.2.2 rollback.

- [ ] **Step 1: Write seven real paginated Word fixtures in the test**

Create column/bar, line/point, XY/bubble, waterfall, Gantt, Mekko-like, and qualitative-control pages with python-docx. Use explicit units, periods, labels, and values.

- [ ] **Step 2: Run the end-to-end test and confirm the missing production paths fail**

Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_quantitative_chart_v123_e2e.py -q`

- [ ] **Step 3: Complete only failures required by the seven-page gate**

Do not add a new chart family or template beyond the six primitives. Verify standard pages contain chart objects, special pages contain editable shapes, the qualitative page contains no quantitative encoding, and page count remains seven.

- [ ] **Step 4: Run relevant and full test suites**

Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests -q`

- [ ] **Step 5: Run Office validation and reopen/readback on the seven-page output**

Run the installed OfficeCLI validation plus python-pptx object/readback assertions. Record exact pass counts and artifact paths in the design document.

- [ ] **Step 6: Update only v1.2.3 metadata and preserve v1.2.2**

Confirm the v1.2.2 tag and installed rollback remain untouched. Do not publish or overwrite an installed plugin cache during this branch task.

- [ ] **Step 7: Commit**

```bash
git add plugins/awesome-editable-ppt-workflow docs/plans/2026-08-29-quantitative-chart-discipline-v1.2.3-design.md
git commit -m "feat: complete quantitative chart discipline v1.2.3"
```

## Self-Review

- Spec coverage: tasks cover extraction, eligibility, permitted calculations, prompt grammar, financial discipline, native charts, special editable shapes, numeric sealing, local validation, fallback, seven representative pages, qualitative refusal, model-call invariants, and version preservation.
- Placeholder scan: no placeholder markers, generic error-handling instruction, or undefined follow-up step remains.
- Type consistency: all downstream tasks consume the same `numeric_authority` and six-value `rendering_primitive`; `charts[]` remains inside the existing manifest; special renderers return existing `shapes[]` and `text_boxes[]` records.

## Execution Handoff

Preferred: execute sequentially in this existing isolated worktree with `superpowers:executing-plans`; the production files overlap heavily, so one owner and per-task checkpoints are simpler.

Alternative: use `superpowers:subagent-driven-development` in this session, but dispatch only independent test/review slices and keep the overlapping production files under one owner.

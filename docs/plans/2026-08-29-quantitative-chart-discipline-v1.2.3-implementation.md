# Quantitative Chart Discipline v1.2.3 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add eight dual-mode business-chart relationships—source-backed quantitative charts plus safe non-scaled qualitative substitutes—and financial-analysis discipline to v1.2.3 while preserving the v1.2.2 Word+SVG input, model-call count, editability, ordinary-page behavior, and rollback path.

**Architecture:** Word remains the only factual source. Complete chart facts become one sealed `numeric_authority`; incomplete or qualitative relationships never create numeric authority and instead select a named non-scaled substitute through the existing director/reviewer/worker path. Standard charts are built with the already-installed `python-pptx`; special quantitative charts and qualitative substitutes reuse existing editable shapes, text, and native tables. OfficeCLI/PowerPoint validation is optional enhancement only.

**Tech Stack:** Python 3, python-docx, python-pptx, Pillow, JSON, pytest. No new dependency.

**Spec:** `docs/plans/2026-08-29-quantitative-chart-discipline-v1.2.3-design.md`

**Starting point:** branch `spike/chart-feasibility-v1.2.3`, commit `47848f3`. Phase 0 proved the manifest lifecycle, Word dimension extraction, incomplete-data fallback, and `numeric_authority` transport. Task 1 replaces its spike-only OfficeCLI construction dependency before production work continues.

## Global Constraints

- User inputs remain exactly one paginated `.docx` and one `.svg` logo.
- Word is factual authority; Image2 never authorizes numbers or calculations.
- Supported primitives remain exactly `column_bar`, `line_point`, `xy`, `cumulative_bridge`, `time_interval`, and `variable_rectangle`.
- Standard primitives carry an explicit source-backed `chart_variant`: `column`, `bar`, `line`, `dot`, `scatter`, or `bubble`.
- The eight relationships are drivers, time change, two-variable relationship, third-variable size, market-size/share, project-stage/time, option comparison, and target/actual/variance.
- Each relationship has exactly two outcomes: a source-scaled quantitative form when dimensions are complete, or its named non-scaled qualitative substitute. Missing data never silently removes the relationship.
- Qualitative substitutes are driver bridge, timeline/roadmap, labelled qualitative quadrant or comparison table, uniform nodes, equal-width hierarchy, roadmap/milestones, comparison table, and goal-current-gap structure.
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

### Task 2: Seal Numeric Authority, Variants, and Dual-Mode Selection

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_materials.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_source.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_reconstruction.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/source_assets.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_materials.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_source.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_reconstruction.py`
- Create: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_source_assets_chart_dimensions.py`

- [ ] Write failing cases for the six primitives and six standard variants, plus quantitative eligibility/refusal pairs for all eight relationships.
- [ ] Preserve only explicit source dimensions: categories/values; `x_values`/`x_label`/`x_unit`/`x_basis`; `y_values`/`y_label`/`y_unit`/`y_basis`; `size_values`/`size_label`/`size_unit`/`size_basis`; start/changes/end; start/end dates; `width_values`/`width_label`/`width_unit`/`width_basis`; and `share_values`/`share_label`/`share_unit`/`share_basis`/`share_denominator`, plus period/source page and a shared basis only for one-dimensional charts explicitly sharing one basis.
- [ ] Add OOXML extraction tests proving `xVal`, `yVal`, bubble size, axis titles, and units survive independently instead of collapsing into generic `times`/`values`.
- [ ] Implement one direct ordered selector. It returns authority only when exactly one chart is complete and unambiguous.
- [ ] Require `chart_variant` for `column_bar`, `line_point`, and `xy`; do not infer line versus dot, column versus bar, or scatter versus bubble downstream.
- [ ] Preserve explicit compatible `target_value` and `actual_value` for target-line/difference-arrow rendering.
- [ ] Prove that qualitative/incomplete relationships produce no `numeric_authority` and retain enough source wording for the director to choose the named substitute.
- [ ] Add refusal tests for mismatched lengths, missing units, missing bubble sizes, missing endpoints, inconsistent totals, multiple eligible charts, and qualitative priority text.
- [ ] Keep incomplete records available for native-table fallback.
- [ ] Run the three focused workflow tests and commit: `feat: seal dual-mode chart authority`.

### Task 3: Render Standard Charts and Direct Target/Comparison Marks

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/references/manifest-schema.md`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/build_pptx_from_manifest.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/validate_pptx.py`
- Create: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_quantitative_charts.py`

- [ ] Add failing manifest and readback tests for unique IDs, in-bounds `box_px`, exact labels/values, units, periods, and explicit variants.
- [ ] Map variants directly to `XL_CHART_TYPE.COLUMN_CLUSTERED`, `BAR_CLUSTERED`, `LINE`, `XY_SCATTER`, and `BUBBLE`; render `dot` with the existing point/connector shapes because PowerPoint has no stable dedicated dot-plot type.
- [ ] Use the existing fixed-canvas `box_px` mapping; remove the spike's independent free-form anchor path.
- [ ] Render target lines and difference arrows only when compatible explicit target/actual values exist; calculate only their direct difference.
- [ ] Validate exact chart type, categories, series values, title, unit, period, target, actual, and displayed difference from the built and final PPTX.
- [ ] Document optional `charts[]` inside the existing manifest; do not create a sidecar schema.
- [ ] Run chart plus manifest lifecycle tests and commit: `feat: render native quantitative charts`.

### Task 4: Render Special Quantitative Forms with Existing Shapes

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/build_pptx_from_manifest.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/editppt/runtime/validate_pptx.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_quantitative_charts.py`

- [ ] Write failing exact-geometry and invalid-input tests for waterfall, Gantt, and variable rectangles.
- [ ] Calculate only values consumed by these renderers: cumulative levels, date durations/positions, normalized widths, and segment shares.
- [ ] Expand them into the existing rectangle, line, and text-box records with deterministic object IDs.
- [ ] Validate editable objects, coordinates, labels, and source totals before recording and after final assembly.
- [ ] Do not add `presentation_annotations`, CAGR, percentage-change, mean/min/max helpers, a formula registry, a generic geometry engine, a qualitative renderer framework, or a renderer class hierarchy.
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
- [ ] Add the exact eight-row dual-mode mapping inside the existing six prompt sections: quantitative form when dimensions are complete; driver bridge, timeline/roadmap, qualitative quadrant/table, uniform nodes, equal-width hierarchy, roadmap, comparison table, or goal-current-gap when they are not.
- [ ] Require visible qualitative signalling and prohibit numeric axes, proportional geometry, bubble-size ranking, target-line magnitude, and difference magnitude without source values.
- [ ] Add TTS-style subject/unit/period/basis discipline inside the same existing sections only.
- [ ] Extend only existing reviewer categories and the existing reviewer call; add no score, category, retry, or model invocation.
- [ ] Verify each qualitative relationship receives its named substitute and never receives `numeric_authority` or quantitative geometry instructions.
- [ ] Run the existing worker/package/prompt/reviewer tests and commit: `feat: connect quantitative chart discipline`.

### Task 6: Run Synthetic and Huangshi Real-Document Gates, Then Update v1.2.3 Metadata

**Files:**
- Create: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_quantitative_chart_v123_e2e.py`
- Create: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_huangshi_v123_acceptance.py`
- Modify: `package-info.json`
- Modify: `.agents/plugins/marketplace.json`
- Modify: `plugins/awesome-editable-ppt-workflow/.codex-plugin/plugin.json`
- Modify: `public-release-audit.json`
- Modify: `public-source-manifest.json`
- Modify: `README.md`
- Modify: `docs/QUICKSTART.zh-CN.md`
- Modify: `docs/RELEASE.md`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/codex_subscription_runtime.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/codex_web_material_gateway.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/generate-slide-body-image/scripts/provider_worker.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_contract.py`
- Modify: `tests/test_awesome_plugin_identity.py`
- Modify: `tests/test_public_distribution.py`
- Modify: `tests/test_release_hardening_v2.py`
- Modify: `tests/test_task1_metadata_v4.py`
- Modify: `tests/test_install_receipt_state_machine.py`
- Modify: `docs/CONSULTING_DIRECTOR_VISUAL_QA.md`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_task6_metadata_v4.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_contract.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_secure_io.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_awesome_pipeline.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_loop.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_accepted_image_worker_reconstruction.py`
- Modify: `docs/plans/2026-08-29-quantitative-chart-discipline-v1.2.3-design.md`

- [ ] Build nine quantitative relationship fixtures covering ten visual marks: column/bar, line, scatter, bubble, dot, waterfall, Gantt, variable rectangle, and one target/actual/variance dot fixture with independent target-line and difference-arrow readback assertions.
- [ ] Build an explicit `8 relationships × 2 modes` matrix. Each quantitative row asserts required dimensions and renderer; each qualitative row asserts its named substitute and absence of `numeric_authority`.
- [ ] Verify standard pages contain native chart objects; special pages and qualitative substitutes contain editable shapes/tables; no qualitative case contains invented quantitative encoding.
- [ ] Add a controlled harness for `C:\Users\24927\Desktop\黄石市产业创新与母基金专业化管理合作建议_PPT生成专用Word副本_V3.docx`, assert SHA-256 `519FC2C5DAA0B4A2E65954E6FA20DF461E04587749C69AFB5952C6535A4A4A11`, assert 42 logical pages, and select pages 5, 10, 14, 20, 21, and 40. Seal deterministic confirmation data directly so no UI interaction occurs.
- [ ] Assert PNG SHA-256 `9681840BACFBA51E87E47D687C1CA1F9C542F9C235577280447E96070726BCF0`, then wrap it in a test-only SVG image container without changing the original PNG or production contract.
- [ ] Assert the six fixed page contracts from design Section 15: pages 5 and 10 use independent KPI facts with no cross-metric bar-length comparison; page 14 equal-weight roadmap; page 20 equal-width `1+4+N`; page 21 separate disclosed facts plus data-gap warning; page 40 three explicit 30-day segments plus separate 12-month milestone.
- [ ] Assert all page-specific forbidden encodings from design Section 15 and explicitly report that line, scatter/bubble, waterfall, and true Mekko lack sufficient real manuscript data.
- [ ] Render the selected real pages, inspect the PPTX and previews, record concrete deficiencies, fix only reproducible workflow defects, and rerun until the acceptance assertions pass.
- [ ] Run the full workflow and reconstruction test suites without OfficeCLI available.
- [ ] Snapshot the existing v1.2.2 counts and prove zero count increases for five paths: first-candidate success; one/two review-directed corrections; correction-model fallback; Paddle-assisted reconstruction, including its existing possible two worker invocations; and accepted-page recovery with zero new model calls. Extend `test_awesome_pipeline.py`, `test_loop.py`, and `test_accepted_image_worker_reconstruction.py` rather than imposing one incorrect universal count.
- [ ] Only after the OfficeCLI-free core gate passes, run optional installed OfficeCLI/PowerPoint validation and record it as non-blocking additional evidence.
- [ ] Update every active version path listed in this task to `1.2.3`/`v1.2.3`, regenerate or deliberately refresh the two public evidence manifests, and pass all listed release tests. In `test_install_receipt_state_machine.py`, update only active-release expectations and retain fixtures/assertions that deliberately prove v1.2.2 rollback compatibility.
- [ ] Commit: `feat: complete quantitative chart discipline v1.2.3`.

## PR Delivery Boundaries

1. **PR1 — chart core:** Tasks 1-3. Required OfficeCLI path removal, sealed dual-mode authority, native standard charts, dot/target/difference support.
2. **PR2 — special forms and workflow contracts:** Tasks 4-5. Special quantitative shapes, eight qualitative substitutes, director/worker/reviewer discipline.
3. **PR3 — acceptance and release evidence:** Task 6. Synthetic matrix, real Huangshi pages, deficiency/fix loop, metadata, and rollback proof.

Execute sequentially with `superpowers:subagent-driven-development`. Each PR receives its own focused review and test evidence. PR2 branches from PR1; PR3 branches from PR2. Do not publish or merge without explicit external-action authority.

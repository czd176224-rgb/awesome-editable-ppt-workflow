# Task 5 Report — Connect Quantitative Chart Discipline

## Status

Complete. The existing worker, six-section director prompt, and independent reviewer now share the sealed quantitative/qualitative contract without changing renderer semantics, schemas, review categories, retry policy, or model-call topology.

## Implementation

- Copied optional `numeric_authority` unchanged from the accepted reconstruction request into the existing `page_request.json` before prompt construction and dispatch. The existing `page_request_sha256` therefore covers the whole request including the authority; no numeric sidecar was added.
- Kept the accepted source image authoritative for composition and style while making the sealed authority authoritative for quantitative marks and labels.
- Instructed the page worker to preserve `rendering_primitive` and `chart_variant`, copy source values unchanged, and never calculate new metrics.
- Added the exact eight dual-mode relationship rows inside the existing six prompt sections:
  - `increase_decrease_drivers`: cumulative bridge / driver bridge
  - `change_over_time`: line or point chart / timeline-roadmap
  - `two_variable_relationship`: scatter plot / source-labelled qualitative quadrant or table
  - `third_variable_size`: bubble chart / uniform nodes
  - `market_size_share`: variable-width hierarchy / equal-width hierarchy
  - `project_stage_time`: time-interval chart / roadmap-milestones
  - `option_comparison`: bar or column chart / comparison table
  - `target_actual_variance`: target-versus-actual chart / goal-current-gap
- Added subject/unit/period/basis label discipline to the existing text-and-typography section.
- Required qualitative substitutes to signal relationships visibly while forbidding numeric axes, proportional geometry, bubble-size ranking, target-line magnitude, and difference magnitude without complete source values.
- Extended the existing ten reviewer grounds through `misleading_fabrication` and `consulting_argument_failure` wording only. The reviewer still makes one existing structured call with the existing schema and categories.

## TDD Evidence

Renderer prerequisite proof before prompt work:

```text
python -m pytest -q .../test_manifest_run_record_finalize.py::test_native_chart_manifest_survives_final_assembly_without_officecli .../test_quantitative_charts.py
58 passed in 6.96s
```

RED run after adding the Task 5 boundary tests:

```text
13 failed, 1 passed in 14.65s
```

The expected failures proved that `page_request.json` omitted the authority, the worker builder emitted no conditional ownership contract, the six-section prompt omitted the eight-row mapping and label discipline, and the reviewer omitted quantitative/qualitative rejection rules. The one passing characterization confirmed the pre-existing selector already withheld authority from incomplete qualitative evidence.

Focused GREEN run:

```text
14 passed in 13.23s
```

Required full worker/package/prompt/reviewer files:

```text
67 passed in 197.08s
```

Expanded renderer/material/reconstruction regression:

```text
189 passed in 19.27s
```

Additional verification:

- All four modified Python implementation files pass `python -m py_compile`.
- `git diff --check` passes.
- The tests prove the dispatch hash equals SHA-256 of the authority-bearing `page_request.json` and that no `numeric_authority*.json` sidecar exists.

## Files Changed

- `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_reconstruction_worker.py`
- `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/prompts/page-worker.md`
- `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/scripts/build-page-worker-prompt.py`
- `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/consulting_prompt.py`
- `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/review.py`
- `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_accepted_image_worker_reconstruction.py`
- `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_current_editable_page_package.py`
- `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_consulting_prompt.py`
- `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_review.py`
- `.superpowers/sdd/2026-08-29-quantitative-chart-discipline-v1.2.3-implementation/task-5-report.md`

## Self-Review

- No renderer file or renderer behavior changed.
- No new schema, review category, score, retry, Agent/model call, framework, dependency, UI, or sidecar was added.
- The prompt compiler still emits exactly the same six headings; Task 5 contracts are appended within existing sections.
- The reviewer call remains one invocation with the same role, images, output schema, and timeout.
- Ordinary qualitative requests still omit `numeric_authority`; their worker prompt only adds the required no-invented-geometry guard.

## Concerns

None blocking.

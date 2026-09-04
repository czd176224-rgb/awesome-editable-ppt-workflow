# Task 3 report

## Verdict

- **PASS** — the shared deterministic manifest-to-PPTX builder now serializes uniquely resolvable sealed directed edges with exact target arrowheads and source-pixel endpoint enforcement.
- The host final authority check remains unchanged.
- The accepted Image2 body, page director, and six-part image prompt contract remain unchanged.

## RED

- Command: `py -m pytest plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_v4_manifest_runtime.py -q`
- Result before production changes: `4 failed, 5 passed`.
- Expected failures proved the old builder left 1px source/target misses outside their nodes, emitted no `tailEnd`, and accepted a 2px target miss.

## Changes

- `build_pptx_from_manifest.py`
  - Recognizes only exact `edge:{from_node}->{to_node}` IDs whose source and target IDs resolve uniquely across manifest objects.
  - Keeps already-contained endpoints unchanged.
  - Snaps source or target endpoint misses whose Euclidean distance to the corresponding `box_px` is at most one source pixel; snapped points receive a deterministic `1/1024` source-pixel inward inset to avoid independent OOXML EMU rounding outside a max boundary.
  - Rejects larger misses, missing/duplicate/ambiguous nodes, duplicate edges, missing node boxes, and missing edge `points_px`.
  - Emits exactly `<a:tailEnd type="triangle"/>` for resolved sealed edges and no `headEnd`; ordinary lines keep their prior geometry and arrowhead behavior.
- Builder regression coverage now includes target-side 1px snapping and PPTX readback, source-side symmetry, `flipH`, 2px rejection, ambiguous IDs, and ordinary-line non-regression.
- Removed test-only post-build arrow injection from all impacted workflow suites so successful relationship tests exercise the production builder output directly.

## GREEN and impacted verification

- Builder regression: `py -m pytest plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_v4_manifest_runtime.py -q` -> `10 passed in 0.58s`.
- Focused builder/validator suites: `py -m pytest plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_v4_manifest_runtime.py plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_fixed_region_reconstruction.py plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_openxml_schema_tokens.py -q` -> `23 passed in 2.33s`.
- Impacted workflow suites: `py -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_quantitative_chart_v123_e2e.py plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_reconstruction_recovery_authority.py plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_accepted_image_worker_reconstruction.py plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_huangshi_v123_acceptance.py -q` -> `37 passed, 1 skipped in 231.35s`.
- Compile: `py -m py_compile` on the builder and five changed test modules -> exit `0`.
- Diff check: `git diff --check` -> exit `0`.

## Self-review

- The implementation is confined to the shared builder and regression tests.
- No dependency, schema, agent, model call, template library, fallback, host tolerance, or manual reconstruction route was added.
- The untracked approved plan remains unmodified and is excluded from the Task 3 commit.

## Commit

- Atomic commit subject: `fix: serialize sealed directed edges`
- Resolve the exact commit with `git rev-parse HEAD`; the report is included in that same commit, so embedding its own final hash is not possible without changing the hash.

## Concerns

- None. The required fresh real-page Task 2 rerun remains a separate downstream task.

## Review repair

- Review verdict: `CHANGES_REQUESTED`; all Important 1-3 and Minor findings were addressed in follow-up commit subject `fix: harden sealed edge serialization`.
- Replaced the fixed `1/1024` source-pixel inset with a dynamic inset derived from the actual source-pixel-to-EMU scale. A snapped endpoint moves two EMUs inward, covering the one-EMU discrepancy caused by independently rounded node offsets/extents and connector endpoints.
- Added the reviewer-provided `34000x16000` regression with target box `[3, 100, 12, 100]` and a right-side 1px miss; PPTX readback is strictly within the target box.
- Sealed edges now reject non-`line` types, non-line presets/polygons, `stroke=none` or empty stroke, missing points, non-positive node boxes, unresolved/duplicate/ambiguous IDs, and misses over one source pixel.
- Added `flipV`, high-resolution, no-stroke, rect-with-points, missing-node, duplicate-node, and zero-size regression coverage. Missing and duplicate coverage is intentionally minimal: one endpoint-missing case exercises directed-edge resolution, while duplicate IDs remain governed by the existing editable-image-v3 global uniqueness gate. Zero-size nodes are rejected at the directed-edge trust boundary.
- Split builder rejection from host authority evidence. The host test now builds and validates a legal PPTX, then independently tampers the readback artifact (missing node, reversed direction, non-line geometry) and requires the exact finalization `ValueError` message. Removing the host check would make all three cases fail.
- Host authority implementation, accepted Image2 authority, page director, prompt contract, dependencies, schemas, and reconstruction routes remain unchanged.

## Review repair verification

- RED builder command: `py -m pytest plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_v4_manifest_runtime.py -q` -> `4 failed, 13 passed`; failures were high-resolution EMU containment, `stroke=none`, rect-with-points, and zero-size node acceptance.
- Pre-change host isolation command: `py -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_quantitative_chart_v123_e2e.py::test_host_finalization_rejects_tampered_sealed_relationship -q` -> `3 passed`, proving the unchanged host check rejected post-validation tampering independently of the builder.
- GREEN builder command: `py -m pytest plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_v4_manifest_runtime.py -q` -> `17 passed in 0.76s`.
- Exact host finalization command: `py -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_quantitative_chart_v123_e2e.py::test_host_finalization_rejects_tampered_sealed_relationship -q` -> `3 passed in 12.00s`.
- Focused builder/validator suites: same three files documented above -> `30 passed in 0.87s`.
- Impacted workflow suites: same four files documented above -> `37 passed, 1 skipped in 228.74s`.
- Final post-review rerun after strict RGB-stroke validation and exact host error assertions: focused builder/validator -> `30 passed in 0.84s`; impacted workflow -> `37 passed, 1 skipped in 247.29s`; exact host tamper cases -> `3 passed in 12.51s`.

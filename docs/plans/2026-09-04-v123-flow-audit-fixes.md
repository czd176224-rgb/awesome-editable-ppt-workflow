# v1.2.3 Overall Flow Audit Fixes

## Goal

Make the current v1.2.3 route enforce one coherent authority from Word facts through page direction, Image2 review, editable reconstruction, assembly, and final verification.

## Global constraints

- Preserve the existing six-part Image2 prompt and automatic added-page behavior.
- Preserve one initial director call and at most two local Image2 corrections.
- Word remains semantic authority; the accepted body image remains visual authority.
- No new model, agent, template library, fallback renderer, or dependency.
- Prefer deletion and shared contracts over new layers.
- Each task is delivered as a separate reviewable commit/PR unit with regression tests first.

## Task 1: Restore coherent color semantics and one review authority

- Add failing tests proving that, with `highlight_color` present:
  - parallel peers keep the same tone;
  - ordered process, stage, or hierarchy may use ordered same-family depth only when source-supported;
  - non-emphasis pages prohibit secondary- and highlight-family text;
  - emphasis pages allow restrained short-text use of secondary or highlight;
  - the reviewer accepts only the current prompt, not a legacy prompt compiled with relaxed color permission.
- Refactor `consulting_prompt.py` to one shared semantic-color contract with optional highlight-role additions.
- Remove the `legacy_prompt` review bypass.
- Keep color rules only in the Visual Style and Color section.

## Task 2: Repair the attachment-input authority regression

- Reproduce the currently failing four-page round test.
- Trace the attachment render receipt and image digest from material preparation to Image2 command construction.
- Fix the shared root cause without weakening the confirmed-input digest guard.
- Restore the full suite to green.

## Task 3: Seal accepted-image and page-publication authority

- Bind accepted image bytes and normalized pixel digest into reconstruction request, dispatch, worker input, and host readback.
- Stage page PPTX, page receipt, and reconstruction receipt before the final `page_complete` transition.
- Reject stale worker validation and check the current worker exit code first.
- Require the current editable reconstruction contract on the formal Word route; isolate legacy/native-direct compatibility from production.

## Task 4: Make assembly and final validation authoritative

- Revalidate every page package, accepted receipt, request, manifest, and PPTX readback inside assembly.
- Report assembly state and final artifact digest in the pipeline report.
- Add post-reconstruction visual comparison and final Office/OpenXML validation to the release path.
- Build a selected-page test project that produces and verifies one actual assembled comparison deck.

## Stop condition

- Task-scoped tests pass after each commit.
- The complete workflow test suite passes.
- A final reviewer finds no unresolved critical or important issue.
- A real selected-page project produces an assembled, editable, Office-validated PPTX whose rendered bodies remain consistent with the accepted images.

## Completion status

- Completed on 2026-09-04 across separate reviewable commits for Tasks 1–4.
- Final workflow suite: 1335 passed, 52 skipped, 0 failed.
- Independent reviewer and supervisor: pass, with no unresolved critical or important issue.
- Real Huangshi selected-page release evidence: 8-page assembled editable deck, release-ready, with OpenXML, PowerPoint, OfficeCLI, and assembled-body visual validation passed.
- Evidence root: `D:\AI项目管理\01-当前项目\黄石\v123-pr4b-release-validation-20260904-212000`.

# Consulting Report Director v1.1.0 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the GitHub v1.0.1 scene-oriented page director and six-part Image2 prompt with one consulting-report director v2 contract, without a legacy runtime fallback.

**Architecture:** Keep the existing V6 `run-pages` pipeline, single director, candidate loop, independent review, reconstruction, and Office validation. Replace the structured director schema and prompt compiler, then propagate the new authority through correction, review, resume, tests, and v1.1.0 release metadata. Existing accepted pages remain historical outputs; an unfinished v1 authority is never parsed as a new prompt and is restarted under v2.

**Tech Stack:** Python 3.12, JSON Schema draft 2020-12, pytest, Codex structured output, gpt-image-2 provider bridge, PowerShell release tooling.

---

## PR topology

1. `upgrade/consulting-director-v1.1.0-pr1` -> base `v1.0.1`
2. `upgrade/consulting-director-v1.1.0-pr2` -> base PR1 branch
3. `upgrade/consulting-director-v1.1.0-pr3` -> base PR2 branch
4. `upgrade/consulting-director-v1.1.0-pr4` -> base PR3 branch
5. `upgrade/consulting-director-v1.1.0-pr5` -> base PR4 branch

Each PR must be independently green and reviewable. No PR may retain an active legacy director fallback.

### PR1: Define the v2 director and six-part prompt contract

**Files:**
- Create: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/schemas/consulting_page_director_v2.schema.json`
- Create: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/consulting_prompt.py`
- Create: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_consulting_prompt.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/references/visual_director.md`

**Step 1: Write failing schema tests**

Add tests requiring `awesome-consulting-page-director-v2`, the new creative-direction fields, and exactly these six prompt fields: `task_and_canvas`, `core_proposition_and_content`, `consulting_information_architecture`, `visual_style_and_color`, `text_and_typography`, and `strict_prohibitions`.

**Step 2: Verify RED**

Run: `python -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_consulting_prompt.py -q`

Expected: FAIL because the v2 schema and compiler do not exist.

**Step 3: Implement the minimal v2 schema and compiler**

The compiler owns geometry, fixed-layer exclusions, confirmed color roles, central 17:8 safe region, and prohibited large accent usage. It emits six headings in the approved order and rejects custody paths, blank text, legacy field names, and duplicated compiler-owned clauses.

**Step 4: Verify GREEN**

Run the targeted test above, then `python -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment -q`.

**Step 5: Commit and open PR1**

Commit: `feat: add consulting director v2 prompt contract`

### PR2: Route initial generation and correction exclusively through v2

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/director.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/__init__.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/review.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_director.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_review.py`
- Delete: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/schemas/complex_page_director_v1.schema.json`

**Step 1: Write failing integration tests**

Require `direct_page` and both correction strategies to emit only the v2 authority and compiler output. Assert that all six old field names are rejected and no old compiler symbol is exported.

**Step 2: Verify RED**

Run the director and review test modules. Expected: FAIL on the active v1 schema, compiler, and exports.

**Step 3: Implement the v2 routing**

Use the consulting-report director recipe: one business proposition, one analytical backbone, explanatory lead, evidence-to-interpretation-to-conclusion path, explicit takeaway, and restrained supporting imagery. Use the same six-part v2 shape for correction. Remove the v1 schema and runtime compiler.

**Step 4: Verify GREEN and commit**

Run the complex-page test suite. Commit: `feat: route page generation through consulting director v2`.

### PR3: Upgrade review and resume behavior

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/review.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/loop.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/workspace.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_review.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_loop.py`

**Step 1: Write failing review and migration tests**

Require review to reject missing explanatory copy, disconnected modules, absent analytical backbone, absent takeaway, decorative hero scenes, and prohibited AI-heavy aesthetics. Require unfinished v1 authority to restart under v2 without being parsed; accepted historical pages remain sealed until explicitly regenerated.

**Step 2: Verify RED, implement, and verify GREEN**

Do not add another review or scoring layer. Update the existing single review and checkpoint logic only.

**Step 3: Commit and open PR3**

Commit: `feat: enforce consulting review and safe v1 restart`.

### PR4: Add structural and visual regressions

**Files:**
- Create: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/fixtures/consulting_director_cases.json`
- Create: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_consulting_director_regressions.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_adaptive_e2e.py`
- Create: `docs/CONSULTING_DIRECTOR_VISUAL_QA.md`

**Step 1: Write failing regression tests**

Cover four public synthetic patterns: three-lane portfolio, five-stage capital loop, four-capability transformation chain, and four-row investment matrix. Check six-section order, confirmed semantic colors, explanatory copy, takeaway, fixed-layer exclusions, and absence of legacy terms.

**Step 2: Verify RED, implement fixtures, and verify GREEN**

Keep Huangshi pages 4/19/33/36 as local private visual regression only; never commit user materials or generated images.

**Step 3: Commit and open PR4**

Commit: `test: add consulting director regression coverage`.

### PR5: Package and verify v1.1.0

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/.codex-plugin/plugin.json`
- Modify: `package-info.json`
- Modify: `README.md`
- Modify: `plugins/awesome-editable-ppt-workflow/README.md`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/SKILL.md`
- Modify: release manifests and generated public audit files through repository release scripts

**Step 1: Write failing metadata/runtime tests**

Require v1.1.0 metadata and forbid legacy director/schema/compiler names in packaged runtime files.

**Step 2: Verify RED, update metadata/docs, regenerate manifests, verify GREEN**

Run targeted tests, full pytest, runtime safety checks, `verify.ps1`, and public release audit. Build the Windows release archive and verify its SHA256.

**Step 3: Private end-to-end visual smoke test**

Run the four Huangshi pages under the built plugin without altering the source project. Compare against the accepted prototype criteria and record only pass/fail notes outside the public package.

**Step 4: Commit and open PR5**

Commit: `release: prepare awesome editable ppt workflow 1.1.0`.

## Final completion criteria

- Five PRs exist and are green.
- Active runtime contains no legacy director fallback.
- New and corrected pages use only the v2 six-part prompt.
- Existing accepted pages remain usable; unfinished v1 pages restart safely under v2.
- Full tests, release verification, archive hash verification, and private four-page visual smoke test pass.

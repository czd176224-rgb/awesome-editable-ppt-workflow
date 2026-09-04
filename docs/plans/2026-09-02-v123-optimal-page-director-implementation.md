# v1.2.3 Optimal Page Director Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the overloaded v1.2.3 page-director contract with one compact, source-traceable page plan that the compiler turns into the existing six Image2 prompt sections, then align review, correction, and reconstruction around that same authority.

**Architecture:** Reuse the current complete Word material view, `source_block_id`, relationship metadata, numeric-authority selector, Image2 provider, and editable reconstruction pipeline. The director chooses one primary relationship and one core exhibit; deterministic code owns fact injection, six-section assembly, material audit, fixed boundaries, hard-error review categories, and reconstruction handoff. No new agent, model call, template library, dependency, or page-splitting behavior is introduced.

**Tech Stack:** Python 3, JSON Schema Draft 2020-12, pytest, existing Image2 provider, python-pptx reconstruction, existing signed authority receipts.

---

## PR map and merge order

| PR | Branch | Purpose | Depends on |
| --- | --- | --- | --- |
| PR 1 | `feat/v123-compact-page-plan` | Compact director output plus deterministic six-section compiler | Current v1.2.3 branch |
| PR 2 | `feat/v123-hard-review-local-repair` | Five hard review errors and deterministic one-defect local correction | PR 1 |
| PR 3 | `feat/v123-reconstruction-page-authority` | Seal and pass the page plan into editable reconstruction | PR 2 |
| PR 4 | `test/v123-huangshi-optimal-director` | Two-batch Huangshi A/B acceptance and regression proof | PR 3 |

Each PR must be independently green. Stage only the files listed for that PR; the worktree already contains unrelated user changes that must not be reset, reverted, or included accidentally.

## Explicit non-work

- Do not add a template catalog or chart-name catalog.
- Do not add another material schema. Existing Word blocks already carry stable IDs and relationship metadata.
- Do not add another director, reviewer, or model call.
- Do not make comparison pages diagram-only; `analytical_table` remains valid.
- Do not change Word-page-to-PPT-page mapping or existing supported extra-page behavior.
- Do not make prompt character count a pass/fail gate.

---

## PR 1: Compact page plan and deterministic six-section compiler

### Task 1: Lock the compact output contract with failing tests

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_director.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_consulting_prompt.py`

**Step 1: Add the compact director fixture**

Replace the test-only `_director_value()` shape with:

```python
{
    "schema_version": "awesome-consulting-page-director-v3",
    "page_number": 5,
    "quality": "high",
    "page_plan": {
        "page_purpose": "Explain how regional resources enter the fund system.",
        "primary_relationship": {
            "grammar": "geography",
            "description": "Regional resource entrances feed the fund platform.",
            "fact_ids": ["body-1", "body-2"],
            "visual_instruction": "Use a map-led resource-entry diagram with explicit connectors.",
            "nodes": [
                {"node_id": "regional-resources", "label": "Regional resources", "fact_ids": ["body-1"]},
                {"node_id": "fund-platform", "label": "Fund platform", "fact_ids": ["body-2"]},
            ],
            "edges": [
                {
                    "from_node": "regional-resources",
                    "to_node": "fund-platform",
                    "label": "enter",
                    "fact_ids": ["body-2"],
                }
            ],
        },
        "core_exhibit": {
            "grammar": "geography",
            "description": "A regional map with labeled resource flows.",
            "fact_ids": ["body-1", "body-2"],
        },
        "support_groups": [
            {"role": "support", "label": "Operating support", "fact_ids": ["body-3"]},
            {"role": "note", "label": "Source limitation", "fact_ids": ["body-4"]},
        ],
        "reading_path": "Read the map first, then the operating support and limitation.",
        "local_visuals": [
            {
                "grammar": "flow",
                "instruction": "Use one small arrow sequence for the source-supported entry path.",
                "fact_ids": ["body-2"],
            }
        ],
    },
    "selected_references": [],
}
```

The fixture must use real `source_block_id` values from the material view.

**Step 2: Add contract assertions**

Add tests proving:

```python
assert "creative_direction" not in value
assert "prompt_sections" not in value
assert "machine_record" not in value
assert artifact.page_plan == value["page_plan"]
```

Add parameterized failures for:

- unknown fact ID;
- one fact omitted from core/support/note allocation;
- one fact allocated twice;
- empty primary relationship;
- more than one core exhibit;
- unsupported visual grammar;
- `analytical_table` accepted for comparison;
- `flow`, `hierarchy`, `geography`, or `causality` accepted only with source-bound nodes and edges plus a non-empty visual instruction.

Count allocation only across `core_exhibit.fact_ids` and `support_groups[*].fact_ids`. Relationship and local-visual references may point to already allocated facts because they describe use, not ownership.
Every fact reference anywhere in `primary_relationship`, `core_exhibit`, `support_groups`, or `local_visuals` must still resolve to a known `source_block_id`.

**Step 3: Add compiler assertions**

Prove the compiled prompt:

- has exactly the six existing headings in order;
- contains every complete Word fact exactly once;
- contains `page_purpose`, primary relationship, core exhibit, support labels, reading path, and local visuals;
- contains the confirmed color contract once;
- contains fixed-region and truth boundaries once;
- does not contain the removed `creative_direction`, `prompt_sections`, material-audit prose, or template names;
- records length for observation without failing on length.

**Step 4: Run the tests and verify failure**

Run from `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow`:

```powershell
python -m pytest tests/complex_page_experiment/test_director.py tests/complex_page_experiment/test_consulting_prompt.py -q
```

Expected: failures reference the old v2 schema and old `creative_direction` / `prompt_sections` requirements.

**Step 5: Commit tests**

```powershell
git add -- tests/complex_page_experiment/test_director.py tests/complex_page_experiment/test_consulting_prompt.py
git commit -m "test: define compact v1.2.3 page plan"
```

### Task 2: Replace the director schema and validation

**Files:**
- Delete: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/schemas/consulting_page_director_v2.schema.json`
- Create: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/schemas/consulting_page_director_v3.schema.json`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/director.py:41-545`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/references/visual_director.md`
- Modify: `package-info.json`
- Modify: `verify.ps1`
- Modify: `README.md`
- Modify: `docs/RELEASE.md`
- Modify: `tests/test_public_distribution.py`
- Modify: `tests/test_release_hardening_v2.py`
- Modify: `tests/test_task1_metadata_v4.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_task6_metadata_v4.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/README.md`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/SKILL.md`

**Step 1: Define the v3 schema**

The root must contain only:

```text
schema_version, page_number, quality, page_plan, selected_references
```

Use these visual grammar values only:

```text
analytical_table, flow, hierarchy, geography, causality,
quantitative_chart, composition_architecture
```

Keep selected-reference fields only where Image2 needs them:

```text
material_id, use, preserve
```

Do not retain model-authored `material_use`, fixed-layer exclusions, facts-and-sources restatements, nine creative fields, or six prompt sections.

`primary_relationship` must also contain source-bound structure:

```text
nodes: node_id, source-supported label, fact_ids
edges: from_node, to_node, optional source-supported label, fact_ids
```

`from_node -> to_node` is the sole direction authority; represent a bidirectional relation as two edges rather than a second direction field. Every edge endpoint must name a declared node. Require a non-empty `visual_instruction` and at least one edge when the primary grammar is `flow`, `hierarchy`, `geography`, or `causality`. `analytical_table`, `quantitative_chart`, and `composition_architecture` may use an empty edge list when no directed relation exists.

Keep the old `$defs.promptSections` definition temporarily inside the v3 schema only because the still-existing v2 correction path reads it. It is not a director output field and must be deleted in PR 2 together with the correction-decision model path. This keeps PR 1 independently green without a compatibility adapter.

**Step 2: Simplify `DirectorArtifact`**

Replace the old property with:

```python
@property
def page_plan(self) -> Mapping[str, object]:
    value = self.value["page_plan"]
    assert isinstance(value, Mapping)
    return value
```

**Step 3: Add one fact-allocation validator**

Implement one local helper in `director.py`; do not create a new module:

```python
def _validate_fact_allocation(
    page_plan: Mapping[str, object], material_view: CompletePageMaterialView
) -> None:
    expected = {
        str(block["source_block_id"])
        for block in material_view.value["complete_word_content"]
    }
    core = page_plan["core_exhibit"]
    groups = page_plan["support_groups"]
    allocated = [str(item) for item in core["fact_ids"]]
    allocated.extend(
        str(item)
        for group in groups
        for item in group["fact_ids"]
    )
    if len(allocated) != len(set(allocated)):
        raise ValueError("each Word fact must be allocated exactly once")
    if set(allocated) != expected:
        raise ValueError("page plan must allocate every Word fact exactly once")
```

Use the existing rule that the fixed title is excluded from `complete_word_content`; do not add title-specific exceptions here.
Add a second short traversal that validates every `fact_ids` entry anywhere in the page plan against `expected`, and validates all edge endpoints against declared node IDs.

**Step 4: Make material audit deterministic**

Delete `_complete_material_use()`. Derive selected reference IDs directly from `selected_references`, validate them against `_image_material_ids()`, and let authority publication record the selected IDs. Fixed-layer exclusions remain compiler-owned.

**Step 5: Shorten the director request**

Keep only:

- Word/material authority;
- confirmed taskbook;
- current color roles for planning;
- complete page material view and image map;
- compact output instructions;
- the seven open visual grammars and the comparison exception.

Delete `_SIX_PART_DESIGN` and any instruction asking the model to restate compiler-owned geometry, colors, fixed layers, material audit, or six prompt sections.

Shorten `visual_director.md` to principles the model must actually decide: one main relationship, one core exhibit, analytical-table exception, mandatory visualization for flow/hierarchy/geography/causality when primary, and local rather than whole-page metaphor. Keep no template list.

Update the public prompt-contract identity from `consulting-page-director-v2-source-text-custody` to a v3 compact-page-plan identity in `package-info.json`, `verify.ps1`, root README, `docs/RELEASE.md`, plugin README/SKILL, and all current metadata/distribution assertions. Do not change the plugin version number in this PR. Historical design documents may retain the old identity as history.

**Step 6: Run the director tests**

```powershell
python -m pytest tests/complex_page_experiment/test_director.py -q
```

Expected: PASS.

**Step 7: Commit implementation**

```powershell
git add -- schemas/consulting_page_director_v2.schema.json schemas/consulting_page_director_v3.schema.json scripts/complex_page_experiment/director.py scripts/complex_page_experiment/references/visual_director.md README.md SKILL.md tests/test_task6_metadata_v4.py ../../../../package-info.json ../../../../verify.ps1 ../../../../README.md ../../../../docs/RELEASE.md ../../../../tests/test_public_distribution.py ../../../../tests/test_release_hardening_v2.py ../../../../tests/test_task1_metadata_v4.py
git commit -m "feat: simplify page director authority"
```

### Task 3: Compile the six Image2 sections deterministically

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/consulting_prompt.py:207-311`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_consulting_prompt.py`

**Step 1: Replace the director branch and retain one temporary correction branch**

The director branch must accept `awesome-consulting-page-director-v3`, read `page_plan`, and never accept model-authored prompt sections. Until PR 2 deletes the correction-decision model call, retain one isolated `awesome-page-correction-v2` branch that validates and compiles its existing six sections. Add a PR 1 test that forces a non-mechanical `decide_correction()` result through this branch. Delete the temporary branch in PR 2; do not expose it to v3 director output.

**Step 2: Add one fact renderer**

Render each Word block once in source order:

```python
def _complete_fact_content(material_view: object) -> str:
    blocks = _material_value(material_view)["complete_word_content"]
    return "\n".join(_render_complete_source_block(block) for block in blocks)
```

`_render_complete_source_block()` must preserve paragraph/list text and every table row/cell in source order. It must never fall back to `block.get("text", "")` for table blocks, because that would silently drop high-density pages such as 14, 31, 40, and 41. Add paragraph, list, and table tests before implementation.

**Step 3: Assemble the six sections from fixed responsibilities**

Use the existing `SECTION_SPECS` order:

1. Task and Canvas: body frame plus visual priority only.
2. Core Proposition and Content: `_complete_fact_content()` once plus the no-loss boundary.
3. Consulting Information Architecture: page purpose, primary relationship, core exhibit, support groups, reading path, and local visuals using fact IDs.
4. Visual Style and Color: existing confirmed color roles once.
5. Text and Typography: existing typography rule once.
6. Strict Prohibitions: fixed layers, source truth, and qualitative/numeric encoding boundary once.

Do not repeat complete facts outside section 2.
Render each selected reference's `use` and `preserve` instruction exactly once in the appropriate execution section; do not leave selected-reference semantics stranded in the schema.

**Step 4: Run compiler tests**

```powershell
python -m pytest tests/complex_page_experiment/test_consulting_prompt.py tests/test_consulting_director_regressions.py -q
```

Expected: PASS, with prompt length printed or stored only as diagnostic metadata.

**Step 5: Commit compiler**

```powershell
git add -- scripts/complex_page_experiment/consulting_prompt.py tests/complex_page_experiment/test_consulting_prompt.py tests/test_consulting_director_regressions.py
git commit -m "feat: compile six sections from page plan"
```

### Task 4: Close PR 1 with focused regression tests

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/fixtures/consulting_director_cases.json`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_consulting_director_regressions.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_huangshi_v123_acceptance.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_loop.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_review.py`
- Modify if recursive tests expose an indirect fixture: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_four_page_round.py`

**Step 1: Migrate every director fixture producer to v3**

Search before running tests:

```powershell
rg -n 'awesome-consulting-page-director-v2|creative_direction|prompt_sections' tests -g '*.py' -g '*.json'
```

Update all director fixture producers to the compact page plan in PR 1. Do not postpone fixture migration to PR 2 or PR 4; PR 1 changes the runtime schema and must be recursively green. Correction fixtures may retain `awesome-page-correction-v2` until PR 2.
Also run `rg -n 'consulting-page-director-v2-source-text-custody' README.md docs tests package-info.json verify.ps1 plugins/awesome-editable-ppt-workflow` from repository root and confirm remaining matches are historical documents or the explicitly temporary correction compatibility test only.

**Step 2: Run the focused suite**

```powershell
python -m pytest tests/complex_page_experiment/test_director.py tests/complex_page_experiment/test_consulting_prompt.py tests/test_consulting_director_regressions.py -q
```

Expected: PASS.

**Step 3: Run the recursive complex-page suite**

```powershell
python -m pytest tests/complex_page_experiment -q
```

Expected: PASS.

**Step 4: Commit fixture migration**

```powershell
git add -- tests/fixtures/consulting_director_cases.json tests/test_consulting_director_regressions.py tests/test_huangshi_v123_acceptance.py tests/complex_page_experiment/test_loop.py tests/complex_page_experiment/test_review.py tests/complex_page_experiment/test_four_page_round.py
git commit -m "test: migrate director fixtures to v3"
```

**Step 5: Check the diff**

```powershell
git diff --check HEAD~3..HEAD
git status --short
```

Expected: no whitespace errors; no unrelated file staged or committed.

**Step 6: Run the release gate for PR 1**

From repository root:

```powershell
.\verify.ps1
.\scripts\release_gate.ps1
```

Expected: PASS before PR 1 is considered mergeable.

---

## PR 2: Five hard review errors and one-defect local correction

### Task 5: Reduce the reviewer to five hard errors

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/schemas/complex_page_review_v1.schema.json`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/schemas/complex_page_review_authority_v1.schema.json`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/schemas/complex_page_acceptance_v1.schema.json`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/review.py:517-639`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_review.py`

**Step 1: Write failing review tests**

Permit only these categories:

```text
fact_integrity
primary_relationship
core_exhibit_prominence
quantitative_truth
severe_usability
```

Require at most one problem per review result. Add acceptance tests proving the reviewer must not reject:

- a professional analytical table used for comparison;
- a valid local diagram without a named metaphor;
- minor connector endpoint drift;
- ordinary aesthetic differences;
- color/card-count differences that do not change facts or relationships.

**Step 2: Run and verify failure**

```powershell
python -m pytest tests/complex_page_experiment/test_review.py -q
```

Expected: old broad review language or schema still permits non-hard-error rejection.

**Step 3: Replace the review prompt**

Keep the independent reviewer and sealed evidence path unchanged. Replace the long rejection list with the five hard errors. Require one most severe visible defect with one concrete repair; explicitly say the reviewer does not redesign the page.
Update the signed review-authority and acceptance schemas in the same commit so the new category enum can be sealed and later accepted.

**Step 4: Run and commit**

```powershell
python -m pytest tests/complex_page_experiment/test_review.py -q
git add -- schemas/complex_page_review_v1.schema.json schemas/complex_page_review_authority_v1.schema.json schemas/complex_page_acceptance_v1.schema.json scripts/complex_page_experiment/review.py tests/complex_page_experiment/test_review.py
git commit -m "feat: limit review to five hard errors"
```

### Task 6: Delete the correction-decision model call

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/director.py:89-104,549-735`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/loop.py:25-35,528-620`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/__init__.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/schemas/consulting_page_director_v3.schema.json`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_director.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_loop.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_four_page_round.py`

**Step 1: Write failing local-correction tests**

Prove that a rejected candidate produces an Image2 edit request containing only:

```text
visible defect
required repair
preserve accepted composition, correct regions, page plan, facts, colors, and fixed boundaries
```

Prove the prior candidate is always the edit input, the page plan is unchanged, facts are not reinjected, and at most two corrections occur.

**Step 2: Remove dead correction machinery**

Delete:

- `_correction_schema()`;
- `decide_correction()`;
- model-authored `CorrectionDecision` fields;
- `regenerate_from_materials` strategy;
- correction-decision role invocation.

Replace it with a deterministic local value, constructed from the single signed `ReviewProblem` and the frozen `DirectorArtifact`. Keep existing evidence recording, but record `model="deterministic-local"`, zero duration, and `quota_bearing=False`.
Remove obsolete imports/exports from `complex_page_experiment/__init__.py`, delete the temporary `$defs.promptSections` retained by PR 1, and delete the temporary `awesome-page-correction-v2` compiler branch. All director fixtures were already migrated in PR 1; this task only updates correction-specific tests.

**Step 3: Keep the two-correction ceiling**

Do not change the existing `max_corrections` validation of integers zero through two. If the second edit still has a hard error, preserve the current honest failure path.

**Step 4: Run and commit**

```powershell
python -m pytest tests/complex_page_experiment/test_director.py tests/complex_page_experiment/test_loop.py tests/complex_page_experiment/test_review.py -q
git add -- schemas/consulting_page_director_v3.schema.json scripts/complex_page_experiment/director.py scripts/complex_page_experiment/loop.py scripts/complex_page_experiment/__init__.py tests/complex_page_experiment/test_director.py tests/complex_page_experiment/test_loop.py tests/complex_page_experiment/test_four_page_round.py
git commit -m "refactor: make image correction local and deterministic"
```

**Step 5: Run the complete PR 2 gate**

```powershell
python -m pytest tests/complex_page_experiment -q
Set-Location ..\..\..\..
.\verify.ps1
.\scripts\release_gate.ps1
```

Expected: PASS before PR 2 is considered mergeable.

---

## PR 3: Carry page authority into editable reconstruction

### Task 7: Seal the accepted page plan

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/schemas/complex_page_acceptance_v1.schema.json`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/loop.py:424-478`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/complex_page_experiment/__init__.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/complex_page_experiment/test_loop.py`

**Step 1: Write a failing seal test**

Assert that the signed acceptance contains the exact v3 `page_plan` and that changing any relationship node, edge endpoint, grammar, core-exhibit fact ID, or reading path invalidates the receipt.

**Step 2: Add `page_plan` to the signed receipt**

Copy `director.page_plan` directly into `_acceptance_value()`. Do not derive or summarize it again. Extend the existing acceptance schema and validation; do not create another authority file.

Promote the existing private `_verify_signed_receipt()` to one exported `verify_signed_acceptance_receipt()` helper and keep the same HMAC, schema, experiment, page, and source-snapshot checks. Reuse this helper in the current loop and in reconstruction; do not duplicate signing logic.

**Step 3: Run and commit**

```powershell
python -m pytest tests/complex_page_experiment/test_loop.py -q
git add -- schemas/complex_page_acceptance_v1.schema.json scripts/complex_page_experiment/loop.py scripts/complex_page_experiment/__init__.py tests/complex_page_experiment/test_loop.py
git commit -m "feat: seal page plan with accepted image"
```

### Task 8: Pass the same plan to the reconstruction worker

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_reconstruction.py:201-303`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts/workflow_v6_reconstruction_worker.py:79-120`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_workflow_v6_reconstruction.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_accepted_image_worker_reconstruction.py`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/scripts/build-page-worker-prompt.py:79-121`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/prompts/page-worker.md:23-34`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/references/page-decision-tree.md:1-13`
- Modify: `plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests/test_current_editable_page_package.py`

**Step 1: Write failing propagation tests**

Assert:

```python
assert request["page_plan"] == receipt["page_plan"]
assert page_request["page_plan"] == receipt["page_plan"]
```

Also prove the request hash changes if the sealed page plan changes, and a stale page plan is rejected on interrupted-run recovery.
Add a package-prompt test proving the final worker prompt contains the sealed relationship nodes, edges, and `from_node -> to_node` direction.
Add a first-load tamper test: alter one sealed node ID or edge endpoint in the accepted receipt without recomputing its HMAC, then assert `build_reconstruction_request()` fails before copying `page_plan` into the request.

**Step 2: Copy the plan before request hashing**

Before reading any page-plan fields, open the live page workspace and call the exported `verify_signed_acceptance_receipt()` on the canonical accepted receipt bytes. Follow the existing `numeric_authority` path only after this verification. Add `page_plan` to `build_reconstruction_request()`, then copy it into the page-worker request before hashing. Do not write a second page-plan JSON file.

Update `build-page-worker-prompt.py`, `page-worker.md`, and `page-decision-tree.md` so a sealed page plan is the second explicit exception to source-image-only authority: it may correct node identity, edge direction, and connector endpoints, while the accepted image still owns composition and style. Without this change, merely copying JSON would not affect reconstruction.

**Step 3: State reconstruction responsibilities once**

The worker request must say:

- accepted image controls composition and style;
- page plan controls node identity, relationship direction, and connector endpoints;
- numeric authority controls values, units, periods, labels, and quantitative geometry;
- reconstruction may correct these precise details but may not replace the core exhibit or reading path.

**Step 4: Run and commit**

```powershell
python -m pytest tests/test_workflow_v6_reconstruction.py tests/test_accepted_image_worker_reconstruction.py ..\reconstruct-editable-slide\cli\tests\test_current_editable_page_package.py -q
git add -- scripts/workflow_v6_reconstruction.py scripts/workflow_v6_reconstruction_worker.py tests/test_workflow_v6_reconstruction.py tests/test_accepted_image_worker_reconstruction.py ..\reconstruct-editable-slide\scripts\build-page-worker-prompt.py ..\reconstruct-editable-slide\prompts\page-worker.md ..\reconstruct-editable-slide\references\page-decision-tree.md ..\reconstruct-editable-slide\cli\tests\test_current_editable_page_package.py
git commit -m "feat: pass page authority to reconstruction"
```

### Task 9: Verify connector, label, and chart authority together

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_huangshi_v123_acceptance.py`
- Modify only if needed: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_quantitative_chart_v123_e2e.py`

**Step 1: Add one combined reconstruction case**

Use a page plan with a hierarchy or geography relationship and a complete numeric authority. Name reconstructed node shapes with their sealed `node_id`. Assert each connector start point falls within the sealed `from_node` shape boundary, its end point falls within the sealed `to_node` shape boundary, and the arrowhead is on the `to_node` end. Allow only the existing small pixel/EMU rounding tolerance. Also assert numeric labels, units, and periods match source values.

**Step 2: Run and commit**

```powershell
python -m pytest tests/test_huangshi_v123_acceptance.py tests/test_quantitative_chart_v123_e2e.py -q
git add -- tests/test_huangshi_v123_acceptance.py tests/test_quantitative_chart_v123_e2e.py
git commit -m "test: verify relationship and numeric reconstruction"
```

**Step 3: Run the complete PR 3 gate**

From repository root:

```powershell
.\verify.ps1
.\scripts\release_gate.ps1
```

Expected: PASS before PR 3 is considered mergeable.

---

## PR 4: Huangshi two-batch A/B acceptance

### Task 10: Update the controlled production-path fixture

**Files:**
- Modify: `plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_huangshi_v123_acceptance.py:145-405`

**Step 1: Replace the old v2 fixture**

Use the compact v3 page-plan fixture. Keep the real production path, fixed frame, reconstruction, and 42-page assembly checks unchanged.

**Step 2: Cover the eight representative pages**

Use source pages 5, 10, 14, 20, 21, 31, 40, and 41. Expected primary grammars:

```text
5 geography
10 quantitative_chart or composition_architecture
14 flow
20 hierarchy
21 causality or analytical_table, based on the complete Word page
31 composition_architecture
40 flow
41 analytical_table
```

Do not hard-code a metaphor, diagram subtype, or space ratio.

**Step 3: Run and commit**

```powershell
python -m pytest tests/test_huangshi_v123_acceptance.py -q
git add -- tests/test_huangshi_v123_acceptance.py
git commit -m "test: update Huangshi acceptance for compact director"
```

### Task 11: Run deterministic regression gates

**Files:** None.

**Step 1: Run targeted tests**

```powershell
python -m pytest tests/complex_page_experiment/test_director.py tests/complex_page_experiment/test_consulting_prompt.py tests/complex_page_experiment/test_review.py tests/complex_page_experiment/test_loop.py tests/test_workflow_v6_reconstruction.py tests/test_accepted_image_worker_reconstruction.py tests/test_huangshi_v123_acceptance.py tests/test_quantitative_chart_v123_e2e.py -q
```

Expected: PASS.

**Step 2: Run the complete plugin suite**

```powershell
python -m pytest tests -q
```

Expected: PASS. If an unrelated pre-existing failure remains, capture the exact failing test and prove the touched targeted suite passes; do not weaken tests.

### Task 12: Run two real A/B batches without UI confirmation

**Files:**
- Source Word: `C:\Users\24927\Desktop\黄石市产业创新与母基金专业化管理合作建议_PPT生成专用Word副本_V3.docx`
- Logo: `C:\Users\24927\Desktop\尚融logo.png`
- Baseline: official v1.2.2 runtime
- Candidate: PR 3/4 v1.2.3 runtime

**Step 1: Create three clean output roots**

Use three separate timestamped directories under `D:\AI项目管理\01-当前项目\黄石`: one official v1.2.2 baseline run and two independent v1.2.3 candidate runs. Do not reuse prior experiment state or accepted-image receipts.

Record reproducibility evidence before each run:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\Users\24927\Desktop\黄石市产业创新与母基金专业化管理合作建议_PPT生成专用Word副本_V3.docx'
Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\Users\24927\Desktop\尚融logo.png'
git rev-parse HEAD
git show -s --format='%H %s' abc3932cd20ba14e6b831278289d52a86d9bd130
```

Also record the sealed taskbook digest, visual-contract digest, official v1.2.2 commit `abc3932cd20ba14e6b831278289d52a86d9bd130`, and candidate commit SHA in the machine-readable result. Use browser automation to submit the already approved confirmation choices; do not ask the user to confirm in the UI.
Initialize all three projects from the same Word/Logo bytes and submit exactly the same confirmed taskbook and visual-contract values. Bind each recorded runtime/package identity to the resolved commit SHA before `run-pages`.

**Step 2: Run one baseline batch and two candidate batches**

Use the public command after initialization and confirmation:

```powershell
python scripts\word_to_editable_ppt.py v6 run-pages --project <project> --pages 5 10 14 20 21 31 40 41
```

For every page record:

- complete fact coverage;
- selected primary relationship and core exhibit;
- first-pass acceptance;
- correction count and final status;
- compiled prompt length;
- final editable connector/chart/label validation.

Prompt length is diagnostic only.

**Step 3: Build one browser-visible comparison**

Create a local HTML comparison showing, per page:

```text
Word facts | v1.2.2 image | v1.2.3 batch 1 | v1.2.3 batch 2 | result notes
```

Judge stability only when both candidate batches preserve facts, expose the primary relationship, emphasize the core exhibit, avoid false quantitative encoding, and reconstruct usable editable objects.

**Step 4: Stop condition**

Do not add new prompt rules merely because one image is aesthetically different. Change code only if the same hard defect repeats across both batches or deterministic reconstruction evidence fails.

**Step 5: Run the PR 4 release gate**

```powershell
.\verify.ps1
.\scripts\release_gate.ps1
```

Expected: PASS, with both A/B batch manifests and comparison locations recorded.

---

## Final release checks

Run from repository root:

```powershell
git diff --check
git status --short
git log --oneline --decorate -12
```

Verify:

- four PR branches contain only their declared files;
- no unrelated dirty worktree content was committed;
- no new dependency, agent, model call, or template library exists;
- v1.2.2 baseline remains runnable for comparison;
- v1.2.3 retains the original extra-page behavior and fixed title/logo/footer/page-number layers;
- the browser comparison and final editable PPT are available for user inspection.

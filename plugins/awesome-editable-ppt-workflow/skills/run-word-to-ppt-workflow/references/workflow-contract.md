This is an on-demand reference. Run command examples from the parent skill directory (`skills/run-word-to-ppt-workflow`), not this reference directory or the plugin root. The public entry is `run-word-to-ppt-workflow`; do not ask the user to invoke another skill or run these internal commands.

# Run Word-to-PPT Workflow V6 — new director development

This is an unpublished development copy derived from installed 1.2.3. Its upgrade is under real-document test, not release-ready. The public installed package is unchanged.

This is the only public Word-to-PowerPoint workflow entry and executes the sealed `awesome-word-ppt-workflow-v1` contract with director output `awesome-page-design-v1`. Create a new V6 project from the original paginated Word and SVG Logo. The resumable project authority is `workflow_v6.json`.

## Authoritative flow

1. `v6 init` locks the original Word and SVG Logo in a new project. Preserve complete Word blocks, original comments, Word images, tables, charts and attachments.
2. Open the three-step Confirm UI once:
   - Step 1 confirms one whole-deck director template: Company & Business Introduction, Investment Committee, Project Initiation, Corporate Planning Report or Investment Project BP. The system recommends and the user confirms.
   - Step 2 confirms only primary, secondary and background colors; CJK and Latin fonts; and title, body and caption sizes.
   - Step 3 confirms the ordered page structure and the seven-field taskbook: use scenario, presenter, primary audience, audience prior knowledge, desired understanding/discussion/decision, Word content to emphasize and content to keep lower prominence.
   Before final confirmation, use the explicit deck-planning action once to read the whole Word context and generate per-page titles, emphasis and continuity. The UI allows title/emphasis edits and seals the result in the same final confirmation. For a chapter trial the optional `00_source/full_deck_context.docx` supplies complete manuscript context, while only the selected chapter pages are output. The structure list is source-backed: only proposed additions with a `structure:` ID may be omitted. Never delete, reorder or change the role of source pages; titles may change through the confirmed plan. A second final submission is rejected.
3. Run `prepare-page-materials` for the project pages. Before the first page receipt is published, the existing conditional project-level material pass searches once only when comments explicitly require missing real logos, people, products, projects or factual screenshots. Unverified results remain `not_found`; never generate fake identity assets.
4. Use `run-pages` for all formal page generation, including a one-page run. It invokes one initial Codex page director with complete materials, visual references and confirmed page goals. The director reasons from complete content through relationships, hierarchy and reading order to visual expression, then delivers one executable `page_plan.image_prompt`. That exact string becomes `actual_prompt` and the initial Image2 instruction; no six-section compiler or downstream design selection intervenes. The content inventory records source coverage only, with no layout slots. Word owns all page information; lossless within-page rewording and regrouping must preserve every fact, explanation, condition and relationship. Confirmed titles, emphasis and visual settings are inputs, not choices to reopen.
5. The candidate loop sends one initial Image2 request, performs the local file/format/1904x896 check, then invokes one independent visual review for that candidate. The reviewer independently loads the same taskbook, loads the actual design to diagnose deviations after independently deriving the source and confirmed goals, and uses a fresh thread. Zero director-selected page references selects `generate`; one to sixteen director-selected page references selects `edit`. The first usable candidate is accepted. A rejected candidate may use at most two existing correction opportunities; same-page correction may use the immediately previous candidate, while a new initial attempt never uses a baseline, prior round or other page candidate.
   - The review may reject only one concrete defect using exactly one of five hard-error categories: `fact_integrity`, `primary_relationship`, `core_exhibit_prominence`, `quantitative_truth`, or `severe_usability`. General aesthetic preference is not a hard error.
   - Each signed review problem declares `repair_route`: `edit` preserves the current plan and corrects the immediately previous image; `replan` invokes the same page director with the rejected plan/image and review, publishes a new immutable signed director revision and sends its replacement image_prompt verbatim. Replanning retains predecessor evidence without using the rejected image as an Image2 composition reference. Both routes share the initial-plus-two-corrections budget. Multi-revision failed pages currently reject explicit failure recovery; they do not reset the budget. Signed accepted pages remain recoverable after receipt verification.
   - Ordinary `run-pages` keeps a sealed failure terminal. Only an explicit `recover-failed-pages --recovery-round N` invocation may open the next contiguous round for selected failed pages. The round verifies and preserves the prior signed failure, candidate/request bytes, source, complete material semantics and frozen director before any external call. A prior candidate is recorded truthfully as recovery-round attempt 1, never as a new Image2 call or an acceptance; a timed-out review is rerun with zero Image2 calls, while a prior signed single-defect correction may use at most two new edits as attempts 2 and 3. Accepted or completed pages, missing/skipped rounds, changed authority and cross-project adoption fail closed.
6. Image2 output is dynamically center-cropped to the largest 17:8 region from its actual returned dimensions, then uniformly resized to 1904x896. The independent reviewer sees this final adapted candidate and is the only image-semantic QA.
7. After acceptance, `run-pages` automatically invokes `reconstruct-editable-slide` through a Codex page worker. The accepted 1904x896 image is its only visual authority. Text and simple shapes become editable objects; fixed title/SVG Logo/footer/page number are added as native layers; the completed pages are then assembled. If the page worker cannot start or complete, the page stops rather than falling back to local reconstruction.

The body image excludes the fixed title, SVG Logo, footer and page number. Do not add another director, semantic QA, scoring layer or user candidate-selection step.

Installed baseline 1.2.3 has its original release record; this development line does not inherit a release claim for its changes.

## Word pagination and automatic page composition

- Start each logical slide with a standalone marker such as `第4页`, `第 33 页`, `第36页 · STORY LINE`, `第26页 PPT`, `PPT第02页`, or `PPT第44页 | PART 4`. Logical markers override Word's physical page breaks.
- Marker numbers are source IDs, not output positions. They may be non-consecutive; the output deck is always renumbered continuously from 1 to N. Duplicate source IDs remain visible as warnings.
- An optional standalone `PPT页型：...` line accepts `封面`/`首页`, `目录`, `章节`, `正文`, `尾页`, or `附录`. The control line is not displayed on the slide.
- `v6 init` proposes missing cover, TOC, section and closing pages by default, reusing clearly identified special pages already in Word. Preserve every source page and its order; the final output count can increase. A dense opening body page cannot replace a cover.
- Titles are derived from full-manuscript and chapter understanding plus the page expression; every original page block, including its old title, remains source material. Section suggestions prefer PART, 第N章 and 一、 headings; without chapter headings, propose the first body-page title as a single section and require confirmation. `--preserve-source-layout` opts out of new structure suggestions but still permits structure confirmation.
- Cover and closing pages hide the visible page number. TOC, section, content and appendix pages show their continuous output number.
- The single final confirmation seals the selected director taskbook, global visual contract and the approved ordered page structure. Existing projects without confirmed structure cannot report `release_ready`. `confirm-ui wait --stage final` only persists that already-approved contract for execution; it does not ask the user to confirm again.
- Confirmed V6 materials are immutable. To rerun a previously initialized manuscript with different pagination or composition, create a fresh project from the original Word and SVG Logo.

## Production commands

```powershell
python scripts\word_to_editable_ppt.py v6 init --word D:\Input\source.docx --logo D:\Input\logo.svg --project D:\Projects\Deck
python scripts\word_to_editable_ppt.py confirm-ui start --project D:\Projects\Deck
python scripts\word_to_editable_ppt.py confirm-ui wait --project D:\Projects\Deck --stage final
python scripts\word_to_editable_ppt.py v6 prepare-page-materials --project D:\Projects\Deck --page 1 --out D:\Projects\Deck\02_v6\awesome_page_materials\page_001.json
python scripts\word_to_editable_ppt.py v6 run-pages --project D:\Projects\Deck --pages 1 2 3
python scripts\word_to_editable_ppt.py v6 recover-failed-pages --project D:\Projects\Deck --pages 16 27 --recovery-round 1
```

Use `doctor` for authentication, CLI, DNS fake-IP, font and optional Office diagnostics. Never print tokens or package user inputs or outputs with the plugin.

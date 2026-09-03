---
name: run-word-to-ppt-workflow
description: Use when converting one paginated Word document and one SVG Logo into a resumable, object-level editable 16:9 PowerPoint with confirmed per-page materials.
---

# Run Word-to-PPT Workflow V6

This is the only public Word-to-PowerPoint workflow entry and executes the sealed `awesome-word-ppt-workflow-v1` contract with prompt contract `consulting-page-director-v3-compact-page-plan`. Create a new V6 project from the original paginated Word and SVG Logo. The resumable project authority is `workflow_v6.json`.

## Authoritative flow

1. `v6 init` locks the original Word and SVG Logo in a new project. Preserve complete Word blocks, original comments, Word images, tables, charts and attachments.
2. Open the three-step Confirm UI once:
   - Step 1 confirms one whole-deck director template: Company & Business Introduction, Investment Committee, Project Initiation, Corporate Planning Report or Investment Project BP. The system recommends and the user confirms.
   - Step 2 confirms only primary, secondary and background colors; CJK and Latin fonts; and title, body and caption sizes.
   - Step 3 confirms only the seven-field taskbook: use scenario, presenter, primary audience, audience prior knowledge, desired understanding/discussion/decision, Word content to emphasize and content to keep lower prominence.
   The submission does not contain page roles, page order, page count, page titles, risk warnings or material-gap fields. A second submission is rejected.
3. Run `prepare-page-materials` for the project pages. Before the first page receipt is published, the existing conditional project-level material pass searches once only when comments explicitly require missing real logos, people, products, projects or factual screenshots. Unverified results remain `not_found`; never generate fake identity assets.
4. Use `run-pages` for all formal page generation, including a one-page run. It opens each live page workspace and invokes exactly one initial Codex page director for that page. The director reads complete page materials, the current visual-direction reference and the confirmed taskbook, selects page-owned references, and emits the unchanged structured result. The taskbook is a presentation constraint, not factual source material: it may guide emphasis, hierarchy, reading path, evidence framing and takeaway selection, but must not add, omit, rewrite or move Word content. Template ID, version, preset and taskbook digest never enter the model prompt.
5. The candidate loop sends one initial Image2 request, performs the local file/format/1904x896 check, then invokes one independent visual review for that candidate. The reviewer independently loads the same taskbook, does not receive the serialized director output and must use a fresh thread. Zero compiler-selected page references selects `generate`; one to sixteen compiler-selected page references selects `edit`. The first usable candidate is accepted. A rejected candidate may use at most two existing correction opportunities; same-page correction may use the immediately previous candidate, while a new initial attempt never uses a baseline, prior round or other page candidate.
   - The same review must reject a body that fails to communicate one coherent consulting argument, lacks an evidence-to-interpretation-to-conclusion flow or explicit takeaway, contradicts confirmed semantic color meaning, fragments into disconnected module grids, or is dominated by AI-heavy spectacle such as decorative hero scenes, 3D machinery, miniature factories or parks, neon/cyberpunk effects, glowing tracks, or toy-model aesthetics.
   - A signed accepted page from v1.0.1 remains recoverable as historical output without any new model call. An unfinished v1 page must never reuse its legacy `director.json`, prompt, or candidates: preserve those artifacts and restart that page as a fresh consulting-director-v2 run. Explicit regeneration of a previously accepted v1 page follows the same fresh-v2 rule.
6. Image2 output is dynamically center-cropped to the largest 17:8 region from its actual returned dimensions, then uniformly resized to 1904x896. The independent reviewer sees this final adapted candidate and is the only image-semantic QA.
7. After acceptance, `run-pages` automatically invokes `reconstruct-editable-slide` through a Codex page worker. The accepted 1904x896 image is its only visual authority. Text and simple shapes become editable objects; fixed title/SVG Logo/footer/page number are added as native layers; the completed pages are then assembled. If the page worker cannot start or complete, the page stops rather than falling back to local reconstruction.

The body image excludes the fixed title, SVG Logo, footer and page number. Do not add another director, semantic QA, scoring layer or user candidate-selection step.

## Word pagination and automatic page composition

- Start each logical slide with a standalone marker such as `第4页`, `第 33 页`, `第36页 · STORY LINE`, `第26页 PPT`, `PPT第02页`, or `PPT第44页 | PART 4`. Logical markers override Word's physical page breaks.
- Marker numbers are source IDs, not output positions. They may be non-consecutive; the output deck is always renumbered continuously from 1 to N. Duplicate source IDs remain visible as warnings.
- An optional standalone `PPT页型：...` line accepts `封面`/`首页`, `目录`, `章节`, `正文`, `尾页`, or `附录`. The control line is not displayed on the slide.
- The existing composer can infer cover, TOC, appendix and closing material, insert a missing chapter divider from TOC/chapter evidence, and synthesize a closing only when the Word source contains a traceable closing statement. These existing page-role and special-page behaviors remain unchanged and are not exposed for confirmation in the three-step UI.
- Cover and closing pages hide the visible page number. TOC, section, content and appendix pages show their continuous output number.
- The single final confirmation seals the selected director taskbook and global visual contract, while the server freezes the existing trusted composition automatically. `confirm-ui wait --stage final` only persists that already-approved contract for execution; it does not ask the user to confirm again.
- Confirmed V6 materials are immutable. To rerun a previously initialized manuscript with different pagination or composition, create a fresh project from the original Word and SVG Logo.

## Production commands

```powershell
python scripts\word_to_editable_ppt.py v6 init --word D:\Input\source.docx --logo D:\Input\logo.svg --project D:\Projects\Deck
python scripts\word_to_editable_ppt.py confirm-ui start --project D:\Projects\Deck
python scripts\word_to_editable_ppt.py confirm-ui wait --project D:\Projects\Deck --stage final
python scripts\word_to_editable_ppt.py v6 prepare-page-materials --project D:\Projects\Deck --page 1 --out D:\Projects\Deck\02_v6\awesome_page_materials\page_001.json
python scripts\word_to_editable_ppt.py v6 run-pages --project D:\Projects\Deck --pages 1 2 3
```

Use `doctor` for authentication, CLI, DNS fake-IP, font and optional Office diagnostics. Never print tokens or package user inputs or outputs with the plugin.

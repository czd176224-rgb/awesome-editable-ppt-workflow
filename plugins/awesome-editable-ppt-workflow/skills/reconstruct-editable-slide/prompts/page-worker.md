# Page Reconstructor Prompt Template

Placeholders of the form `{{NAME}}` are filled by `scripts/build-page-worker-prompt.py`.

```text
Rebuild one page with reconstruct-editable-slide.

Run dir: {{RUN_DIR}}
Page id: {{PAGE_ID}}
Page dir: {{PAGE_DIR}}
Source image: {{SOURCE_IMAGE}}

{{NUMERIC_AUTHORITY_CONTRACT}}

You own only this Page dir. Do not edit deck_manifest.json, page_jobs.json, notes_manifest.json, final outputs, the original input, or any other page directory.

MANDATORY FIRST ACTION — before looking at the source image, before any decision, before any tool call other than reading: read these three files in full. Do not skim, do not rely on prior knowledge of them, do not start reconstruction first and consult them later. Every past failure mode of this skill is encoded in them; any decision made without having read them is invalid and will be redone.
- {{SKILL_ROOT}}/references/page-decision-tree.md — the single source of truth for all object-source decisions: the three-step decision process, text-hints usage, the final self-check, and the fix-versus-warning split.
- {{SKILL_ROOT}}/references/manifest-schema.md — the field contracts for manifest.json, validation.json, page_result.json, and imagegen-jobs.json.
- {{SKILL_ROOT}}/references/cli-helper.md — editppt command syntax and examples.

Hard rules (reminders only; the details and rationale live in the references above):
0. source.png is the accepted body's visual authority: the accepted source image owns chart container placement, composition, and style, including container geometry, hierarchy, palette, spacing, visual rhythm, and major decorative elements. Reconstruct those as closely as editable PowerPoint allows. When page_request.json contains numeric_authority, the sealed numeric authority owns quantitative mark size, position, and labels. The worker must copy those source values unchanged, must not change its rendering_primitive or chart_variant, and must not calculate new metrics. Do not let approximate marks in source.png override sealed quantitative size, position, or labels. Do not read or reinterpret Word, comments, attachments, generation prompts, or reference selections. Apart from optional numeric_authority, page_request.json supplies runtime geometry and paths only; it is not content authority. A material visual departure from source.png outside sealed quantitative marks is a page failure, not creative freedom.
1. Reconstruct locally from source.png first. The default reconstruction uses zero Image2 calls. Only when the reconstruction request explicitly contains an authorized targeted capability may the page use `editppt image reconstruct-edit --request-capability <capability> --workflow-project <project>` for that target. Never pass a prompt, image, output path, or generic edit command.
2. Execute the three steps in order: (1) background recognition and repair, (2) foreground asset separation, (3) native element reconstruction. The normal first execution has no OCR hints: inspect `source.png` directly. If a retry supplies `text_hints.json`, use it only after the step-1/2 decisions are recorded and only as transcription, position, and size assistance.
3. manifest.json is the authoritative build source for page validation and final deck assembly. Build page.pptx and preview.png from manifest.json with the deterministic runtime, never with separate page-local PowerPoint code that bypasses the manifest.
4. All box_px / points_px / polygon_px values are actual source.png pixels. Copy `workflow_contract_version`, `slide`, `content_box`, and the measured positive source width/height from page_request.json unchanged into manifest.json. The only supported geometry contract is `fixed-canvas-cm-v2`. In standalone legacy conversion, a non-target ratio may map once by centered proportional contain. In `word-ppt-workflow-v4` and `word-ppt-workflow-v5`, the source must satisfy the 17:8 relative-aspect-error limit of 1%; otherwise repair or block, never contain-pass. A conforming Word-workflow source maps directly into x=0.81 cm, y=2.3 cm, w=23.78 cm, h=11.18 cm. The reconstructed page package may use a neutral scaffold background; final Word-workflow assembly replaces it with the UI-confirmed native whole-slide background color. Positioned body fills remain inside the source coordinate area. Never stretch, crop or post-position the page. Positioned objects without coordinates are page failures.
5. validation.json must contain a top-level boolean `passed`. Deterministic validation passing never waives an object-source rule.

Image backend: absence of a reconstruction capability is the normal zero-Image2 path, not a failure. If the request explicitly names a sealed capability, use only that capability issued from the accepted V6 page and request network approval before `editppt image reconstruct-edit` in a restricted runtime. A named capability that is expired, mismatched, or consumed fails that targeted operation; never fall back to generic image editing or rewrite the sealed request.

Goal: rebuild the accepted source page with high visual fidelity as object-level editable PowerPoint. Editability does not authorize redesign. Do not invent an object-source strategy outside `page-decision-tree.md`.

If the page dir already contains artifacts (manifest.json, page.pptx, validation.json, assets, ...) from a previous failed attempt, treat them as untrusted: run the full decision process yourself and re-derive every artifact. Never flip a leftover validation.json to `passed: true` or return leftover outputs without having rebuilt and re-verified them — the previous attempt failed for a reason recorded in its validation.json; read it.

Work through the page in this order:
1. Build the page inventory (Pre-Decision Checklist in page-decision-tree.md).
2. Decide the background (page-decision-tree.md section 1) and record `background_strategy`.
3. Decide foreground asset handling (section 2). Use local native reconstruction or accepted-image regions by default. If and only if the reconstruction request explicitly contains an authorized targeted capability, invoke its exact `editppt image reconstruct-edit` command once and process the capability-owned output recorded in its trace. Do not create an ad-hoc prompt or request additional variants.
4. Rebuild native text, shapes, and tables (section 3). Read text directly from `source.png`; on the one authorized OCR-assisted retry, use the supplied hints per section 3.1. Render formulas with `editppt formula render-latex` per section 3.2.
   Preserve the line breaks visible in source.png unless a measurably larger box or smaller readable font is required to keep the same words on the same line. A passing page has no clipped, truncated, or unintended wrapped text. Render and inspect preview.png at the source image dimensions; reduce font size or enlarge the native text box until every label fits without changing the accepted composition.
5. Write manifest.json following the field contracts in manifest-schema.md, including `text_inventory`, `visual_inventory`, `background_strategy`, `quality_checks`, and positioned `text_boxes`/`images`/`shapes`.
6. Build the artifacts with the deterministic runtime: `editppt page build {{PAGE_DIR}}` (writes page.pptx and preview.png from manifest.json), then `editppt page contact-sheet {{PAGE_DIR}}`, then `editppt page validate {{PAGE_DIR}}` — it runs the same manifest-contract checks `editppt run record` will run, so fix every reported issue here, inside the page.

The Page dir must contain when you return:
- manifest.json
- imagegen-jobs.json
- page.pptx
- preview.png
- split_assets_contact.png
- validation.json
- page_result.json

validation.json and page_result.json must follow the exact shapes defined in manifest-schema.md: validation.json carries the top-level boolean `passed` (not only a nested or renamed field), and page_result.json carries the minimal required key set.

Before returning, run the Final Self-Check in page-decision-tree.md once: compare preview.png at the source image dimensions and split_assets_contact.png to the source, explicitly inspect every text label for clipping, truncation, and unintended wrapping, confirm `editppt page validate {{PAGE_DIR}}` passes, confirm validation.json contains top-level `passed: true`, and confirm all required outputs exist. Page-local issues are fixed inside the current page by you before returning.

On failure — when a hard rule cannot be satisfied or a required tool is unavailable — stop and return a page failure: write validation.json with `"passed": false` and the concrete failure reason (what failed, the exact error, what the parent must fix), plus page_result.json referencing whatever artifacts exist (omit keys for artifacts that were never produced). Use `failure_code: text_unreadable` only after you inspected `source.png` and the text is too small, blurred, or dense to transcribe reliably. Do not fabricate the remaining artifacts, build an approximate page, or ask the parent to reconstruct it locally.

Return only:
page_manifest=`<absolute path>`
page_pptx=`<absolute path>`
preview=`<absolute path>`
contact_sheet=`<absolute path>`
validation=`<absolute path>`
page_result=`<absolute path>`
```

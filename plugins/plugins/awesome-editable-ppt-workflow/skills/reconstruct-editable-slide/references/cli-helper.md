# CLI Helper

This is the `editppt` command manual: install check, command tree, and syntax examples. Workflow policy lives in `SKILL.md`; object decisions and text-hints usage live in `references/page-decision-tree.md`; file and field contracts live in `references/manifest-schema.md`.

Usage principles:

- If a deterministic action can be completed with `editppt`, call the CLI directly instead of rewriting it as a temporary Python script.
- When full parameters are needed, read `editppt <command> --help` or `editppt image <command> --help` first.
- In network-restricted agents, OCR and sealed `editppt image reconstruct-edit` calls need network approval. The approval policy lives in `SKILL.md`.

## Command Tree

```text
editppt                         - top-level CLI for setup, run orchestration, image assets, and formulas
|-- setup                       - create or verify the user-level runtime home and config files
|-- doctor                      - check local runtime health, dependencies, and backend availability
|-- config                      - write the optional Paddle token
|-- prepare                     - normalize image/PDF/PPTX inputs into a run directory and page jobs
|-- run                         - advance run state and coordinate page workers
|   |-- next                    - read current run state and return the next required action
|   |-- status                  - inspect run/page state for debugging or manual checks
|   |-- backend                 - override or inspect the run-level image backend contract
|   |-- dispatch                - record that a page worker was spawned
|   |-- record                  - validate required page outputs and record page result hashes
|   |-- reset                   - return a failed or stuck page to pending for re-dispatch
|   `-- finalize                - rebuild the final PPTX from recorded page manifests and validate it
|-- page                        - page-local helpers
|   |-- build                   - build page.pptx and preview.png from manifest.json
|   |-- contact-sheet           - create the origin-versus-preview comparison image
|   `-- validate                - validate page.pptx against manifest.json as run record will
|-- image                       - generate, edit, import, and process bitmap assets
|   |-- generate                - create a new image from a text prompt
|   |-- edit                    - edit a source image for clean bases or source-faithful asset sheets
|   |-- import                  - copy a selected image into the page dir and record provenance
|   `-- process-sheet           - split a chroma-key asset sheet into transparent assets
`-- formula                     - render formula assets from agent-transcribed LaTeX
    `-- render-latex            - render LaTeX into SVG/PNG/PDF plus a manifest fragment
```

## Common Help Entrypoints

```bash
editppt --help
editppt run --help
editppt image --help
editppt image reconstruct-edit --help
editppt formula render-latex --help
```

`editppt image` uses the installed Codex OAuth capability. There is no second endpoint or credential path.

`editppt image generate/edit` is disabled. Reconstruction consumes the accepted page artifact only through a signed, single-use capability:

```bash
editppt image reconstruct-edit --request-capability <absolute-capability.json> --workflow-project <absolute-project>
```

The command accepts no prompt, image, endpoint, output, or trace arguments. Output and trace locations come from the verified capability.

## Skill Script Commands

```bash
python <skill-root>/scripts/build-page-worker-prompt.py <run> --page page_001 --out <absolute-run-dir>/pages/page_001/worker-prompt.md
```

Purpose: generate a page-worker prompt from the skill-local `prompts/page-worker.md` template. This is a skill script, not an `editppt` CLI command, because it reads skill documentation and references.

The script writes the prompt file and prints JSON with `prompt_file`, `page_id`, and `dispatch_command_template`. It does not create a page worker or claim local execution and must run before `editppt run dispatch`.

## Pre-Run Check

The `editppt` CLI is a required runtime surface for this skill. First confirm that the CLI is available:

```bash
editppt --help
```

If the shell returns command not found, or if the skill was just updated, install the skill-local CLI in editable mode:

```bash
pipx install --force --editable <skill-root>/cli
```

If `pipx` itself is unavailable, fall back to one of:

```bash
uv tool install --force --editable <skill-root>/cli
python3 -m pip install --user -e <skill-root>/cli
```

`<skill-root>` is the `reconstruct-editable-slide` directory that contains `SKILL.md`. On Windows, use the same directory's `cli` subdirectory path.

After the CLI is available, run local runtime checks:

```bash
editppt setup
editppt doctor
```

Image generation is subscription-only through Codex OAuth. Do not configure or persist API keys in the project directory, run directory, prompts, manifests, or plugin settings.

Optionally configure a PaddleOCR-VL token. It is used only after a page worker has inspected the accepted image and explicitly reports unreadable text:

```bash
editppt config --paddle-ocr-token "<token>"
```

Without a token, direct Codex reconstruction remains the normal path; an unreadable-text page stops rather than using a lower-quality substitute.

## Run Commands

```bash
editppt prepare input.png
editppt prepare input.pdf
```

Purpose: normalize a single image, multiple images, a PDF, or an image-based PPTX into a run directory and generate `deck_manifest.json`, `page_jobs.json`, `notes_manifest.json`, plus per-page `pages/page_NNN/source.png` and `page_request.json`. Prepare does not run OCR.

```bash
editppt run next <run> --json
```

Purpose: read current run state and return the next stage. `stage=dispatch_pages` lists `suggested_pages` that must each be dispatched to a page worker, including a one-page run. `stage=wait` means wait for dispatched pages to complete; slow dispatched workers remain active and must not be reset or replaced because they occupy a slot. `stage=finalize` means proceed to final assembly. `stage=configure_backend` appears only when `deck_manifest.json.image_backend` is missing; follow the returned `next_command`.

Generate the page-worker prompt with the skill script before spawning a worker:

```bash
python <skill-root>/scripts/build-page-worker-prompt.py <run> --page page_001 --out <absolute-run-dir>/pages/page_001/worker-prompt.md
```

```bash
editppt run dispatch <run> --page page_001 --agent-id <worker-id> --prompt-file <absolute-run-dir>/pages/page_001/worker-prompt.md
```

Purpose: record that a page has been dispatched to a worker. First create the worker, then run this command. `--prompt-file` uses the same absolute path as the prompt-builder `--out`. `--agent-id` is any stable identifier for the execution; the same id must be reused at `run record`.

```bash
editppt run record <run> --page page_001 --agent-id <worker-id>
```

Purpose: after the page reconstructor writes its required outputs (see `manifest-schema.md`), validate `page.pptx` against `manifest.json` and record the page result. Missing `box_px` / `points_px` on positioned objects is a page failure. The command also fails when `validation.json` does not contain top-level `passed: true` — a failed page is never recorded; fix the root cause, `run reset` the page, and dispatch or claim a fresh page execution.

```bash
editppt run reset <run> --page page_001 --agent-id <worker-id> --confirm-lost
editppt run reset <run> --page page_001 --for-repair
```

Purpose: return a dispatched or recorded page to `pending`, clearing its dispatch and result records, so a new worker can be dispatched. Recorded pages can be reset with only `--page`. Dispatched pages require `--agent-id` plus `--confirm-lost`, and the id must match the recorded dispatch. An accepted page can be reopened only with `--for-repair` after final visual QA identifies a targeted repair. Use reset only for those explicit failure, cancellation, lost-worker, or final-QA-repair cases. The failure-handling policy is in `SKILL.md` Phase 3.

```bash
editppt run finalize <run>
```

Purpose: after all pages are recorded, rebuild, validate, and output the final PPTX. Final assembly reads each recorded `pages/page_NNN/manifest.json` in page order; `page.pptx` is a page-local deliverability artifact, not the final assembly input.

## Page Build Commands

These are the worker-side commands for turning a finished `manifest.json` into the required page artifacts. Use them instead of writing any page-local PowerPoint or imaging code.

```bash
editppt page build pages/page_001
```

Purpose: build `page.pptx` and render `preview.png` from `manifest.json` with the deterministic runtime. Optional `--manifest/--out/--preview` override the default file names inside the page directory.

```bash
editppt page contact-sheet pages/page_001
```

Purpose: create `split_assets_contact.png`, the origin-versus-preview comparison image, from `source.png` and `preview.png` in the page directory.

```bash
editppt page validate pages/page_001
```

Purpose: validate `page.pptx` against `manifest.json` with the same manifest-contract checks `editppt run record` will run (record additionally verifies the full artifact set, hashes, and top-level `passed: true`). Run it before returning so manifest-contract failures are fixed inside the page instead of bouncing back from the parent's record step. Optional `--report <file>` writes a JSON report.

## Image Backend Commands

Consume the accepted page's sealed reconstruction request:

```bash
editppt image reconstruct-edit \
  --request-capability <absolute-capability.json> \
  --workflow-project <absolute-project>
```

The capability fixes the prompt, accepted image bytes, operation, endpoint, and canonical output/trace. Generic generation/editing and API fallbacks are unavailable.

## Asset Processing Commands

Record a selected image output:

```bash
editppt image import pages/page_001 \
  --job-id icon-sheet \
  --source-image /tmp/generated.png \
  --dest assets/icon-sheet.png \
  --role asset_sheet
```

Process a chroma-key asset sheet:

```bash
editppt image process-sheet pages/page_001 \
  --job-id icon-sheet \
  --asset-sheet-source assets/icon-sheet.png \
  --assets-dir assets/icons
```

The asset sheet key color is determined by the generation prompt; `process-sheet` samples the key color from the image edge. Key-color selection and when to regenerate a sheet with a different key color are defined in `page-decision-tree.md` section 2.2.

## Formula Commands

```bash
editppt formula render-latex pages/page_001 \
  --tex "\\sum_{i \\in N} p_{ij}x_{ij} \\ge a_j u_j" \
  --out assets/formula_001.svg \
  --box 100,120,360,80 \
  --id formula_001 \
  --fragment assets/formula_001.fragment.json
```

The agent transcribes the formula from the source into LaTeX. The CLI only renders it into an image asset and manifest fragment.

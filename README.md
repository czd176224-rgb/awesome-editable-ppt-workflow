# Awesome Editable PPT Workflow 1.2.3

Public Codex plugin for converting a paginated Word document plus an SVG Logo into an object-level editable 16:9 PowerPoint.

## V6 adaptive production contract

The workflow contract is `awesome-word-ppt-workflow-v1`; the prompt contract is `consulting-page-director-v3-compact-page-plan`.

Version 1.2.3 is `release-ready`: real-document acceptance, the automated regression suite, public-package checks, clean installation and same-version repair passed on Windows on 2026-09-07.

- Every source Word page is retained in its original order. Confirmed structure pages can increase the total slide count. The body is 1904x896 (17:8).
- Non-contiguous Word page labels such as `第4页`、`第33页`、`第26页` remain in the user's order.
- The first visible Word paragraph is the slide-title authority; later headings stay in the body.
- Cover, TOC, section and closing pages use native editable layouts and preserve every frozen source block.
- One three-step UI confirms a whole-deck director preset, the visual contract, a seven-field presentation taskbook and the ordered page structure. Word remains the factual and page-content authority.
- The confirmed background color becomes the native whole-slide background on every final PPT page, while positioned fills may still structure the page body.
- The confirmed emphasis content is conservatively matched to Word pages in the background. Only those pages may use the secondary-color family for text; non-emphasis pages retain hierarchy through weight, size, position, shapes and fills without secondary-family text.
- One consulting page director outputs only `page_purpose`, `primary_relationship`, `core_exhibit`, `support_groups`, `reading_path`, and `local_visuals`. Conclusions are used only when supplied by the source.
- The director makes explicit source relationships visible through color, position, shape, connectors and hierarchy, including process, level, parallelism, ownership, comparison and causality.
- Complete source-backed numeric dimensions can become editable native charts or editable special-chart shapes; incomplete relationships use named non-scaled substitutes and never invent quantitative geometry.
- The sealed Image2 prompt has exactly six consulting-report sections. The compiler owns the canvas, fixed-layer exclusions, semantic color roles, accent limits, and formal-report prohibitions.
- The single final UI submission is the sole material/reference authority. Every staged reference requires explicit keep/remove; the backend cannot reinterpret it afterward.
- Zero confirmed references uses Image2 `generate`; 1-16 confirmed refs uses `edit`, preserving their ordered role descriptions.
- Reference fusion is high-fidelity best effort, never a pixel-perfect guarantee.
- The final adapted candidate receives one independent review with exactly five hard-error categories: `fact_integrity`, `primary_relationship`, `core_exhibit_prominence`, `quantitative_truth`, and `severe_usability`.
- A rejected page may receive at most two deterministic local edits of the immediately previous image. Each edit repairs one concrete defect, preserves the frozen page plan, and has no correction-model fallback.
- The provider trace records the requested size, service-original dimensions and quality. The service image uses a dynamic centered 17:8 crop and uniform resize to 1904x896 without stretching.
- Fixed title, original SVG logo, footer and page number are PPT layers and never Image2 body content.
- V6 has no V4/V5 runtime fallback, exact overlay, or post-reconstruction visual repair.
- After acceptance, a Codex page worker reconstructs editable text, native simple geometry and independent image objects before the fixed frame and deck assembly. Worker or authentication failure stops the page rather than producing a simplified fallback.
- Reconstruction has one engine and one worker attempt: unreadable text stops the page. There is no cloud OCR retry, substitute generation model, or rejected-first-candidate acceptance path.

## Install

Download the immutable `v1.2.3` Windows release ZIP:

`https://github.com/czd176224-rgb/awesome-editable-ppt-workflow/releases/download/v1.2.3/awesome-editable-ppt-workflow-1.2.3-windows.zip`

Download the adjacent `SHA256SUMS.txt`, verify locally with `Get-FileHash`, extract the ZIP and run `install.ps1`. Restart Codex after installation or upgrade.

## Word page controls

Keep the user-authored pagination. To make a special page explicit, add one standalone paragraph or Word comment inside that page:

- `PPT页型：封面`
- `PPT页型：目录`
- `PPT页型：章节`
- `PPT页型：正文`
- `PPT页型：尾页`
- `PPT页型：附录`

The control is removed from visible slide content. `v6 init` proposes missing cover, TOC, section and closing pages by default and reuses clearly identified special pages already in Word. A dense opening body page cannot replace a cover. All substantive titles come from Word: chapter suggestions prefer PART, 第N章 or 一、 headings; without chapter headings, the first body-page title is proposed as a single section for confirmation.

Step 3 shows the ordered structure. Only proposed additions with a `structure:` ID can be omitted; source pages cannot be deleted or reordered. `--preserve-source-layout` opts out of new structure suggestions while still allowing structure confirmation. Existing projects without confirmed structure cannot report `release_ready`.

Repository development and release instructions are in [docs/RELEASE.md](docs/RELEASE.md).
Structural and privacy-safe visual regression instructions are in [docs/CONSULTING_DIRECTOR_VISUAL_QA.md](docs/CONSULTING_DIRECTOR_VISUAL_QA.md).

## Source

Repository: <https://github.com/czd176224-rgb/awesome-editable-ppt-workflow>

License and notices are included in `LICENSE` and `NOTICE`.

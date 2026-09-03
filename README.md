# Awesome Editable PPT Workflow 1.2.3

Public Codex plugin for converting a paginated Word document plus an SVG Logo into an object-level editable 16:9 PowerPoint.

## V6 adaptive production contract

The workflow contract is `awesome-word-ppt-workflow-v1`; the prompt contract is `consulting-page-director-v3-compact-page-plan`.

- One Word page becomes one slide. The body is 1904x896 (17:8).
- Non-contiguous Word page labels such as `第4页`、`第33页`、`第26页` remain in the user's order.
- The first visible Word paragraph is the slide-title authority; later headings stay in the body.
- Cover, TOC, section, closing and appendix pages use native editable layouts and preserve every frozen source block.
- One three-step UI confirms a whole-deck director preset, the visual contract and a seven-field presentation taskbook. Word remains the factual and page-content authority.
- The confirmed background color becomes the native whole-slide background on every final PPT page, while positioned fills may still structure the page body.
- The confirmed emphasis content is conservatively matched to Word pages in the background. Only those pages may use the secondary-color family for text; non-emphasis pages retain hierarchy through weight, size, position, shapes and fills without secondary-family text.
- One consulting page director turns each body into a coherent business argument with explanatory copy, an analytical backbone, evidence-to-interpretation-to-conclusion flow, and an explicit takeaway.
- The director makes explicit source relationships visible through color, position, shape, connectors and hierarchy, including process, level, parallelism, ownership, comparison and causality.
- Complete source-backed numeric dimensions can become editable native charts or editable special-chart shapes; incomplete relationships use named non-scaled substitutes and never invent quantitative geometry.
- The sealed Image2 prompt has exactly six consulting-report sections. The compiler owns the canvas, fixed-layer exclusions, semantic color roles, accent limits, and formal-report prohibitions.
- The single final UI submission is the sole material/reference authority. Every staged reference requires explicit keep/remove; the backend cannot reinterpret it afterward.
- Zero confirmed references uses Image2 `generate`; 1-16 confirmed refs uses `edit`, preserving their ordered role descriptions.
- Reference fusion is high-fidelity best effort, never a pixel-perfect guarantee.
- The final adapted candidate receives a single independent consulting visual review. It rejects disconnected module grids, semantic-color misuse, and AI-heavy spectacle unsuitable for formal reporting. Only an explicit acceptance may enter reconstruction; a rejected page may use at most two corrections.
- The provider trace records the requested size, service-original dimensions and quality. The service image uses a dynamic centered 17:8 crop and uniform resize to 1904x896 without stretching.
- Fixed title, original SVG logo, footer and page number are PPT layers and never Image2 body content.
- V6 has no V4/V5 runtime fallback, exact overlay, or post-reconstruction visual repair.
- After acceptance, a Codex page worker reconstructs editable text, native simple geometry and independent image objects before the fixed frame and deck assembly. Worker or authentication failure stops the page rather than producing a simplified fallback.

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

The control is removed from visible slide content. If no control is present, the workflow uses only conservative source-text recognition; it does not invent a special page or move Word material.

Repository development and release instructions are in [docs/RELEASE.md](docs/RELEASE.md).
Structural and privacy-safe visual regression instructions are in [docs/CONSULTING_DIRECTOR_VISUAL_QA.md](docs/CONSULTING_DIRECTOR_VISUAL_QA.md).

## Source

Repository: <https://github.com/czd176224-rgb/awesome-editable-ppt-workflow>

License and notices are included in `LICENSE` and `NOTICE`.

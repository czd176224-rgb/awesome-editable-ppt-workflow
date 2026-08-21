# Awesome Editable PPT Workflow 1.1.0

Public Codex plugin for converting a paginated Word document plus an SVG Logo into an object-level editable 16:9 PowerPoint.

## V6 adaptive production contract

The workflow contract is `awesome-word-ppt-workflow-v1`; the prompt contract is `consulting-page-director-v2-six-part-image2`.

- One Word page becomes one slide. The body is 1904x896 (17:8).
- One consulting page director turns each body into a coherent business argument with explanatory copy, an analytical backbone, evidence-to-interpretation-to-conclusion flow, and an explicit takeaway.
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

Download the immutable `v1.1.0` Windows release ZIP:

`https://github.com/czd176224-rgb/awesome-editable-ppt-workflow/releases/download/v1.1.0/awesome-editable-ppt-workflow-1.1.0-windows.zip`

Download the adjacent `SHA256SUMS.txt`, verify locally with `Get-FileHash`, extract the ZIP and run `install.ps1`. Restart Codex after installation or upgrade.

Repository development and release instructions are in [docs/RELEASE.md](docs/RELEASE.md).
Structural and privacy-safe visual regression instructions are in [docs/CONSULTING_DIRECTOR_VISUAL_QA.md](docs/CONSULTING_DIRECTOR_VISUAL_QA.md).

## Source

Repository: <https://github.com/czd176224-rgb/awesome-editable-ppt-workflow>

License and notices are included in `LICENSE` and `NOTICE`.

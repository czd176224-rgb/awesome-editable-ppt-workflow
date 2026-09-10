# Awesome Editable PPT Workflow — new director development

Public Codex plugin for converting a paginated Word document plus an SVG Logo into an object-level editable 16:9 PowerPoint.

## Current development contract

This branch is `development-not-release-ready`. The immutable 1.2.3 release remains separately available. New-director sample pages, whole-deck recovery and installation/release acceptance remain required; see [the milestone scope](docs/NEW_PAGE_DIRECTOR.md).

The workflow contract is `awesome-word-ppt-workflow-v1`; the prompt contract is `awesome-page-design-v1`.

- Read the complete manuscript and derive chapters and page titles before the single final confirmation. Keep original titles, facts, explanations and conditions as source material.
- One public workflow entry coordinates the three-step confirmation UI, page materials, the new director, Image2, independent review and editable reconstruction.
- The new director produces one `page_plan.image_prompt`, handed to Image2 verbatim. Source inventories prove coverage; there is no six-section compiler or alternate old director.
- Initial generation and replanning use the same new director. Old director authorities cannot be adopted as its output. Local edits and replanning share the initial-plus-two-corrections limit.
- Confirmed materials retain their identity and order. Zero selected references uses `generate`; 1–16 uses `edit`. Reference fusion is best effort, not a pixel-perfect guarantee.
- The independent reviewer checks facts, relationships, confirmed emphasis, quantitative truth and severe usability. A candidate must be accepted before reconstruction; aesthetics alone do not justify rejection.
- The accepted 1904×896 (17:8) body is reconstructed into editable text, shapes and image objects. Its body colors remain intact. Confirmed title, SVG Logo, footer, page number and background are applied as fixed layers.
- Incomplete numeric sources cannot produce invented quantitative geometry. Unreadable text, worker or authentication failure stops the page; no OCR/model/local reconstruction fallback can turn failure into success.

## Install the existing stable version

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

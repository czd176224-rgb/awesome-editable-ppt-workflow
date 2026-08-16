---
name: run-word-to-ppt-workflow
description: Use when converting one paginated Word document and one SVG Logo into a resumable, object-level editable 16:9 PowerPoint with confirmed per-page materials.
---

# Run Word-to-PPT Workflow V6

This is the only public Word-to-PowerPoint workflow entry and executes the sealed `awesome-word-ppt-workflow-v1` contract. Create a new V6 project from the original paginated Word and SVG Logo. The resumable project authority is `workflow_v6.json`.

## Authoritative flow

1. `v6 init` locks the original Word and SVG Logo in a new project. Preserve complete Word blocks, original comments, Word images, tables, charts and attachments.
2. Open the three-step Confirm UI once. The confirmed UI contract directly controls background, colors, fonts and the body region.
3. Run `prepare-page-materials` for the project pages. Before the first page receipt is published, the existing conditional project-level material pass searches once only when comments explicitly require missing real logos, people, products, projects or factual screenshots. Unverified results remain `not_found`; never generate fake identity assets.
4. Use `run-pages` for all formal page generation, including a one-page run. It opens each live page workspace and invokes exactly one initial Codex page director for that page. The director reads complete page materials and the current visual-direction reference, selects page-owned references, and emits the existing structured result.
5. The candidate loop sends one initial Image2 request, performs the local file/format/1904x896 check, then invokes one independent visual review for that candidate. Zero compiler-selected page references selects `generate`; one to sixteen compiler-selected page references selects `edit`. The first usable candidate is accepted. A rejected candidate may use at most two existing correction opportunities; same-page correction may use the immediately previous candidate, while a new initial attempt never uses a baseline, prior round or other page candidate.
6. Image2 output is dynamically center-cropped to the largest 17:8 region from its actual returned dimensions, then uniformly resized to 1904x896. The independent reviewer sees this final adapted candidate and is the only image-semantic QA.
7. After acceptance, `run-pages` automatically invokes `reconstruct-editable-slide` through a Codex page worker. The accepted 1904x896 image is its only visual authority. Text and simple shapes become editable objects; fixed title/SVG Logo/footer/page number are added as native layers; the completed pages are then assembled. If the page worker cannot start or complete, the page stops rather than falling back to local reconstruction.

The body image excludes the fixed title, SVG Logo, footer and page number. Do not add another director, semantic QA, scoring layer or user candidate-selection step.

## Production commands

```powershell
python scripts\word_to_editable_ppt.py v6 init --word D:\Input\source.docx --logo D:\Input\logo.svg --project D:\Projects\Deck
python scripts\word_to_editable_ppt.py confirm-ui start --project D:\Projects\Deck
python scripts\word_to_editable_ppt.py confirm-ui wait --project D:\Projects\Deck --stage final
python scripts\word_to_editable_ppt.py v6 prepare-page-materials --project D:\Projects\Deck --page 1 --out D:\Projects\Deck\02_v6\awesome_page_materials\page_001.json
python scripts\word_to_editable_ppt.py v6 run-pages --project D:\Projects\Deck --pages 1 2 3
```

Use `doctor` for authentication, CLI, DNS fake-IP, font and optional Office diagnostics. Never print tokens or package user inputs or outputs with the plugin.

# Editable PPT Workflow

`run-word-to-ppt-workflow` is the plugin's single public Word-to-PowerPoint entry. It creates a resumable V6 project from one paginated Word document, one SVG Logo and optional attachments, then sends formal multi-page generation through `run-pages`.

The sealed workflow contract is `awesome-word-ppt-workflow-v1`.

Each page uses one initial Codex page director, one initial Image2 candidate and one independent visual-review chain. Only an explicit rejection can consume either of the two correction opportunities. Image2 results are center-cropped from their actual returned dimensions to the largest 17:8 region and uniformly resized to 1904x896 before review.

The Word body remains the page fact and narrative authority; comments guide presentation without rewriting facts. The confirmed UI contract directly controls the canvas background, colors, fonts and body region. When a comment explicitly requires a missing real identity image, a single conditional project-level search pass accepts only verifiable official sources and otherwise records `not_found` without generating a fake asset.

After acceptance, the accepted 1904x896 body image is the only visual authority for editable reconstruction. The fixed title, original SVG Logo, footer and page number are added as native PowerPoint layers. Page-image generation, editable reconstruction and final validation remain internal capabilities of this workflow, not alternative Word-to-PowerPoint entry points.

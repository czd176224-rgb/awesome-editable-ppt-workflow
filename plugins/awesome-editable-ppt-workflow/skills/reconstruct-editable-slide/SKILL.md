---
name: reconstruct-editable-slide
description: Internal editable PPT workflow module for reconstructing an accepted page body into verified editable PowerPoint objects; invoke only for a dispatched reconstruction task.
---

# Internal editable reconstruction

The main workflow dispatches this module automatically. Do not redirect the user to a separate skill or task.

- The accepted V6 body image is the only visual authority. Preserve its layout, text, shapes and colors, including accepted body text colors. Do not reinterpret Word or reopen page design.
- Use the existing `editppt` runtime and real page worker. If unavailable, follow the pre-run check in [cli-helper.md](references/cli-helper.md); a missing worker stops the page rather than permitting local or raster fallback.
- Follow [page-worker.md](prompts/page-worker.md) for ownership and outputs, [page-decision-tree.md](references/page-decision-tree.md) for object decisions, and [manifest-schema.md](references/manifest-schema.md) for artifact contracts. Read the applicable references when executing a page, not at main-entry startup.
- Rebuild text and supported geometry as editable objects. An entire source image with overlaid editable text is not valid reconstruction. Unreadable text stops the page with `text_unreadable`; do not substitute OCR or another model.
- Default reconstruction makes zero Image2 calls. Any explicitly authorized targeted reconstruction edit must use the existing sealed capability and accepted image only. Send no unrelated files or credentials.
- Advance state only through runtime commands; preserve active workers, accepted outputs and failure evidence. Record/finalize must validate the manifests and PPTX before claiming delivery.

Read [reconstruction-operations.md](references/reconstruction-operations.md) only for manual diagnosis, detailed dispatch/record/recovery operations or explicit legacy image/PDF/V4 maintenance. Legacy conversion rules do not override V6 acceptance.

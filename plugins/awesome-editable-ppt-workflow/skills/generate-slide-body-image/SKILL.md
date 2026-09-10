---
name: generate-slide-body-image
description: Internal editable PPT workflow provider module; invoke only with a sealed V6 page ImageRequest from the runtime.
---

# Generate Slide Body Image V6

Use this skill only for a page prepared by `run-word-to-ppt-workflow`. Consume its sealed prompt and verified reference list without adding facts or reinterpreting comments.

## Adaptive operation contract

- This provider module is private to the workflow. Never invoke it with a free-form prompt or image. It requires the sealed project/page ImageRequest capability emitted by the runtime gate.
- With zero director-selected page-owned references, the workflow calls `generate` and passes no image inputs.
- With one to sixteen director-selected page-owned references, the workflow calls `edit` with aligned image bytes, roles, digests, and capability identity.
- Initial generation uses only the director-selected references. An approved local correction may edit the immediately previous candidate; replanning uses the new signed director prompt and its source references. These routes share one budget.
- Use `medium` for ordinary pages and `high` for Logo, screenshot, dense-data, small-text or high-detail risk. Produce at most three candidates total: one initial candidate and two correction opportunities.
- Treat Logo, screenshot and real-photo fusion as high-fidelity best effort; do not promise exact reproduction.
- The output is the 1904x896, 17:8 body. Do not draw the fixed page title, SVG Logo, footer or page number.

Return the output and trace to the orchestrator. Only an explicit independent-review acceptance may select a candidate; review failure or exhausted correction opportunities stop the page. Use the existing Codex OAuth provider, never print OAuth tokens, invent an independent prompt, or block on unavailable references.

For CLI parameters, read `references/openai-images-api-parameters.md`.

---
name: validate-ppt-output
description: Internal optional post-delivery PPTX inspection or narrow repair using an already installed officecli; never required for the core Word-to-PPT workflow.
---

# Optional PPTX inspection

Use only after the core workflow produces a PPTX and `officecli --version` succeeds. If absent or failing, report this optional check as unavailable and retain the core validation result. Do not download an installer.

Inspect the existing deck, previews or structural issues requested by the workflow or user. This module does not replace generation, semantic review, editable reconstruction or assembly. Do not load generic Word, Excel, animation or unrelated skills.

Use `officecli --help` and the relevant command help to verify current syntax. Prefer read-only inspection. For an authorized narrow repair, preserve the original and validate the resulting PPTX before returning it; do not modify workflow acceptance evidence to hide a failure.

The archived [officecli-reference.md](references/officecli-reference.md) is available only for a specific command lookup. Its generic tutorials and suggestions to load other skills are not part of this plugin's normal execution.

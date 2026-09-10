---
name: run-word-to-ppt-workflow
description: Convert a paginated Word and SVG Logo into an editable PowerPoint, or continue an existing conversion, through one plugin entry and one final confirmation.
---

# Word to editable PowerPoint

This is the plugin's single user-facing workflow entry. This development copy is not yet a verified formal release.

## Start or continue

- For a new deck, obtain the original paginated Word, SVG Logo and a project folder; retain supplied attachments. Ask only for missing essential inputs.
- For a new project, run `python scripts/word_to_editable_ppt.py v6 run --project <project> --word <word.docx> --logo <logo.svg>` from this skill directory. Quote paths containing spaces. This is an internal agent operation, not a command the user must execute.
- For “continue”, locate the existing project and run `python scripts/word_to_editable_ppt.py v6 run --project <project>`. The runtime reads `workflow_v6.json`, preserves the locked sources and skips completed pages. Never initialize over an existing project or edit its state JSON by hand.
- Open the confirmation URL returned by the runtime. The existing three-step UI gathers template, visual settings, taskbook and ordered structure with one final confirmation. Before that confirmation, full-manuscript and chapter planning derives page titles from page expression; all original page content, including old titles, remains source material. Do not substitute hard-coded titles or ask for another prompt confirmation.
- The same run waits for the final confirmation, prepares missing materials for the whole project, then drives the existing page pipeline and assembly. If the confirmation wait times out, continue with the same command after the user finishes the UI; do not request another final confirmation. Do not ask the user to call internal skills, choose agents, execute commands or open another task.

## Waiting, failures and delivery

- Follow actual runtime state and preserve completed pages. A running worker is not failed merely because it is slow. Keep progress messages concise.
- Ordinary `run` does not reopen sealed failures. An explicit recovery uses `--pages <numbers> --recovery-round <1..999>` on the same entry, following the existing recovery constraints; a sealed failure does not authorize opening unlimited recovery rounds. Diagnose the concrete failure and preserve all prior evidence. Ask the user only for information or a decision that cannot be obtained automatically.
- Report success only when the final PPTX exists and the runtime's delivery checks pass for every required page. A `--pages` subset can make progress, but the command returns nonzero until the whole deck is complete and release-ready. Return its absolute file link and actual validation result. A generated image, partial deck or flattened screenshot is not completed editable delivery.
- Preserve all source facts, explanations, conditions and uncertainty. Never fabricate identity assets, accepted reviews or execution receipts. Never expose credentials or overwrite old project/rollback data.

Read [workflow-contract.md](references/workflow-contract.md) only for pagination, sealed material rules, repair budgets or low-level diagnosis. The runtime explicitly loads the [page-director method](scripts/complex_page_experiment/references/visual_director.md); do not add a director layer or rewrite its final Image2 prompt.

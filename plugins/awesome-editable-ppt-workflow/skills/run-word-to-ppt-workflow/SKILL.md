---
name: run-word-to-ppt-workflow
description: Convert a paginated Word and SVG Logo into an editable PowerPoint, or continue an existing conversion, through one plugin entry and one final confirmation.
---

# Word to editable PowerPoint

This is the plugin's single user-facing workflow entry. This development copy is not yet a verified formal release.

## Start or continue

- For a new deck, obtain the original paginated Word, SVG Logo and a project folder; retain supplied attachments. Ask only for missing essential inputs.
- For a new project, use the existing internal `v6 init` command and start its Confirm UI. The dispatcher is `scripts/word_to_editable_ppt.py` relative to this skill directory. Command examples are in [workflow-contract.md](references/workflow-contract.md); these are agent operations, not user instructions.
- For “continue”, locate the existing project and read its `workflow_v6.json` state, then resume the applicable existing stage. Never initialize over an existing project or edit its state JSON by hand.
- Open the confirmation URL returned by the runtime. The existing three-step UI gathers template, visual settings, taskbook and ordered structure with one final confirmation. Before that confirmation, full-manuscript and chapter planning derives page titles from page expression; all original page content, including old titles, remains source material. Do not substitute hard-coded titles or ask for another prompt confirmation.
- After `confirm-ui wait --stage final` persists the approved confirmation, prepare the project page materials and call `v6 run-pages` for the required pages. It drives direction, Image2, independent review, reconstruction and assembly. The agent coordinates these existing stages within this same public skill; there is currently no unified `v6 run` command. Do not ask the user to call internal skills, choose agents, execute commands or open another task.

## Waiting, failures and delivery

- Follow actual runtime state and preserve completed pages. A running worker is not failed merely because it is slow. Keep progress messages concise.
- Use bounded recovery supported by the runtime; a sealed failure does not authorize opening unlimited recovery rounds. Diagnose the concrete failure and preserve all prior evidence. Ask the user only for information or a decision that cannot be obtained automatically.
- Report success only when the final PPTX exists and the runtime's delivery checks pass for every required page. Return its absolute file link and actual validation result. A generated image, partial deck or flattened screenshot is not completed editable delivery.
- Preserve all source facts, explanations, conditions and uncertainty. Never fabricate identity assets, accepted reviews or execution receipts. Never expose credentials or overwrite old project/rollback data.

Read [workflow-contract.md](references/workflow-contract.md) only for pagination, sealed material rules, repair budgets or low-level diagnosis. The runtime explicitly loads the [page-director method](scripts/complex_page_experiment/references/visual_director.md); do not add a director layer or rewrite its final Image2 prompt.

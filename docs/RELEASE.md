# 1.2.3 public release runbook

Release identity is fixed by `package-info.json`: version `1.2.3`, tag `v1.2.3`, workflow `awesome-word-ppt-workflow-v1`, prompt contract `consulting-page-director-v2-source-text-custody`, and policy `generate-without-refs-edit-with-confirmed-refs`. The immutable `v1.2.2` release remains the supported rollback baseline.

The single final UI submission remains the sole material/reference authority. Word remains the page-content and factual authority. The confirmed background color becomes the native whole-slide background on every final page. The workflow conservatively matches the taskbook's confirmed emphasis content to Word pages; only matched emphasis pages may use secondary-color-family text. Non-emphasis pages may use related colors in shapes, lines and text-box fills, while text emphasis uses weight, size, position or another non-accent treatment. The page director exposes source-backed process, hierarchy, parallelism, ownership, comparison and causality through color, position, shape, connectors and visual hierarchy.

The final adapted candidate receives one independent visual review; only explicit acceptance may enter reconstruction, and a rejected page may use at most two corrections. Service-original dimensions and quality remain truthful in the trace. After acceptance, the Codex page worker performs object-level reconstruction before fixed layers and final assembly. There is no V4/V5 runtime fallback, forced candidate acceptance, exact overlay, or post-reconstruction visual repair. Authentication failure is an external credential failure, not a successful reconstruction.

1. Run `scripts/release_gate.ps1` locally, including portable smoke where the environment permits.
2. Refresh and verify the public source manifest and release audit through the existing export gate.
3. Merge the reviewed release commit into `main` with green bounded Windows CI.
4. Create annotated tag `v1.2.3` on the exact reviewed merge commit and push it.
5. The release workflow repeats the gate, builds the deterministic Windows ZIP and publishes the GitHub Release.
6. Download the published ZIP and `SHA256SUMS.txt`, verify the digest, perform a clean installation, restart Codex and confirm the installed plugin reports `1.2.3`.

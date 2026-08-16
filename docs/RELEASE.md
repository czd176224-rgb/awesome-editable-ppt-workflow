# 1.0.0 public release runbook

Release identity is fixed by `package-info.json`: version `1.0.0`, tag `v1.0.0`, workflow `awesome-word-ppt-workflow-v1`, prompt contract `page-prompt-v6-adaptive-confirmed-materials`, and policy `generate-without-refs-edit-with-confirmed-refs`. Never overwrite the tag or reuse the version.

The single final UI submission is the sole material/reference authority; every staged reference requires explicit keep/remove. Zero references uses generate and 1–16 confirmed refs uses edit. Fidelity is high-fidelity best effort, never pixel-perfect. The final adapted candidate receives a single independent visual review; only explicit acceptance may enter reconstruction, and a rejected page may use at most two corrections. Service-original dimensions and quality remain truthful in the trace; a dynamic centered 17:8 crop is uniformly resized to 1904x896 without stretching. After acceptance, the Codex page worker performs object-level reconstruction before the fixed title/original SVG logo/footer/page number and final assembly. There is no V4/V5 runtime fallback, forced candidate acceptance, exact overlay, or post-reconstruction visual repair. `401 token_expired` or any worker authentication failure is an external credential failure, not a successful reconstruction.

1. Merge all milestone PRs into `main` with green bounded Windows CI.
2. Run `scripts/release_gate.ps1` locally, including portable smoke where the environment permits.
3. Refresh and verify the public source manifest and release audit.
4. Create annotated tag `v1.0.0` on the exact reviewed merge commit and push it.
5. The release workflow repeats the gate, builds the deterministic Windows ZIP and publishes the GitHub Release.
6. Install `awesome-editable-ppt-workflow@editable-ppt-public`, restart Codex and verify the installed plugin reports `1.0.0`.

# Task 16 report — compact v3 execution-contract cleanup

## Result

- The confirmed taskbook and actual director request now map only to `page_purpose`, `primary_relationship`, `core_exhibit`, `support_groups`, `reading_path`, and `local_visuals`.
- The eight deleted v2 director fields are absent from the active runtime/public prompt surfaces, with a full-prompt negative regression covering every name.
- Conclusions are optional and source-conditional. The public contract now states exactly five hard review errors, at most two deterministic edits of the immediately previous image, and no correction-model fallback.
- Public metadata truthfully marks this branch `development-not-release-ready`; historical A/B artifacts are not used as readiness proof.
- Every Huangshi sealed manifest derives from its page's signed plan. The acceptance fixture also applies the existing relationship-arrowhead reconstruction helper before final readback.
- The release gate runs Huangshi acceptance after the existing authority suites, not in the initial generic test batch.
- `public-source-manifest.json` and `public-release-audit.json` were regenerated from clean committed state with the existing export script.

## Commits

- `9c39faf` — align compact-v3 runtime, public contracts, Huangshi fixture, and release gate.
- `7bcf35c` — regenerate v1.2.3 public controls from clean committed state.

## Verification

- Clean committed director/compiler suite: `143 passed in 28.81s`.
- Clean committed public contract/metadata suite: `15 passed in 23.26s`.
- Clean committed authority, signature, reconstruction, Huangshi, and release-hardening aggregate before control refresh: `60 passed, 1 skipped`; its sole failure was the expected stale committed public-manifest hash.
- Huangshi acceptance independently passed during fixture repair: `1 passed, 1 skipped in 121.23s`.
- Clean committed final release-hardening after control refresh: `24 passed in 262.45s`.
- `verify.ps1 -MetadataOnly`: `verify-metadata-preflight=ok`.
- Removed-v2 active-surface scan: clean.
- `git diff --check`: pass.

## Scope and preservation

- No dependency, generator, abstraction, schema, template library, correction model, or additional model call was added.
- Image2 was not rerun and historical Task 12 evidence was not changed.
- Overlapping pre-existing dirty documentation and test hunks were preserved and excluded from both implementation commits.

## Remaining release blockers

- The preserved Task 12 manual audit still rejects both historical v1.2.3 batches for source-fidelity defects.
- Historical candidates predate the final host-enforced authority contract and cannot establish current release readiness.
- The release status therefore remains **NOT RELEASE-READY** until a future authorized fresh generation satisfies the current sealed authority and visual acceptance gates.

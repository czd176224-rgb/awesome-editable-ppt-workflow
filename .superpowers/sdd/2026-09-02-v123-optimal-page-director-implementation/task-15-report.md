# Task 15 report — release controls and evidence authority

## Result

- Task 12 evidence now compares each sealed relationship node and edge against the final editable PPTX and manifest.
- Edge evidence records object type, exact endpoints, source/target containment, direction, and arrowhead readback.
- Numeric authority is compared against the final manifest and PPTX readback when present; the selected historical pages contain no sealed numeric authority.
- Raw connector/chart/text counts remain available only as object inventory and are explicitly not labelled authority validation.
- Existing Task 12 manual release conclusion remains `fail`; provenance remains `checkout_state_verified=true`, `run_binding_verified=false`.
- Public source manifest and release audit were regenerated from a clean worktree with the existing generator. Both identify `consulting-page-director-v3-compact-page-plan`, include the v3 schema, and omit the deleted v2 schema/path.

## Commits

- `ea68b0b` — Task 12 sealed editable authority evidence.
- `c43169c` — initial v3 release-control refresh.
- `f0fd75e` — installed-runtime import fix found by release hardening.
- `08d573c` — release controls rebound to the install fix.
- `de61a47` — fail-closed canonical validator import for source and installed runtimes.
- `77926d6` — release controls regenerated from the import-hardening commit.

## Review remediation

- Removed the broad `ImportError` fallback from `workflow_v6_reconstruction.py`; internal dependency failures can no longer be mistaken for a missing top-level package.
- A source checkout now adds the existing local `reconstruct-editable-slide/cli` directory to `sys.path`, then source and installed execution both use the single canonical `editppt.runtime.validate_pptx` import.
- The regression test statically enforces the canonical import and rejects restoration of either `except ImportError` or the legacy top-level `validate_pptx` fallback.

## Verification

- Task 12 collector consistency: pass. Candidate A has 5 authority-applicable pages and 0 compliant pages; candidate B has 6 applicable pages and 0 compliant pages. Manual fact-coverage counts remain baseline 6/8, A 4/8, B 5/8.
- `python -m py_compile .../task-12-authority-evidence.py`: pass.
- Relationship/numeric authority and installed import-boundary regression tests: `18 passed in 88.10s`.
- Committed manifest hash test after the final control refresh: `1 passed in 22.33s`.
- Full release hardening at committed `77926d6` in a clean detached worktree: `23 passed in 285.68s`.
- `git diff --check`: pass.

## Remaining release blockers

- The preserved Task 12 candidates predate host-enforced object IDs and real connector authority: none of the authority-applicable selected pages satisfy the final sealed-authority contract.
- The preserved Task 12 manual visual audit still rejects both v1.2.3 batches for source-fidelity defects, so the truthful release verdict remains **NOT RELEASE-READY**.
- Historical execution binding remains explicitly unverified because no genuine pre-run receipt exists. No Image2 run was repeated in Task 15.

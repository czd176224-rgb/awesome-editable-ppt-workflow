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

## Verification

- Task 12 collector consistency: pass. Candidate A has 5 authority-applicable pages and 0 compliant pages; candidate B has 6 applicable pages and 0 compliant pages. Manual fact-coverage counts remain baseline 6/8, A 4/8, B 5/8.
- `python -m py_compile .../task-12-authority-evidence.py`: pass.
- Relationship/numeric authority regression tests: `17 passed in 92.67s`.
- Committed manifest hash test: `1 passed in 22.13s`.
- Root release hardening in a clean committed worktree: `23 passed in 280.63s`.
- `git diff --check`: pass.

## Remaining release blockers

- The preserved Task 12 candidates predate host-enforced object IDs and real connector authority: none of the authority-applicable selected pages satisfy the final sealed-authority contract.
- The preserved Task 12 manual visual audit still rejects both v1.2.3 batches for source-fidelity defects, so the truthful release verdict remains **NOT RELEASE-READY**.
- Historical execution binding remains explicitly unverified because no genuine pre-run receipt exists. No Image2 run was repeated in Task 15.

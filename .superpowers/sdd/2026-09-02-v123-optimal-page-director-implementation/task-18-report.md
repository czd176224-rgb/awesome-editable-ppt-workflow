# Task 18 report — atomic capability signing across rotation

## Result

- Both image-request and reconstruction capability issuers now call `signing_key()` once per capability and reuse the returned `(key_id, key)` pair for metadata and HMAC signing.
- Removed the obsolete `_capability_secret()` second-read path.
- Added one parameterized rotation regression covering both issuers. It rotates the real keyring immediately after the first read, asserts one signing-key read, and verifies the issued HMAC with the capability's recorded key ID.
- Regenerated public controls from clean committed code with the existing Windows PowerShell exporter. No Image2 or Task 12 evidence was changed.

## Commits

- `6c6b225` — issue capabilities with one signing key pair and add the rotation regression.
- `1b3cc16`, `c03f803`, `d40f955` — regenerate controls, then restore the repository's existing Windows PowerShell JSON formatting; the combined control delta is 12 lines.

## Verification

- Focused rotation regression: `2 passed, 33 deselected in 10.59s`.
- Focused signing/isolation selection: `12 passed` (exit 0).
- Final clean-HEAD suite covering capability issuance/verification, secure keyring rotation, packaged reconstruction isolation, public distribution, release controls, and committed hashes: `83 passed in 350.04s`.
- Clean-HEAD `git diff --check`: pass.
- Clean verification worktree status: no changes.

## Scope

- No abstraction, dependency, lock, fallback, schema, generator, Image2/model rerun, or Task 12 evidence change.
- Unrelated dirty/user edits in the main worktree were not staged or modified by Task 18.

# Task 17 report — packaged signing and native-direct chart integrity

## Result

- Moved the single `provider_keyring.py` implementation to the packaged Word-workflow scripts boundary. The two direct image-provider entry points add that existing shared directory before importing the same module; there is no fallback, duplicate key logic, or new dependency.
- Manifestless native-direct pages remain supported when chart-free. If such a finalized page contains a native chart, assembly now fails before slide copying or final output instead of silently removing the chart relationship.
- Added a genuine two-page regression with a native chart on page 2. It proves assembly rejects the undeclared chart, creates no final deck, and leaves the source page's editable chart intact.
- Updated the active workflow README and legacy-restart error to compact v3 wording and the exact five hard-error categories.
- Regenerated both public controls from clean committed state with the existing exporter. The manifest contains the new shared keyring path and omits the retired location.

## Commits

- `12c6d55` — close packaged-runtime import, native-direct chart-loss, wording, and regression gaps.
- `962564b` — refresh packaged integrity controls from committed source.

## Verification

- RED: packaged isolation import failed with `ModuleNotFoundError: provider_keyring`; the new page-2 chart test silently assembled; v3 wording/README tests failed as expected.
- Focused GREEN: `5 passed in 11.92s` for isolated import, chart-free native-direct, page-2 chart fail-closed, v3 restart wording, and README contract.
- Clean committed authority suite: `159 passed, 6 skipped in 693.37s`; two pre-existing Pillow deprecation warnings only.
- Clean public/release-hardening suite: `27 passed in 314.39s`.
- `verify.ps1 -MetadataOnly`: `verify-metadata-preflight=ok`.
- Clean `git diff --check`: pass; clean test worktree status: no changes.
- A broader run in the user's dirty worktree produced `244 passed, 6 skipped, 2 failed`; both failures came only from the pre-existing unstaged `test_director.py` expectation for `No color duty: neutrals suffice`, outside Task 17 and absent from the committed tree.

## Scope

- No Image2/model rerun, evidence mutation, dependency, schema, generator, template library, fallback, or chart-manifest fabrication.
- Historical Task 12 evidence remains unchanged and the existing NOT RELEASE-READY verdict remains unchanged.

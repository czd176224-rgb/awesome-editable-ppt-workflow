# New-director release runbook

This development branch is not release-ready. The existing immutable `v1.2.3` archive remains the old-version rollback package; never overwrite its tag or assets. Select a new version in the final release milestone and update all package, plugin and marketplace identities together.

The new prompt contract is `awesome-page-design-v1`. Only the new full-context page director runs. Complete source facts, a single confirmation, independent review, bounded corrections, signed recovery and editable accepted-image reconstruction are release requirements.

1. Complete real sample-page, whole-deck and supported recovery acceptance. Keep `releaseStatus` at `development-not-release-ready` until acceptance and `scripts/release_gate.ps1` pass, including the portable installation check.
2. Verify the old package and its separate runtime remain usable. Record installation identity, checksums and actual validation evidence.
3. Set `release-ready` only after the final milestone is satisfied. Refresh the public manifest and audit using the export gate; exporting alone does not approve a release.
4. Merge the reviewed release commit with green Windows CI, then create the new annotated tag from `package-info.json` on that exact commit. Do not reuse an existing tag.
5. Let the release workflow build the deterministic Windows ZIP. Download the ZIP and SHA256SUMS, verify the digest, install into the intended environment and verify the installed version and representative use.

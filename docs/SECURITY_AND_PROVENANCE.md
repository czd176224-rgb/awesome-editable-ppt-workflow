# Security and provenance

The private repository is the development authority. Public publication is a
sanitized, no-private-history snapshot derived from one reviewed commit.
`git ls-files` is the export authority; untracked, ignored, cached, generated
user, credential, and private-development files cannot enter the snapshot.

`public-source-manifest.json` records the immutable release identity, a
tracked-index content digest, and SHA-256 of exported files; it deliberately
does not expose a private commit, branch, remote, or local path. The public
release workflow may separately attest its own public merge commit.
`ARCHIVE-MANIFEST.json` records every production ZIP member. The ZIP is
built in sorted order with normalized timestamps and is accompanied by
`SHA256SUMS.txt`.

Runtime network operations are limited to the documented Image2, signed QA,
and signed reconstruction services. Credentials are read from Codex auth or
process environment and are not persisted in projects or packages. Installer
scripts do not download and execute mutable remote scripts. OfficeCLI is not
bundled and is optional. PowerPoint or LibreOffice remains the external
render/open backend for high-quality completion.

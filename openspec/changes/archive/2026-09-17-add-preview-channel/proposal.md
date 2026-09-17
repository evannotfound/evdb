## Why

Operators need an explicit way to try the latest successful main build before a stable release.
A rolling preview should retain the standalone installer guarantees and expose its source identity.

## What Changes

- Build native amd64 and arm64 standalone executables on main pushes and publish checksums and
  attestations to one rolling `preview` GitHub prerelease.
- Add installer `--preview` selection with a consistent snapshot of the published commit and version.
- Preserve stable default installation, positional exact versions, and tagged release publication.
- Restrict SCM tag discovery to `v[0-9]*`, retain SCM development versions, and document preview use.

## Capabilities

### New Capabilities

### Modified Capabilities

- `release-distribution`: Add preview publication and installation alongside stable releases, with
  SCM-derived development identity and safe concurrent publication and download behavior.

## Impact

GitHub workflows, `install.sh`, `tools/check_release.py`, setuptools-scm configuration, focused
release/installer tests, and the README installation section. No additional production runtime tools.

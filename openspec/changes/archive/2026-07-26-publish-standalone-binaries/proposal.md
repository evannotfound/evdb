## Why

Production installation currently exposes Python packaging and `uv` internals to operators, turning
a straightforward CLI install into a long, fragile bootstrap. Public self-hosters should receive a
normal release experience: native architecture-specific downloads, a short installer, and concise
project documentation, while maintainers continue to use `uv` for development.

## What Changes

- Publish checksummed standalone Linux binaries for ARM64 and x86_64 from semantic-version GitHub
  tags, with build provenance and the canonical systemd assets in each release archive.
- Add a one-line installer that selects the host architecture, resolves the latest release by
  default or accepts an exact version, verifies it, and installs the existing versioned tool layout.
- Keep `evdb host update VERSION`, but make it download and verify an exact GitHub Release candidate
  before using the existing compatibility, atomic activation, and rollback workflow.
- Add `evdb --version` and use one embedded application version consistently in source packages,
  release tags, archives, status, setup, and updates.
- **BREAKING**: Remove Python and `uv` as production host prerequisites and remove Python package
  registry installation from host setup and updates. `uv` remains the development environment and
  lock-file tool.
- Rewrite the README for public self-hosters around the product, features, short installation,
  representative usage, focused documentation links, development, and licensing. Move operational
  detail to the existing topic guides.
- License the public project under MIT.

## Capabilities

### New Capabilities

- `release-distribution`: Defines tagged GitHub binary releases, supported Linux architectures,
  checksums and provenance, version identity, and safe initial installation.

### Modified Capabilities

- `host-setup`: Replaces Python package and `uv` acquisition with verified standalone release
  candidates while preserving versioned installation, compatibility checks, atomic activation,
  rollback, and the boundary against database deployment.

## Impact

- Adds a release workflow, installer script, PyInstaller development dependency, binary entry point,
  release archive layout, checksums, attestations, and an MIT license.
- Changes `host.py`, runtime version lookup, CLI version output, host prerequisites, update download
  and extraction, packaged unit discovery, and related unit/configuration tests.
- Changes the host setup specification, setup documentation, README, project metadata, and
  documentation assertions.
- GitHub Releases must be public before anonymous installation is advertised. Builds target Ubuntu
  22.04 or newer on Linux ARM64 and x86_64; production data and `config/production-host` remain outside
  this change.

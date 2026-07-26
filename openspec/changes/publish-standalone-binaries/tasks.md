## 1. Version and Binary Foundation

- [x] 1.1 Make `evanovation_db.__version__` the single application version source and derive Python
  package metadata from it
- [x] 1.2 Add a package `__main__` entry point and global `evdb --version` output
- [x] 1.3 Add PyInstaller to the `uv` development dependencies and lock it without making it a runtime
  dependency
- [x] 1.4 Add a reproducible one-file PyInstaller build command for the evdb entry point
- [x] 1.5 Add tests that keep source metadata, CLI output, and release-compatible semantic versions in
  agreement

## 2. Release Layout and Host Runtime

- [x] 2.1 Define the release layout as `bin/evdb` plus the complete canonical `units/` directory
- [x] 2.2 Make frozen production commands read adjacent release units while development commands use
  package resources
- [x] 2.3 Replace package metadata version lookup in setup and status with the embedded application
  version
- [x] 2.4 Remove Python and `uv` from host prerequisites and delete the `uv` package bootstrap path
- [x] 2.5 Update version-directory validation for standalone release files without weakening path,
  executable, or symlink checks

## 3. Verified Release Acquisition

- [x] 3.1 Map Linux `aarch64`/`arm64` and `x86_64`/`amd64` to stable GitHub Release asset names and
  reject other platforms
- [x] 3.2 Download an exact release archive and checksum through the bundled Python standard library
  without shell commands or credentials
- [x] 3.3 Verify the SHA-256 record before opening an archive and reject malformed or mismatched checksum
  files
- [x] 3.4 Validate archive members as the exact regular-file release contract, rejecting traversal,
  absolute paths, links, duplicates, missing units, and unexpected members
- [x] 3.5 Write validated members with explicit modes into private staging, verify the candidate's
  reported version, and atomically establish the version directory
- [x] 3.6 Clean downloads, staging, and incomplete candidates after ordinary failures and process
  interruption

## 4. Exact Host Updates

- [x] 4.1 Replace `uv tool install` in `host update VERSION` with exact architecture-specific GitHub
  Release acquisition
- [x] 4.2 Read candidate units from the release layout and preserve existing systemd verification and
  unit comparison
- [x] 4.3 Preserve pre-activation compatibility checks, confirmation preview, active and previous links,
  machine state, timer state, post-activation health checks, and rollback
- [x] 4.4 Confirm host updates never resolve latest, invoke Python tooling, regenerate database Compose,
  restart databases, or modify database data

## 5. Public Installer

- [x] 5.1 Add a portable `install.sh` release asset that requires Linux, detects ARM64 or x86_64, and
  selects latest by default or an exact supplied semantic version
- [x] 5.2 Make the installer download the matching archive and checksum, verify both, reject an
  unexpected layout or binary version, and clean its temporary directory on exit
- [x] 5.3 Stage the release under `/opt/evdb/versions/<version>` and establish `current` and
  `/usr/local/bin/evdb` only after validation
- [x] 5.4 Refuse an existing managed installation without changing it, and print the appropriate
  `evdb host update` or `sudo evdb host setup` next step
- [x] 5.5 Add installer syntax and disposable latest, pinned, unsupported-platform, checksum-failure,
  version-mismatch, and existing-installation tests

## 6. GitHub Release Automation

- [x] 6.1 Add a semantic-version tag release workflow with the minimum source, release, OIDC, and
  attestation permissions
- [x] 6.2 Run repository checks once and reject a tag that differs from `evdb --version` before builds
- [x] 6.3 Build and smoke-test native one-file executables on Ubuntu 22.04 ARM64 and x86_64 runners
- [x] 6.4 Assemble and inspect stable-name architecture archives, generate SHA-256 files, and include
  `install.sh`
- [x] 6.5 Generate GitHub artifact attestations and publish all verified assets to the matching GitHub
  Release only after both architecture builds succeed
- [x] 6.6 Add configuration tests for release triggers, runner matrix, archive names, checksums,
  attestations, and release permissions

## 7. Public Documentation and Licensing

- [x] 7.1 Add the MIT license with the selected copyright
- [x] 7.2 Rewrite the README with a concise product identity, status badges, features, two-command
  installation, representative usage, requirements, documentation links, development, and license
- [x] 7.3 Remove exhaustive configuration, command, implementation, recovery, update, and internal
  migration detail from the README while retaining it in focused topic documentation
- [x] 7.4 Rewrite `docs/setup.md` for latest and pinned binary installation, supported architectures,
  release verification, version layout, exact updates, and rollback
- [x] 7.5 Generalize public package metadata and update documentation tests to enforce the public entry
  point without forcing operator internals into the README
- [x] 7.6 Document that anonymous installation begins only after the repository and GitHub Releases are
  public

## 8. Safety and Verification

- [x] 8.1 Adapt host setup and update tests from mocked `uv` installs to mocked release candidates while
  preserving every existing compatibility, interruption, activation, timer, and rollback assertion
- [x] 8.2 Add focused tests for architecture selection, URL pinning, checksum parsing, unsafe archives,
  incomplete units, executable modes, version mismatches, and cleanup
- [x] 8.3 Run Ruff, formatting, unit, config, documentation, Restic, and integration collection checks
  through `uv`
- [x] 8.4 Build, archive, checksum, unpack, and smoke-test the local architecture release artifact without a
  host Python path
- [x] 8.5 Build the wheel and source archive to confirm the development package remains valid
- [x] 8.6 Validate all OpenSpec artifacts, run `git diff --check`, and prove production configuration
  and migration inputs are unchanged

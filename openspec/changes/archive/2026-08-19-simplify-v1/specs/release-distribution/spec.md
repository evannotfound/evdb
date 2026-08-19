## MODIFIED Requirements

### Requirement: Complete release executable
Each architecture asset SHALL be one executable with exactly the two canonical systemd unit templates
embedded. The executable SHALL run and install those units without host Python or a Python package
manager.

#### Scenario: Release executable is inspected
- **WHEN** the release workflow builds an architecture executable
- **THEN** it verifies the reported version and that both embedded units are available and non-empty

### Requirement: Release integrity and provenance
Every architecture executable SHALL have a matching SHA-256 file and GitHub artifact attestation tied to
the release workflow and source revision. Initial installation and configured-host installer updates
SHALL verify the checksum before executing or installing the candidate.

#### Scenario: Downloaded executable is corrupted
- **WHEN** an executable digest differs from its checksum
- **THEN** installation fails without creating or selecting the candidate release

### Requirement: Consistent version identity
evdb SHALL expose `evdb --version`, and source package metadata, release tags, release executables, and
runtime status SHALL use the same exact semantic version. No machine-state tool-version field SHALL be
required.

#### Scenario: Operator checks the installed version
- **WHEN** the operator runs `evdb --version`
- **THEN** the command prints the exact version represented by the active release

### Requirement: Release-only OpenCode dependency
OpenCode, Node.js, provider configuration, and model credentials SHALL remain release-CI dependencies
only. Standalone executables, the installer, initialization, status, scheduled backup, database, and
backup commands SHALL operate without those dependencies.

#### Scenario: Managed host installs OpenCode-authored release notes
- **WHEN** an operator installs that release
- **THEN** no host-local evdb operation requires OpenCode, Node.js, or model credentials

### Requirement: Public initial installer
Each release SHALL include an anonymously fetchable installer. It SHALL detect supported architecture,
resolve latest by default or accept an exact version, verify checksum and executable version, and
atomically replace `/usr/local/bin/evdb` without
Python or `uv`. A new host SHALL be directed to `sudo evdb init`. A configured host SHALL be updated
and then refreshed automatically with `evdb init --yes`.

#### Scenario: New host installs latest
- **WHEN** a host with no managed installation runs the public installer
- **THEN** the exact downloaded version is selected and the operator is directed to `sudo evdb init`

#### Scenario: Operator pins installation
- **WHEN** the installer receives exact version `1.2.3`
- **THEN** it downloads only that tag and rejects an executable reporting another version

#### Scenario: Configured host updates
- **WHEN** `config.yml` and a managed executable already exist
- **THEN** the installer installs the verified requested executable and runs `evdb init --yes`

#### Scenario: Configured refresh fails
- **WHEN** post-selection initialization returns nonzero
- **THEN** the installer returns nonzero with that error and leaves the verified selected executable installed

### Requirement: Development and production tool boundary
The repository SHALL use `uv` and its lock file for development, checks, and release builds. Installed
commands, initialization, systemd jobs, and installer-driven updates SHALL NOT require or invoke
Python, pip, pipx, `uv`, or a Python package registry.

#### Scenario: Host has no Python development tools
- **WHEN** a standalone release runs on a supported host without Python or `uv`
- **THEN** initialization, status, database, backup, scheduling, and installer updates remain operable

### Requirement: Disposable host source workflow
The repository SHALL retain a guarded workflow that syncs only explicit source, test, and locked
project files to a disposable non-production VPS and runs a native `uv sync --locked` environment.
It SHALL reject `montreal-01`, copy no ignored files or credentials, and leave
the installed `/usr/local/bin/evdb` unchanged. Development commands SHALL invoke the checkout's
executable explicitly rather than temporarily replacing the installed command.

#### Scenario: Developer tests an uncommitted source revision
- **WHEN** explicit development files are synced to an approved VPS
- **THEN** its architecture-native environment runs the source revision without a release

#### Scenario: Developer targets production
- **WHEN** the target resolves to `montreal-01`
- **THEN** the workflow refuses before any copy, command, or link change

#### Scenario: Development command completes
- **WHEN** a checkout command exits
- **THEN** `/usr/local/bin/evdb` remains the installed release executable

### Requirement: Public project entry point
The README SHALL describe evdb as pre-v1 Ubuntu tooling for Postgres and Redis-compatible containers,
TLS routing, managed credentials, and automatic checked backup creation. It SHALL present installation,
`evdb init`, database creation, connection information, automatic scheduling, direct commands,
requirements, focused documentation, development commands, license, and explicit v1 limitations. It
SHALL NOT claim live restore, restore verification, remote retention, or automatic rollback.

#### Scenario: New self-hoster evaluates evdb
- **WHEN** the README is opened
- **THEN** the supported engines, core workflow, host requirements, backup boundary, and omitted recovery features are clear without reading implementation details

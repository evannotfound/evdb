# Release Distribution Specification

## Purpose

Define standalone release publication, integrity, installation, and production runtime boundaries.

## Requirements

### Requirement: Tagged standalone releases
Each evdb release SHALL originate from an exact semantic-version Git tag and SHALL publish a
standalone executable whose reported version exactly matches the source version and tag. A release
SHALL NOT be published when these versions differ.

#### Scenario: Release tag differs from the application
- **WHEN** a release workflow for tag `v1.2.3` builds an executable reporting `1.2.4`
- **THEN** the workflow fails before publishing a GitHub Release

### Requirement: Supported Linux release architectures
Every release SHALL provide native Linux archives for ARM64 and x86_64 built on Ubuntu 22.04 runners.
The release and installer SHALL reject unsupported operating systems and architectures explicitly.

#### Scenario: ARM64 host selects a release
- **WHEN** installation runs on an Ubuntu 22.04 `aarch64` host
- **THEN** it selects the native `evdb_linux_arm64.tar.gz` release archive

#### Scenario: Unsupported host requests installation
- **WHEN** installation runs on an unsupported operating system or machine architecture
- **THEN** it exits before downloading or changing managed tool paths and names the unsupported value

### Requirement: Complete release archive
Each architecture archive SHALL contain exactly one executable at `bin/evdb` and every canonical,
non-empty systemd service and timer under `units/`. The executable SHALL run without a host Python
installation or Python package manager.

#### Scenario: Release archive is inspected
- **WHEN** the release workflow assembles an architecture archive
- **THEN** it verifies the expected executable, canonical units, file types, modes, and absence of
  unexpected members before publication

### Requirement: Release integrity and provenance
Every architecture archive SHALL have a matching SHA-256 file and a GitHub artifact attestation tied
to the release workflow and source revision. Installation and host update SHALL verify the checksum
before reading archive members or placing executable content.

#### Scenario: Downloaded archive is corrupted
- **WHEN** an archive digest differs from its release checksum
- **THEN** installation fails without creating or changing a managed version, active link, or stable
  command

### Requirement: Consistent version identity
evdb SHALL expose `evdb --version`, and source package metadata, release tags, release archives,
machine-owned tool state, and runtime status SHALL use the same exact semantic version.

#### Scenario: Operator checks the installed version
- **WHEN** the operator runs `evdb --version`
- **THEN** the command prints the exact version represented by the active release and exits successfully

### Requirement: Public initial installer
Each release SHALL include an installer that can be fetched anonymously from GitHub Releases. The
installer SHALL detect the supported architecture, resolve the latest release by default or accept an
exact version, verify the release checksum and executable version, stage the complete archive, install
it under `/opt/evdb/versions/<version>`, and establish `/opt/evdb/current` and
`/usr/local/bin/evdb` without requiring Python or `uv`.

#### Scenario: New host installs the latest release
- **WHEN** an operator pipes the public latest `install.sh` release asset to a root shell on a supported
  host with no managed evdb installation
- **THEN** the exact downloaded version is installed and selected, and the installer directs the
  operator to run `sudo evdb host setup`

#### Scenario: Operator pins initial installation
- **WHEN** the installer receives exact version `1.2.3`
- **THEN** it downloads only release tag `v1.2.3` and rejects an archive reporting any other version

#### Scenario: Managed installation already exists
- **WHEN** the installer finds an existing managed current version
- **THEN** it leaves all versions and links unchanged and directs the operator to `evdb host update`

### Requirement: Development and production tool boundary
The repository SHALL continue to use `uv` and its lock file for development, checks, and release
builds. Installed production commands, setup, systemd jobs, and host updates SHALL NOT require or
invoke Python, pip, pipx, `uv`, or a Python package registry.

#### Scenario: Host has no Python development tools
- **WHEN** a release archive is installed on a supported host with the documented database-tool
  prerequisites but without Python or `uv`
- **THEN** evdb setup, status, scheduled jobs, and exact-version updates remain operable

### Requirement: Public project entry point
The README SHALL present evdb to public self-hosters with a concise product description, core
features, short installation and representative usage, supported requirements, focused documentation
links, development commands, release status, and MIT licensing. Detailed configuration, command,
recovery, update, and migration procedures SHALL remain in topic documentation rather than the
README.

#### Scenario: New user evaluates evdb
- **WHEN** a self-hoster opens the repository README
- **THEN** they can understand the supported databases, safety model, host requirements, installation,
  first command, documentation paths, and license without reading implementation details

## MODIFIED Requirements

### Requirement: Published exact-version package
evdb SHALL be distributed as a checksummed, architecture-specific standalone release archive. Initial
installation SHALL select and verify the exact version embedded in the downloaded archive, and host
updates SHALL select an exact semantic version rather than an unbounded latest release. Each archive
SHALL expose one `evdb` executable with canonical setup and systemd assets.

#### Scenario: Operator installs the first host version
- **WHEN** the public installer downloads and verifies release `1.0.0` for the host architecture
- **THEN** the resulting version directory exposes one standalone `evdb` executable with canonical
  setup and systemd assets

### Requirement: Prerequisite boundary
Setup SHALL validate Docker with Compose, Restic, rclone, systemd, writable canonical filesystems, DNS
routing inputs, and availability of native ports 5432 and 6379. It SHALL NOT require a host Python
runtime, Python package manager, or `uv`. It SHALL provide installation guidance for missing database
tooling but SHALL NOT install or upgrade unrelated host prerequisites.

#### Scenario: Port 5432 belongs to another proxy
- **WHEN** setup detects an existing process bound to the dedicated Postgres listener
- **THEN** setup fails before starting Traefik and directs the operator to perform an explicit routing
  migration

#### Scenario: Python is absent
- **WHEN** a standalone evdb release runs setup on a host without Python or `uv`
- **THEN** setup does not report either development tool as a missing prerequisite

### Requirement: Exact-version host update
`evdb host update VERSION` SHALL acquire the host lock, download the exact architecture-specific
GitHub Release archive and checksum, verify its digest, members, executable version, and systemd
assets, and stage it as a candidate. It SHALL then run the candidate read-only against current config,
state, Compose, backup records, and units, preview compatible migrations, and require confirmation
before switching. It SHALL NOT resolve or install an unbounded latest release.

#### Scenario: Candidate cannot read current state
- **WHEN** the selected standalone release does not support the installed state schema
- **THEN** update refuses before changing the active tool, config, units, or services

#### Scenario: Candidate archive is unsafe
- **WHEN** the selected archive has a mismatched checksum, traversal path, link, duplicate, unexpected
  member, incomplete units, or executable version other than the selected version
- **THEN** update removes its staging files and leaves the active and previous versions unchanged

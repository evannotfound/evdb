# Host Setup Specification

## Purpose

Define exact-version evdb installation, idempotent host setup, and recoverable host tool updates.

## Requirements

### Requirement: Published exact-version package
evdb SHALL be distributed as a versioned Python package installable through `uv`. Initial installation and host updates SHALL select an exact semantic version and SHALL NOT resolve an unbounded `latest` version.

#### Scenario: Operator installs the first host version
- **WHEN** the operator installs `evanovation-db==1.0.0`
- **THEN** the resulting package exposes one `evdb` executable with canonical setup and systemd assets

### Requirement: Idempotent host setup
`sudo evdb host setup` SHALL check required prerequisites, create the service account and group, establish canonical directories and ownership, initialize host configuration through guided or explicit inputs, create the dedicated Docker network, generate dedicated Traefik files, install canonical systemd units, and finish with a host check. Rerunning setup SHALL converge without changing database data, credentials, timer enablement, or healthy service definitions unnecessarily.

#### Scenario: Setup runs twice
- **WHEN** an already configured host runs `evdb host setup` again with unchanged inputs
- **THEN** the second run reports the host ready without restarting databases or replacing secrets

### Requirement: Prerequisite boundary
Setup SHALL validate compatible Python, Docker with Compose, Restic, rclone, systemd, writable canonical filesystems, DNS routing inputs, and availability of native ports 5432 and 6379. It SHALL provide installation guidance for missing software but SHALL NOT install or upgrade unrelated host prerequisites.

#### Scenario: Port 5432 belongs to another proxy
- **WHEN** setup detects an existing process bound to the dedicated Postgres listener
- **THEN** setup fails before starting Traefik and directs the operator to perform an explicit routing migration

### Requirement: Least file privilege
Setup SHALL create a non-login service account, private state and secret directories, root-controlled source configuration, and systemd jobs that run under the service account. Docker access SHALL be documented as root-equivalent.

#### Scenario: Backup timer starts
- **WHEN** systemd launches a scheduled backup
- **THEN** the job runs as the evdb service account with access only to Docker and required evdb files

### Requirement: Versioned tool installation
Managed tool versions SHALL live under `/opt/evdb/versions/<version>`, `/opt/evdb/current` SHALL select the active tool, `/opt/evdb/previous` SHALL identify one prior tool, and `/usr/local/bin/evdb` SHALL resolve through the active tool. Database configuration, Compose, secrets, state, and data SHALL NOT be stored in a tool version directory.

#### Scenario: Tool version changes
- **WHEN** evdb updates from 1.0.0 to 1.1.0
- **THEN** database files remain at their stable paths while new command invocations use 1.1.0

### Requirement: Exact-version host update
`evdb host update VERSION` SHALL acquire the host lock, install the exact published candidate through `uv`, run candidate read-only compatibility checks against current config, state, Compose, backup records, and units, preview compatible migrations, and require confirmation before switching.

#### Scenario: Candidate cannot read current state
- **WHEN** the selected package does not support the installed state schema
- **THEN** update refuses before changing the active tool, config, units, or services

### Requirement: Atomic tool activation and recovery
Host update SHALL snapshot affected config and unit files, atomically switch the active tool, refresh canonical units without changing timer enablement, and run `evdb host check`. If activation or checking fails, it SHALL restore the previous tool, files, units, and loaded systemd definitions.

#### Scenario: Updated unit fails host check
- **WHEN** the candidate tool activates but its installed unit contract is invalid
- **THEN** evdb restores the prior tool and unit files and reports the failed update without touching database data

### Requirement: Tool updates do not deploy databases
A tool update SHALL NOT regenerate database Compose, change image digests, restart database projects, restore data, or perform an engine migration. A candidate that cannot operate existing definitions SHALL be rejected before activation.

#### Scenario: New renderer would change Compose
- **WHEN** a package update contains different defaults for newly created databases
- **THEN** existing installed database Compose remains unchanged solely because the tool updated

### Requirement: No Ansible runtime dependency
Host setup and updates SHALL NOT invoke Ansible, generate inventory, depend on a controller checkout, copy source modules from a workstation, or maintain a separate bootstrap runtime.

#### Scenario: Fresh host setup succeeds
- **WHEN** the exact package and documented prerequisites are present on a VPS
- **THEN** `evdb host setup` can prepare the host without `ansible-playbook`

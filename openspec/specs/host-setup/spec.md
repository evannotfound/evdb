# Host Setup Specification

## Purpose

Define exact-version evdb installation, idempotent host initialization, and direct tool activation.

## Requirements

### Requirement: Published exact-version package
evdb SHALL be distributed as checksummed architecture-specific standalone executables with the two
canonical backup systemd units embedded. Initial install and configured-host updates SHALL verify the
exact version reported by the downloaded executable before activation.

#### Scenario: Operator installs the first host version
- **WHEN** the public installer downloads and verifies release `1.0.0` for the host architecture
- **THEN** `/usr/local/bin/evdb` is one standalone command that can install the embedded service and timer

### Requirement: Idempotent host setup
Top-level `sudo evdb init` SHALL validate prerequisites and canonical source, create root-owned
directories, initialize routing and Traefik, initialize or verify the one Restic repository,
install the two systemd units, enable the backup timer, and finish with status. On an existing host it
SHALL rerun those direct convergence steps without replacing `secrets.yml` values, the configured
external rclone file, database data, or healthy database Compose projects.

#### Scenario: Initialization runs twice
- **WHEN** an already initialized host runs `evdb init` with unchanged source
- **THEN** the second run verifies the repository and host assets without replacing credentials or restarting databases

#### Scenario: Repository path is absent
- **WHEN** initialization reaches a configured missing rclone repository path
- **THEN** it initializes that path before enabling the backup timer

#### Scenario: Initialization fails midway
- **WHEN** one direct initialization command fails
- **THEN** evdb reports that command and preserves completed canonical files so the operator can correct the cause and rerun init

### Requirement: Prerequisite boundary
Initialization SHALL validate Docker with Compose, Restic 0.17 or newer, rclone, systemd, writable
canonical roots, DNS routing input, one backup repository, and free native ports 5432 and 6379. It
SHALL NOT require host Python, a Python package manager, or `uv`, and SHALL NOT install or upgrade
unrelated host packages.

#### Scenario: Port 5432 belongs to another proxy
- **WHEN** initialization detects an unrelated listener on the dedicated Postgres port
- **THEN** it fails before starting Traefik and names the occupied port

#### Scenario: Restic is too old
- **WHEN** the installed Restic version predates deterministic missing-repository exit codes
- **THEN** initialization names version 0.17 as the minimum and does not inspect or initialize the repository

#### Scenario: Python is absent
- **WHEN** the standalone release initializes a host without Python or `uv`
- **THEN** neither development tool is reported as a missing prerequisite

### Requirement: Consistent root ownership
Canonical host commands and the backup job SHALL run as root. Initialization SHALL create root-owned
private source, generated, database, lock, and partial-backup paths and SHALL NOT create an evdb account
or grant Docker-group access to another user. Backup ancestors SHALL be root-owned and traverse-only;
completed backups SHALL be handed to the configured rclone owner read-only. Database containers retain
their image-specific runtime identities, and only Restic and its rclone child SHALL drop to the rclone
owner.

#### Scenario: Backup timer starts
- **WHEN** systemd launches the scheduled backup
- **THEN** the service runs as root while each Restic and rclone repository subprocess runs as the configured non-root rclone owner

### Requirement: Direct tool installation
The verified release SHALL be one root-owned regular executable at `/usr/local/bin/evdb`. No
`/opt/evdb`, current or previous link, version directory, or installed application machine state SHALL
be created. Source, generated files, credentials, backups, and data SHALL remain outside the command.

#### Scenario: Tool version changes
- **WHEN** the installer changes from 1.0.0 to 1.1.0
- **THEN** stable database files remain unchanged and new command invocations use 1.1.0

### Requirement: Atomic tool activation
The public installer SHALL fully download and verify a candidate before atomically replacing
`/usr/local/bin/evdb`. After selection it SHALL run `evdb init --yes` on configured hosts.
Initialization failure SHALL be reported directly and SHALL NOT trigger an automatic tool rollback.

#### Scenario: Archive validation fails
- **WHEN** the candidate checksum or reported version is invalid
- **THEN** the installer leaves the installed executable unchanged

#### Scenario: Post-selection initialization fails
- **WHEN** the new command cannot refresh configured host assets
- **THEN** the installer exits nonzero with the initialization error while the verified new executable remains installed

### Requirement: Tool updates do not deploy databases
Installer-driven updates and their `evdb init --yes` refresh SHALL NOT rewrite project source,
credentials, database Compose, data, or images and SHALL NOT restart database roles. They MAY refresh
Traefik and the two canonical backup units from unchanged host source.

#### Scenario: New release has different engine defaults
- **WHEN** a configured host installs it
- **THEN** existing explicit database settings and generated Compose remain unchanged solely because the tool changed

### Requirement: No Ansible runtime dependency
Host setup and updates SHALL NOT invoke Ansible, generate inventory, depend on a controller checkout, copy source modules from a workstation, or maintain a separate bootstrap runtime.

#### Scenario: Fresh host setup succeeds
- **WHEN** the exact package and documented prerequisites are present on a VPS
- **THEN** `evdb host setup` can prepare the host without `ansible-playbook`

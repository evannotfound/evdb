## MODIFIED Requirements

### Requirement: Idempotent host setup
Top-level `sudo evdb init` SHALL validate prerequisites and the complete candidate before mutation,
create root-owned managed directories, initialize routing and Traefik, obtain or verify the configured
host wildcard certificate, initialize or verify the one Restic repository, install the two systemd
units, enable the backup timer, and finish with status. On an existing host it SHALL rerun those direct
convergence steps without replacing `secrets.yml` values, an external rclone file, database data, or
healthy database Compose projects.

#### Scenario: Initialization runs twice
- **WHEN** an already initialized host runs `evdb init` with unchanged source
- **THEN** the second run verifies the wildcard certificate, repository, and host assets without replacing credentials or restarting databases

#### Scenario: Repository path is absent
- **WHEN** initialization reaches a configured missing rclone or local repository path
- **THEN** it initializes that path under the selected repository operator before enabling the backup timer

#### Scenario: Initialization fails midway
- **WHEN** one direct initialization command or wildcard issuance fails
- **THEN** evdb reports the failing operation and preserves completed canonical files so the operator can correct the cause and rerun init

#### Scenario: Candidate validation fails
- **WHEN** fresh setup contains an unsupported provider, unsafe repository, incomplete credentials, or invalid host value
- **THEN** evdb changes no canonical source, generated file, container, repository, or systemd unit

### Requirement: Prerequisite boundary
Initialization SHALL validate Docker with Compose, Restic 0.17 or newer, systemd, writable canonical
roots, DNS routing input, one supported backup repository, and free native ports 5432 and 6379. It SHALL
require the trusted rclone executable only when the selected repository uses rclone. It SHALL NOT require
host Python, a Python package manager, or `uv`, and SHALL NOT install or upgrade unrelated host packages.

#### Scenario: Port 5432 belongs to another proxy
- **WHEN** initialization detects an unrelated listener on the dedicated Postgres port
- **THEN** it fails before starting Traefik and names the occupied port

#### Scenario: Restic is too old
- **WHEN** the installed Restic version predates deterministic missing-repository exit codes
- **THEN** initialization names version 0.17 as the minimum and does not inspect or initialize the repository

#### Scenario: Local repository host lacks rclone
- **WHEN** a host with a valid local repository has no rclone executable
- **THEN** initialization does not report rclone as a missing prerequisite

#### Scenario: Python is absent
- **WHEN** the standalone release initializes a host without Python or `uv`
- **THEN** neither development tool is reported as a missing prerequisite

### Requirement: Consistent root ownership
Canonical host commands and the backup job SHALL run as root. Initialization SHALL create root-owned
private source, generated, database, lock, and partial-backup paths and SHALL NOT create an evdb account
or grant Docker-group access to another user. Backup ancestors SHALL be root-owned and traverse-only;
completed backups SHALL be handed read-only to the repository operator. Database containers retain their
image-specific runtime identities, and only Restic and its optional rclone child SHALL drop to the
repository operator.

#### Scenario: Rclone backup timer starts
- **WHEN** systemd launches a scheduled backup for an rclone repository
- **THEN** the service runs as root while Restic and its rclone child run as the non-root owner of the configured rclone file

#### Scenario: Local backup timer starts
- **WHEN** systemd launches a scheduled backup for a local repository
- **THEN** the service runs as root while Restic runs as the non-root owner derived from the repository path without starting rclone

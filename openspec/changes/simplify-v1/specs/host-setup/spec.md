## MODIFIED Requirements

### Requirement: Published exact-version package
evdb SHALL be distributed as checksummed architecture-specific standalone archives. Initial install
and configured-host updates SHALL select and verify the exact version embedded in the archive. Each
archive SHALL expose one `evdb` executable plus the two canonical backup systemd units.

#### Scenario: Operator installs the first host version
- **WHEN** the public installer downloads and verifies release `1.0.0` for the host architecture
- **THEN** the selected release exposes one standalone command and the backup service and timer

### Requirement: Idempotent host setup
Top-level `sudo evdb init` SHALL validate prerequisites and canonical source, create the service
account and directories, initialize routing and Traefik, initialize or verify the one Restic repository,
install the two systemd units, enable the backup timer, and finish with status. On an existing host it
SHALL rerun those direct convergence steps without replacing `secrets.yml` values, mutable
`rclone.conf`, database data, or healthy database Compose projects.

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

### Requirement: Least file privilege
Initialization SHALL create a non-login evdb service account, root-controlled `config.yml`, private
mode-`0600` `secrets.yml` and `rclone.conf`, service-owned generated and mutable paths, and the backup
job running as evdb. Docker group access SHALL remain documented as root-equivalent.

#### Scenario: Backup timer starts
- **WHEN** systemd launches the scheduled backup
- **THEN** the job runs as evdb with Docker and required evdb file access

### Requirement: Versioned tool installation
Verified releases SHALL live under `/opt/evdb/versions/<version>`, `current` SHALL select the active
release, `previous` SHALL identify one prior release after update, and `/usr/local/bin/evdb` SHALL
resolve through `current`. Source, generated files, credentials, backups, and data SHALL remain outside
tool versions. No installed application machine state SHALL duplicate the active version.

#### Scenario: Tool version changes
- **WHEN** the installer changes from 1.0.0 to 1.1.0
- **THEN** stable database files remain unchanged and new command invocations use 1.1.0

### Requirement: Atomic tool activation and recovery
The public installer SHALL fully download, verify, and extract a candidate before atomically changing
`current`; it SHALL retain one previous verified release directory. After selection it SHALL run
`evdb init --yes` on configured hosts. Initialization failure SHALL be reported directly and SHALL NOT
trigger a second Python compatibility or rollback transaction.

#### Scenario: Archive validation fails
- **WHEN** the candidate checksum, layout, or reported version is invalid
- **THEN** the installer leaves current and previous links unchanged

#### Scenario: Post-selection initialization fails
- **WHEN** the new command cannot refresh configured host assets
- **THEN** the installer exits nonzero with the initialization error while the previous verified release remains on disk

### Requirement: Tool updates do not deploy databases
Installer-driven updates and their `evdb init --yes` refresh SHALL NOT rewrite project source,
credentials, database Compose, data, or images and SHALL NOT restart database roles. They MAY refresh
Traefik and the two canonical backup units from unchanged host source.

#### Scenario: New release has different engine defaults
- **WHEN** a configured host installs it
- **THEN** existing explicit database settings and generated Compose remain unchanged solely because the tool changed

## REMOVED Requirements

### Requirement: Exact-version host update
**Reason**: Release downloading, compatibility checks, file snapshots, activation, and rollback are
duplicated between `host.py` and the verified installer.

**Migration**: Rerun `install.sh` with an exact version; the installer accepts configured hosts and
refreshes them through `evdb init --yes`.

### Requirement: Safe host uninstall
**Reason**: Destructive host and data cleanup is outside the v1 container-and-backup workflow.

**Migration**: Stop Compose and systemd units and remove files manually when decommissioning a
disposable v1 host.

## ADDED Requirements

### Requirement: User-run repository subprocesses
Every Restic repository operation SHALL run as the non-root owner of
`host.backup.rclone_config`, using that account's primary and supplementary groups. evdb SHALL replace
the inherited root environment with explicit `HOME`, `USER`, `LOGNAME`, `RCLONE_CONFIG`, and a
user-writable Restic cache location. It SHALL invoke trusted absolute Restic and rclone executables and
configure Restic to launch that rclone path. The Restic password SHALL be supplied through an inherited
Linux memory-file descriptor, not arguments, environment variables, or a persistent file.

#### Scenario: Scheduled repository inspection runs
- **WHEN** the root backup service checks the configured repository
- **THEN** Restic and its rclone child run with the rclone file owner's identity and canonical mutable configuration

#### Scenario: Restic reads its password
- **WHEN** evdb starts any Restic operation
- **THEN** Restic reads the password from an inherited memory descriptor that is closed when the operation ends

### Requirement: Automatic host repository initialization
`evdb init` SHALL initialize the one configured host Restic repository before enabling scheduled
backups. It SHALL run `restic cat config`; success SHALL reuse the repository, the documented
missing-repository exit SHALL run `restic init --repository-version 1`, and every other result SHALL
fail with the repository URL and original credential-redacted error. Restic through rclone SHALL create
the missing remote path without a separate rclone directory operation.

#### Scenario: Configured remote path does not exist
- **WHEN** Restic 0.17 or newer reports that the configured rclone repository is missing
- **THEN** initialization creates a format-v1 repository at that exact path and verifies it before enabling the timer

#### Scenario: Repository already exists
- **WHEN** `restic cat config` succeeds
- **THEN** initialization preserves the existing repository and does not run `restic init`

#### Scenario: Repository access is denied
- **WHEN** repository inspection fails for a reason other than the documented missing-repository exit
- **THEN** initialization fails without attempting another command or hiding the repository and error

## MODIFIED Requirements

### Requirement: Snapshot identity
Every snapshot in the host repository SHALL use stable host, project, role, concrete engine, backup,
and purpose tags. History SHALL filter all required identity tags so Postgres, Redis, and Dragonfly
backups from different projects remain distinct inside one repository.

#### Scenario: Latest snapshots are queried
- **WHEN** history reads `example-prod-01/kv`
- **THEN** it returns only matching host, project, role, and concrete-engine snapshots from the shared repository

### Requirement: Repository locking
All Restic work SHALL use one lock derived from the configured host repository. Repository
initialization, backup upload, and snapshot listing SHALL NOT race or use `--no-lock`.

#### Scenario: Upload is running
- **WHEN** another role reaches the same host repository
- **THEN** it waits for the shared repository lock or fails clearly at the configured timeout

### Requirement: Repository format
V1 SHALL require Restic 0.17 or newer, initialize new repositories as format v1, and reject an existing
repository whose configuration cannot be read with the configured password. Repository upgrades are
outside v1.

#### Scenario: Restic is too old
- **WHEN** host initialization detects Restic older than 0.17
- **THEN** it fails before repository inspection and names the required minimum version

#### Scenario: Repository reports format v2
- **WHEN** initialization reads an existing format-v2 repository
- **THEN** it reports that v1 requires repository format v1 and does not upload

### Requirement: Local test repository
Restic integration tests SHALL create a new disposable repository and password inside temporary test
storage. At least one integration SHALL use an rclone local remote whose target path is initially
absent and prove automatic initialization. Tests SHALL NOT use production OneDrive remotes,
credentials, passwords, or rclone configuration.

#### Scenario: Restic integration runs
- **WHEN** the test initializes its missing rclone-backed repository path
- **THEN** the resulting repository and all snapshots remain inside the disposable temporary directory

## REMOVED Requirements

### Requirement: Retention and prune
**Reason**: Automatic remote deletion and its reviewed dry-run workflow are outside the backup-only v1
boundary.

**Migration**: Remote snapshots accumulate until the operator runs Restic retention manually or a
future focused capability is added.

### Requirement: Repository checks
**Reason**: Rotating structure and data maintenance jobs are not required to establish reliable basic
backup creation.

**Migration**: Operators may run Restic checks outside evdb when needed.

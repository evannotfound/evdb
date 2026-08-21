# Jobs Specification

## Purpose

Define operational status, safe execution, and the scheduled all-database backup job.

## Requirements

### Requirement: Safe command execution
External commands SHALL use argument arrays without `shell=True`, enforce finite timeouts, and check
return codes. evdb SHALL redact exact credential values loaded from `secrets.yml` and credential fields
in the configured external rclone file, including required encoded forms, while preserving repository URLs, remote names,
paths, images, snapshot IDs, and unrelated stdout or stderr. Repository subprocesses SHALL use numeric
user and group identities, exact environment replacement, and explicit inherited file descriptors.

#### Scenario: Tool prints a managed credential in an error
- **WHEN** stderr contains an exact managed password or token
- **THEN** displayed and journal-captured output replaces that value with a redaction marker

#### Scenario: Tool prints the repository location
- **WHEN** stderr names the configured Restic repository
- **THEN** the operator sees the complete repository URL

### Requirement: Unified database status
`evdb status` SHALL report each configured project/role's concrete engine, running and native health,
latest valid backup time and availability, and current assessment error. Human output SHALL use one
compact overview with Database, Engine, Status, and Backup and SHALL omit images, hashes, generated
configuration comparison, and full errors until a database is selected or explicitly requested.

#### Scenario: All databases are healthy
- **WHEN** every configured role is running, passes native health, and has a recent remote backup
- **THEN** status prints one compact healthy row per project/role and exits zero

#### Scenario: One database is unhealthy
- **WHEN** one role fails a native health check
- **THEN** its compact row identifies the unhealthy state, other roles remain visible, and status exits nonzero

#### Scenario: Operator opens one database
- **WHEN** the operator selects a role from the guided overview
- **THEN** its full image, paths, settings, backup records, and current error are shown outside the root table

#### Scenario: Status assesses several durable databases
- **WHEN** one status collection includes multiple durable roles from the same host repository
- **THEN** evdb reads the host snapshot list once and filters that result by each role's complete identity

### Requirement: Host status
Status SHALL include host identity, running evdb version, Traefik and network health, native listeners,
one backup timer state, repository availability, source validation, and storage assessment. Storage SHALL
cover `/var/lib/evdb` and every configured database root with exact path, backing mount point and source,
filesystem type, used/total/free capacity, and assigned project/roles. It SHALL NOT assess machine-state
compatibility, tool-version match, generated contract hashes, transaction directories, restore state, or
maintenance timers.

#### Scenario: Disk space is below policy
- **WHEN** free space for state storage or a configured database root is below the configured minimum
- **THEN** host status identifies the affected path and backing filesystem and marks the host unhealthy

#### Scenario: Several database roots are configured
- **WHEN** roles select database roots backed by different mounted filesystems
- **THEN** host details show every root, its actual mount facts and capacity, and the roles assigned to it

#### Scenario: Backup timer is inactive
- **WHEN** the one packaged backup timer is not loaded, enabled, and active
- **THEN** host details report the inactive timer without listing per-database unit names

### Requirement: Live engine checks
The host-local runtime SHALL inspect Compose service state and perform an engine-native ping or simple query for each running database. A running container alone SHALL NOT be considered healthy.

#### Scenario: Container runs but engine rejects queries
- **WHEN** Docker reports a running primary but the native check fails
- **THEN** status marks the project/role unhealthy and records the engine check failure

### Requirement: Backup and recovery freshness
Status SHALL compare the newest matching confirmed Restic snapshot for each durable project/role with
the configured maximum backup age. It SHALL distinguish missing or stale backup state from runtime
database health and SHALL NOT evaluate restore-test freshness.

#### Scenario: Backup is stale
- **WHEN** no confirmed matching snapshot exists within maximum age
- **THEN** the role's Backup column reports stale or missing and status exits nonzero

#### Scenario: Cache role is displayed
- **WHEN** a KV role uses cache mode
- **THEN** its Backup column reports disabled rather than stale

### Requirement: Structured status output
`evdb status --json` SHALL emit one credential-free JSON object containing integer `version`, boolean
`healthy`, a host object, a databases object keyed by exact project/role, and an errors array. Host
fields SHALL cover identity, tool version, infrastructure, state storage, configured database-root
storage, repository, and the one timer. Database fields SHALL cover project, role, engine, running,
health, configured image, latest backup, and bounded error. Output SHALL omit machine state, contract
comparison, operation history, restore, backup tests, and maintenance units.

#### Scenario: Automation requests JSON
- **WHEN** status runs with `--json`
- **THEN** stdout contains exactly one parseable document with additive mount-aware storage facts and no terminal presentation or credentials

#### Scenario: Human contract changes incompatibly
- **WHEN** a future release removes or changes a required structured field
- **THEN** the top-level status version changes

### Requirement: Partial assessment
Failure to assess one project/role SHALL NOT prevent status from assessing and reporting other databases or host infrastructure when local execution remains possible.

#### Scenario: One engine check times out
- **WHEN** one native health command exceeds its timeout
- **THEN** status records that timeout and continues with remaining roles

### Requirement: Systemd jobs and timer preservation
The package SHALL include exactly one `evdb-backup.service` and one `evdb-backup.timer`. The service
SHALL run `evdb backup create --all` as root with finite timeout and low CPU and I/O
priority, while only its Restic and rclone subprocesses drop to the configured rclone owner. The timer
SHALL be daily, persistent, randomized, and automatically enabled by `evdb init`.
Initialization and installer refresh SHALL converge it to loaded, enabled, and active.

#### Scenario: Host was offline at backup time
- **WHEN** the persistent timer becomes active after the host returns
- **THEN** systemd schedules the missed all-database backup

#### Scenario: Database is added after initialization
- **WHEN** a new durable role is added
- **THEN** the unchanged all-database service includes it on the next run without a timer instance or daemon reload

#### Scenario: Installer refreshes units
- **WHEN** a configured host installs a new evdb release
- **THEN** rerunnable initialization installs the two packaged units and leaves the timer active

## MODIFIED Requirements

### Requirement: Unified database status
`evdb status` SHALL report every configured project/role's running state, concrete engine health, installed-configuration match, current image, latest completed local backup, latest confirmed Restic snapshot and upload result, latest successful backup test, and current operation failure in one concise table.

#### Scenario: All databases are healthy
- **WHEN** every configured role is running, matches installed Compose, is backed up, and has recent recovery verification
- **THEN** status prints one compact healthy row per project/role and exits zero

#### Scenario: One database is unhealthy
- **WHEN** one role fails an engine health check
- **THEN** its row identifies the failure, remaining rows are reported, and status exits nonzero

### Requirement: Host status
Status SHALL include host identity, installed evdb version, configuration and machine-state consistency, dedicated Traefik and network health, native listener state, free data and backup disk space, required timer state, and any incomplete settings, restore, setup, or update transaction. It SHALL not depend on SSH reachability or an active deployment release.

#### Scenario: Disk space is below policy
- **WHEN** free space is below the configured minimum
- **THEN** status marks the host unhealthy and names the affected evdb filesystem without exposing unrelated host data

#### Scenario: Settings transaction was interrupted
- **WHEN** private transaction state remains after an interrupted mutation
- **THEN** host status identifies the affected project/role and directs the operator to a safe host check

### Requirement: Live engine checks
The host-local runtime SHALL inspect Compose service state and perform an engine-native ping or simple query for each running database. A running container alone SHALL NOT be considered healthy.

#### Scenario: Container runs but engine rejects queries
- **WHEN** Docker reports a running primary but the native check fails
- **THEN** status marks the project/role unhealthy and records the engine check failure

### Requirement: Drift reporting
Status SHALL compare authoritative host source, installed generated Compose hashes, resolved image state, dedicated network and Traefik, and every expected primary and sidecar's image, health, and service-contract label. Human output SHALL say that configuration or a running service differs rather than exposing desired/deployed/release terminology.

#### Scenario: Live image changed manually
- **WHEN** a running image digest differs from installed generated Compose and machine state
- **THEN** status reports that the selected project/role differs from its installed configuration

#### Scenario: Generated Compose was edited
- **WHEN** an installed Compose file differs from the canonical source-derived structure
- **THEN** status marks the role unhealthy and directs the operator to run its settings workflow

#### Scenario: Sidecar is unhealthy
- **WHEN** expected PgBouncer or HTTP service is absent, stopped, unhealthy, or has the wrong contract label
- **THEN** status marks only that database unhealthy and continues assessing others

#### Scenario: Traefik has no health status
- **WHEN** dedicated Traefik is running without concrete healthy Docker status
- **THEN** host status marks native routing unhealthy

### Requirement: Backup and recovery freshness
Status SHALL evaluate project/role backup uploads and full backup-test timestamps against host policy and distinguish stale state from the latest operation failure.

#### Scenario: Backup is stale
- **WHEN** no confirmed remote snapshot exists within configured maximum age
- **THEN** status marks the project/role backup stale and exits nonzero

#### Scenario: Backup test is stale
- **WHEN** no successful isolated backup test exists within configured maximum age
- **THEN** status marks recovery verification stale

#### Scenario: Last backup attempt failed
- **WHEN** an operation recorded an error after the latest successful snapshot
- **THEN** status reports the failure separately from the age of the last success

### Requirement: Structured status output
`evdb status --json` SHALL emit one secret-free JSON object containing integer `version`, boolean `healthy`, object `host`, object `databases`, and array `errors`. `host` SHALL include host identity, installed tool version, infrastructure health, disk status, timer status, and active transaction summary. `databases` SHALL be keyed by exact `<project>/<role>` identity and each value SHALL include project, role, concrete engine, running and health state, installed image, configuration match, latest backup/upload/test summaries, and a bounded current error when present. Errors SHALL use stable codes and bounded human messages without subprocess dumps or secrets. Additive fields MAY be introduced within one version; removing fields or changing their type or meaning SHALL increment `version`. The command SHALL use the same health exit status as human output and remain suitable for SSH polling and a future read-only central monitor.

#### Scenario: Automation requests JSON
- **WHEN** status runs non-interactively with `--json`
- **THEN** it emits one parseable JSON document, no menu or table text, and no credential-bearing field

#### Scenario: Status contract changes incompatibly
- **WHEN** a release removes a required field or changes its type or meaning
- **THEN** the top-level status version is incremented and the prior tool is not treated as producing the new contract

### Requirement: Partial assessment
Failure to assess one project/role SHALL NOT prevent status from assessing and reporting other databases or host infrastructure when local execution remains possible.

#### Scenario: One engine check times out
- **WHEN** one native health command exceeds its timeout
- **THEN** status records that timeout and continues with remaining roles

### Requirement: Structured logs
Commands SHALL write concise structured logs with host, project, role, concrete engine when relevant, command, step, result, duration, and redacted error fields. Logs SHALL be useful through journald and readable during local tests.

#### Scenario: Backup fails
- **WHEN** an engine backup command fails
- **THEN** one error record identifies project/role and failed step without exposing credentials

### Requirement: Systemd jobs and timer preservation
The installed package SHALL include canonical systemd units for per-database backups, daily status, due backup testing, weekly Restic checks and retention, and monthly prune. Timers SHALL use persistent scheduling, randomized delays, execution timeouts, and low CPU and I/O priority. Host setup and tool updates SHALL preserve each timer's enabled and running state unless the operator explicitly changes scheduling.

#### Scenario: Host was offline at backup time
- **WHEN** a persistent timer becomes active after the host returns
- **THEN** systemd schedules missed work instead of waiting a full day

#### Scenario: Tool update refreshes units
- **WHEN** a candidate package installs compatible canonical units
- **THEN** enabled and running timers retain state and disabled timers remain disabled

# Jobs Specification

## Purpose

Define operational status, safe execution, structured records, and scheduled maintenance behavior.

## Requirements

### Requirement: Safe command execution
External commands SHALL use argument arrays without `shell=True`, enforce timeouts, check return codes, and redact secret values from logs and errors.

#### Scenario: Tool prints a secret in an error
- **WHEN** stderr contains a configured secret value
- **THEN** the stored and displayed error replaces that value with a redaction marker

### Requirement: Unified database status
`evdb status` SHALL report every configured database's running state, engine health, deployment drift, latest completed local backup, latest confirmed remote backup and upload result, latest successful full backup verification, and current operation failure in one concise table.

#### Scenario: All databases are healthy
- **WHEN** every configured database is reachable, current, backed up, and recently verified
- **THEN** status prints one compact healthy row per database and exits zero

#### Scenario: One database is unhealthy
- **WHEN** a database fails an engine health check
- **THEN** its row identifies the failure, the remaining rows are still reported, and status exits nonzero

### Requirement: Host status
Status SHALL include SSH reachability, free data and backup disk space, active release identity, configuration-lock consistency, and required timer state.

#### Scenario: Host is unreachable
- **WHEN** SSH cannot reach the configured host
- **THEN** status reports the host failure locally, performs no write, and exits nonzero

#### Scenario: Disk space is below policy
- **WHEN** free space is below the configured minimum
- **THEN** status marks the host unhealthy and reports the affected filesystem without exposing unrelated host data

### Requirement: Live engine checks
The remote runtime SHALL inspect container state and perform an engine-native ping or simple query for each running database. A running container alone SHALL NOT be considered healthy.

#### Scenario: Container is running but database rejects queries
- **WHEN** Docker reports a running container but the engine-native check fails
- **THEN** status marks the database unhealthy and records the engine check failure

### Requirement: Drift reporting
Status SHALL compare the active release, locked images, generated configuration, shared network, Traefik, and every expected primary and sidecar container's image, health, and service-contract label. It SHALL distinguish pending desired changes from unplanned live drift.

#### Scenario: Live image was changed manually
- **WHEN** a container image digest differs from both the active release and desired lock
- **THEN** status reports unplanned image drift for that database

#### Scenario: Source has an unapplied change
- **WHEN** desired configuration differs from the healthy active release
- **THEN** local status reports the database as pending rather than unhealthy solely because it is unapplied

#### Scenario: Live service was replaced with the same image
- **WHEN** a primary database or Traefik container has the expected image but a missing or wrong service-contract hash label
- **THEN** status reports unplanned service-definition drift

#### Scenario: Database sidecar is unhealthy
- **WHEN** an expected PgBouncer or HTTP sidecar is absent, stopped, unhealthy, or has a wrong contract label
- **THEN** status reports database deployment drift, marks the database unhealthy, and continues assessing other databases

#### Scenario: Traefik has no Docker health status
- **WHEN** Traefik is running without a concrete healthy Docker health status
- **THEN** status marks host infrastructure unhealthy rather than treating the missing healthcheck as healthy

### Requirement: Backup and recovery freshness
Status SHALL evaluate backup upload and full restore-verification timestamps against host policy and distinguish stale state from operation failure.

#### Scenario: Backup is stale
- **WHEN** no confirmed remote snapshot exists within the configured maximum age
- **THEN** status marks backup freshness stale and exits nonzero

#### Scenario: Verification is stale
- **WHEN** no successful full backup verification exists within the configured maximum age
- **THEN** status marks recovery verification stale

#### Scenario: Last backup attempt failed
- **WHEN** a backup operation recorded an error after the last successful snapshot
- **THEN** status reports the error separately from the age of the last success

### Requirement: Structured status output
`evdb status --json` SHALL emit a versioned JSON document containing the same host and database assessment as human output. It SHALL use the same exit status and SHALL contain no secret values.

#### Scenario: Automation requests JSON
- **WHEN** status is invoked with `--json`
- **THEN** it emits parseable versioned JSON and no human table text

### Requirement: Partial assessment
Failure to assess one database SHALL NOT prevent status from assessing and reporting other databases when the host remains reachable.

#### Scenario: One engine check times out
- **WHEN** one database health command exceeds its timeout
- **THEN** status records that timeout and continues with the remaining configured databases

### Requirement: Structured logs
Commands SHALL write concise structured logs with host, database, command, step, result, duration, and error fields. Logs SHALL be useful through journald and readable during local tests.

#### Scenario: Database backup fails
- **WHEN** an engine command fails
- **THEN** one error record identifies the database and failed step without exposing credentials

### Requirement: Systemd jobs and timer preservation
The repository SHALL include canonical systemd units for per-database backup, daily status, due restore verification, weekly checks and retention, and monthly prune. Timers SHALL use persistent scheduling, randomized delays, execution timeouts, and low CPU and I/O priority.

Routine apply and release activation SHALL preserve each existing timer's enabled and running state.

#### Scenario: Host was offline at backup time
- **WHEN** a persistent timer becomes active after the host returns
- **THEN** systemd schedules the missed work instead of waiting a full day

#### Scenario: Routine apply updates units
- **WHEN** canonical systemd units are activated for a healthy release
- **THEN** timers that were enabled or running retain that state and disabled timers remain disabled

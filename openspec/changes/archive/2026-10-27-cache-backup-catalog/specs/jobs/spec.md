## MODIFIED Requirements

### Requirement: Guided status refresh lifecycle
The guided CLI SHALL run at most one in-process background status refresh. It SHALL refresh on session start, after backup creation or explicit inventory refresh, and when returning to root after the completed result is 60 seconds old. It SHALL reuse fresher results and SHALL NOT poll continuously. Status refresh SHALL use only local backup evidence. Before process exit or a conflicting mutation, it SHALL cancel and reap the worker and any active external command process group.

#### Scenario: Operator navigates while runtime checks load
- **WHEN** guided runtime assessment remains active and the operator selects a numbered view
- **THEN** navigation proceeds without waiting and the single worker may continue updating the session cache

#### Scenario: Operator returns to root with a fresh result
- **WHEN** the last complete status result is less than 60 seconds old and no relevant operation invalidated it
- **THEN** evdb reuses that result without another assessment

#### Scenario: Operator creates a backup
- **WHEN** backup creation begins while background status is active
- **THEN** evdb cancels the background command before taking the repository lock and invalidates status for the next root refresh

#### Scenario: Operator exits during assessment
- **WHEN** the guided session exits while a background external command is active
- **THEN** evdb kills the command process group, reaps the worker, and exits without leaving subprocesses running

### Requirement: Unified database status
`evdb status` SHALL report each configured project/role's concrete engine, running and native health, latest known backup time and availability, and current assessment error. Direct human and JSON status SHALL complete local and runtime assessments synchronously before returning output and an exit code. Guided status MAY publish partial runtime assessments. Backup and repository reporting SHALL use persistent local evidence without Restic/rclone calls or dump hashing. Human output SHALL use one compact overview with Database, Engine, Status, and Backup, show remote inventory freshness, and omit images, hashes, generated configuration comparison, and full errors until a database is selected or explicitly requested.

#### Scenario: All databases are healthy
- **WHEN** every configured role is running, passes native health, and has a recent known remote backup with no reported host failures
- **THEN** status prints one compact healthy row per project/role and exits zero

#### Scenario: One database is unhealthy
- **WHEN** one role fails a native health check
- **THEN** its compact row identifies the unhealthy state, other roles remain visible, and status exits nonzero

#### Scenario: Operator opens one database
- **WHEN** the operator selects a role from the guided overview
- **THEN** its full image, paths, settings, cached backup records, and current error are shown outside the root table

#### Scenario: Status assesses several durable databases
- **WHEN** one status collection includes multiple durable roles
- **THEN** evdb reads one saved host catalog and filters it by each role's complete identity without querying the repository

#### Scenario: Automation requests status
- **WHEN** a caller runs direct `evdb status` or `evdb status --json`
- **THEN** the command completes runtime and cached backup assessment and emits no pending state

### Requirement: Backup and recovery freshness
Status SHALL compare the newest matching known Restic snapshot for each durable project/role with the configured maximum backup age using the current clock. It SHALL expose the last successful inventory check and the latest backup attempt separately. Missing inventory SHALL be unknown rather than proof of missing backups. A refresh failure SHALL retain known backup facts and appear separately from backup freshness. Status SHALL distinguish backup state from runtime database health and SHALL NOT evaluate restore-test freshness.

#### Scenario: Backup is stale
- **WHEN** the newest known snapshot exceeds maximum age, or a checked inventory has no matching snapshot
- **THEN** the role's Backup column reports stale or missing and status exits nonzero

#### Scenario: Catalog has never been populated
- **WHEN** there is no checked inventory and no known successful upload for the role
- **THEN** backup status is unknown, not current or confirmed missing, and status exits nonzero without remote work

#### Scenario: Refresh fails with a current known backup
- **WHEN** a remote refresh failed but the previously known snapshot remains within maximum age
- **THEN** its backup freshness remains current and the repository refresh failure is reported separately

#### Scenario: Cache role is displayed
- **WHEN** a KV role uses cache mode
- **THEN** its Backup column reports disabled rather than stale

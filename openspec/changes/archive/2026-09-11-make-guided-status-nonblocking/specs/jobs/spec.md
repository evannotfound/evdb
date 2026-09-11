## MODIFIED Requirements

### Requirement: Unified database status
`evdb status` SHALL report each configured project/role's concrete engine, running and native health,
latest valid backup time and availability, and current assessment error. Direct human and JSON status
SHALL complete every assessment synchronously before returning output and an exit code. Guided status MAY
publish config-only and local-runtime snapshots before one background repository assessment completes.
Human output SHALL use one compact overview with Database, Engine, Status, and Backup and SHALL omit
images, hashes, generated configuration comparison, and full errors until a database is selected or
explicitly requested.

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

#### Scenario: Automation requests status
- **WHEN** a caller runs direct `evdb status` or `evdb status --json`
- **THEN** the command waits for complete runtime and backup assessment and emits no pending state

## ADDED Requirements

### Requirement: Guided status refresh lifecycle
The guided CLI SHALL run at most one in-process background status refresh. It SHALL refresh on session
start, after backup creation, and when returning to root after the completed result is 60 seconds old. It
SHALL reuse fresher results, SHALL NOT poll continuously, and SHALL NOT start a repository query after each
menu action. Before process exit or a conflicting mutation, it SHALL cancel and reap the worker and any
active external command process group.

#### Scenario: Operator navigates while backups load
- **WHEN** a guided repository query remains active and the operator selects a numbered view
- **THEN** navigation proceeds without waiting and the single worker may continue updating the session cache

#### Scenario: Operator returns to root with a fresh result
- **WHEN** the last complete status result is less than 60 seconds old and no relevant operation invalidated it
- **THEN** evdb reuses that result without starting another Restic query

#### Scenario: Operator creates a backup
- **WHEN** backup creation begins while background status is active
- **THEN** evdb cancels the background command before taking the repository lock and invalidates status for the next root refresh

#### Scenario: Operator exits during assessment
- **WHEN** the guided session exits while a background external command is active
- **THEN** evdb kills the command process group, reaps the worker, and exits without leaving Restic or rclone running

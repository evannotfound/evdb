## ADDED Requirements

### Requirement: Private PgBouncer runtime access
Generated PgBouncer services SHALL run without root privileges under an identity that can read the
evdb-owned configuration and authentication files mounted into that service. PgBouncer configuration
and authentication files SHALL retain private host permissions, and their contents SHALL NOT appear
in generated Compose environment, labels, commands, status, activity, or logs.

#### Scenario: Default PgBouncer starts from private files
- **WHEN** evdb starts a newly added Postgres role with PgBouncer enabled and managed files owned by the evdb service account
- **THEN** PgBouncer reads only its mounted configuration and authentication files, becomes healthy as a non-root process, and accepts the managed Postgres login

#### Scenario: Generated Compose remains secret-free
- **WHEN** evdb renders or validates the Postgres Compose project
- **THEN** the Compose document contains the private file paths and runtime identity but no password or authentication-file content

## MODIFIED Requirements

### Requirement: Idempotent database creation
`evdb database add PROJECT postgres` and `evdb database add PROJECT kv [--engine ENGINE]` SHALL
validate identity, select and persist explicit defaults, generate required private host credentials or
accept a securely supplied initial Postgres password, render and validate an independent Compose
project, preview the operation without the credential value, require confirmation, start services, and
require full health before committing successful installation state. Rerunning an interrupted or
matching add SHALL not duplicate a role, data directory, secret, project, or route. A matching
Postgres add with the same supplied password SHALL be unchanged, while a different supplied password
SHALL be rejected as an unsupported rotation.

#### Scenario: Default KV is added
- **WHEN** the operator confirms a valid absent KV role without selecting an engine
- **THEN** one Dragonfly-backed KV role with HTTP and backups becomes healthy and source records the engine explicitly

#### Scenario: Postgres receives a supplied initial password
- **WHEN** the operator confirms a valid absent Postgres role with a securely supplied initial password
- **THEN** Postgres and PgBouncer become healthy using that exact password for the fixed `default` login without persisting the value outside private managed secret files

#### Scenario: Postgres password is omitted
- **WHEN** the operator confirms a valid absent Postgres role without supplying a password
- **THEN** evdb generates the private initial password and creates the same healthy default Postgres service

#### Scenario: Existing Postgres receives a different password
- **WHEN** an operator reruns add for a matching Postgres role with a supplied password different from the installed credential
- **THEN** evdb performs no mutation and reports that password rotation requires a separate operation

#### Scenario: New creation fails health
- **WHEN** candidate services cannot become healthy
- **THEN** evdb stops candidate services, does not claim installation success, and removes only empty files and data proven to belong to that transaction

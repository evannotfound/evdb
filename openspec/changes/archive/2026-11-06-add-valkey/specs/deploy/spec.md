## MODIFIED Requirements

### Requirement: Idempotent database creation
`evdb database add PROJECT postgres` and `evdb database add PROJECT kv [--engine ENGINE]` SHALL
validate identity, persist explicit role settings, generate or securely accept required credentials in
`secrets.yml`, render role-local files, start Compose, and wait for full native health. Existing matching
roles SHALL not be duplicated. Creation failure SHALL leave readable source and generated files for
inspection and retry rather than running candidate rollback. KV creation SHALL accept mode, supported memory, and HTTP overrides before first startup; conflicting supplied settings on an existing role SHALL be rejected without mutation.

#### Scenario: Default KV is added
- **WHEN** the operator adds an absent KV role without selecting an engine
- **THEN** one durable Valkey-backed KV role with HTTP and backups is recorded and started

#### Scenario: Postgres receives a supplied initial password
- **WHEN** an absent Postgres role is added with a valid securely supplied password
- **THEN** `secrets.yml`, Postgres, and PgBouncer use that exact password for the fixed `default` login

#### Scenario: Postgres password is omitted
- **WHEN** an absent Postgres role is added without a password
- **THEN** evdb generates the password, stores it in `secrets.yml`, and starts the same default service

#### Scenario: Existing Postgres receives a different password
- **WHEN** add is rerun for an existing Postgres role with another supplied password
- **THEN** evdb performs no mutation and reports that password rotation is outside v1

#### Scenario: New creation fails health
- **WHEN** new services cannot become healthy
- **THEN** evdb reports the concrete failure without deleting the configured role, generated files, or data

#### Scenario: Fresh PostgreSQL 18 role is created
- **WHEN** an empty host data directory is used for an official PostgreSQL 18 or newer role
- **THEN** evdb prepares the parent mount for the image's postgres account before Compose starts and the image initializes `<major>/docker`

#### Scenario: Interrupted PostgreSQL 18 creation is retried
- **WHEN** a matching configured role has an empty uninitialized data directory from a failed start
- **THEN** rerunning Add or Start rerenders, prepares, starts, and health-checks the same role without replacing its credentials

## ADDED Requirements

### Requirement: Valkey persistence and runtime
Valkey SHALL use its native server and authenticated CLI, existing KV routing and data mounts, and a configurable memory limit defaulting to 256mb. Durable mode SHALL enable AOF with every-second syncing and noeviction. Cache mode SHALL disable AOF and use allkeys-lru. Both modes SHALL disable automatic RDB snapshots. Graceful stops SHALL allow time to flush persistence. Valkey information SHALL report its actual version rather than the Redis compatibility version.

#### Scenario: Durable Valkey restarts after a crash
- **WHEN** writes have reached durable AOF storage and the process is abruptly terminated and restarted
- **THEN** those writes are recovered from AOF without requiring a scheduled RDB snapshot

#### Scenario: Cache Valkey is created
- **WHEN** Valkey is created in cache mode
- **THEN** AOF and scheduled RDB saves are disabled, allkeys-lru is active, and evdb does not back it up

#### Scenario: Valkey memory is configured
- **WHEN** the memory setting is changed through configure
- **THEN** the running server uses the new limit after the normal Compose and health operation

# Deploy Specification

## Purpose

Define direct host-local database creation, configuration, lifecycle, routing, and health checks.

## Requirements

### Requirement: Direct settings transaction
`evdb database configure PROJECT/ROLE` SHALL collect and validate role-specific values, atomically
update `config.yml`, rerender that role's generated files, run Compose once, and wait for native health.
It SHALL NOT stage a deployment transaction, create a safety backup, snapshot prior files, or recover
the prior service automatically. Guided settings SHALL show changed values and require one save
confirmation; an explicit direct command SHALL execute immediately.

#### Scenario: Operator changes two Dragonfly settings
- **WHEN** memory and threads are saved in one guided session
- **THEN** evdb writes both values, renders once, invokes Compose once, and reports the final health result

#### Scenario: New settings fail health
- **WHEN** the rendered service cannot become healthy
- **THEN** evdb preserves the readable configured values and generated files, reports the failing operation, and permits correction followed by `database start`

### Requirement: Dedicated native routing proxy
evdb SHALL manage one dedicated Traefik Compose project that owns host ports 5432 and 6379, uses the Docker provider on a dedicated external network, carries a concrete healthcheck, and routes every database through a TLS `HostSNI` router and unique role-qualified backend. Postgres and KV roles in the same project MAY share the same SNI hostname because they use separate native entrypoints.

#### Scenario: Project has both roles
- **WHEN** one project runs Postgres and KV on the shared network and clients connect with the same SNI hostname on ports 5432 and 6379
- **THEN** native SNI traffic reaches the matching type-qualified backend without a shared `postgres` or `redis` alias

### Requirement: ACME DNS certificates
Dedicated Traefik SHALL use a configured ACME DNS-01 resolver for native database certificates, a scoped DNS-provider credential in a private host file, and persistent `acme.json` with mode `0600`. It SHALL NOT require or bind ports 80 or 443.

#### Scenario: Native certificate renews
- **WHEN** Traefik completes a DNS-01 renewal
- **THEN** certificate state persists across proxy restarts without exposing the provider token in Compose, status, or logs

### Requirement: Private PgBouncer runtime access
Generated PgBouncer services SHALL run without root under an identity that can read the private
PgBouncer configuration and authentication files generated for that Postgres role. The source password
SHALL live only in `secrets.yml`; generated files SHALL remain private and credential content SHALL
not appear in Compose, status, errors, or logs.

#### Scenario: Default PgBouncer starts from private files
- **WHEN** evdb starts a Postgres role with PgBouncer enabled
- **THEN** PgBouncer reads its role-local generated files, becomes healthy as a non-root process, and accepts the managed Postgres login

#### Scenario: Generated Compose remains secret-free
- **WHEN** evdb renders the Postgres Compose project
- **THEN** the document contains private file paths and runtime identity but no password or authentication-file content

### Requirement: Idempotent database creation
`evdb database add PROJECT postgres` and `evdb database add PROJECT kv [--engine ENGINE]` SHALL
validate identity, persist explicit role settings, generate or securely accept required credentials in
`secrets.yml`, render role-local files, start Compose, and wait for full native health. Existing matching
roles SHALL not be duplicated. Creation failure SHALL leave readable source and generated files for
inspection and retry rather than running candidate rollback.

#### Scenario: Default KV is added
- **WHEN** the operator adds an absent KV role without selecting an engine
- **THEN** one Dragonfly-backed KV role with HTTP and backups is recorded and started

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

### Requirement: Routine lifecycle commands
The CLI SHALL provide `database start`, `stop`, `restart`, and `logs` for one project/role. Start and
restart SHALL rerender role files from current source before invoking Compose and SHALL wait for
container and engine health. Stop SHALL preserve source, generated files, credentials, data, and backup
history. Logs SHALL be bounded and redact exact credential values while preserving other output.

#### Scenario: Database is stopped
- **WHEN** the operator runs `evdb database stop PROJECT/ROLE`
- **THEN** only that role's Compose services stop and its persistent files remain unchanged

#### Scenario: Configured role is retried
- **WHEN** an operator corrects source after a failed start and runs `database start`
- **THEN** evdb rerenders the corrected role and starts it without requiring a creation transaction

#### Scenario: Logs are requested
- **WHEN** the operator requests bounded logs
- **THEN** evdb prints the selected Compose project's original log text except for exact managed credential values

### Requirement: Unambiguous selectors
Database commands SHALL use `<project>/postgres` or `<project>/kv`. Guided flows MAY select the same identity from a numbered project list. A project name alone SHALL be accepted only when exactly one role exists and no credential-bearing output can be disclosed ambiguously.

#### Scenario: Project has two roles
- **WHEN** an operator supplies only the project name
- **THEN** evdb performs no mutation and lists the valid project/role identities

### Requirement: Production guardrails
Write operations SHALL run on the authoritative host, validate complete canonical source, use
subprocess argument arrays and relevant host, database, or repository locks, and name the affected host
and project/role. Guided source edits SHALL require one final save or create confirmation. Explicit
direct lifecycle and backup commands SHALL execute without a generic `--yes` confirmation layer.

#### Scenario: Source is invalid
- **WHEN** a write command loads invalid `config.yml` or `secrets.yml`
- **THEN** it fails before writing generated files or invoking Docker, Restic, or systemd

### Requirement: Production paths
Canonical evdb source SHALL live under `/etc/evdb`; generated services, Traefik assets, local backups,
locks, and fixed database data SHALL live under `/var/lib/evdb`; and the verified tool SHALL be the
regular executable `/usr/local/bin/evdb`. No copied rclone file, deployment machine state, activity
record, restore staging, transaction tree, or configurable data root SHALL be created.

#### Scenario: Tool version changes
- **WHEN** the verified installer atomically replaces `/usr/local/bin/evdb`
- **THEN** every database continues using stable source, generated, credential, backup, and data paths

### Requirement: Mutable rclone configuration
Initialization SHALL validate and persist the absolute path of a provided private native rclone file.
evdb SHALL use that file in place and SHALL NOT copy, replace, chown, or regenerate it from
`secrets.yml` during initialization, database changes, or installer updates. The file SHALL remain
owned and atomically writable by its non-root operator; evdb repository subprocesses SHALL use that
identity so token refreshes preserve normal manual rclone use.

#### Scenario: Live OAuth token changed
- **WHEN** initialization runs after rclone refreshes its token
- **THEN** evdb continues using the configured host file without creating another rclone configuration

### Requirement: Generated Compose and shared routing
The system SHALL generate independent Compose YAML for every Postgres and KV role plus dedicated
Traefik. Generated services SHALL use configured non-`latest` images directly and SHALL reference
private role-local files without embedding credentials. Only dedicated Traefik SHALL publish native
database ports. Services SHALL use role-qualified projects and unique network identities without
service contract hash labels.

#### Scenario: Generated Compose validates
- **WHEN** a database or Traefik definition is rendered
- **THEN** `docker compose config --quiet` succeeds before Compose is started

#### Scenario: Two databases share a native port
- **WHEN** several Postgres or KV roles run on one host
- **THEN** Traefik SNI routes each hostname on the role's native port to its unique backend

#### Scenario: One project shares one hostname across roles
- **WHEN** one project contains Postgres and KV
- **THEN** separate entrypoints route the shared hostname to distinct role-qualified services

### Requirement: Local deployment test
Generated files, direct settings changes, each concrete engine, PgBouncer, HTTP, Traefik, host
initialization, and installer updates SHALL be tested on disposable infrastructure. Tests SHALL NOT
invoke production hosts, repositories, credentials, Docker changes, or systemd changes on
`montreal-01`.

#### Scenario: Full disposable operation completes
- **WHEN** the integration suite creates and reconfigures test databases
- **THEN** it proves independent projects, routing, native health, direct retry behavior, and checked backups without production resources

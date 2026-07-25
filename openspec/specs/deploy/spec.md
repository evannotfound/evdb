# Deploy Specification

## Purpose

Define safe remote management, immutable deployment releases, health-gated activation, and release rollback.

## Requirements

### Requirement: Local operator command
The system SHALL provide an `evdb` command that reads local source configuration and manages the configured host over SSH without requiring operators to invoke Ansible or host-local Python commands directly.

#### Scenario: Operator requests status
- **WHEN** the operator runs `evdb status`
- **THEN** the command connects to the host declared in `host.yml` and prints the remote status result locally

### Requirement: Safe SSH transport
The controller SHALL use subprocess argument arrays and a fixed versioned remote command. Normal operations SHALL execute code and runtime configuration through `/opt/evanovation-db/current`. A separate `/opt/evanovation-db/host-runtime` mode SHALL read an atomically refreshed normalized runtime from `/opt/evanovation-db/host-runtime/runtime` only after explicit confirmation for first install or protocol upgrade.

The transport SHALL NOT read preserved `/etc` legacy JSON, use a shell command string, or place secret values in SSH arguments.

#### Scenario: Remote operation is invoked
- **WHEN** the controller sends an operation to the host
- **THEN** operation data is validated JSON over stdin and stdout and the process argument list contains no secret value

#### Scenario: Active runtime is unavailable
- **WHEN** the fixed active runtime is explicitly absent or reports an incompatible protocol
- **THEN** confirmed bootstrap installs stable code, atomically refreshes its dedicated normalized runtime, removes stale bootstrap instance JSON, and release-state plus apply use it until the candidate release activates

#### Scenario: Existing release uses an older runtime schema
- **WHEN** protocol mismatch triggers confirmed bootstrap on a host with active timers and legacy active runtime JSON
- **THEN** bootstrap leaves current release units, pointer, `/etc` runtime, and timer state unchanged until a healthy candidate activates transactionally

#### Scenario: Remote response is malformed
- **WHEN** SSH fails, output is malformed, active configuration is invalid, or a remote operation fails
- **THEN** the controller fails closed without invoking Ansible or treating the error as a bootstrap condition

### Requirement: Read-only plan
`evdb plan` SHALL compare normalized desired configuration with the active remote release and live state without changing source, lock, secrets, releases, services, containers, or data.

#### Scenario: New database is planned
- **WHEN** source contains a database absent from the active release
- **THEN** plan reports a create action and performs no remote write

#### Scenario: Deployed database was removed from source
- **WHEN** the active release contains a database absent from source
- **THEN** plan reports the removal as blocked and directs the operator to a future retirement workflow

### Requirement: Immutable deployment releases
Each apply SHALL stage an immutable release containing application code, normalized secret-free runtime configuration, generated Compose files, canonical systemd units, the image lock, and a manifest of every expected database service and container, engine major versions, shared `traefik-net`, and Traefik. Every managed Compose service SHALL carry its non-circular generated project service-contract hash label recorded in the manifest.

Mutable data, secrets, logs, and operation state SHALL remain outside releases.

#### Scenario: Release is staged
- **WHEN** apply prepares a deployment
- **THEN** every artifact needed to reproduce its service definitions is stored under one unique release id without copying secret values or database data

### Requirement: Confirmed health-gated apply
`evdb apply` SHALL show the plan, require interactive confirmation unless `--yes` is supplied, stage a release, apply only affected Compose projects, and activate the release only after validation and health checks succeed.

Apply SHALL inspect and idempotently create the shared network when absent, require a concrete healthy Docker health status for Traefik before databases, and require every expected primary and sidecar container plus engine-native database health. Missing, stopped, unhealthy, wrong-image, or wrong-contract sidecars SHALL be affected even when the primary image is unchanged.

#### Scenario: Operator declines apply
- **WHEN** the operator does not confirm the displayed production plan
- **THEN** no lock, secret, release, service, container, or data change occurs

#### Scenario: Apply succeeds
- **WHEN** all staged configuration and affected services validate and become healthy
- **THEN** the new release becomes active and unchanged database projects are not restarted

#### Scenario: Fresh host has no shared network
- **WHEN** initial apply finds `docker network inspect traefik-net` absent
- **THEN** it creates the network before Traefik and database services

### Requirement: Automatic failed-apply recovery
If an affected project fails to become healthy, apply SHALL restore the prior release's Traefik and database definitions and images for changed projects, verify recovered health, and leave the prior release active.

Before candidate secret installation, apply SHALL snapshot every target's bytes, mode, ownership, and existence in memory. Compose validation, health, activation, or recovery failure SHALL restore exact prior files and remove newly created files before prior Compose is restarted. Secret contents SHALL never be persisted in release or recovery state. Existing database credentials and protected configuration SHALL NOT change during apply; new databases and missing files MAY be installed. Restic password replacement and existing rclone preservation semantics SHALL remain unchanged.

#### Scenario: New image fails health check
- **WHEN** an updated database container does not become healthy within its timeout
- **THEN** the prior locked image and Compose definition are restored without changing database data

#### Scenario: Recovery also fails
- **WHEN** the prior service cannot be restored automatically
- **THEN** apply retains both releases, records a high-severity failure with recovery steps, and exits nonzero without claiming either unhealthy release as successfully active

#### Scenario: Release assets activate
- **WHEN** every affected service is healthy
- **THEN** apply snapshots prior unit files, runtime files, and active pointer, installs the staged canonical units, switches runtime and pointer, reloads systemd, and preserves each timer's enabled and running state

#### Scenario: Unit activation fails
- **WHEN** unit installation, runtime refresh, pointer replacement, or daemon reload fails
- **THEN** prior unit bytes and modes, active runtime, pointer, and loaded definitions are restored before prior services are recovered

### Requirement: Idempotent database creation
`evdb create <type> <name>` SHALL atomically add a minimal source entry, ensure convention-based 1Password fields exist, display the resulting plan, and use the normal apply path after confirmation. Rerunning an interrupted create SHALL converge without duplicating configuration or secret items.

#### Scenario: New database is created
- **WHEN** the operator confirms creation of a valid absent database
- **THEN** source contains one minimal entry, 1Password contains one managed item, and the healthy deployment is active

#### Scenario: Remote apply fails after local creation
- **WHEN** configuration and secrets were created but remote deployment fails
- **THEN** desired configuration and the secret item remain intact and the command can be rerun safely

### Requirement: Secure 1Password writes
Create workflows SHALL require a write-capable desktop or service-account 1Password session, send item templates and secret material through stdin, and suppress secret-bearing output and logs.

#### Scenario: Connect-only authentication is active
- **WHEN** a create workflow detects Connect-only environment variables without write-capable authentication
- **THEN** it fails before source or remote changes and explains the required authentication mode

#### Scenario: Existing managed item is found
- **WHEN** the convention-based item and required fields already exist
- **THEN** create reuses them without rotating or revealing their values

### Requirement: Routine lifecycle commands
The controller SHALL provide start, stop, restart, and logs commands for a selected database. These operations SHALL retain data, configuration, secrets, and backups.

#### Scenario: Database is stopped
- **WHEN** the operator confirms a stop command
- **THEN** the database project stops while its data, release configuration, secrets, and backups remain present

#### Scenario: Logs are requested
- **WHEN** the operator requests logs for a database
- **THEN** the controller streams that project's remote logs without exposing protected secret files

### Requirement: Unambiguous selectors
Commands SHALL accept a plain name when it resolves to one database and SHALL require `<type>/<name>` when more than one configured database shares the name.

#### Scenario: Plain name is ambiguous
- **WHEN** an operator selects a name used by Postgres and KV databases
- **THEN** the command performs no operation and lists the valid typed selectors

### Requirement: Production guardrails
Write operations SHALL identify the target host, show affected databases and host infrastructure, serialize conflicting operations with locks, and require explicit confirmation or `--yes`. Generated inventory for the real target SHALL retain production group membership and target production so mutable extra variables cannot downgrade the guard. Operations SHALL fail closed when controller and remote protocol versions are incompatible.

#### Scenario: Protocol versions differ
- **WHEN** the local controller cannot safely communicate with the deployed runtime
- **THEN** the operation stops before any remote write and requests installation of a compatible release

### Requirement: Production paths
Immutable releases SHALL live under `/opt/evanovation-db/releases`, the active release SHALL be addressed through `/opt/evanovation-db/current`, confirmed bootstrap assets SHALL live under `/opt/evanovation-db/host-runtime`, protected host files SHALL live under `/etc/evanovation-db/secrets`, and mutable backup and operation state SHALL live under `/var/lib/evanovation-db`.

#### Scenario: Secret file is installed
- **WHEN** apply writes a resolved secret required by a new database
- **THEN** the file is owned by the service account with mode `0600` and is not stored in the release

### Requirement: Mutable rclone configuration
Apply SHALL seed the live rclone configuration from 1Password only when it is absent. Once created, the live file SHALL be owned by the service account and SHALL NOT be overwritten during routine applies because rclone updates its OAuth token.

#### Scenario: Live token has changed
- **WHEN** apply runs after rclone refreshed its token
- **THEN** the existing live configuration remains unchanged

### Requirement: Service account
Deployed jobs SHALL run as a dedicated service account with access to Docker and only the files required by this system. Documentation SHALL state that Docker access is root-equivalent.

#### Scenario: Backup service starts
- **WHEN** systemd starts a backup unit
- **THEN** it runs as the service account rather than as an interactive user

### Requirement: Generated Compose and shared routing
Each release SHALL contain generated Compose definitions for Postgres, Redis, Dragonfly, HTTP sidecars, and Traefik using locked images. Only Traefik SHALL publish host database ports 5432 and 6379; database and pooler containers SHALL remain internal.

Traefik SHALL use TLS `HostSNI` routes to send Postgres traffic to the matching PgBouncer and KV traffic to the matching Redis or Dragonfly container by a database-specific backend name.

#### Scenario: Two Postgres databases share port 5432
- **WHEN** clients connect to two configured Postgres domains on host port 5432
- **THEN** TLS SNI sends each connection to that domain's PgBouncer and database

#### Scenario: Two KV databases share port 6379
- **WHEN** clients connect to two configured KV domains on host port 6379
- **THEN** TLS SNI sends each connection to that domain's Redis or Dragonfly container

#### Scenario: Shared Redis alias is used
- **WHEN** a Traefik KV route points to a shared name such as `redis:6379`
- **THEN** validation fails and requires the database-specific backend name

### Requirement: Explicit release rollback
`evdb rollback [release]` SHALL default to the previous active release, display affected service and image changes, require confirmation, health-check restored projects, and activate the selected release only on success.

#### Scenario: Previous release is rolled back
- **WHEN** the operator confirms a compatible previous release
- **THEN** its service definitions and locked images become active while current database data remains in place

### Requirement: Data-format compatibility guard
Rollback SHALL compare engine types and major versions with live data and SHALL block a release whose engine cannot safely open the existing data format.

#### Scenario: Postgres major downgrade is requested
- **WHEN** rollback would run an older Postgres major version against data initialized by a newer major version
- **THEN** rollback fails before stopping the live database and directs the operator to backup restore instead

### Requirement: Release audit and retention
The system SHALL record creation time, source and lock hashes, controller version, active predecessor, plan summary, and outcome for every staged release. It SHALL retain the active and previous successful releases and SHALL never remove a failed or recovery-relevant release automatically.

#### Scenario: Operator inspects releases
- **WHEN** release history is requested
- **THEN** it identifies active, successful, failed, and rolled-back releases without exposing secrets

### Requirement: Rollback does not restore data
Release rollback SHALL NOT replace, rename, restore, or delete a database data directory or select a backup snapshot.

#### Scenario: Operator needs older data
- **WHEN** the selected release is healthy but the operator needs database contents from an earlier time
- **THEN** rollback leaves data unchanged and directs the operator to the staged restore workflow

### Requirement: Local deployment test
Generated Compose, normalized runtime configuration, systemd units, remote transport, apply, and recovery SHALL be tested on disposable local infrastructure before deployment behavior is accepted.

#### Scenario: Full local deploy runs
- **WHEN** the deployment integration test finishes
- **THEN** the test host can validate configuration and run local backup and restore operations without production credentials

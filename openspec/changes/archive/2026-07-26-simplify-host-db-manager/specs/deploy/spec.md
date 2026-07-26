## ADDED Requirements

### Requirement: Direct settings transaction
`evdb database configure PROJECT/ROLE` SHALL collect and validate typed settings, render private candidate files, resolve immutable image digests, validate candidate Compose, preview exact changes and restart effects, and require confirmation before changing installed files or services. It SHALL perform at most one restart for all changes in one session.

#### Scenario: Operator changes two Dragonfly settings
- **WHEN** memory and threads change in one confirmed session
- **THEN** evdb installs one generated definition and restarts the KV project once

### Requirement: Safety backup before durable service recreation
A confirmed settings transaction that recreates the primary container of a durable database SHALL create, validate, and upload a current backup before stopping or replacing that service. Cache-mode KV and sidecar-only changes SHALL not claim to have created a database recovery point when no backup ran.

#### Scenario: Durable Postgres image is updated
- **WHEN** a compatible same-major image change requires container recreation
- **THEN** a confirmed Restic snapshot of current data exists before Compose replaces the container

### Requirement: Failed settings recovery
Before installing a settings transaction, evdb SHALL retain exact prior source, generated files, secret-file metadata, and resolved state. If candidate validation, startup, or health fails after mutation starts, it SHALL restore the previous files and same-engine Compose definition, restart affected prior services, and verify their health without prompting.

#### Scenario: New image is unhealthy
- **WHEN** a configured same-major image cannot pass container and engine health
- **THEN** evdb restores the prior image definition and settings and reports whether prior health recovered

### Requirement: Settings activity record
Every confirmed settings transaction SHALL append a bounded secret-free activity record containing host, project/role, command, changed setting names, start and finish times, result, and recovery result. It SHALL NOT retain a selectable deployment release.

#### Scenario: Settings recovery succeeds
- **WHEN** candidate health fails and previous settings recover
- **THEN** activity reports the failed change and successful recovery without storing prior secret values

### Requirement: Unsupported role removal is non-destructive
Omitting an installed project role from source configuration SHALL NOT stop or remove its containers, route, Compose, secrets, machine state, backups, or data. Host check and status SHALL report an unsupported removal or orphan condition, and every mutating command SHALL fail closed until source is restored or a future retirement workflow handles the role.

#### Scenario: Installed role is removed from YAML
- **WHEN** host source no longer contains a project/role that still has installed generated state
- **THEN** evdb leaves all service and persistent assets unchanged and reports that database removal is unsupported

### Requirement: Dedicated native routing proxy
evdb SHALL manage one dedicated Traefik Compose project that owns host ports 5432 and 6379, uses the Docker provider on a dedicated external network, carries a concrete healthcheck, and routes every database through a unique TLS `HostSNI` router and backend.

#### Scenario: Project has both roles
- **WHEN** one project runs Postgres and KV on the shared network
- **THEN** native SNI traffic reaches the matching type-qualified backend without a shared `postgres` or `redis` alias

### Requirement: ACME DNS certificates
Dedicated Traefik SHALL use a configured ACME DNS-01 resolver for native database certificates, a scoped DNS-provider credential in a private host file, and persistent `acme.json` with mode `0600`. It SHALL NOT require or bind ports 80 or 443.

#### Scenario: Native certificate renews
- **WHEN** Traefik completes a DNS-01 renewal
- **THEN** certificate state persists across proxy restarts without exposing the provider token in Compose, status, or logs

## MODIFIED Requirements

### Requirement: Idempotent database creation
`evdb database add PROJECT postgres` and `evdb database add PROJECT kv [--engine ENGINE]` SHALL validate identity, select and persist explicit defaults, generate required private host credentials, render and validate an independent Compose project, preview the operation, require confirmation, start services, and require full health before committing successful installation state. Rerunning an interrupted or matching add SHALL not duplicate a role, data directory, secret, project, or route.

#### Scenario: Default KV is added
- **WHEN** the operator confirms a valid absent KV role without selecting an engine
- **THEN** one Dragonfly-backed KV role with HTTP and backups becomes healthy and source records the engine explicitly

#### Scenario: New creation fails health
- **WHEN** candidate services cannot become healthy
- **THEN** evdb stops candidate services, does not claim installation success, and removes only empty files and data proven to belong to that transaction

### Requirement: Routine lifecycle commands
The host CLI SHALL provide `database start`, `stop`, `restart`, and `logs` for one project/role. Start SHALL use installed generated Compose and reconcile stopped services; stop and restart SHALL preserve source, generated definitions, secrets, data, backup history, and tool history. Start and restart SHALL require container, sidecar, contract, and engine health.

#### Scenario: Database is stopped
- **WHEN** the operator confirms `evdb database stop PROJECT/ROLE`
- **THEN** only that role's Compose services stop while its project sibling role and all persistent files remain unchanged

#### Scenario: Logs are requested
- **WHEN** the operator requests bounded logs
- **THEN** evdb reads that Compose project's logs and redacts credentials, URLs, references, and secret-bearing fields

### Requirement: Unambiguous selectors
Database commands SHALL use `<project>/postgres` or `<project>/kv`. Guided flows MAY select the same identity from a numbered project list. A project name alone SHALL be accepted only when exactly one role exists and no credential-bearing output can be disclosed ambiguously.

#### Scenario: Project has two roles
- **WHEN** an operator supplies only the project name
- **THEN** evdb performs no mutation and lists the valid project/role identities

### Requirement: Production guardrails
Write operations SHALL run on the authoritative host, display its configured host ID and affected project/role or host infrastructure, acquire compatible host/database/repository locks, and require explicit confirmation or `--yes`. They SHALL fail closed on invalid config, unsafe ownership, incompatible schemas, missing prerequisites, or changed operation state.

#### Scenario: Host state changes during confirmation
- **WHEN** installed source or generated files differ from the previewed candidate before mutation
- **THEN** evdb aborts and asks the operator to run the command again

### Requirement: Production paths
Host source and generated service files SHALL live under `/etc/evdb`, mutable state under `/var/lib/evdb`, managed tool versions under `/opt/evdb`, and database data under the configured project-first data root. Secrets and mutable state SHALL remain outside tool versions and generated Compose.

#### Scenario: Tool updates
- **WHEN** `/opt/evdb/current` changes versions
- **THEN** every database continues using stable Compose, secret, state, and data paths

### Requirement: Mutable rclone configuration
Host setup SHALL seed live rclone configuration only when absent. Once created, the service account-owned file under `/var/lib/evdb/rclone` SHALL remain mutable and SHALL NOT be overwritten by setup, settings changes, tool updates, or generated Compose because rclone may refresh OAuth data.

#### Scenario: Live OAuth token changed
- **WHEN** setup or tool update runs after rclone refreshes its token
- **THEN** the existing live rclone file remains byte-for-byte unchanged

### Requirement: Service account
Database jobs and routine operations SHALL run under the dedicated non-login evdb service account where privilege permits. Setup SHALL grant only required file access and Docker group membership; documentation SHALL identify Docker access as root-equivalent. Privileged setup and tool activation SHALL require root.

#### Scenario: Restore verification starts
- **WHEN** systemd or an operator starts a backup test
- **THEN** temporary Docker work and private files use the evdb service identity rather than an interactive user's home

### Requirement: Generated Compose and shared routing
The system SHALL generate independent YAML Compose definitions for every Postgres and KV role plus dedicated Traefik. Generated images SHALL resolve to immutable digests; no secret value SHALL appear in YAML. Only dedicated Traefik SHALL publish native database ports. Postgres, PgBouncer, Redis, Dragonfly, and HTTP sidecars SHALL use type-qualified projects and unique network identities.

#### Scenario: Generated Compose validates
- **WHEN** a candidate database or Traefik definition is rendered
- **THEN** `docker compose config --quiet` succeeds before the file is installed

#### Scenario: Two databases share native port
- **WHEN** several Postgres or KV roles run on one host
- **THEN** Traefik SNI routes each hostname to its unique database-specific backend

### Requirement: Data-format compatibility guard
Settings operations SHALL compare the installed role, concrete engine, source image major, backup compatibility, and live data contract. They SHALL reject engine replacement and engine-major changes before stopping a service. Compatible same-engine image updates MAY proceed through the safety-backup transaction.

#### Scenario: Postgres major changes
- **WHEN** an operator selects a Postgres image with another major version
- **THEN** evdb rejects the settings change and identifies major migration as a future explicit workflow

#### Scenario: KV implementation changes
- **WHEN** an operator attempts to change an installed KV role from Redis to Dragonfly
- **THEN** evdb refuses to treat the conversion as a settings update

### Requirement: Local deployment test
Generated YAML, direct settings transactions, failed-settings recovery, dedicated Traefik, host setup, and tool updates SHALL be tested on disposable local infrastructure. Tests SHALL NOT invoke production hosts, repositories, credentials, Docker changes, cron changes, or systemd changes on `montreal-01`.

#### Scenario: Full disposable operation completes
- **WHEN** the integration suite adds and configures test databases
- **THEN** it proves independent projects, native routing, health gates, safety files, and failure recovery without production resources

## REMOVED Requirements

### Requirement: Local operator command
**Reason**: The authoritative command now runs on the database host; SSH is an operator transport rather than a controller architecture.
**Migration**: Invoke the installed host CLI directly or through ordinary SSH.

### Requirement: Safe SSH transport
**Reason**: The custom versioned SSH JSON protocol and dual runtimes are removed.
**Migration**: Automation runs explicit non-interactive evdb commands and consumes secret-free JSON.

### Requirement: Read-only plan
**Reason**: Each concrete command previews only its own complete operation.
**Migration**: Use guided or explicit database, backup, restore, setup, and update commands.

### Requirement: Immutable deployment releases
**Reason**: Host-wide code/config/Compose releases obscured independent persistent database operations.
**Migration**: Use stable generated files, one previous settings transaction, exact tool versions, and verified data backups.

### Requirement: Confirmed health-gated apply
**Reason**: There is no generic desired-state apply operation.
**Migration**: Every mutating command validates, confirms, applies, and health-checks its direct target.

### Requirement: Automatic failed-apply recovery
**Reason**: Recovery is now scoped to the database settings transaction that failed.
**Migration**: Direct settings recovery restores one role's prior source and Compose definition.

### Requirement: Secure 1Password writes
**Reason**: Host-owned credentials replace workstation 1Password creation.
**Migration**: Password export and production credential import are separate changes.

### Requirement: Explicit release rollback
**Reason**: Deployment release rollback is removed; it never restored database contents.
**Migration**: Use exact tool-version recovery for software and `evdb restore` for database data.

### Requirement: Release audit and retention
**Reason**: The system no longer stages deployment releases.
**Migration**: Use bounded activity records, one previous source copy, Restic backup history, and one previous tool version.

### Requirement: Rollback does not restore data
**Reason**: The ambiguous rollback command no longer exists.
**Migration**: Operators use the explicit restore workflow when older data is required.

## ADDED Requirements

### Requirement: Immutable deployment releases
Each apply SHALL stage an immutable release containing application code, normalized secret-free runtime configuration, generated Compose files, canonical systemd units, the image lock, and a manifest of every expected database service/container, engine major versions, shared `traefik-net`, and Traefik. Every managed Compose service SHALL carry its non-circular generated project service-contract hash label recorded in the manifest. Mutable data, secrets, logs, and operation state SHALL remain outside releases.

#### Scenario: Release is staged
- **WHEN** apply prepares a deployment
- **THEN** every artifact needed to reproduce its service definitions is stored under one unique release id without copying secret values or database data

### Requirement: Health-gated activation
Apply SHALL validate the staged release, inspect and idempotently create the shared network when absent, apply only affected Compose projects, apply and require a concrete healthy Docker health status for Traefik before databases, and require every expected primary and sidecar container plus engine-native database health before making the release active. Missing, stopped, unhealthy, wrong-image, or wrong-contract sidecars SHALL be affected even when the primary image is unchanged.

#### Scenario: Changed database becomes healthy
- **WHEN** the staged service starts and passes its health check
- **THEN** apply activates the staged release after all other affected services also pass

#### Scenario: Unchanged database is present
- **WHEN** a database's generated service definition and locked image are unchanged
- **THEN** apply does not restart that database

#### Scenario: Fresh host has no shared network
- **WHEN** initial apply finds `docker network inspect traefik-net` absent
- **THEN** it creates the network before Traefik and database services

### Requirement: Automatic failed-apply recovery
If an affected project fails to become healthy, apply SHALL restore the prior release's Traefik and database service definitions and images for changed projects, verify recovered health, and leave the prior release active.

Before candidate secret installation, apply SHALL snapshot every target's bytes, mode, ownership, and existence in memory. Compose validation, health, activation, or recovery failure SHALL restore exact prior files and remove newly created files before prior Compose is restarted. Secret contents SHALL never be persisted in release or recovery state. A secret restoration failure SHALL produce high-severity recovery state.

Existing deployed database credential and protected configuration files SHALL NOT change during apply because credential rotation is outside this change. New databases and missing files MAY be installed. Restic password replacement and existing rclone preservation semantics SHALL remain unchanged.

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

### Requirement: Explicit release rollback
`evdb rollback [release]` SHALL default to the previous active release, display the affected service and image changes, require confirmation, health-check the restored projects, and activate the selected release only on success.

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

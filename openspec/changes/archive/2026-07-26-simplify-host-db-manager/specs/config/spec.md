## ADDED Requirements

### Requirement: Project role catalog
Host configuration SHALL organize managed databases under project IDs. A project SHALL contain at most one `postgres` role and at most one `kv` role, and each database SHALL be addressed as `<project>/<role>`.

#### Scenario: Project uses both database roles
- **WHEN** `code-share-prod-01` contains Postgres and Dragonfly-backed KV settings
- **THEN** configuration exposes exactly `code-share-prod-01/postgres` and `code-share-prod-01/kv`

#### Scenario: Duplicate project role is configured
- **WHEN** one project declares two Postgres roles or two KV roles
- **THEN** validation fails before generated files or live services change

### Requirement: Stable KV role and concrete engine
The KV role SHALL record a concrete engine of `dragonfly` or `redis`. Creation SHALL default an omitted KV engine to Dragonfly and SHALL persist the resolved engine explicitly. Redis and Dragonfly SHALL remain distinct engines for runtime, backup, restore, and compatibility checks.

#### Scenario: Default KV is created
- **WHEN** the operator adds `example-prod-01/kv` without an engine option
- **THEN** source records `engine: dragonfly` and generated Compose uses Dragonfly

### Requirement: Per-database image sources
Each database SHALL record its own primary image source and any enabled PgBouncer or HTTP sidecar image source. Creation MAY begin from built-in defaults but SHALL persist resolved source choices so later default changes do not alter existing databases.

#### Scenario: One Postgres image changes
- **WHEN** the operator updates the image source for one project's Postgres role
- **THEN** no other project's source configuration or generated Compose changes

### Requirement: Canonical host paths
Source configuration SHALL live at `/etc/evdb/host.yml`, generated database assets under `/etc/evdb/projects/<project>/<role>`, dedicated Traefik assets under `/etc/evdb/traefik`, private secrets under `/etc/evdb/secrets`, mutable operational state under `/var/lib/evdb`, and data under `<data_root>/<project>/<role>/data`.

#### Scenario: Postgres paths are derived
- **WHEN** project `example-prod-01` has a Postgres role
- **THEN** its Compose path is `/etc/evdb/projects/example-prod-01/postgres/compose.yaml` and its data path ends in `/example-prod-01/postgres/data`

### Requirement: Machine-owned host state
The system SHALL store resolved image digests, stable HTTP loopback ports, schema versions, and operation results in private machine-owned state under `/var/lib/evdb/state`. Operators SHALL NOT need to edit or commit this state.

#### Scenario: New KV receives an HTTP port
- **WHEN** an HTTP-enabled KV role is added
- **THEN** evdb allocates an unused loopback port and preserves it across ordering changes and tool updates

### Requirement: Command-owned atomic configuration
Normal configuration changes SHALL occur through typed evdb commands. A command SHALL validate a complete candidate, write source and generated files atomically, retain one `/etc/evdb/host.previous.yml`, and append a secret-free activity record. It SHALL NOT retain user-selectable configuration releases.

#### Scenario: Configuration write is interrupted
- **WHEN** a process stops before the atomic replacement
- **THEN** the prior complete host configuration remains readable and active

## MODIFIED Requirements

### Requirement: Single human-owned host configuration
Each managed host SHALL have one readable `/etc/evdb/host.yml` containing host settings and every project database role. The host-local CLI SHALL be authoritative for normal changes; no workstation source file, separate Postgres/KV file, generated runtime copy, or active release SHALL be required to operate the host.

#### Scenario: Operator reviews a host
- **WHEN** the operator opens `/etc/evdb/host.yml`
- **THEN** host settings, projects, roles, engines, images, and explicit overrides are visible without reading machine state or Compose

### Requirement: Minimal database entries
A Postgres role SHALL require its project identity and persisted image source. A KV role SHALL require its project identity, persisted concrete engine, and image source; command creation defaults the engine to Dragonfly. Safe defaults SHALL supply PgBouncer, durability, HTTP, pool sizing, Dragonfly resources, and connection limits when no override is selected.

#### Scenario: Minimal Postgres role is created
- **WHEN** the operator accepts creation defaults for `example-prod-01/postgres`
- **THEN** source records an independently imaged durable Postgres role with backups and PgBouncer enabled

#### Scenario: Minimal KV role is created
- **WHEN** the operator accepts creation defaults for `example-prod-01/kv`
- **THEN** source records a durable Dragonfly role with backups and authenticated HTTP enabled

### Requirement: Derived deployment values
The system SHALL derive type-qualified Compose project names, unique service and network aliases, data and generated-file paths, SNI domains, default settings, backup behavior, HTTP behavior, and secret-file paths from host settings plus each project role. Compose project identity SHALL remain distinct when one project has both roles.

#### Scenario: Two roles share one project
- **WHEN** one project has Postgres and KV
- **THEN** generated project names are `evdb-<project>-postgres` and `evdb-<project>-kv` and Docker does not merge their Compose metadata

### Requirement: Explicit exceptional settings
Configuration SHALL accept typed overrides for KV durability mode, enabled HTTP, HTTP connections, PgBouncer use and sizing, Dragonfly memory and threads, and per-database images. Invalid settings for a role or concrete engine SHALL be rejected and omitted from its guided settings menu.

#### Scenario: Redis receives Dragonfly threads
- **WHEN** a Redis-backed KV role contains a Dragonfly thread override
- **THEN** validation names the invalid field and no file or service changes

### Requirement: Machine-owned runtime and observed state
Resolved digests, allocated ports, operation outcomes, installed tool version, generated Compose hashes, and live Docker observations SHALL remain outside `host.yml`. Live observations SHALL be recomputed and SHALL NOT become desired source fields.

#### Scenario: Running image differs from installed Compose
- **WHEN** Docker reports an image digest unlike the generated definition
- **THEN** status reports that configuration differs without rewriting source configuration

### Requirement: Configuration validation
Validation SHALL reject unsafe project IDs, missing required environment suffixes, duplicate roles, unsupported KV engines, unsafe paths, colliding project/service/domain/port identities, unversioned, `latest`, or invalid image sources, invalid role-specific overrides, unsupported engine-major changes, and secret values in source or machine state. Explicit version tags SHALL be permitted only when they resolve to immutable digests in machine state.

#### Scenario: Project suffix is absent
- **WHEN** a project ID does not end in `-dev-N`, `-test-N`, or `-prod-N`
- **THEN** validation fails before candidate rendering

### Requirement: Old source schema is not supported
The loader SHALL reject the controller-owned `databases` list, engine-first identities, `current` or `target` fields, separate Postgres/KV files, checked-in `host.lock.json`, and migration-only release fields. Production conversion SHALL be performed only by the separate approved migration change.

#### Scenario: Controller source is supplied
- **WHEN** configuration contains the old top-level `databases` list
- **THEN** host setup or checking fails with guidance to use project roles

## REMOVED Requirements

### Requirement: Managed database catalog
**Reason**: Production inventory no longer belongs in a repository-owned source contract; each host owns its project catalog.
**Migration**: Inventory and conversion of the 25 production databases is specified by the separate production migration change.

### Requirement: Host-level image versions
**Reason**: Existing databases must update independently and persist their own primary and sidecar image sources.
**Migration**: Creation defaults seed each role, and production migration copies current concrete image choices into every project role.

### Requirement: Generated reproducibility lock
**Reason**: A checked-in lock beside controller source is incompatible with host-owned command updates.
**Migration**: Resolved digests and stable allocated ports move to private machine-owned host state.

### Requirement: Convention-based secret references
**Reason**: The host generates and reads local service files; 1Password references and controller resolution are removed.
**Migration**: Secret import and password recovery are deferred to a separate design and production migration.

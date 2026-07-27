# Config Specification

## Purpose

Define the concise human-owned host configuration, generated reproducibility lock, and validation contract.

## Requirements

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
The system SHALL store resolved image digests, stable HTTP loopback ports, schema versions, generated Compose hashes, installed flags, installed tool version, and operation results in private machine-owned state under `/var/lib/evdb/state`. Operators SHALL NOT need to edit or commit this state. State loading SHALL require the complete current writer-owned schema for the recorded version and SHALL reject missing or malformed fields instead of synthesizing compatibility defaults. Image engine majors SHALL be derived from each image source when needed rather than stored as independent state.

#### Scenario: New KV receives an HTTP port
- **WHEN** an HTTP-enabled KV role is added
- **THEN** evdb allocates an unused loopback port and preserves it across ordering changes and tool updates

#### Scenario: Partial machine state is supplied
- **WHEN** a machine state file omits current-version writer-owned fields such as images, roles, installed flags, operation records, generated Compose hashes, or tool version
- **THEN** validation fails before generated files or live services change

### Requirement: Command-owned atomic configuration
Normal configuration changes SHALL occur through typed evdb commands. A command SHALL validate a complete candidate, write source and generated files atomically, retain one `/etc/evdb/host.previous.yml`, and append a secret-free activity record. It SHALL NOT retain user-selectable configuration releases.

#### Scenario: Configuration write is interrupted
- **WHEN** a process stops before the atomic replacement
- **THEN** the prior complete host configuration remains readable and active

### Requirement: Single human-owned host configuration
Each managed host SHALL have one readable `/etc/evdb/host.yml` containing host settings and every project database role. The host-local CLI SHALL be authoritative for normal changes; no workstation source file, separate Postgres/KV file, generated runtime copy, or active release SHALL be required to operate the host.

#### Scenario: Operator reviews a host
- **WHEN** the operator opens `/etc/evdb/host.yml`
- **THEN** host settings, projects, roles, engines, images, and explicit overrides are visible without reading machine state or Compose

### Requirement: Minimal database entries
A Postgres role SHALL require its project identity and persisted image source. A KV role SHALL require its project identity, persisted concrete engine, and image source; command creation defaults the engine to Dragonfly. Safe defaults SHALL supply PgBouncer, durability, HTTP, pool sizing, Dragonfly resources, and connection limits when no override is selected. Fixed runtime policy such as the Postgres username, default database name, and operation timeout values SHALL NOT appear as source-owned settings. Role override sections in source SHALL use explicit mapping fields rather than undocumented boolean shorthands.

#### Scenario: Minimal Postgres role is created
- **WHEN** the operator accepts creation defaults for `example-prod-01/postgres`
- **THEN** source records an independently imaged durable Postgres role with backups and PgBouncer enabled

#### Scenario: Minimal KV role is created
- **WHEN** the operator accepts creation defaults for `example-prod-01/kv`
- **THEN** source records a durable Dragonfly role with backups and authenticated HTTP enabled

#### Scenario: Fixed Postgres connection defaults are used
- **WHEN** Postgres connection details are derived for a role
- **THEN** evdb uses the fixed host-local username `default` and database name `postgres` without reading those values from source configuration

### Requirement: Derived deployment values
The system SHALL derive type-qualified Compose project names, unique service and network aliases, data and generated-file paths, project SNI domains, default settings, backup behavior, HTTP behavior, and secret-file paths from host settings plus each project role. Public native database domains SHALL use `<project>.<host-id>.<base-domain>` for both Postgres and KV roles. Default KV HTTP domains SHALL use the same project hostname. Compose project identity SHALL remain distinct when one project has both roles.

#### Scenario: Two roles share one project
- **WHEN** one project has Postgres and KV
- **THEN** generated project names are `evdb-<project>-postgres` and `evdb-<project>-kv`, both roles derive `<project>.<host-id>.<base-domain>` as their public native hostname, and Docker does not merge their Compose metadata

#### Scenario: KV HTTP default uses project hostname
- **WHEN** an HTTP-enabled KV role has no explicit HTTP domain override
- **THEN** its intended public HTTPS endpoint uses `<project>.<host-id>.<base-domain>`

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
Validation SHALL reject unsafe project IDs, missing required environment suffixes, duplicate roles, unsupported KV engines, unsafe paths, colliding project/service/native-route/HTTP identities, incomplete host routing or backup settings, unversioned, `latest`, or invalid image sources, invalid role-specific overrides, unsupported engine-major changes, and secret values in source or machine state. The same invariants SHALL apply to YAML-loaded configuration and programmatically constructed configuration before candidate rendering or installation. Explicit version tags SHALL be permitted only when they resolve to immutable digests in machine state.

#### Scenario: Project suffix is absent
- **WHEN** a project ID does not end in `-dev-N`, `-test-N`, or `-prod-N`
- **THEN** validation fails before candidate rendering

#### Scenario: Project roles share a public hostname
- **WHEN** one project contains both Postgres and KV roles using the derived project hostname
- **THEN** validation accepts the shared hostname because the native routes use different ports and entrypoints

#### Scenario: Enabled KV HTTP domains collide
- **WHEN** two enabled KV HTTP sidecars are configured with the same intended public HTTPS hostname
- **THEN** validation fails before generated files or live services change

#### Scenario: Initial setup constructs invalid host settings
- **WHEN** initial setup supplies invalid host routing, backup repositories, retention values, or role settings before a source file exists
- **THEN** the same validation failure is raised that would be raised for an equivalent invalid `host.yml`

### Requirement: Old source schema is not supported
The loader SHALL reject the controller-owned `databases` list, engine-first identities, `current` or `target` fields, separate Postgres/KV files, checked-in `host.lock.json`, and migration-only release fields. Production conversion SHALL be performed only by the separate approved migration change.

#### Scenario: Controller source is supplied
- **WHEN** configuration contains the old top-level `databases` list
- **THEN** host setup or checking fails with guidance to use project roles

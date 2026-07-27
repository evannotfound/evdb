## MODIFIED Requirements

### Requirement: Machine-owned host state
The system SHALL store resolved image digests, stable HTTP loopback ports, schema versions, generated Compose hashes, installed flags, installed tool version, and operation results in private machine-owned state under `/var/lib/evdb/state`. Operators SHALL NOT need to edit or commit this state. State loading SHALL require the complete current writer-owned schema for the recorded version and SHALL reject missing or malformed fields instead of synthesizing compatibility defaults. Image engine majors SHALL be derived from each image source when needed rather than stored as independent state.

#### Scenario: New KV receives an HTTP port
- **WHEN** an HTTP-enabled KV role is added
- **THEN** evdb allocates an unused loopback port and preserves it across ordering changes and tool updates

#### Scenario: Partial machine state is supplied
- **WHEN** a machine state file omits current-version writer-owned fields such as images, roles, installed flags, operation records, generated Compose hashes, or tool version
- **THEN** validation fails before generated files or live services change

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

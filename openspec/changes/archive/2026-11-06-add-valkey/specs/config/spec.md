## MODIFIED Requirements

### Requirement: Stable KV role and concrete engine
The KV role SHALL record a concrete engine of `valkey`, `dragonfly`, or `redis`. Creation SHALL default an omitted KV engine to Valkey and SHALL persist the resolved engine explicitly. The engines SHALL remain distinct for runtime, generated configuration, health, and backup behavior. Existing roles SHALL retain their recorded engines and images when creation is retried or evdb is updated.

#### Scenario: Default KV is created
- **WHEN** the operator adds `example-prod-01/kv` without an engine option
- **THEN** configuration records `engine: valkey` and a pinned Valkey image, and generated Compose uses Valkey

#### Scenario: Existing Redis creation is retried
- **WHEN** add is rerun without an engine for an existing Redis role
- **THEN** it remains Redis with its recorded image and credentials

### Requirement: Explicit exceptional settings
Configuration SHALL accept typed overrides for KV durability mode, enabled HTTP, HTTP connections, PgBouncer use and sizing, Valkey and Dragonfly memory, Dragonfly threads, and per-database images. Invalid settings for a role or concrete engine SHALL be rejected and omitted from its guided settings menu. Valkey mode SHALL be chosen at creation and SHALL NOT be changed through configure or reset; its mode SHALL remain visible in database details.

#### Scenario: Redis receives Dragonfly threads
- **WHEN** a Redis-backed KV role contains a Dragonfly thread override
- **THEN** validation names the invalid field and no file or service changes

#### Scenario: Valkey mode change is requested
- **WHEN** configure or reset would change an existing Valkey role between cache and durable
- **THEN** the request fails before source, generated files, data, or services change

#### Scenario: Valkey memory round trips
- **WHEN** a valid Valkey memory limit is written and loaded
- **THEN** the configured value is retained and threads remain unsupported

### Requirement: Minimal database entries
A Postgres role SHALL persist its image and PgBouncer settings. A KV role SHALL persist its concrete
engine, image, durability mode, HTTP settings, and Valkey or Dragonfly resources when applicable. Creation SHALL
write explicit stable values selected from the engine module's defaults. Fixed Postgres connection
values SHALL remain runtime policy, while generated passwords and HTTP tokens SHALL be written under
the matching role in `secrets.yml`.

#### Scenario: Minimal Postgres role is created
- **WHEN** the operator accepts creation defaults for `example-prod-01/postgres`
- **THEN** `config.yml` records the Postgres and PgBouncer values and `secrets.yml` records its generated password

#### Scenario: Minimal KV role is created
- **WHEN** the operator accepts creation defaults for `example-prod-01/kv`
- **THEN** `config.yml` records a durable Valkey role with HTTP and `secrets.yml` records its password and HTTP token

#### Scenario: Fixed Postgres connection defaults are used
- **WHEN** Postgres connection details are derived for a role
- **THEN** evdb uses username `default` and database `postgres` without storing either as a configurable source field

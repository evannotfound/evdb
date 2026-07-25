## ADDED Requirements

### Requirement: Single human-owned host configuration
The system SHALL use one `config/<host>/host.yml` file as the only human-owned configuration for a managed host. It SHALL NOT require separate Postgres or KV source files.

#### Scenario: Operator opens host configuration
- **WHEN** an operator reviews a managed host
- **THEN** host defaults and every managed database are visible in one YAML file

### Requirement: Minimal database entries
A normal database entry SHALL require only a name and one of the supported types `postgres`, `redis`, or `dragonfly`. The system SHALL apply safe built-in defaults for all omitted settings.

#### Scenario: Minimal Postgres entry is loaded
- **WHEN** an entry contains only `name: example-prod-01` and `type: postgres`
- **THEN** the system derives a durable Postgres deployment with backups and PgBouncer enabled

#### Scenario: Minimal KV entry is loaded
- **WHEN** an entry contains only a name and a Redis or Dragonfly type
- **THEN** the system derives a durable KV deployment with backups and authenticated HTTP enabled

### Requirement: Derived deployment values
The system SHALL derive container names, Compose project names, data paths, domains, database defaults, backup behavior, HTTP behavior, and secret references from host defaults plus each concise database entry.

#### Scenario: Deployment model is normalized
- **WHEN** valid source configuration is loaded
- **THEN** every value required by Compose, backup, restore, status, and deployment is present in the normalized model without being repeated in source YAML

### Requirement: Explicit exceptional settings
The source schema SHALL accept concise overrides only for behavior that differs from defaults, including cache mode, PgBouncer use, pool sizing, Dragonfly memory or threads, and HTTP enablement.

#### Scenario: Postgres pooler is disabled
- **WHEN** a Postgres entry sets `pooler: false`
- **THEN** the normalized model disables PgBouncer for only that database

#### Scenario: Cache mode is selected
- **WHEN** an entry explicitly sets `mode: cache`
- **THEN** the normalized model permits backups to be disabled for that entry

### Requirement: Host-level image versions
The operator SHALL configure each infrastructure image once at host level. Per-database image settings SHALL NOT be required.

Each source image SHALL use an explicit non-`latest` tag or an immutable `@sha256` reference. Untagged implicit latest and explicit `:latest` references SHALL be rejected.

Postgres, Redis, and Dragonfly source images SHALL also have a tagged positive integer engine major. Digest-only engine references and non-version tags SHALL be rejected. Infrastructure images MAY remain immutable digest-only references.

#### Scenario: Postgres image is changed
- **WHEN** the host-level Postgres image version changes
- **THEN** every Postgres database inherits the new desired image unless a future explicit exception is supported

#### Scenario: Engine tag has no major version
- **WHEN** an engine source uses `postgres:stable`, `redis:0`, or a digest without a tagged major
- **THEN** source validation fails before image resolution or release staging

### Requirement: Generated reproducibility lock
The system SHALL maintain a tool-owned, secret-free `host.lock.json` containing resolved platform image digests and stable allocated values. Generated Compose SHALL use locked image references rather than mutable source tags.

#### Scenario: Image tag is resolved
- **WHEN** plan or apply resolves a configured image tag
- **THEN** the selected platform digest is written atomically to the lock and used by generated deployment artifacts

#### Scenario: Image digest is configured
- **WHEN** a source image already contains an immutable `@sha256` reference
- **THEN** its digest is reused without registry resolution and generated artifacts contain one digest suffix

#### Scenario: Existing HTTP assignment is loaded
- **WHEN** a database already has an HTTP port in the lock
- **THEN** normalization preserves that port regardless of database ordering or newly added entries

### Requirement: Convention-based secret references
Source configuration SHALL contain only the host 1Password vault and system item settings. Database password and token references SHALL be derived from database identity, and no secret value SHALL be accepted in source or lock files.

#### Scenario: Database secret reference is derived
- **WHEN** a database named `example-prod-01` is normalized
- **THEN** its password reference targets the convention-based Postgres or KV item without a per-entry secret field

#### Scenario: Secret value is committed
- **WHEN** source or lock configuration contains a resolved password or token
- **THEN** validation fails without printing the value

### Requirement: Runtime and observed state are machine-owned
The system SHALL keep normalized runtime JSON, active release facts, and live Docker observations outside human source YAML.

#### Scenario: Live image differs from desired image
- **WHEN** the running image differs from the locked desired image
- **THEN** plan reports drift without adding a `current` section to `host.yml`

### Requirement: Configuration validation
Validation SHALL reject unsupported engine types, duplicate typed identities, unsafe names, colliding derived values, invalid overrides, missing host requirements, and source-to-lock inconsistencies with concise field-specific errors.

#### Scenario: Duplicate typed identity is configured
- **WHEN** two entries have the same type and name
- **THEN** validation fails and names the duplicate identity

#### Scenario: Same product has two database types
- **WHEN** Postgres and KV entries share a name
- **THEN** both entries validate because their typed identities differ

### Requirement: Old source schema is not supported
The loader SHALL reject migration-only instance fields and the old separate instance files rather than maintaining a compatibility layer.

#### Scenario: Current and target fields are present
- **WHEN** an entry contains `current` or `target`
- **THEN** validation fails with guidance to use the concise schema

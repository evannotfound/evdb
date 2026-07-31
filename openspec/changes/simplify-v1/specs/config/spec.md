## MODIFIED Requirements

### Requirement: Stable KV role and concrete engine
The KV role SHALL record a concrete engine of `dragonfly` or `redis`. Creation SHALL default an
omitted KV engine to Dragonfly and SHALL persist the resolved engine explicitly. Redis and Dragonfly
SHALL remain distinct engines for runtime, generated configuration, health, and backup behavior.

#### Scenario: Default KV is created
- **WHEN** the operator adds `example-prod-01/kv` without an engine option
- **THEN** `config.yml` records `engine: dragonfly` and generated Compose uses Dragonfly

### Requirement: Per-database image sources
Each database SHALL record the image used by its primary and any enabled PgBouncer or HTTP sidecar in
`config.yml`. Image values SHALL use an explicit non-`latest` tag or SHA-256 reference and SHALL be
written directly into generated Compose without separate resolution or image machine state.

#### Scenario: One Postgres image changes
- **WHEN** the operator updates the configured image for one project's Postgres role
- **THEN** rerendering that role changes no other project's source or generated Compose

#### Scenario: Database restarts without an image edit
- **WHEN** an operator restarts a role whose configured image value is unchanged
- **THEN** evdb uses the same configured image and does not perform an implicit image pull or update

### Requirement: Canonical host paths
Non-secret source SHALL live at `/etc/evdb/config.yml`, secret source at
`/etc/evdb/secrets.yml`, and `host.backup.rclone_config` SHALL reference a private native rclone file
in place. Generated database assets SHALL live under `/var/lib/evdb/projects/<project>/<role>`,
dedicated Traefik assets under `/var/lib/evdb/traefik`, mutable local backups and locks under
`/var/lib/evdb`, and database data under `/var/lib/evdb/databases/<project>/<role>/data`.

#### Scenario: Postgres paths are derived
- **WHEN** project `example-prod-01` has a Postgres role
- **THEN** its Compose path is `/var/lib/evdb/projects/example-prod-01/postgres/compose.yaml` and its data path is `/var/lib/evdb/databases/example-prod-01/postgres/data`

#### Scenario: Operator locates host inputs
- **WHEN** an operator reviews `/etc/evdb` and `/var/lib/evdb`
- **THEN** evdb's desired settings and managed credentials are separated from generated runtime and database data

### Requirement: Command-owned atomic configuration
Normal guided and direct configuration changes SHALL validate complete `config.yml` and
`secrets.yml` candidates and atomically replace only the source files they change. They SHALL NOT
create configuration releases, activity records, prior-file snapshots, or transaction state.

#### Scenario: Configuration write is interrupted
- **WHEN** a process stops before one candidate file is atomically replaced
- **THEN** the prior complete file remains readable and the next command can rerender from it

### Requirement: Single human-owned host configuration
Each managed host SHALL have one readable `/etc/evdb/config.yml` containing host settings and every
project database role. `/etc/evdb/secrets.yml` SHALL contain the matching host and role credentials
without duplicating non-secret settings. No workstation source, generated runtime copy, active release,
or machine-state file SHALL be required to understand or operate the host.

#### Scenario: Operator reviews a host
- **WHEN** the operator opens `/etc/evdb/config.yml`
- **THEN** host settings, projects, roles, engines, images, and explicit overrides are visible without reading generated Compose or runtime state

#### Scenario: Operator reviews managed credentials
- **WHEN** an authorized operator opens `/etc/evdb/secrets.yml`
- **THEN** the Restic, DNS, database, and HTTP credentials are organized under their host or matching project and role

### Requirement: Minimal database entries
A Postgres role SHALL persist its image and PgBouncer settings. A KV role SHALL persist its concrete
engine, image, durability mode, HTTP settings, and Dragonfly resources when applicable. Creation SHALL
write explicit stable values selected from the engine module's defaults. Fixed Postgres connection
values SHALL remain runtime policy, while generated passwords and HTTP tokens SHALL be written under
the matching role in `secrets.yml`.

#### Scenario: Minimal Postgres role is created
- **WHEN** the operator accepts creation defaults for `example-prod-01/postgres`
- **THEN** `config.yml` records the Postgres and PgBouncer values and `secrets.yml` records its generated password

#### Scenario: Minimal KV role is created
- **WHEN** the operator accepts creation defaults for `example-prod-01/kv`
- **THEN** `config.yml` records a durable Dragonfly role with HTTP and `secrets.yml` records its password and HTTP token

#### Scenario: Fixed Postgres connection defaults are used
- **WHEN** Postgres connection details are derived for a role
- **THEN** evdb uses username `default` and database `postgres` without storing either as a configurable source field

### Requirement: Derived deployment values
The system SHALL derive Compose project names, service and network aliases, data and generated-file
paths, project SNI domains, public ports, and engine credential-file paths from `config.yml` and
`secrets.yml`. Public native database domains SHALL use `<project>.<host-id>.<base-domain>` for both
Postgres and KV; default KV HTTP domains SHALL use the same project hostname.

#### Scenario: Two roles share one project
- **WHEN** one project has Postgres and KV
- **THEN** generated project names are `evdb-<project>-postgres` and `evdb-<project>-kv`, both roles derive the same project hostname on their distinct native ports, and Docker does not merge their Compose metadata

#### Scenario: KV HTTP default uses project hostname
- **WHEN** an HTTP-enabled KV role has no HTTP domain override
- **THEN** its intended public HTTPS endpoint uses `<project>.<host-id>.<base-domain>`

### Requirement: Configuration validation
Validation SHALL reject unsafe project IDs, missing environment suffixes, duplicate roles, unsupported
engines, an unsafe external rclone path, colliding routes or HTTP identities, incomplete routing or
backup settings, `latest` or unversioned images, invalid role-specific settings, missing matching
secrets, unexpected secret keys, and source files with unsafe ownership or modes. `config.yml` SHALL
contain no credential value; `secrets.yml` SHALL contain only supported credential fields and SHALL be
mode `0600`. The same role invariants SHALL apply before source writes or generated service changes.

The external rclone file SHALL be a normalized absolute, non-symlink regular file with exact mode
`0600`, owned by a known non-root user. Its immediate parent SHALL be safe, owned and writable by that
user so rclone can atomically refresh OAuth state. evdb SHALL derive that user's numeric identity,
primary and supplementary groups, username, and existing home from the host account database.

#### Scenario: Project suffix is absent
- **WHEN** a project ID does not end in `-dev-N`, `-test-N`, or `-prod-N`
- **THEN** validation fails before generated files or live services change

#### Scenario: Role secret is absent
- **WHEN** a configured database has no matching password in `secrets.yml`
- **THEN** status and lifecycle commands fail with the exact project and role before invoking Compose

#### Scenario: Enabled KV HTTP domains collide
- **WHEN** two enabled KV HTTP sidecars use the same intended public HTTPS hostname
- **THEN** validation fails before generated files or live services change

#### Scenario: Root owns the rclone file
- **WHEN** `host.backup.rclone_config` names an otherwise private root-owned file
- **THEN** validation rejects it before Restic or rclone starts

### Requirement: Old source schema is not supported
The loader SHALL reject `/etc/evdb/host.yml`, separate generated secret trees, machine-owned host
state, controller-era database lists, engine-first identities, and migration-only release fields.
Production conversion SHALL remain a separate approved change.

#### Scenario: Previous pre-v1 layout is present
- **WHEN** initialization finds `host.yml` or machine state without the new canonical source files
- **THEN** it fails with a concise reset-or-migrate message and does not synthesize compatibility values

## REMOVED Requirements

### Requirement: Machine-owned host state
**Reason**: Resolved images, installed flags, Compose hashes, operation records, and tool versions
duplicate readable configuration and drive most of the current compatibility framework.

**Migration**: Use direct configured image values, generated files, local backup manifests, and live
Docker/systemd observations. Pre-v1 machine state is not migrated.

### Requirement: Machine-owned runtime and observed state
**Reason**: V1 does not persist a second deployment contract or operation history.

**Migration**: Recompute live container, timer, and backup observations when status or details are
requested.

## ADDED Requirements

### Requirement: Versioned DNS provider catalog
The standalone command SHALL contain a generated catalog of canonical DNS provider codes, display names,
documented credential variables, descriptions, and help URLs from the exact lego version embedded in the
pinned Traefik image. Source validation SHALL accept only cataloged canonical providers and documented
provider variables. Updating the pinned Traefik or lego version SHALL require updating and checking the
catalog metadata in the same source change.

#### Scenario: Supported provider is selected
- **WHEN** setup selects a provider present in the catalog bundled for Traefik `v3.7.8` and lego `v5.2.2`
- **THEN** configuration stores its canonical code and accepts only variables documented for that provider

#### Scenario: Catalog and image versions differ
- **WHEN** source changes the pinned Traefik image without matching provider catalog metadata
- **THEN** project checks fail before building a release executable

## MODIFIED Requirements

### Requirement: Canonical host paths
Non-secret source SHALL live at `/etc/evdb/config.yml` and secret source at `/etc/evdb/secrets.yml`.
An rclone repository SHALL reference one private native rclone file in place; a local repository SHALL
omit `host.backup.rclone_config`. Generated database assets SHALL live under
`/var/lib/evdb/projects/<project>/<role>`, dedicated Traefik assets under `/var/lib/evdb/traefik`,
mutable local backups and locks under `/var/lib/evdb`, and database data under
`/var/lib/evdb/databases/<project>/<role>/data`.

#### Scenario: Postgres paths are derived
- **WHEN** project `example-prod-01` has a Postgres role
- **THEN** its Compose path is `/var/lib/evdb/projects/example-prod-01/postgres/compose.yaml` and its data path is `/var/lib/evdb/databases/example-prod-01/postgres/data`

#### Scenario: Local repository is configured
- **WHEN** `host.backup.repository` is a normalized absolute local path
- **THEN** `config.yml` contains no rclone configuration field

#### Scenario: Operator locates host inputs
- **WHEN** an operator reviews `/etc/evdb` and `/var/lib/evdb`
- **THEN** evdb's desired settings and managed credentials are separated from generated runtime and database data

### Requirement: Minimal database entries
A Postgres role SHALL persist its image, PgBouncer settings, username, and database name. A KV role
SHALL persist its concrete engine, image, durability mode, HTTP settings, and Dragonfly resources when
applicable. Creation SHALL write explicit stable values selected from the engine module's defaults.
Generated passwords and HTTP tokens SHALL be written under the matching role in `secrets.yml`.

#### Scenario: Minimal Postgres role is created
- **WHEN** the operator accepts creation defaults for `example-prod-01/postgres`
- **THEN** `config.yml` records username `default`, database `postgres`, Postgres and PgBouncer values, and `secrets.yml` records its generated password

#### Scenario: Minimal KV role is created
- **WHEN** the operator accepts creation defaults for `example-prod-01/kv`
- **THEN** `config.yml` records a durable Dragonfly role with HTTP and `secrets.yml` records its password and HTTP token

#### Scenario: Custom Postgres identity is created
- **WHEN** Postgres creation receives username `app_user` and database name `app_db`
- **THEN** both non-secret values are stored under that Postgres role and are not exposed as mutable guided settings

### Requirement: Configuration validation
Validation SHALL reject unsafe project IDs, missing environment suffixes, duplicate roles, unsupported
engines or DNS providers, DNS variables not documented for the selected provider, unsafe repository
settings, colliding routes or HTTP identities, incomplete routing or backup settings, `latest` or
unversioned images, invalid or missing Postgres identity, invalid role-specific settings, missing
matching secrets, unexpected secret keys, and source files with unsafe ownership or modes. `config.yml`
SHALL contain no credential value; `secrets.yml` SHALL contain only supported credential fields and
SHALL be mode `0600`. The same invariants SHALL apply before source writes or generated service changes.

For an rclone repository, the repository SHALL have form `rclone:<remote>:<safe-path>` and name a remote
present in a normalized absolute, non-symlink regular rclone file with exact mode `0600`, owned by a
known non-root user. Its immediate parent SHALL be safe, owned and writable by that user. For a local
repository, the repository SHALL be a normalized absolute non-symlink path whose existing directory or
immediate parent is safe, user-writable, and owned by a known non-root user; `rclone_config` SHALL be
absent. evdb SHALL derive the selected user's numeric identity, primary and supplementary groups,
username, and existing safe home from the host account database.

#### Scenario: Project suffix is absent
- **WHEN** a project ID does not end in `-dev-N`, `-test-N`, or `-prod-N`
- **THEN** validation fails before generated files or live services change

#### Scenario: Role secret is absent
- **WHEN** a configured database has no matching password in `secrets.yml`
- **THEN** status and lifecycle commands fail with the exact project and role before invoking Compose

#### Scenario: Enabled KV HTTP domains collide
- **WHEN** two enabled KV HTTP sidecars use the same intended public HTTPS hostname
- **THEN** validation fails before generated files or live services change

#### Scenario: Rclone remote is absent
- **WHEN** the repository names a remote not present in the selected rclone configuration
- **THEN** initialization fails before writing source or invoking Restic

#### Scenario: Root owns the rclone file
- **WHEN** `host.backup.rclone_config` names an otherwise private root-owned file
- **THEN** validation rejects it before Restic or rclone starts

#### Scenario: Root owns a local repository parent
- **WHEN** a missing local repository's immediate parent is root-owned
- **THEN** validation rejects it because no non-root repository operator can be derived

#### Scenario: Postgres identity contains an unsafe value
- **WHEN** a Postgres username or database name is empty, multiline, contains NUL, or exceeds PostgreSQL's identifier limit
- **THEN** validation fails before source or services change

### Requirement: Old source schema is not supported
The loader SHALL reject `/etc/evdb/host.yml`, separate generated secret trees, machine-owned host state,
controller-era database lists, engine-first identities, obsolete release fields, and Postgres entries
without explicit username and database name. evdb SHALL not convert, import, or synthesize current source
from an unsupported layout.

#### Scenario: Unsupported source layout is present
- **WHEN** initialization finds unsupported source or incomplete Postgres identity fields
- **THEN** it names the unsupported source and requires the operator to provide current `config.yml`

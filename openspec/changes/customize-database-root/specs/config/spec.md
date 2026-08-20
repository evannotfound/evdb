## ADDED Requirements

### Requirement: Database data root catalog and immutable placement
`host.data_roots` SHALL be a non-empty ordered list of unique normalized absolute non-symlink paths.
Entries SHALL NOT overlap one another, source, generated assets, Traefik assets, backups, locks, or a
local Restic repository. A non-canonical root's immediate parent SHALL already exist and be safe. Every
Postgres and KV role SHALL store one exact catalog entry as `data_root`. evdb SHALL reject a desired role
bind source that differs from the source recorded in existing generated Compose and SHALL NOT move data.

#### Scenario: Two roles select different roots
- **WHEN** Postgres selects `/data` and KV selects `/var/lib/evdb/databases`
- **THEN** each role derives `<selected-root>/<project>/<role>/data`

#### Scenario: Root overlaps backup storage
- **WHEN** one catalog entry is equal to or contains the managed backup path
- **THEN** validation fails before source, directories, or services change

#### Scenario: Catalog roots overlap
- **WHEN** the catalog contains `/data` and `/data/fast`
- **THEN** validation rejects the catalog before mutation

#### Scenario: Role selects an absent root
- **WHEN** a role selects `/removed` and that path is not in `host.data_roots`
- **THEN** source validation rejects the exact project and role

#### Scenario: Existing generated bind differs
- **WHEN** a role's generated Compose records one data root and source selects another
- **THEN** rendering fails before creating the new data directory or replacing Compose

## MODIFIED Requirements

### Requirement: Canonical host paths
Non-secret source SHALL live at `/etc/evdb/config.yml` and secret source at `/etc/evdb/secrets.yml`.
An rclone repository SHALL reference one private native rclone file in place; a local repository SHALL
omit `host.backup.rclone_config`. Generated database assets SHALL live under
`/var/lib/evdb/projects/<project>/<role>`, dedicated Traefik assets under `/var/lib/evdb/traefik`, and
mutable local backups and locks under `/var/lib/evdb`. Database data SHALL live under each role's
required allowlisted `data_root` using `<project>/<role>/data`; fresh initialization SHALL begin the root
catalog with `/var/lib/evdb/databases` and write all selections explicitly.

#### Scenario: Default Postgres paths are derived
- **WHEN** project `example-prod-01` Postgres selects `/var/lib/evdb/databases`
- **THEN** its Compose path is `/var/lib/evdb/projects/example-prod-01/postgres/compose.yaml` and its data path is `/var/lib/evdb/databases/example-prod-01/postgres/data`

#### Scenario: Custom Postgres data path is derived
- **WHEN** Postgres selects `/data` from `host.data_roots`
- **THEN** its data path is `/data/<project>/postgres/data` while its Compose path remains canonical

#### Scenario: Local repository is configured
- **WHEN** `host.backup.repository` is a normalized absolute local path
- **THEN** `config.yml` contains no rclone configuration field

#### Scenario: Operator locates host inputs
- **WHEN** an operator reviews `/etc/evdb`, `/var/lib/evdb`, and `host.data_roots`
- **THEN** evdb's desired settings and managed credentials are separated from generated runtime and database data

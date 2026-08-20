## ADDED Requirements

### Requirement: Database data root safety and immutability
`host.data_root` SHALL be a normalized absolute non-symlink path for one dedicated evdb database tree.
It SHALL NOT overlap source, generated assets, Traefik assets, backups, locks, or a local Restic
repository. A custom root's immediate parent SHALL already exist and be safe. evdb SHALL reject a desired
database bind source that differs from the source recorded in an existing generated role Compose file,
and SHALL NOT move database data.

#### Scenario: Custom root uses mounted storage
- **WHEN** `host.data_root` is `/mnt/database-volume/evdb`
- **THEN** project `example-prod-01` Postgres data resolves to `/mnt/database-volume/evdb/example-prod-01/postgres/data`

#### Scenario: Root overlaps backup storage
- **WHEN** `host.data_root` is equal to or contains the managed backup path
- **THEN** validation fails before source, directories, or services change

#### Scenario: Existing generated bind differs
- **WHEN** a role's generated Compose file records a data bind under one root and source selects another
- **THEN** rendering fails before creating the new data directory or replacing Compose

## MODIFIED Requirements

### Requirement: Canonical host paths
Non-secret source SHALL live at `/etc/evdb/config.yml` and secret source at `/etc/evdb/secrets.yml`.
An rclone repository SHALL reference one private native rclone file in place; a local repository SHALL
omit `host.backup.rclone_config`. Generated database assets SHALL live under
`/var/lib/evdb/projects/<project>/<role>`, dedicated Traefik assets under `/var/lib/evdb/traefik`, and
mutable local backups and locks under `/var/lib/evdb`. Database data SHALL live under the required
`host.data_root` using `<project>/<role>/data`; fresh initialization SHALL default that root to
`/var/lib/evdb/databases` and write it explicitly.

#### Scenario: Default Postgres paths are derived
- **WHEN** fresh setup creates project `example-prod-01` with a Postgres role using the default data root
- **THEN** its Compose path is `/var/lib/evdb/projects/example-prod-01/postgres/compose.yaml` and its data path is `/var/lib/evdb/databases/example-prod-01/postgres/data`

#### Scenario: Custom Postgres data path is derived
- **WHEN** `host.data_root` is `/mnt/database-volume/evdb` and project `example-prod-01` has a Postgres role
- **THEN** its data path is `/mnt/database-volume/evdb/example-prod-01/postgres/data` while its Compose path remains canonical

#### Scenario: Local repository is configured
- **WHEN** `host.backup.repository` is a normalized absolute local path
- **THEN** `config.yml` contains no rclone configuration field

#### Scenario: Operator locates host inputs
- **WHEN** an operator reviews `/etc/evdb`, `/var/lib/evdb`, and `host.data_root`
- **THEN** evdb's desired settings and managed credentials are separated from generated runtime and database data

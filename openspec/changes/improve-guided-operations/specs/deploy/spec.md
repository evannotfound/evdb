## MODIFIED Requirements

### Requirement: ACME DNS certificates
Dedicated Traefik SHALL use a configured ACME DNS-01 resolver and a scoped DNS-provider credential in a
private host file to obtain one wildcard certificate for `*.<host-id>.<base-domain>` during host
initialization. The certificate and account state SHALL persist in mode-`0600` `acme.json`. Native TCP
routers SHALL terminate TLS with that host wildcard and SHALL not initiate independent exact-host ACME
orders. Traefik SHALL NOT require or bind ports 80 or 443.

#### Scenario: Fresh host obtains its wildcard
- **WHEN** Traefik completes DNS-01 issuance during fresh initialization
- **THEN** evdb finds a non-empty stored certificate and key for the exact wildcard before reporting setup complete

#### Scenario: Wildcard issuance fails
- **WHEN** the provider, credentials, zone access, or propagation prevents issuance
- **THEN** Traefik may remain running with its internal certificate but evdb reports initialization failure and does not describe ACME as ready

#### Scenario: Native certificate renews
- **WHEN** Traefik renews the host wildcard through DNS-01
- **THEN** certificate state persists across proxy restarts without exposing the provider credential or private ACME state in Compose, status, or logs

#### Scenario: Database route is added
- **WHEN** a Postgres or KV route for `<project>.<host-id>.<base-domain>` becomes active
- **THEN** its TLS router uses the matching stored wildcard without requesting another certificate

### Requirement: Idempotent database creation
`evdb database add PROJECT postgres` and `evdb database add PROJECT kv [--engine ENGINE]` SHALL
validate identity, persist explicit role settings, generate or securely accept required credentials in
`secrets.yml`, render role-local files, start Compose, and wait for full native health. Postgres creation
MAY accept immutable username and database-name inputs used by Postgres, PgBouncer, health, backup, info,
and connection operations. Existing matching roles SHALL not be duplicated. Creation failure SHALL leave
readable source and generated files for inspection and retry rather than running candidate rollback.

#### Scenario: Default KV is added
- **WHEN** the operator adds an absent KV role without selecting an engine
- **THEN** one Dragonfly-backed KV role with HTTP and backups is recorded and started

#### Scenario: Postgres receives a supplied identity
- **WHEN** an absent Postgres role is added with username `app_user`, database `app_db`, and a valid securely supplied password
- **THEN** `config.yml`, `secrets.yml`, Postgres, PgBouncer, health, and connection details use that exact identity

#### Scenario: Postgres identity is omitted
- **WHEN** an absent Postgres role is added without advanced identity input
- **THEN** evdb stores username `default` and database `postgres`, generates a password, and starts the default service

#### Scenario: Existing Postgres receives a different creation identity
- **WHEN** add is rerun for an existing Postgres role with a different username, database name, or supplied password
- **THEN** evdb performs no mutation and reports that changing initialized Postgres identity is outside this operation

#### Scenario: New creation fails health
- **WHEN** new services cannot become healthy
- **THEN** evdb reports the concrete failure without deleting the configured role, generated files, or data

### Requirement: Local deployment test
Generated files, direct settings changes, each concrete engine, PgBouncer, HTTP, Traefik, host
initialization, and installer updates SHALL be tested only with disposable infrastructure, repositories,
and credentials selected for that test run.

#### Scenario: Full disposable operation completes
- **WHEN** the integration suite creates and reconfigures test databases
- **THEN** it proves independent projects, routing, native health, direct retry behavior, and checked backups without external resources

## ADDED Requirements

### Requirement: Separate direct and transaction-pooled Postgres endpoints
Every Postgres role SHALL route `<project>.<host-id>.<base-domain>` directly to its primary Postgres service through the existing TLS proxy. When PgBouncer is enabled, evdb SHALL additionally route `<project>-pooled.<host-id>.<base-domain>` to that role's PgBouncer service with explicit transaction pooling. Both endpoints SHALL use the existing Postgres entrypoint and configured native ports, with distinct role-qualified routers and services. Enabling pooling SHALL require a valid pooled DNS label and hostname under the existing length limits.

#### Scenario: PgBouncer is enabled
- **WHEN** evdb renders a Postgres role with PgBouncer enabled
- **THEN** the plain hostname targets Postgres, the pooled hostname targets PgBouncer, and generated PgBouncer configuration specifies `pool_mode = transaction`
- **AND** both routes require TLS and use the existing wildcard hostname level without publishing container ports

#### Scenario: PgBouncer is disabled
- **WHEN** evdb renders a Postgres role with PgBouncer disabled
- **THEN** the direct route remains and no PgBouncer service or pooled route is generated
- **AND** the pooled hostname does not fall back to direct access

#### Scenario: Pooled hostname is too long
- **WHEN** an enabled Postgres pool would produce an invalid DNS label or hostname after adding `-pooled`
- **THEN** configuration validation rejects it before deployment

#### Scenario: Client needs session state
- **WHEN** a client connects to the direct endpoint
- **THEN** its Postgres session is retained across transactions until disconnect

#### Scenario: Transaction completes through the pool
- **WHEN** a pooled client commits a transaction while remaining connected
- **THEN** its server connection can serve another client without waiting for the first client to disconnect

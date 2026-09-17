## ADDED Requirements

### Requirement: Ordered native host ports

`host.routing` SHALL accept `postgres_ports` and `kv_ports` as ordered, nonempty lists of unique integers in 1–65535. The lists SHALL NOT overlap or contain ports 80 or 443, which remain outside evdb native routing. Booleans SHALL NOT be accepted as port numbers. Omitted fields SHALL resolve to `[5432]` and `[6379]` respectively. New initialization and explicit port updates SHALL persist both resolved lists. Loading configuration or refreshing it without port flags SHALL NOT rewrite source solely to insert these defaults.

The first port in each list SHALL be the preferred external port for that protocol. Additional ports SHALL be equivalent entrypoints to the same managed databases. Configuration serialization SHALL preserve the operator's ordering.

#### Scenario: Existing source omits port settings
- **WHEN** evdb loads current-schema configuration without either port list
- **THEN** Postgres uses `[5432]`, KV uses `[6379]`, and loading does not modify source

#### Scenario: Operator selects temporary and standard ports
- **WHEN** routing contains `postgres_ports: [15432, 5432]` and `kv_ports: [16379, 6379]`
- **THEN** configuration round trips retain that order and the preferred ports are 15432 and 16379

#### Scenario: Invalid port list is supplied
- **WHEN** a list is empty, contains a non-integer, boolean, duplicate, out-of-range port, port 80 or 443, or a port assigned to the other protocol
- **THEN** validation identifies the invalid routing setting before source or runtime changes

## MODIFIED Requirements

### Requirement: Derived deployment values

The system SHALL derive Compose project names, service and network aliases, data and generated-file paths, project SNI domains, public ports, and engine credential-file paths from `config.yml` and `secrets.yml`. Public native database domains SHALL use `<project>.<host-id>.<base-domain>` for both Postgres and KV; default KV HTTP domains SHALL use the same project hostname. Native connection details SHALL use the first port in the corresponding host routing list. Internal database, PgBouncer, and Redis HTTP backend ports SHALL remain 5432 for Postgres and 6379 for KV.

#### Scenario: Two roles share one project
- **WHEN** one project has Postgres and KV
- **THEN** generated project names are `evdb-<project>-postgres` and `evdb-<project>-kv`, both roles derive the same project hostname on their distinct configured native host ports, and Docker does not merge their Compose metadata

#### Scenario: KV HTTP default uses project hostname
- **WHEN** an HTTP-enabled KV role has no HTTP domain override
- **THEN** its intended public HTTPS endpoint uses `<project>.<host-id>.<base-domain>`

#### Scenario: Native host ports change
- **WHEN** an operator changes preferred Postgres and KV ports to 15432 and 16379
- **THEN** native connection details use those ports while engine ports, PgBouncer connections, HTTP URLs, HTTP tokens, and gateway loopback ports remain unchanged

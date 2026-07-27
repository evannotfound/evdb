## MODIFIED Requirements

### Requirement: Dedicated native routing proxy
evdb SHALL manage one dedicated Traefik Compose project that owns host ports 5432 and 6379, uses the Docker provider on a dedicated external network, carries a concrete healthcheck, and routes every database through a TLS `HostSNI` router and unique role-qualified backend. Postgres and KV roles in the same project MAY share the same SNI hostname because they use separate native entrypoints.

#### Scenario: Project has both roles
- **WHEN** one project runs Postgres and KV on the shared network and clients connect with the same SNI hostname on ports 5432 and 6379
- **THEN** native SNI traffic reaches the matching type-qualified backend without a shared `postgres` or `redis` alias

### Requirement: Generated Compose and shared routing
The system SHALL generate independent YAML Compose definitions for every Postgres and KV role plus dedicated Traefik. Generated images SHALL resolve to immutable digests; no secret value SHALL appear in YAML. Only dedicated Traefik SHALL publish native database ports. Postgres, PgBouncer, Redis, Dragonfly, and HTTP sidecars SHALL use type-qualified projects and unique network identities. Native route uniqueness SHALL be scoped to the Traefik entrypoint so one project can use the same hostname for Postgres and KV.

#### Scenario: Generated Compose validates
- **WHEN** a candidate database or Traefik definition is rendered
- **THEN** `docker compose config --quiet` succeeds before the file is installed

#### Scenario: Two databases share native port
- **WHEN** several Postgres or KV roles run on one host
- **THEN** Traefik SNI routes each hostname on that role's native port to its unique database-specific backend

#### Scenario: One project shares one hostname across roles
- **WHEN** one project has both Postgres and KV roles
- **THEN** generated native routers use the same `HostSNI` hostname on separate `postgres` and `kv` entrypoints with separate role-qualified backend services

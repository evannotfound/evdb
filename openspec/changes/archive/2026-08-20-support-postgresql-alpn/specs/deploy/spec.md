## MODIFIED Requirements

### Requirement: Dedicated native routing proxy
evdb SHALL manage one dedicated Traefik Compose project that owns host ports 5432 and 6379, uses the Docker provider on a dedicated external network, carries a concrete healthcheck, and routes every database through a TLS `HostSNI` router and unique role-qualified backend. Postgres and KV roles in the same project MAY share the same SNI hostname because they use separate native entrypoints. The proxy SHALL negotiate TLS with PostgreSQL clients that advertise the registered `postgresql` ALPN protocol while retaining Traefik's existing ALPN protocols.

#### Scenario: Project has both roles
- **WHEN** one project runs Postgres and KV on the shared network and clients connect with the same SNI hostname on ports 5432 and 6379
- **THEN** native SNI traffic reaches the matching type-qualified backend without a shared `postgres` or `redis` alias

#### Scenario: PostgreSQL client advertises ALPN
- **WHEN** a PostgreSQL client connects through the native proxy and advertises the `postgresql` ALPN protocol
- **THEN** Traefik completes TLS negotiation and routes the connection through the matching Postgres SNI router

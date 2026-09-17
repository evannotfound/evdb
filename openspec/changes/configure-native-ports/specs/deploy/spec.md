## MODIFIED Requirements

### Requirement: Dedicated native routing proxy

evdb SHALL manage one dedicated Traefik Compose project that publishes every configured Postgres host port to its internal port 5432 and every configured KV host port to its internal port 6379. It SHALL use the Docker provider on a dedicated external network, carry a concrete healthcheck, and route every database through a TLS `HostSNI` router and unique role-qualified backend. Postgres and KV roles in the same project SHALL be able to share the same SNI hostname because they use separate native entrypoints. The proxy SHALL negotiate TLS with PostgreSQL clients that advertise the registered `postgresql` ALPN protocol while retaining Traefik's existing ALPN protocols.

All published ports for one protocol SHALL reach the same backend for a given hostname. Native port configuration SHALL NOT change database services, images, credentials, data, or HTTP gateways. Port mappings SHALL be rendered in stable order independent of the preferred port ordering in source.

#### Scenario: Project has both roles
- **WHEN** one project runs Postgres and KV on the shared network and clients connect with the same SNI hostname on their respective configured host ports
- **THEN** native SNI traffic reaches the matching type-qualified backend without a shared `postgres` or `redis` alias

#### Scenario: PostgreSQL client advertises ALPN
- **WHEN** a PostgreSQL client connects through the native proxy and advertises the `postgresql` ALPN protocol
- **THEN** Traefik completes TLS negotiation and routes the connection through the matching Postgres SNI router

#### Scenario: Temporary and standard ports coexist
- **WHEN** Postgres ports are `[15432, 5432]` and KV ports are `[16379, 6379]`
- **THEN** Traefik publishes both Postgres host ports to internal 5432 and both KV host ports to internal 6379 using the existing two entrypoints and database routes

#### Scenario: Preferred port changes without changing bindings
- **WHEN** the operator reverses the order of a protocol's port list without changing its members
- **THEN** the connection URL changes to the new first port and generated Compose remains unchanged, so that reorder alone does not recreate Traefik

#### Scenario: Temporary port is removed
- **WHEN** an operator replaces `[5432, 15432]` with `[5432]` and convergence succeeds
- **THEN** evdb no longer publishes 15432, continues publishing 5432, and has not restarted database or HTTP gateway containers

## MODIFIED Requirements

### Requirement: External proxy boundary
evdb SHALL record each sidecar's intended public domain and stable loopback endpoint as an integration contract. The default intended public domain SHALL be the same `<project>.<host-id>.<base-domain>` hostname used by native Postgres and KV access for that project. Dedicated database Traefik SHALL NOT route HTTP, and evdb SHALL NOT inspect, authenticate to, plan, configure, or change the external public HTTP proxy.

#### Scenario: KV settings change
- **WHEN** evdb regenerates an HTTP-enabled KV definition
- **THEN** no external proxy API, credential, image, route, certificate, or generated file is accessed

#### Scenario: Default HTTP domain is recorded
- **WHEN** an HTTP-enabled KV role has no explicit HTTP domain override
- **THEN** evdb reports its intended public HTTPS endpoint as `https://<project>.<host-id>.<base-domain>`

### Requirement: Complete Postgres connection details
The Postgres information view SHALL show hostname, port, username, database name, TLS requirement, and a complete percent-encoded `postgresql://` URL containing the current host-owned password. The hostname SHALL be the project hostname `<project>.<host-id>.<base-domain>`.

#### Scenario: Postgres info is shown
- **WHEN** the operator requests an installed Postgres role
- **THEN** the URL targets its project SNI hostname on port 5432 and requires TLS

### Requirement: Complete KV connection details
The KV information view SHALL show concrete Redis or Dragonfly engine, hostname, port, TLS requirement, and a complete percent-encoded `rediss://` URL containing the current host-owned password. The hostname SHALL be the project hostname `<project>.<host-id>.<base-domain>`. When HTTP is enabled, it SHALL also show intended public HTTPS endpoint and current token.

#### Scenario: Dragonfly HTTP info is shown
- **WHEN** the operator requests an HTTP-enabled Dragonfly-backed KV role
- **THEN** info contains native TLS URL, public HTTP contract, and usable HTTP token that use the project hostname unless an explicit HTTP domain override is configured

#### Scenario: KV HTTP is disabled
- **WHEN** the selected KV role has HTTP disabled
- **THEN** info contains native connection details and identifies HTTP as disabled

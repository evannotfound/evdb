## ADDED Requirements

### Requirement: Per-instance HTTP choice
Every KV instance SHALL have an `http.enabled` setting. The initial config SHALL enable all 11 managed KV sidecars, while later config changes MAY disable an individual sidecar.

#### Scenario: HTTP sidecar is disabled
- **WHEN** a KV instance sets `http.enabled` to false
- **THEN** its Compose has no serverless Redis HTTP service

### Requirement: Serverless HTTP sidecar
An enabled KV HTTP service SHALL run a pinned `serverless-redis-http` image, use `SRH_MODE=env`, use the configured connection limit, and receive its token and database connection through a private environment file built from `op://` references.

#### Scenario: HTTP image uses latest
- **WHEN** the target HTTP image uses `latest` or has no digest
- **THEN** config validation fails

#### Scenario: Secret is rendered
- **WHEN** Ansible renders the sidecar environment file
- **THEN** the file has mode `0600` and neither secret appears in Compose, logs, or Git

### Requirement: Loopback binding
Each enabled sidecar SHALL publish container port 80 only on its unique `127.0.0.1:133xx` host port. It SHALL NOT bind to a public host address.

#### Scenario: Sidecar binds publicly
- **WHEN** rendered Compose binds a sidecar to `0.0.0.0`, `::`, or an unspecified host address
- **THEN** validation fails

### Requirement: Backend isolation
Each sidecar SHALL connect to its own Redis or Dragonfly container through an instance-specific Docker name. It SHALL NOT use a shared network alias that another Compose project can also claim.

#### Scenario: Two KV projects share one Docker network
- **WHEN** both sidecars make authenticated requests at the same time
- **THEN** each request reads and writes only its own instance's data

### Requirement: External proxy boundary
The repository SHALL record each sidecar's intended domain and loopback port as an integration contract. It SHALL NOT inspect, authenticate to, plan, configure, or change the external HTTP proxy.

#### Scenario: Database deployment runs
- **WHEN** Ansible renders or applies database assets
- **THEN** no external proxy API, credentials, image, route, certificate, or generated file is accessed

### Requirement: Initial external route facts
The missing `oai-co-prod-02` route and two current `kv-na01.storage.evanovation.com` domains SHALL be recorded only as informational follow-ups for the external proxy owner during the later production move.

#### Scenario: Initial HTTP contract is rendered
- **WHEN** all `production-host` KV config is rendered
- **THEN** it contains 11 unique intended domains and 11 unique loopback ports without a proxy route plan

### Requirement: HTTP authentication
The serverless HTTP endpoint SHALL reject missing or incorrect tokens and accept the configured token without exposing it in responses or logs.

#### Scenario: Invalid token is used
- **WHEN** a request sends no token or the wrong token
- **THEN** the request is rejected and no database command runs

### Requirement: Local HTTP routing test
Local integration tests SHALL run Redis and Dragonfly sidecars and prove authentication, backend isolation, and loopback-only bindings without an external proxy.

#### Scenario: Two sidecars use separate loopback ports
- **WHEN** each sidecar receives authenticated writes on its loopback port
- **THEN** each value appears only in the matching backend

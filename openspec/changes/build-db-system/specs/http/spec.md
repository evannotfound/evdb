## ADDED Requirements

### Requirement: Per-instance HTTP choice
Every KV instance SHALL have `http.enabled` and `http.public` settings. The initial config SHALL set both values to true for all 11 managed KV instances, while later config changes MAY choose either value per instance.

#### Scenario: HTTP sidecar is disabled
- **WHEN** a KV instance sets `http.enabled` to false
- **THEN** its Compose has no serverless Redis HTTP service and no public HTTP route

#### Scenario: HTTP sidecar is private
- **WHEN** a KV instance enables HTTP but sets `http.public` to false
- **THEN** the sidecar binds to its loopback port and Nginx Proxy Manager has no route for it

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

### Requirement: Nginx Proxy Manager HTTPS
Nginx Proxy Manager SHALL remain the public HTTP server on ports 80 and 443. Every public KV HTTP endpoint SHALL use forced HTTPS and route its target domain to the sidecar's loopback port.

#### Scenario: Public endpoint is requested
- **WHEN** a client requests `https://<instance>.kv-montreal-01.storage.evanovation.com`
- **THEN** Nginx Proxy Manager presents a matching certificate and forwards the request to that instance's loopback port

### Requirement: Managed NPM routes
The repository SHALL keep the desired Nginx Proxy Manager domain and port mapping in KV config. Ansible SHALL plan and apply those proxy hosts through the Nginx Proxy Manager API and SHALL NOT edit generated files under `data/nginx/proxy_host`.

#### Scenario: Route already matches
- **WHEN** the Nginx Proxy Manager API reports the configured domain, port, TLS, and enabled state
- **THEN** the plan reports no change

#### Scenario: Production apply is not enabled
- **WHEN** the HTTP role runs without the explicit production apply flag
- **THEN** it reports the planned route changes and does not call a write endpoint

### Requirement: Initial public routes
The target config SHALL define public HTTPS routes for all 11 managed KV instances. It SHALL add the missing `oai-co-prod-02` target route and replace the two target `kv-na01.storage.evanovation.com` names with `kv-montreal-01.storage.evanovation.com` names during the later production move.

#### Scenario: Initial route list is rendered
- **WHEN** all `montreal-01` KV config is rendered
- **THEN** it contains 11 unique public domains and 11 unique loopback ports using the standard target suffix

### Requirement: HTTP authentication
The serverless HTTP endpoint SHALL reject missing or incorrect tokens and accept the configured token without exposing it in responses or logs.

#### Scenario: Invalid token is used
- **WHEN** a request sends no token or the wrong token
- **THEN** the request is rejected and no database command runs

### Requirement: Local HTTP routing test
Local integration tests SHALL run at least two KV instances behind one HTTPS listener and prove hostname routing, TLS, authentication, backend isolation, loopback-only sidecars, Redis support, and Dragonfly support.

#### Scenario: Two HTTPS hosts share port 443
- **WHEN** each hostname sends authenticated writes through the same HTTPS port
- **THEN** each value appears only in the matching backend

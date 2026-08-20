## MODIFIED Requirements

### Requirement: Per-database HTTP choice
KV project roles SHALL have authenticated HTTP access enabled by default and MAY disable it through guided or explicit settings. The role's concrete Redis or Dragonfly engine SHALL not change the public KV identity.

#### Scenario: Default KV is created
- **WHEN** the operator adds a KV role with default settings
- **THEN** generated KV Compose includes its HTTP API sidecar

#### Scenario: HTTP is disabled
- **WHEN** the operator confirms `http: false`
- **THEN** generated Compose removes the sidecar while native KV access and data remain unchanged

### Requirement: Serverless HTTP sidecar
An enabled KV HTTP service SHALL run its role-persisted immutable `serverless-redis-http` image, use `SRH_MODE=env`, enforce its configured connection limit, and receive its token and database connection through a private host-generated environment file.

#### Scenario: Sidecar secret file is generated
- **WHEN** an HTTP-enabled KV role is installed
- **THEN** its environment file has mode `0600` and neither credential appears in Compose, config, state, status, or logs

### Requirement: Loopback binding
Each enabled sidecar SHALL publish container port 80 only on its stable machine-assigned `127.0.0.1:<port>` endpoint. It SHALL NOT bind to a public host address.

#### Scenario: Candidate binds publicly
- **WHEN** generated Compose would bind the HTTP sidecar to `0.0.0.0`, `::`, or an unspecified address
- **THEN** validation fails before installation

### Requirement: Backend isolation
Each sidecar SHALL connect to the matching project/role KV service through a unique Docker service or network identity. It SHALL NOT use a shared `redis`, `dragonfly`, or other alias that another Compose project can claim.

#### Scenario: Two KV projects share the evdb network
- **WHEN** both sidecars make authenticated requests concurrently
- **THEN** each request reads and writes only its own project's KV data

### Requirement: External proxy boundary
evdb SHALL record each sidecar's intended public domain and stable loopback endpoint as an integration contract. Dedicated database Traefik SHALL NOT route HTTP, and evdb SHALL NOT inspect, authenticate to, plan, configure, or change the external public HTTP proxy.

#### Scenario: KV settings change
- **WHEN** evdb regenerates an HTTP-enabled KV definition
- **THEN** no external proxy API, credential, image, route, certificate, or generated file is accessed

### Requirement: HTTP authentication
The HTTP endpoint SHALL reject missing or incorrect tokens and accept the configured host-owned token without exposing it in responses, Compose, status, or logs.

#### Scenario: Invalid token is used
- **WHEN** a request sends no token or the wrong token
- **THEN** the sidecar rejects it and does not run the requested database command

### Requirement: Local HTTP routing test
Disposable integration tests SHALL run Redis and Dragonfly HTTP sidecars and prove authentication, backend isolation, stable loopback-only bindings, and generated YAML without calling an external proxy or production host.

#### Scenario: Two sidecars use separate ports
- **WHEN** tests write distinct values through two loopback sidecars
- **THEN** each value appears only in its matching KV engine

### Requirement: Detailed database view
`evdb database info PROJECT/ROLE` SHALL display configured role and concrete engine, live image version and health, data and Compose paths, latest backup summary, native connection fields, and HTTP fields when enabled. The command SHALL deliberately retrieve credentials from private host files and print them only to the terminal.

#### Scenario: Stopped database is shown
- **WHEN** the selected role is configured but stopped
- **THEN** info still shows settings, storage and connection details and clearly identifies stopped state

### Requirement: Complete Postgres connection details
The Postgres information view SHALL show hostname, port, username, database name, TLS requirement, and a complete percent-encoded `postgresql://` URL containing the current host-owned password.

#### Scenario: Postgres info is shown
- **WHEN** the operator requests an installed Postgres role
- **THEN** the URL targets its project SNI hostname on port 5432 and requires TLS

### Requirement: Complete KV connection details
The KV information view SHALL show concrete Redis or Dragonfly engine, hostname, port, TLS requirement, and a complete percent-encoded `rediss://` URL containing the current host-owned password. When HTTP is enabled, it SHALL also show intended public HTTPS endpoint and current token.

#### Scenario: Dragonfly HTTP info is shown
- **WHEN** the operator requests an HTTP-enabled Dragonfly-backed KV role
- **THEN** info contains native TLS URL, public HTTP contract, and usable HTTP token

#### Scenario: KV HTTP is disabled
- **WHEN** the selected KV role has HTTP disabled
- **THEN** info contains native connection details and identifies HTTP as disabled

### Requirement: Local credential resolution
The host-local command SHALL read required credentials from private host files only for an explicit `database info` request. It SHALL NOT require a workstation 1Password session, send credentials over an application SSH protocol, or fall back to partial credential output.

#### Scenario: Credential file is missing
- **WHEN** a required password or token cannot be read safely
- **THEN** info exits nonzero without printing a partial credential-bearing URL

### Requirement: Correct URL construction
Connection URLs SHALL percent-encode usernames, passwords, database names, and other URL components and SHALL use the protocol and TLS parameters required by the dedicated native routing and external HTTP contracts.

#### Scenario: Password contains URL punctuation
- **WHEN** a password contains reserved URL characters
- **THEN** the displayed URL parses back to the exact credential and endpoint

### Requirement: Deliberate terminal-only secret output
Database info SHALL intentionally reveal complete requested credentials only on terminal output. It SHALL NOT support JSON or persist those credentials in host YAML, machine state, Compose, backup records, activity, structured logs, errors, or status.

#### Scenario: Other command reads the same role
- **WHEN** status, logs, backup history, host check, or tool update is requested
- **THEN** that command reveals no password, token, or credential-bearing URL

### Requirement: Unambiguous details selector
Credential-bearing information SHALL require an exact project/role identity whenever a project contains both roles. A guided selection SHALL display the role and concrete engine before revealing credentials.

#### Scenario: Project name is ambiguous
- **WHEN** the operator requests info using only a project with Postgres and KV
- **THEN** evdb reveals no credentials and lists the two exact identities

## REMOVED Requirements

### Requirement: Initial external route facts
**Reason**: production-specific route gaps and legacy domains belong to the separate production migration, not the general host-owned HTTP contract.
**Migration**: Record and resolve them during the approved external proxy migration.

# HTTP Specification

## Purpose

Define database access details, authenticated KV HTTP sidecars, routing boundaries, and deliberate local credential display.

## Requirements

### Requirement: Per-database HTTP choice
KV databases SHALL derive authenticated HTTP access as enabled by default and MAY use a concise per-database override to disable it. The initial configuration SHALL enable all 11 managed KV sidecars.

#### Scenario: HTTP sidecar is disabled
- **WHEN** a KV database sets its HTTP override to false
- **THEN** its generated Compose has no serverless Redis HTTP service

### Requirement: Serverless HTTP sidecar
An enabled KV HTTP service SHALL run the locked `serverless-redis-http` image, use `SRH_MODE=env`, use the configured connection limit, and receive its token and database connection through a private environment file built from convention-based 1Password references.

#### Scenario: Secret is rendered
- **WHEN** apply renders the sidecar environment file
- **THEN** the file has mode `0600` and neither secret appears in Compose, logs, or Git

### Requirement: Loopback binding
Each enabled sidecar SHALL publish container port 80 only on its unique `127.0.0.1:133xx` host port. It SHALL NOT bind to a public host address.

#### Scenario: Sidecar binds publicly
- **WHEN** generated Compose binds a sidecar to `0.0.0.0`, `::`, or an unspecified host address
- **THEN** validation fails

### Requirement: Backend isolation
Each sidecar SHALL connect to its own Redis or Dragonfly container through a database-specific Docker name. It SHALL NOT use a shared network alias that another Compose project can claim.

#### Scenario: Two KV projects share one Docker network
- **WHEN** both sidecars make authenticated requests at the same time
- **THEN** each request reads and writes only its own database's data

### Requirement: External proxy boundary
The repository SHALL record each sidecar's intended domain and loopback port as an integration contract. It SHALL NOT inspect, authenticate to, plan, configure, or change the external HTTP proxy.

#### Scenario: Database deployment runs
- **WHEN** apply generates or installs database assets
- **THEN** no external proxy API, credentials, image, route, certificate, or generated file is accessed

### Requirement: Initial external route facts
The missing `oai-co-prod-02` route and two existing `kv-na01.storage.evanovation.com` domains SHALL be recorded only as informational follow-ups for the external proxy owner.

#### Scenario: Initial HTTP contract is rendered
- **WHEN** all `montreal-01` KV configuration is generated
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

### Requirement: Detailed database view
`evdb show <database>` SHALL display the selected database's typed identity, configured engine, live engine and image versions, running and health state, active release, data path, container name, latest backup summary, connection fields, and 1Password item name.

#### Scenario: Running database is shown
- **WHEN** the operator shows a reachable configured database
- **THEN** the command presents its derived configuration, live deployment facts, health, backup summary, and access details in one concise view

#### Scenario: Database is stopped
- **WHEN** the operator shows a configured database whose project is stopped
- **THEN** the command still shows its configured connection and storage details and identifies the stopped state

### Requirement: Complete Postgres connection details
The details view for Postgres SHALL show hostname, port, username, database name, TLS requirement, and a complete usable `postgresql://` connection URL containing the current 1Password password.

#### Scenario: Postgres details are shown
- **WHEN** the operator runs show for a Postgres database
- **THEN** the URL targets its SNI hostname on port 5432, includes the configured user and database, and requires TLS

### Requirement: Complete KV connection details
The details view for Redis or Dragonfly SHALL show hostname, port, TLS requirement, and a complete usable `rediss://` URL containing the current 1Password password. When HTTP access is enabled, it SHALL also show the public HTTPS endpoint and current HTTP token.

#### Scenario: Dragonfly with HTTP is shown
- **WHEN** the operator runs show for an HTTP-enabled Dragonfly database
- **THEN** the view contains its native TLS URL, public HTTPS endpoint, and usable HTTP token

#### Scenario: KV HTTP is disabled
- **WHEN** the selected Redis or Dragonfly database has HTTP disabled
- **THEN** the view contains native connection details and clearly identifies HTTP access as disabled

### Requirement: Local credential resolution
The controller SHALL resolve credentials for the details view directly from convention-based 1Password items on the local workstation. It SHALL NOT read credential values from the managed host or transmit resolved values over SSH.

#### Scenario: Local 1Password session is available
- **WHEN** all required fields can be read from the configured vault
- **THEN** the controller combines those values with non-secret derived and remote facts to render complete access details locally

#### Scenario: Credential cannot be resolved
- **WHEN** a required password or token is absent or local 1Password authentication fails
- **THEN** show exits nonzero without printing a partial credential-bearing URL or obtaining the value from the remote host

### Requirement: Correct URL construction
Connection URLs SHALL percent-encode usernames, passwords, database names, and other URL components and SHALL use the protocol and TLS parameters required by the configured routing contract.

#### Scenario: Password contains URL punctuation
- **WHEN** a database password contains reserved URL characters
- **THEN** the displayed connection URL parses back to the exact credential and correct endpoint

### Requirement: Deliberate terminal-only secret output
The details command SHALL intentionally reveal complete requested credentials in terminal output. It SHALL NOT write those credentials to source YAML, generated locks, runtime JSON, state, release manifests, structured logs, error messages, SSH arguments, SSH input, or remote output.

#### Scenario: Details command succeeds
- **WHEN** complete connection details are printed
- **THEN** no persistent local or remote artifact created by the command contains the password, token, or full credential-bearing URL

#### Scenario: Other command renders the same database
- **WHEN** status, plan, logs, backup history, or release history is requested
- **THEN** that command does not reveal a password, token, or credential-bearing URL

### Requirement: Unambiguous details selector
The details command SHALL accept a plain unique name and SHALL require `<type>/<name>` when multiple configured databases share the same name.

#### Scenario: Product has Postgres and KV databases
- **WHEN** the operator shows the shared product name without a type
- **THEN** the command reveals no credentials and lists the valid typed selectors

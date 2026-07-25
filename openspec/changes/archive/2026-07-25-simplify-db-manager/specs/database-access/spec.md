## ADDED Requirements

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

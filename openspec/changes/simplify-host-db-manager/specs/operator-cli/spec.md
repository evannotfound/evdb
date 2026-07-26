## ADDED Requirements

### Requirement: Guided root menu
Running `evdb` with an interactive terminal and no subcommand SHALL display current host health and a numbered menu for databases, backups, restore, host checks, and exit. The menu SHALL NOT require arrow-key terminal support or an additional TUI framework.

#### Scenario: Operator runs evdb over an interactive SSH session
- **WHEN** stdin and stdout are terminals and the operator runs `evdb`
- **THEN** the command shows host and database summary state followed by numbered actions

### Requirement: Context-aware database menu
The guided database flow SHALL list databases by `<project>/<role>`, show engine and current health, and expose only actions and settings valid for the selected role and concrete engine. Current values SHALL identify whether they are defaults or custom values.

#### Scenario: Operator configures Dragonfly
- **WHEN** the operator selects a Dragonfly-backed KV database and opens its settings
- **THEN** the menu shows mode, memory, threads, HTTP, and image settings without showing PostgreSQL pool settings

#### Scenario: Operator configures Redis
- **WHEN** the operator selects a Redis-backed KV database
- **THEN** the menu does not offer Dragonfly-only memory or thread settings

### Requirement: Guided settings session
The settings editor SHALL allow the operator to change multiple values, keep a value unchanged, reset an override to its default, discard the session, or save once. Saving SHALL show old and new values, identify services that restart, describe expected interruption and safety backup behavior, and require one confirmation before mutation.

#### Scenario: Several settings change
- **WHEN** the operator changes Dragonfly memory and threads in one guided session
- **THEN** evdb previews both changes and performs at most one settings transaction and one service restart

### Requirement: Grouped command interface
The canonical non-menu interface SHALL group commands under `database`, `backup`, and `host`, with top-level `status` and `restore`. It SHALL use the same domain operations as guided flows and SHALL NOT expose `plan`, `apply`, `releases`, `rollback`, `promote`, or a second internal CLI.

#### Scenario: Script creates a backup
- **WHEN** automation runs `evdb backup create PROJECT/ROLE`
- **THEN** it invokes the same checked backup operation available from the guided menu

### Requirement: TTY-aware prompting
Missing human inputs MAY prompt only when stdin and stdout are terminals. In non-interactive execution, missing values SHALL produce a concise error that names the required argument and shows a valid example; the command SHALL NOT wait for input.

#### Scenario: Systemd invokes an incomplete command
- **WHEN** a non-interactive process omits a required database identity
- **THEN** evdb exits nonzero immediately with a secret-free usage error

### Requirement: Deliberate confirmations
Every operation that starts, stops, restarts, reconfigures, restores, installs, or updates production services SHALL identify the host and affected database or host infrastructure and require confirmation unless `--yes` is supplied. `--yes` SHALL confirm a complete operation but SHALL NOT invent missing inputs.

#### Scenario: Operator declines a settings change
- **WHEN** the operator rejects the displayed settings transaction
- **THEN** source configuration, generated files, secrets, containers, data, and audit state remain unchanged

### Requirement: Database information view
`evdb database info PROJECT/ROLE` SHALL show configured settings, engine and image versions, live health, data and Compose paths, backup summary, native connection details, and HTTP details when enabled. It SHALL intentionally include current credentials in terminal output and SHALL NOT offer JSON output.

#### Scenario: KV information is displayed
- **WHEN** the operator requests information for an HTTP-enabled KV database
- **THEN** the terminal shows the concrete Redis or Dragonfly engine, native TLS URL, HTTP endpoint, and usable credentials without persisting them to logs or state

### Requirement: Secret-free machine output
`evdb status --json` and every other machine-readable output SHALL be versioned and contain no password, token, credential-bearing URL, secret-file content, or DNS-provider credential.

#### Scenario: Monitoring polls status
- **WHEN** a future monitor invokes `evdb status --json`
- **THEN** it receives parseable host and database status without any secret-bearing field

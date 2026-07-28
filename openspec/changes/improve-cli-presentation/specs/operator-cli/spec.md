## ADDED Requirements

### Requirement: Terminal presentation
When stdout is an interactive terminal, evdb SHALL render human output with an adaptive terminal presenter that may use color, table layout, emphasis, and progress/status indicators. The presenter SHALL preserve numbered menu choices, text-entry prompts, ordinary SSH compatibility, and plain behavior when stdout is not an interactive terminal or output is injected by tests.

#### Scenario: Operator uses guided CLI in a terminal
- **WHEN** stdin and stdout are terminals and the operator runs `evdb`
- **THEN** evdb renders status, menus, prompts, and operation feedback in a readable terminal layout while choices remain entered as numbers or text

#### Scenario: Output is captured by automation
- **WHEN** stdout is not a terminal or the caller injects an output function
- **THEN** evdb does not emit terminal control sequences or Rich markup as part of the command output

#### Scenario: Terminal cannot display color
- **WHEN** terminal settings disable color or indicate a plain terminal
- **THEN** evdb preserves readable text layout without requiring color to understand state or available actions

### Requirement: Human operation summaries
Interactive terminal mutations SHALL finish with concise human summaries rather than raw developer-shaped result dictionaries. The summary SHALL name the affected host, database or host component, result state, changed settings or selected operation when relevant, and important backup or snapshot identifiers when relevant. Non-terminal mutation output MAY retain existing machine-oriented formatting unless a command explicitly defines a JSON mode.

#### Scenario: Database creation succeeds in guided mode
- **WHEN** an operator creates a database from the guided terminal menu and the operation succeeds
- **THEN** evdb reports that the selected `<project>/<role>` is healthy and includes relevant changed settings or safety backup information without printing a raw JSON dictionary

#### Scenario: Mutation runs outside a terminal
- **WHEN** automation runs a mutation command without interactive stdout
- **THEN** evdb preserves parseable or existing machine-oriented result text and does not require Rich terminal rendering

## MODIFIED Requirements

### Requirement: Guided root menu
Running `evdb` with an interactive terminal and no subcommand SHALL display current host health and a numbered menu for databases, backups, restore, host checks, and exit. The menu SHALL use an SSH-safe terminal presentation and SHALL NOT require arrow-key terminal support, cursor-addressed navigation, or an additional full-screen TUI framework.

#### Scenario: Operator runs evdb over an interactive SSH session
- **WHEN** stdin and stdout are terminals and the operator runs `evdb`
- **THEN** the command shows host and database summary state followed by numbered actions

### Requirement: Context-aware database menu
The guided database flow SHALL list databases by `<project>/<role>`, show engine and current health, and expose only actions and settings valid for the selected role and concrete engine. Current values SHALL identify whether they are defaults or custom values. Interactive terminal output SHALL present this information in a readable layout without changing the numbered selection model.

#### Scenario: Operator configures Dragonfly
- **WHEN** the operator selects a Dragonfly-backed KV database and opens its settings
- **THEN** the menu shows mode, memory, threads, HTTP, and image settings without showing PostgreSQL pool settings

#### Scenario: Operator configures Redis
- **WHEN** the operator selects a Redis-backed KV database
- **THEN** the menu does not offer Dragonfly-only memory or thread settings

### Requirement: Deliberate confirmations
Every operation that starts, stops, restarts, reconfigures, restores, installs, or updates production services SHALL identify the host and affected database or host infrastructure and require confirmation unless `--yes` is supplied. `--yes` SHALL confirm a complete operation but SHALL NOT invent missing inputs. In an interactive terminal, confirmations SHALL be shown outside long-running progress indicators and SHALL present preview details in a readable human layout.

#### Scenario: Operator declines a settings change
- **WHEN** the operator rejects the displayed settings transaction
- **THEN** source configuration, generated files, secrets, containers, data, and audit state remain unchanged

### Requirement: Secret-free machine output
`evdb status --json` and every other machine-readable output SHALL be versioned and contain no password, token, credential-bearing URL, secret-file content, or DNS-provider credential. Terminal presentation SHALL NOT change the stdout contract of `evdb status --json`, which SHALL emit exactly one parseable JSON document and no menu, table, progress indicator, or styled terminal text.

#### Scenario: Monitoring polls status
- **WHEN** a future monitor invokes `evdb status --json`
- **THEN** it receives parseable host and database status without any secret-bearing field

#### Scenario: Operator requests JSON status from a terminal
- **WHEN** an operator invokes `evdb status --json` while stdout is a terminal
- **THEN** stdout contains one parseable JSON document rather than Rich-rendered status output

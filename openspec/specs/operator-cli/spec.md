# Operator CLI Specification

## Purpose

Define the guided and explicit host-local operator command interface.

## Requirements

### Requirement: Guided root menu
Running `evdb` with an interactive terminal and no subcommand SHALL display current host health and a numbered menu for databases, backups, restore, host checks, and exit. The menu SHALL use an SSH-safe terminal presentation and SHALL NOT require arrow-key terminal support, cursor-addressed navigation, or an additional full-screen TUI framework.

#### Scenario: Operator runs evdb over an interactive SSH session
- **WHEN** stdin and stdout are terminals and the operator runs `evdb`
- **THEN** the command shows host and database summary state followed by numbered actions

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

### Requirement: Context-aware database menu
The guided database flow SHALL list databases by `<project>/<role>`, show engine and current health, and expose only actions and settings valid for the selected role and concrete engine. Current values SHALL identify whether they are defaults or custom values. Interactive terminal output SHALL present this information in a readable layout without changing the numbered selection model.

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

### Requirement: Secure initial Postgres password input
Direct Postgres creation SHALL accept an optional `--password-file PATH` whose content becomes the
initial password for the fixed `default` login. Guided Postgres creation SHALL offer a masked password
prompt where blank input selects generation. evdb SHALL NOT accept a password value as a command
argument, environment variable, ordinary echoed prompt, preview field, log field, or machine-readable
output. A supplied password SHALL be non-empty and contain no NUL, carriage return, or embedded line
feed after one trailing line ending is removed.

#### Scenario: Script supplies a password file
- **WHEN** automation runs `evdb database add app-prod-01 postgres --password-file PATH` with a valid private file
- **THEN** evdb reads the password from the file, protects it from subprocess output, and does not place it in the process arguments or operation preview

#### Scenario: Guided creation keeps the entered password hidden
- **WHEN** an operator enters a password in the guided Postgres add flow
- **THEN** the terminal does not echo or redisplay the value and the confirmed operation uses it as the initial managed password

#### Scenario: Guided creation requests generation
- **WHEN** an operator leaves the guided Postgres password prompt blank
- **THEN** evdb generates the initial managed password without requiring another credential input

#### Scenario: Password file has invalid content
- **WHEN** the selected password file is empty or contains a NUL or embedded line break
- **THEN** evdb rejects creation before changing source, state, secrets, Compose, containers, routes, or data

### Requirement: Deliberate confirmations
Every operation that starts, stops, restarts, reconfigures, restores, installs, or updates production services SHALL identify the host and affected database or host infrastructure and require confirmation unless `--yes` is supplied. `--yes` SHALL confirm a complete operation but SHALL NOT invent missing inputs. In an interactive terminal, confirmations SHALL be shown outside long-running progress indicators and SHALL present preview details in a readable human layout.

#### Scenario: Operator declines a settings change
- **WHEN** the operator rejects the displayed settings transaction
- **THEN** source configuration, generated files, secrets, containers, data, and audit state remain unchanged

### Requirement: Human operation summaries
Interactive terminal mutations SHALL finish with concise human summaries rather than raw developer-shaped result dictionaries. The summary SHALL name the affected host, database or host component, result state, changed settings or selected operation when relevant, and important backup or snapshot identifiers when relevant. Non-terminal mutation output MAY retain existing machine-oriented formatting unless a command explicitly defines a JSON mode.

#### Scenario: Database creation succeeds in guided mode
- **WHEN** an operator creates a database from the guided terminal menu and the operation succeeds
- **THEN** evdb reports that the selected `<project>/<role>` is healthy and includes relevant changed settings or safety backup information without printing a raw JSON dictionary

#### Scenario: Mutation runs outside a terminal
- **WHEN** automation runs a mutation command without interactive stdout
- **THEN** evdb preserves parseable or existing machine-oriented result text and does not require Rich terminal rendering

### Requirement: Database information view
`evdb database info PROJECT/ROLE` SHALL show configured settings, engine and image versions, live health, data and Compose paths, backup summary, native connection details, and HTTP details when enabled. It SHALL intentionally include current credentials in terminal output and SHALL NOT offer JSON output.

#### Scenario: KV information is displayed
- **WHEN** the operator requests information for an HTTP-enabled KV database
- **THEN** the terminal shows the concrete Redis or Dragonfly engine, native TLS URL, HTTP endpoint, and usable credentials without persisting them to logs or state

### Requirement: Secret-free machine output
`evdb status --json` and every other machine-readable output SHALL be versioned and contain no password, token, credential-bearing URL, secret-file content, or DNS-provider credential. Terminal presentation SHALL NOT change the stdout contract of `evdb status --json`, which SHALL emit exactly one parseable JSON document and no menu, table, progress indicator, or styled terminal text.

#### Scenario: Monitoring polls status
- **WHEN** a future monitor invokes `evdb status --json`
- **THEN** it receives parseable host and database status without any secret-bearing field

#### Scenario: Operator requests JSON status from a terminal
- **WHEN** an operator invokes `evdb status --json` while stdout is a terminal
- **THEN** stdout contains one parseable JSON document rather than Rich-rendered status output

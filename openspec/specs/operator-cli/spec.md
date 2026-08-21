# Operator CLI Specification

## Purpose

Define the guided and explicit host-local operator command interface.

## Requirements

### Requirement: Guided root menu
Running `evdb` in an interactive terminal SHALL show a compact host summary and one numbered database
overview. Database rows SHALL be directly selectable, followed by Add database, Host, and Exit. The
root SHALL NOT expose separate top-level backup, restore, or host-maintenance categories.

#### Scenario: Operator runs evdb over SSH
- **WHEN** stdin and stdout are terminals and the operator runs `evdb`
- **THEN** the command shows `#`, database identity, engine, runtime status, and latest backup before the numbered choices

### Requirement: Guided host actions
Selecting Host from the guided root SHALL show mount-aware state and database-root storage, routing
infrastructure, repository, timer, and current host errors followed by Restart traffic and Back actions.
Restart traffic SHALL warn that active database connections may briefly drop and require confirmation
before changing Traefik runtime state.

#### Scenario: Operator reviews custom storage
- **WHEN** the host configures several database roots
- **THEN** the Host view shows every exact path, backing mount and source, capacity, and assigned roles

#### Scenario: Operator declines traffic restart
- **WHEN** the operator selects Restart traffic and declines confirmation
- **THEN** Traefik and every database remain unchanged

#### Scenario: Operator confirms traffic restart
- **WHEN** the operator confirms Restart traffic
- **THEN** the Host view reports completion only after the dedicated Traefik container becomes healthy

### Requirement: Terminal presentation
Interactive output SHALL use an SSH-safe terminal presenter with readable semantic emphasis and compact
layout. In a color-capable terminal, headings and questions SHALL be emphasized, effective prompt defaults
SHALL be visually distinct from alternatives, and healthy, warning, and failure states SHALL use distinct
styles. Color SHALL supplement explicit wording rather than replace any state label. The presenter SHALL
separate screens, results, errors, and prompts with blank lines, preserve numbered input, and avoid
cursor-addressed navigation except for replacing one transient loading-status line or updating the
database overview while backup status loads. Plain or injected output SHALL contain no terminal control
sequences or Rich markup.

#### Scenario: Guided flow advances between screens
- **WHEN** the operator selects a database and then a backup action
- **THEN** headings, content, result, and next prompt are visually separated without clearing prior terminal history

#### Scenario: Operator scans mixed database health
- **WHEN** a terminal overview contains healthy, stopped, stale, unknown, or failed operational states
- **THEN** the known state values use the corresponding success, warning, or failure style while retaining their explicit text labels

#### Scenario: Prompt has a default answer
- **WHEN** a guided terminal question shows `[Y/n]`, `[y/N]`, or another bracketed default
- **THEN** the question and effective default are visually distinguishable while the entered answer remains ordinary terminal text

#### Scenario: Output is captured by automation
- **WHEN** stdout is not a terminal or output is injected by a test
- **THEN** evdb emits the existing stable plain text without style control sequences

#### Scenario: Terminal cannot display color
- **WHEN** terminal settings disable color
- **THEN** labels and wording preserve every state distinction and interaction default without requiring color

#### Scenario: Interactive assessment takes noticeable time
- **WHEN** local host and database checks finish before backup repository assessment
- **THEN** interactive `evdb` and human `evdb status` show the complete overview with loading durable Backup cells, update those cells in place, and do not prompt for a guided choice until backup assessment finishes

#### Scenario: Other interactive work takes noticeable time
- **WHEN** Details, backup, log, or another synchronous view waits for external work
- **THEN** one transient terminal status line identifies the current phase and is removed before the completed screen or error is appended

### Requirement: Context-aware database menu
The guided root SHALL use a table only for the database overview. Selecting a role SHALL open a
key/value summary and numbered actions for Details, Connection, Settings, Start or Stop, Restart,
Backups, Logs, and Back. Full image references, credentials, generated paths, backup records, and error
details SHALL appear only in their relevant submenu.

#### Scenario: Operator opens Postgres details
- **WHEN** the operator selects a Postgres role and opens Details
- **THEN** the view shows its full configured images and PgBouncer settings without widening the root table

#### Scenario: Details refreshes backup information
- **WHEN** the operator opens Details from a database menu whose runtime was just assessed
- **THEN** evdb reuses that runtime assessment, performs one fresh matching backup-history query, and does not repeat the complete host assessment

#### Scenario: Operator configures Redis
- **WHEN** the operator opens Settings for Redis-backed KV
- **THEN** the menu omits Dragonfly-only memory and thread settings

#### Scenario: Narrow terminal displays the root
- **WHEN** terminal width is 60 columns
- **THEN** the root does not print image digests and the database identity remains readable without being replaced by an ellipsis-only value

### Requirement: Guided settings session
The guided settings editor SHALL expose only settings valid for the selected engine, let the operator
keep or change several values, and require one final Save confirmation. Saving SHALL write once,
rerender once, start Compose once, and report health. It SHALL NOT describe safety backups, rollback,
deployment transactions, or service contract hashes.

#### Scenario: Several settings change
- **WHEN** the operator changes Dragonfly memory and threads and confirms Save
- **THEN** evdb performs one source update and one Compose invocation

#### Scenario: Settings are discarded
- **WHEN** the operator leaves without confirming Save
- **THEN** source, generated files, and containers remain unchanged

### Requirement: Grouped command interface
The non-menu interface SHALL provide top-level `init` and `status`, group retained operations under
`database` and `backup`, and expose `--version`. It SHALL NOT expose restore, backup test, retention,
prune, repository check, host setup, host check, host update, host uninstall, plan, apply, releases,
rollback, promote, or a second internal CLI.

#### Scenario: Script creates one backup
- **WHEN** automation runs `evdb backup create PROJECT/ROLE`
- **THEN** it invokes the same checked create-and-upload operation available under the database Backups submenu

#### Scenario: Systemd backs up the host
- **WHEN** the packaged service runs `evdb backup create --all`
- **THEN** every configured durable database is attempted and the command exits nonzero if any attempt fails

### Requirement: TTY-aware prompting
Missing human inputs MAY prompt only when stdin and stdout are terminals. In non-interactive execution, missing values SHALL produce a concise error that names the required argument and shows a valid example; the command SHALL NOT wait for input.

#### Scenario: Systemd invokes an incomplete command
- **WHEN** a non-interactive process omits a required database identity
- **THEN** evdb exits nonzero immediately with a secret-free usage error

### Requirement: Secure initial Postgres password input
Direct Postgres creation SHALL accept optional `--password-file PATH`; guided creation SHALL use a
masked prompt where blank selects generation. The validated value SHALL be stored only in
`secrets.yml` and derived private role files. No inline password argument, environment input, echoed
prompt, preview, status, or machine output SHALL be accepted.

#### Scenario: Script supplies a password file
- **WHEN** automation adds Postgres with a valid private password file
- **THEN** evdb stores the value under the matching `secrets.yml` role without placing it in process arguments or output

#### Scenario: Guided creation requests generation
- **WHEN** the masked prompt is left blank
- **THEN** evdb generates and stores a valid initial password

#### Scenario: Password file is invalid
- **WHEN** the file is empty, non-private, symlinked, non-regular, or contains NUL or embedded line breaks
- **THEN** creation fails before source or services change

### Requirement: Deliberate confirmations
Guided database creation and settings editing SHALL present one concise summary and require one final
Create or Save confirmation. Explicit direct database lifecycle, backup, and initialization commands
SHALL execute without a generic confirmation or `--yes`, except the installer MAY use `init --yes` to
assert that required existing source is complete without prompting.

#### Scenario: Operator declines guided creation
- **WHEN** the operator rejects the final Create prompt
- **THEN** source, generated files, credentials, containers, routes, and data remain unchanged

#### Scenario: Operator runs an explicit backup
- **WHEN** the operator invokes `evdb backup create PROJECT/ROLE`
- **THEN** backup starts without repeating a confirmation of the already explicit command

### Requirement: Human operation summaries
Interactive and direct human commands SHALL finish with concise text naming the affected database or
host operation and result. Backup completion SHALL include time, backup identity, and snapshot ID.
Commands SHALL NOT print raw Python dictionaries or developer-shaped JSON unless a documented JSON
flag was explicitly requested.

#### Scenario: Database creation succeeds
- **WHEN** a database reaches native health
- **THEN** evdb reports that `<project>/<role>` is healthy and offers its selected next view without raw result data

#### Scenario: Backup upload fails
- **WHEN** checked local files exist but Restic fails
- **THEN** evdb names the database, repository URL, retained local backup, and original Restic failure

### Requirement: Database information view
The selected database Details view and `database info` SHALL show configured settings, full images,
live health, data and Compose paths, backup summary, and native or HTTP connection details. Credentials
SHALL appear only in the deliberate Connection view or terminal `database info`; neither SHALL offer
JSON output.

#### Scenario: KV information is displayed
- **WHEN** the operator opens an HTTP-enabled KV Connection view
- **THEN** evdb shows its native TLS URL, HTTP endpoint, and usable credentials

#### Scenario: Root overview is displayed
- **WHEN** the operator has not selected a database
- **THEN** no password, token, full connection URL, full image reference, or image digest is printed

### Requirement: Secret-free machine output
`evdb status --json` and non-interactive structured output SHALL contain no database, HTTP, DNS,
Restic, or rclone credential value. Exact credentials and required encoded forms SHALL be replaced,
while repository URLs, remote names, ordinary paths, image references, snapshot IDs, and unrelated
subprocess stderr SHALL remain visible.

#### Scenario: Monitoring polls status
- **WHEN** monitoring invokes `evdb status --json`
- **THEN** it receives one parseable status document without credentials

#### Scenario: Restic reports a missing repository
- **WHEN** Restic stderr includes the configured repository URL
- **THEN** evdb prints that URL unchanged rather than replacing it with a redaction marker

#### Scenario: Subprocess prints an exact credential
- **WHEN** command output includes a value loaded from `secrets.yml` or credential fields in `rclone.conf`
- **THEN** evdb replaces that exact value before display or journald capture

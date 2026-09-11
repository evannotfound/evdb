## MODIFIED Requirements

### Requirement: Terminal presentation
Interactive output SHALL use an SSH-safe terminal presenter with readable semantic emphasis and compact
layout. In a color-capable terminal, headings and questions SHALL be emphasized, effective prompt defaults
SHALL be visually distinct from alternatives, and healthy, warning, pending, and failure states SHALL use
distinct styles. Color SHALL supplement explicit wording rather than replace any state label. The
presenter SHALL separate screens, results, errors, and prompts with blank lines, preserve numbered input,
and avoid cursor-addressed navigation except for updating the active guided overview while background
status loads. Plain or injected output SHALL contain no terminal control sequences or Rich markup.

#### Scenario: Guided flow advances between screens
- **WHEN** the operator selects a database and then a backup action
- **THEN** headings, content, result, and next prompt are visually separated without clearing prior terminal history

#### Scenario: Operator scans mixed database health
- **WHEN** a terminal overview contains checking, healthy, stopped, stale, unknown, or failed operational states
- **THEN** the known state values use the corresponding pending, success, warning, or failure style while retaining their explicit text labels

#### Scenario: Prompt has a default answer
- **WHEN** a guided terminal question shows `[Y/n]`, `[y/N]`, or another bracketed default
- **THEN** the question and effective default are visually distinguishable while the entered answer remains ordinary terminal text

#### Scenario: Output is captured by automation
- **WHEN** stdout is not a terminal or output is injected by a test
- **THEN** evdb emits the existing stable plain text without style control sequences

#### Scenario: Terminal cannot display color
- **WHEN** terminal settings disable color
- **THEN** labels and wording preserve every state distinction and interaction default without requiring color

#### Scenario: Guided status loads in the background
- **WHEN** the operator starts guided `evdb` while runtime or repository assessment is incomplete
- **THEN** evdb immediately shows stable numbered choices and accepts input while pending cells update in the active overview

#### Scenario: Other interactive work takes noticeable time
- **WHEN** Details, backup, log, or another synchronous view waits for external work
- **THEN** one transient terminal status line identifies the current phase and is removed before the completed screen or error is appended

### Requirement: Context-aware database menu
The guided root SHALL use a table only for the database overview. Selecting a role SHALL immediately open
a key/value summary and numbered actions for Details, Connection, Settings, Start or Stop, Restart,
Backups, Logs, and Back. A role whose runtime assessment is pending SHALL show `checking`, offer a neutral
Start/Stop action, and wait only when the selected action requires live runtime state. Full image
references, credentials, generated paths, backup records, and error details SHALL appear only in their
relevant submenu.

#### Scenario: Operator opens Postgres details
- **WHEN** the operator selects a Postgres role and opens Details
- **THEN** the view shows its full configured images and PgBouncer settings without widening the root table

#### Scenario: Details refreshes backup information
- **WHEN** the operator opens Details before or after background status completes
- **THEN** evdb obtains the selected runtime state, performs one fresh matching backup-history query, and does not repeat the complete host assessment

#### Scenario: Operator selects a pending role
- **WHEN** the operator enters a database number before its runtime assessment completes
- **THEN** its submenu opens with `Status: checking` and non-runtime actions remain immediately available

#### Scenario: Operator configures Redis
- **WHEN** the operator opens Settings for Redis-backed KV
- **THEN** the menu omits Dragonfly-only memory and thread settings

#### Scenario: Narrow terminal displays the root
- **WHEN** terminal width is 60 columns
- **THEN** the root does not print image digests and the database identity remains readable without being replaced by an ellipsis-only value

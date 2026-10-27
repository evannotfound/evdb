## ADDED Requirements

### Requirement: Cached backup views and explicit refresh
Status, database Details, and backup history SHALL use dated local backup evidence without querying the remote repository. Human history SHALL identify remote availability as cached and show the last successful inventory check or not-checked state, plus any latest refresh failure. The CLI SHALL offer `evdb backup refresh`, and the guided Backups submenu SHALL offer a clearly host-wide refresh action. Refresh SHALL report completion or an actionable failure and invalidate guided cached status.

#### Scenario: Operator requests fresh inventory
- **WHEN** the operator runs `evdb backup refresh` or selects the guided host-wide backup refresh action
- **THEN** evdb refreshes the saved inventory once and reports its result

#### Scenario: Operator opens old history
- **WHEN** saved remote inventory is a day old
- **THEN** history displays it with its check time without waiting for OneDrive

## MODIFIED Requirements

### Requirement: Context-aware database menu
The guided root SHALL use a table only for the database overview. Selecting a role SHALL immediately open a key/value summary and numbered actions for Details, Connection, Settings, Start or Stop, Restart, Backups, Logs, Delete, and Back. A role whose runtime assessment is pending SHALL show `checking`, offer a neutral Start/Stop action, and wait only when the selected action requires live runtime state. Full image references, credentials, generated paths, backup records, and error details SHALL appear only in their relevant submenu.

#### Scenario: Operator opens Postgres details
- **WHEN** the operator selects a Postgres role and opens Details
- **THEN** the view shows its full configured images and PgBouncer settings without widening the root table

#### Scenario: Details reads backup information
- **WHEN** the operator opens Details before or after background status completes
- **THEN** evdb obtains the selected runtime state and reads dated local backup history without querying the remote repository or repeating the complete host assessment

#### Scenario: Operator selects a pending role
- **WHEN** the operator enters a database number before its runtime assessment completes
- **THEN** its submenu opens with `Status: checking` and non-runtime actions remain immediately available

#### Scenario: Operator configures Redis
- **WHEN** the operator opens Settings for Redis-backed KV
- **THEN** the menu omits Dragonfly-only memory and thread settings

#### Scenario: Operator deletes a database
- **WHEN** the operator confirms deletion, types the exact database identity, and types the required deletion phrase
- **THEN** evdb removes local runtime, source, credentials, generated files, and live data while retaining local and remote backups

#### Scenario: Remote backup is not current before deletion
- **WHEN** the selected role has no current known remote backup
- **THEN** deletion requires the exact phrase `DELETE WITHOUT BACKUP` rather than `DELETE`

#### Scenario: Narrow terminal displays the root
- **WHEN** terminal width is 60 columns
- **THEN** the root does not print image digests and the database identity remains readable without being replaced by an ellipsis-only value

## MODIFIED Requirements

### Requirement: Independent backup operation state
The system SHALL create and upload one project/role backup at a time. Complete local manifests and
tagged Restic snapshots SHALL be the backup history; v1 SHALL NOT duplicate latest operation,
verification, safety, or error records in machine state. A failure for one role in `--all` SHALL NOT
prevent later roles from being attempted.

#### Scenario: One project role fails
- **WHEN** one database cannot create or upload a backup during `backup create --all`
- **THEN** its error is reported, remaining durable databases are attempted, and the overall command exits nonzero

#### Scenario: Later upload fails
- **WHEN** a checked local backup completes but upload fails after an earlier snapshot succeeded
- **THEN** the new local folder remains available and history still includes the earlier remote snapshot

### Requirement: Backup manifest
Each completed backup SHALL contain `backup.json` with host, project, role, concrete engine, start and
finish times, configured image, engine version, backup format, file names, sizes, SHA-256 hashes,
checks performed, purpose, and upload state. It SHALL contain no restore verification or safety-backup
transaction fields.

#### Scenario: Backup file changes after completion
- **WHEN** a completed file no longer matches its recorded size or hash
- **THEN** the backup is rejected before upload or history treats it as valid

### Requirement: Backup locks and timeouts
Backup creation SHALL use the selected project/role lock, every Restic operation SHALL use the one host
repository lock, and every external command SHALL have a finite timeout.

#### Scenario: Backup is already running
- **WHEN** another process holds the selected project/role lock
- **THEN** the new run exits without starting a second engine backup

#### Scenario: Another upload holds the repository
- **WHEN** a backup reaches Restic while another role is uploading
- **THEN** it waits for the repository lock or fails clearly after the lock timeout

### Requirement: Direct database backup
`evdb backup create PROJECT/ROLE` SHALL run the selected durable database's checked engine backup and
upload it to the configured host repository. `evdb backup create --all` SHALL run the same operation
sequentially for every durable role, continue after individual failures, and return failure when any
role failed.

#### Scenario: Backup succeeds
- **WHEN** engine validation, manifest hashing, and Restic upload all succeed
- **THEN** the command reports the human time, backup identity, snapshot ID, repository URL, and success

#### Scenario: Upload fails after local backup
- **WHEN** checked local files complete but Restic upload fails
- **THEN** the local backup remains, the repository and original error are shown, and no remote recovery point is claimed

#### Scenario: Cache backup is requested
- **WHEN** a cache-mode KV role is selected
- **THEN** evdb reports that backups are disabled and invokes neither the engine backup nor Restic

#### Scenario: All-database backup has no durable roles
- **WHEN** the scheduled command runs on a host with no durable database
- **THEN** it exits successfully after reporting that no backup was due

### Requirement: Backup history
`evdb backup list PROJECT/ROLE` SHALL merge valid completed local folders and matching tagged Restic
snapshots in reverse chronological order. Each item SHALL show human time, purpose, local and remote
availability, backup ID, and snapshot ID when present. History SHALL NOT contain backup-test state.

#### Scenario: Local and remote records refer to one backup
- **WHEN** a local manifest records a confirmed Restic snapshot ID
- **THEN** history presents one item with both local and remote availability

#### Scenario: Only a remote snapshot remains
- **WHEN** local cleanup removed a backup that remains in Restic
- **THEN** history still lists that remote snapshot

### Requirement: Secret-safe backup output
Backup commands, manifests, history, and errors SHALL redact exact database, HTTP, DNS, Restic, and
rclone credential values. Repository URLs, remote names, backup and snapshot IDs, file paths, image
references, and unrelated command output SHALL remain visible.

#### Scenario: Restic fails against the configured repository
- **WHEN** Restic stderr names the repository and also contains an exact managed credential
- **THEN** the operator sees the repository and error context with only the credential value replaced

## REMOVED Requirements

### Requirement: Pre-change safety backup
**Reason**: V1 backup is an explicit and scheduled operation, not a deployment or restore gate.

**Migration**: Run `evdb backup create PROJECT/ROLE` manually before a risky settings edit if desired.

### Requirement: Full backup verification
**Reason**: Isolated restore-based testing retains most of the restore engine and scheduling system
that this change removes.

**Migration**: Rely on engine-native creation checks and manifest hashes until a focused restore
capability is designed.

### Requirement: Remote snapshot selection
**Reason**: Exact snapshot selection existed for backup test and restore, neither of which is in v1.

**Migration**: Backup history remains available for manual Restic operations.

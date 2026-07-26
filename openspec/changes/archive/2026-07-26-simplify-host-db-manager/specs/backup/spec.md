## ADDED Requirements

### Requirement: Pre-change safety backup
Before a durable database settings transaction recreates the primary container or before live restore replaces data, evdb SHALL create, validate, and upload a checked backup of current live data. The operation SHALL record the exact safety snapshot and SHALL not proceed to service or data mutation if upload fails.

#### Scenario: Restore prepares to replace live data
- **WHEN** isolated restore verification succeeds for a durable database
- **THEN** evdb confirms a new Restic snapshot of current live data before asking for the final outage confirmation

## MODIFIED Requirements

### Requirement: Checked Dragonfly backup
Dragonfly backup SHALL call `SAVE DF` with a unique basename for every run, require the command to finish successfully, and capture exactly one complete native snapshot generation containing its summary and every numbered shard. It SHALL copy, size, and hash every generated DFS file and remove only the source files created by that run. It SHALL reject missing summaries, missing shards, mixed generations, unsafe names, and unlisted files.

#### Scenario: Old Dragonfly snapshots exist
- **WHEN** the data directory contains old RDB or DFS files
- **THEN** backup captures only the newly named native DFS generation and leaves every old file unchanged

#### Scenario: Native snapshot shard is missing
- **WHEN** a `SAVE DF` result has no summary or has an incomplete numbered shard set
- **THEN** backup fails without publishing a completed backup folder

### Requirement: Independent backup operation state
The system SHALL back up one project/role at a time and SHALL store latest local completion, latest successful upload, latest successful verification, per-backup verification records, safety-backup purpose, and current errors independently. A failed operation for one project/role SHALL NOT overwrite earlier success or prevent operations for other databases. Conflicting work for the same project/role or Restic repository SHALL remain locked.

#### Scenario: One project role fails
- **WHEN** one database cannot create a backup
- **THEN** its project/role records a clear failure while other eligible database jobs continue

#### Scenario: Later upload fails
- **WHEN** a new local backup completes but upload fails after an earlier recent upload succeeded
- **THEN** status remains fresh from the earlier snapshot and reports the current upload failure separately

#### Scenario: Later backup test fails
- **WHEN** earlier backups have successful test records and checking a later backup fails
- **THEN** earlier records remain verified and the selected backup records its own failure

### Requirement: Safe backup folder
Each run SHALL use a private `.partial` folder under `/var/lib/evdb/backups/<project>/<role>` and SHALL rename it only after all required files, checks, hashes, and `backup.json` are complete. Backup files and folders SHALL NOT be readable by unrelated users.

#### Scenario: Process stops during backup
- **WHEN** the process exits before the run is complete
- **THEN** no partial folder is treated as a valid backup

### Requirement: Backup manifest
Each completed backup SHALL contain `backup.json` with the host, project, role, concrete engine, start and finish times, source image and engine version, backup format, file names, sizes, SHA-256 hashes, checks performed, purpose, and upload state.

#### Scenario: Backup file changes after completion
- **WHEN** a completed file no longer matches its recorded size or hash
- **THEN** the backup is rejected before upload, test, or restore

### Requirement: Backup locks and timeouts
The system SHALL use the project/role operation lock for backup creation, use the repository lock for Restic work, and enforce a timeout for every external command.

#### Scenario: Backup is already running
- **WHEN** another process holds the selected project/role lock
- **THEN** the new run exits without starting a second backup

### Requirement: Local backup retention
The system SHALL keep at least the two newest uploaded backups for each durable project/role. It SHALL NOT automatically delete an unuploaded backup and SHALL stop before starting a backup when configured free-space limits would be crossed.

#### Scenario: Upload fails
- **WHEN** a completed backup cannot be uploaded
- **THEN** it remains on disk and previous uploaded backups remain available

### Requirement: Direct database backup
`evdb backup create PROJECT/ROLE` SHALL run the checked engine backup and Restic upload workflow on the authoritative host for the selected durable database.

#### Scenario: Backup succeeds
- **WHEN** engine validation, manifest hashing, and Restic upload all succeed
- **THEN** the command reports a human timestamp, backup identity, and snapshot ID and records success

#### Scenario: Upload fails after local backup
- **WHEN** a checked local backup completes but Restic upload fails
- **THEN** the local backup is retained, upload failure is recorded, and the command exits nonzero without claiming a remote recovery point

#### Scenario: Cache backup is requested
- **WHEN** the selected KV role uses cache mode
- **THEN** evdb explains that backups are disabled and does not start an engine or Restic operation

### Requirement: Backup history
`evdb backup list PROJECT/ROLE` SHALL list completed local backups and tagged Restic snapshots in reverse chronological order with human time, purpose, local/remote availability, snapshot ID, and backup-test state. Guided restore selection SHALL present this history without requiring the operator to memorize IDs.

#### Scenario: Local and remote records refer to one backup
- **WHEN** a local manifest records a confirmed Restic snapshot ID
- **THEN** history presents them as one backup with both local and remote availability

#### Scenario: Only a remote snapshot remains
- **WHEN** local retention removed a backup that remains in Restic
- **THEN** history still lists the remote snapshot as restorable

### Requirement: Full backup verification
`evdb backup test PROJECT/ROLE [BACKUP|latest]` SHALL verify manifest and file hashes, restore the selected backup into an isolated compatible concrete engine, run engine-specific content checks, record the result, and clean up temporary resources. Omission SHALL select the newest restorable backup.

#### Scenario: Guided backup test runs
- **WHEN** the operator selects a backup by displayed time from the menu
- **THEN** evdb tests the corresponding exact backup without changing live data

#### Scenario: Restored data is unusable
- **WHEN** files pass integrity checks but engine-specific restore or content checks fail
- **THEN** the test records failure, removes its isolated container, and leaves live data unchanged

### Requirement: Remote snapshot selection
Backup test and restore SHALL accept an exact Restic snapshot returned by project/role history and SHALL reject ambiguous, missing, cross-host, cross-project, cross-role, or incompatible-engine snapshots.

#### Scenario: Snapshot belongs to another role
- **WHEN** the selected snapshot lacks the expected host/project/role tags
- **THEN** the operation fails before restoring files

### Requirement: Secret-safe backup output
Backup commands, history, manifests, state, activity, and logs SHALL NOT contain database passwords, HTTP tokens, ACME credentials, Restic passwords, rclone credentials, or credential-bearing URLs.

#### Scenario: Backup process fails with secret-bearing stderr
- **WHEN** a subprocess error contains a protected value
- **THEN** the operator receives a redacted error and no secret is persisted

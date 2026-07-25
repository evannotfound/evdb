## ADDED Requirements

### Requirement: Direct database backup
`evdb backup <database>` SHALL run the existing checked backup and Restic upload workflow on the managed host for the selected durable database.

#### Scenario: Backup succeeds
- **WHEN** database dump validation, manifest hashing, and Restic upload all succeed
- **THEN** the command reports the completed backup time and snapshot id and records success state

#### Scenario: Upload fails after local backup
- **WHEN** a checked local backup completes but Restic upload fails
- **THEN** the local backup is retained, the upload failure is recorded, and the command exits nonzero

### Requirement: Backup history
`evdb backups <database>` SHALL list completed local backups and tagged Restic snapshots in reverse chronological order with source, time, snapshot id when present, and verification state.

#### Scenario: Local and remote records refer to one backup
- **WHEN** a local manifest records a confirmed Restic snapshot id
- **THEN** history presents them as one backup with both local and remote availability

#### Scenario: Only a remote snapshot remains
- **WHEN** local retention removed a backup that remains in Restic
- **THEN** history still lists the remote snapshot as restorable

### Requirement: Full backup verification
`evdb backup-check <database>` SHALL verify the selected manifest and file hashes, restore the backup into an isolated compatible engine container, run engine-specific content checks, and record the result. It SHALL select the latest backup when no id is supplied.

#### Scenario: Latest backup is checked
- **WHEN** the operator runs backup-check without selecting a backup
- **THEN** the newest completed restorable backup is verified end to end

#### Scenario: Manifest hash is invalid
- **WHEN** any selected backup file does not match its manifest hash
- **THEN** verification fails before starting an engine container and records the integrity error

#### Scenario: Restored data is unusable
- **WHEN** files pass integrity checks but engine-specific restore or content checks fail
- **THEN** verification fails, cleans up its isolated container, and leaves the live database unchanged

### Requirement: Remote snapshot selection
Backup check and restore workflows SHALL accept an exact Restic snapshot id returned by backup history and SHALL reject ambiguous, missing, or cross-database snapshots.

#### Scenario: Snapshot belongs to another database
- **WHEN** the selected snapshot lacks the expected host and database tags
- **THEN** the operation fails before restoring files

### Requirement: Independent backup operation state
A failed backup or verification for one database SHALL NOT overwrite the last success or prevent later operations for other databases. Conflicting work for the same database or Restic repository SHALL remain locked.

Latest local completion, latest successful upload, latest successful verification summary, per-backup verification records, and current errors SHALL be stored independently.

#### Scenario: Later upload fails
- **WHEN** a new local backup completes but upload fails after an earlier recent upload succeeded
- **THEN** status remains fresh from the earlier snapshot and reports the current upload failure separately

#### Scenario: Later backup check fails
- **WHEN** two backups have successful verification records and checking a later backup fails
- **THEN** both earlier backups remain verified and the failed backup records its own result

#### Scenario: One scheduled backup fails
- **WHEN** a backup-all or timer run encounters one database failure
- **THEN** it records that failure and continues independent eligible databases

### Requirement: Secret-safe backup output
Backup commands, history, manifests, state, and logs SHALL NOT contain database passwords, HTTP tokens, Restic passwords, rclone credentials, or resolved secret references.

#### Scenario: Backup process fails with secret-bearing stderr
- **WHEN** a subprocess error could contain protected values
- **THEN** the operator receives a redacted operation error and no secret is persisted to normal logs or state

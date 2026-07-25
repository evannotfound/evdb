# Backup Specification

## Purpose

Define checked, isolated database backups, their local and remote history, and operator verification workflows.

## Requirements

### Requirement: Independent backup operation state
The system SHALL back up one database at a time and SHALL store latest local completion, latest successful upload, latest successful verification, per-backup verification records, and current errors independently. A failed operation for one database SHALL NOT overwrite earlier success or prevent operations for other databases.

Conflicting work for the same database or Restic repository SHALL remain locked.

#### Scenario: One database fails
- **WHEN** one database cannot create a backup
- **THEN** that database records a clear failure while other eligible database jobs continue

#### Scenario: Later upload fails
- **WHEN** a new local backup completes but its upload fails after an earlier recent upload succeeded
- **THEN** status remains fresh from the earlier snapshot and reports the current upload failure separately

#### Scenario: Later backup check fails
- **WHEN** two backups have successful verification records and checking a later backup fails
- **THEN** both earlier backups remain verified and the failed backup records its own result

### Requirement: Safe backup folder
Each run SHALL use a private `.partial` folder and SHALL rename it only after all required files, checks, hashes, and `backup.json` are complete. Backup files and folders SHALL NOT be readable by other users.

#### Scenario: Process stops during backup
- **WHEN** the process exits before the run is complete
- **THEN** no partial folder is treated as a valid backup

### Requirement: Backup manifest
Each completed backup SHALL contain `backup.json` with the host, database identity, engine, start and finish times, engine version, file names, sizes, SHA-256 hashes, checks performed, and upload state.

#### Scenario: Backup file changes after completion
- **WHEN** a completed file no longer matches its recorded size or hash
- **THEN** the backup is rejected before upload or restore

### Requirement: Backup locks and timeouts
The system SHALL use a database operation lock for backup creation, use the repository lock for Restic work, and enforce a timeout for every external command.

#### Scenario: Backup is already running
- **WHEN** another process holds the selected database's operation lock
- **THEN** the new run exits without starting a second backup

### Requirement: Local backup retention
The system SHALL keep at least the two newest uploaded backups for each durable database. It SHALL NOT automatically delete an unuploaded backup and SHALL stop before starting a backup when configured free-space limits would be crossed.

#### Scenario: Upload fails
- **WHEN** a completed backup cannot be uploaded
- **THEN** it remains on disk and the previous uploaded backups remain available

### Requirement: Checked Postgres backup
Postgres backup SHALL find every database where `datallowconn` is true and `datistemplate` is false, create a custom archive with `pg_dump -Fc`, and create a plain SQL globals file with `pg_dumpall --globals-only`. Role password hashes SHALL be preserved.

Every archive SHALL be nonempty and readable by `pg_restore --list`. The manifest SHALL record the server version, database names, file sizes, and hashes.

#### Scenario: Postgres cluster has several databases
- **WHEN** a cluster contains several connectable non-template databases
- **THEN** each database has its own custom archive and the run has one globals file

#### Scenario: Archive table of contents cannot be read
- **WHEN** `pg_restore --list` fails for any archive
- **THEN** the run remains incomplete and is not uploaded

### Requirement: Checked Dragonfly backup
Dragonfly backup SHALL call `SAVE RDB` with a new local name for every run, require the command to finish successfully, copy that exact nonempty RDB, and remove only the temporary source file created by the run. It SHALL check file identity, size, and SHA-256 hash and SHALL NOT call default `SAVE`, assume `dump.rdb` changed, or require `redis-check-rdb`.

#### Scenario: Old dump.rdb exists
- **WHEN** the data directory contains an old `dump.rdb`
- **THEN** the backup uses the newly named RDB and never copies the old file

#### Scenario: Dragonfly writes a private RDB record
- **WHEN** a Dragonfly RDB cannot be accepted by `redis-check-rdb`
- **THEN** the backup can still complete when its direct checks pass and its later Dragonfly restore verification succeeds

### Requirement: Checked Redis backup
Redis backup SHALL record `LASTSAVE`, start background persistence, wait for it to finish, require a successful newer save, and copy the resulting RDB. Authentication SHALL use a protected environment value or file and SHALL NOT appear in command arguments.

The backup SHALL require a nonempty RDB, validate it with matching Redis tools, and record its size, hash, Redis version, key count, and database count.

#### Scenario: Redis background save fails
- **WHEN** Redis reports a failed background save or the save time does not advance before timeout
- **THEN** the run remains incomplete and no old RDB is accepted

#### Scenario: Redis RDB is damaged
- **WHEN** the Redis RDB checker reports an error
- **THEN** the run remains incomplete and is not uploaded

### Requirement: Direct database backup
`evdb backup <database>` SHALL run the checked backup and Restic upload workflow on the managed host for the selected durable database.

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

### Requirement: Secret-safe backup output
Backup commands, history, manifests, state, and logs SHALL NOT contain database passwords, HTTP tokens, Restic passwords, rclone credentials, or resolved secret references.

#### Scenario: Backup process fails with secret-bearing stderr
- **WHEN** a subprocess error could contain protected values
- **THEN** the operator receives a redacted operation error and no secret is persisted to normal logs or state

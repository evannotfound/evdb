# Backup Specification

## Purpose

Define checked database backups, their local and remote history, and bounded local cleanup.

## Requirements

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

### Requirement: Safe backup folder
Each run SHALL use a private `.partial` folder under `/var/lib/evdb/backups/<project>/<role>` and SHALL rename it only after all required files, checks, hashes, and `backup.json` are complete. Backup files and folders SHALL NOT be readable by unrelated users.

#### Scenario: Process stops during backup
- **WHEN** the process exits before the run is complete
- **THEN** no partial folder is treated as a valid backup

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

### Requirement: Local backup retention
The system SHALL keep at least the two newest uploaded backups for each durable project/role. It SHALL NOT automatically delete an unuploaded backup and SHALL stop before starting a backup when configured free-space limits would be crossed.

#### Scenario: Upload fails
- **WHEN** a completed backup cannot be uploaded
- **THEN** it remains on disk and previous uploaded backups remain available

### Requirement: Backup ownership handoff
Backup creation SHALL keep partial directories and their files root-owned and private. After engine and
manifest validation, evdb SHALL make each completed directory and its regular files owned and readable
but not writable by the configured rclone owner before invoking Restic. Root-owned traverse-only backup
ancestors SHALL permit access to a known completed path without permitting directory listing or access
to database data, generated files, Traefik state, or locks.

#### Scenario: Checked backup reaches upload
- **WHEN** root completes and validates a partial backup
- **THEN** its completed directories are operator-owned mode `0500`, its files are operator-owned mode `0400`, and Restic can read it without database-data access

#### Scenario: Upload fails
- **WHEN** dropped Restic cannot upload a handed-off completed backup
- **THEN** the operator-readable local folder remains read-only with an unconfirmed upload record

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
Dragonfly backup SHALL call `SAVE DF` with a unique basename for every run, require the command to finish successfully, and capture exactly one complete native snapshot generation containing its summary and every numbered shard. It SHALL copy, size, and hash every generated DFS file and remove only the source files created by that run. It SHALL reject missing summaries, missing shards, mixed generations, unsafe names, and unlisted files.

#### Scenario: Old Dragonfly snapshots exist
- **WHEN** the data directory contains old RDB or DFS files
- **THEN** backup captures only the newly named native DFS generation and leaves every old file unchanged

#### Scenario: Native snapshot shard is missing
- **WHEN** a `SAVE DF` result has no summary or has an incomplete numbered shard set
- **THEN** backup fails without publishing a completed backup folder

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

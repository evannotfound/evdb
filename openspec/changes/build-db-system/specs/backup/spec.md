## ADDED Requirements

### Requirement: Independent backups
The system SHALL back up one instance at a time. Failure of one instance SHALL NOT block another instance from creating and uploading its own backup.

#### Scenario: One instance fails
- **WHEN** one instance cannot create a backup
- **THEN** that instance fails with a clear status while other instance jobs can still run

### Requirement: Safe backup folder
Each run SHALL use a private `.partial` folder and SHALL rename it only after all required files, checks, hashes, and `backup.json` are complete. Backup files and folders SHALL NOT be readable by other users.

#### Scenario: Process stops during backup
- **WHEN** the process exits before the run is complete
- **THEN** no partial folder is treated as a valid backup

### Requirement: Backup manifest
Each completed backup SHALL contain `backup.json` with the host, instance, engine, start and finish times, engine version, file names, sizes, SHA-256 hashes, checks performed, and upload state.

#### Scenario: Backup file changes after completion
- **WHEN** a completed file no longer matches its recorded size or hash
- **THEN** the backup is rejected before upload or restore

### Requirement: Locks and timeouts
The system SHALL use an instance lock for backup creation, a repository lock for Restic work, and a timeout for every external command.

#### Scenario: Backup is already running
- **WHEN** another process holds the instance lock
- **THEN** the new run exits without starting a second backup

### Requirement: Local backup history
The system SHALL keep at least the two newest uploaded backups for each instance. It SHALL NOT automatically delete an unuploaded backup. It SHALL stop before starting a backup when configured free-space limits would be crossed.

#### Scenario: Upload fails
- **WHEN** a completed backup cannot be uploaded
- **THEN** it remains on disk and the previous uploaded backups remain available

### Requirement: Postgres backup
Postgres backup SHALL find every database where `datallowconn` is true and `datistemplate` is false, create a custom archive with `pg_dump -Fc`, and create a plain SQL globals file with `pg_dumpall --globals-only`. Role password hashes SHALL be preserved.

#### Scenario: Postgres cluster has several databases
- **WHEN** a cluster contains several connectable non-template databases
- **THEN** each database has its own custom archive and the run has one globals file

### Requirement: Postgres checks
Every Postgres archive SHALL be nonempty and readable by `pg_restore --list`. The manifest SHALL record the server version, database names, file sizes, and hashes.

#### Scenario: Archive table of contents cannot be read
- **WHEN** `pg_restore --list` fails for any archive
- **THEN** the run remains incomplete and is not uploaded

### Requirement: Dragonfly backup
Dragonfly backup SHALL call `SAVE RDB` with a new local name for every run, require the command to finish successfully, copy that exact RDB, require a nonempty new file, and remove only the temporary source file created by the run. It SHALL NOT call default `SAVE` or assume `dump.rdb` changed.

#### Scenario: Old dump.rdb exists
- **WHEN** the data directory contains an old `dump.rdb`
- **THEN** the backup uses the newly named RDB and never copies the old file

### Requirement: Dragonfly checks
Dragonfly backup SHALL check command success, file identity, size, and SHA-256 hash. It SHALL NOT require `redis-check-rdb`, because valid Dragonfly RDB files can contain Dragonfly records that Redis does not understand.

#### Scenario: Dragonfly writes a private RDB record
- **WHEN** a Dragonfly RDB cannot be accepted by `redis-check-rdb`
- **THEN** the backup can still complete when its direct checks pass and its later Dragonfly restore test succeeds

### Requirement: Redis backup
Redis backup SHALL record `LASTSAVE`, start background persistence, wait for it to finish, require a successful newer save, and copy the resulting RDB. Authentication SHALL use a protected environment value or file and SHALL NOT appear in command arguments.

#### Scenario: Redis background save fails
- **WHEN** Redis reports a failed background save or the save time does not advance before timeout
- **THEN** the run remains incomplete and no old RDB is accepted

### Requirement: Redis checks
Redis backup SHALL require a nonempty RDB, validate it with the matching Redis tools, and record its size, hash, Redis version, key count, and database count.

#### Scenario: Redis RDB is damaged
- **WHEN** the Redis RDB checker reports an error
- **THEN** the run remains incomplete and is not uploaded

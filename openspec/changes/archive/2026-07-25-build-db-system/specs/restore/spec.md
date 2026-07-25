## ADDED Requirements

### Requirement: Isolated restore
Every restore test SHALL use temporary containers and storage with no Traefik labels, no published host ports, and no access to live data paths. The system SHALL never overwrite a running production data directory.

#### Scenario: Restore target points at live data
- **WHEN** a restore target matches a configured live data path or container
- **THEN** the command refuses to start

### Requirement: Checked input
Restore SHALL accept only a completed local backup or a Restic snapshot whose files match `backup.json` sizes and hashes.

#### Scenario: Restored file hash differs
- **WHEN** any restored file does not match its manifest hash
- **THEN** the restore test fails before starting the database

### Requirement: Postgres restore test
Postgres restore SHALL start a matching Postgres image, load globals before databases, create databases from `template0`, restore each archive with errors treated as fatal, connect to every restored database, and check expected objects.

#### Scenario: Database archive has a restore error
- **WHEN** `pg_restore` reports an SQL or archive error
- **THEN** the restore test fails and keeps the live Postgres instances untouched

### Requirement: Redis restore test
Redis restore SHALL start a matching Redis image from the saved RDB and check that it loads successfully. It SHALL compare database count, key count, key types, selected value hashes, and TTL behavior with the backup manifest.

#### Scenario: TTL is lost
- **WHEN** a key that had a finite TTL becomes persistent after restore
- **THEN** the restore test fails

### Requirement: Dragonfly restore test
Dragonfly restore SHALL start the recorded Dragonfly image from the saved RDB and check that it loads successfully. It SHALL compare key count, key types, selected value hashes, and TTL behavior with the backup manifest.

#### Scenario: Dragonfly cannot load its RDB
- **WHEN** the temporary Dragonfly container rejects the backup file
- **THEN** the restore test fails even if the daily file checks passed

### Requirement: Restore cleanup and record
Temporary containers, networks, and storage SHALL be removed after success or failure. The result and time of the latest restore test SHALL be stored without deleting the backup that was tested.

#### Scenario: Restore command is interrupted
- **WHEN** the restore process receives an interrupt or reaches its timeout
- **THEN** it stops and removes the temporary Docker resources it created

### Requirement: Restore schedule support
The system SHALL include a job that can select backups due for a restore test so every durable instance can be tested at least once every 30 days. The job SHALL be built and tested locally but SHALL NOT be enabled on production in this change.

#### Scenario: Instance has never been restore-tested
- **WHEN** the due check runs and an instance has no successful restore record
- **THEN** that instance is selected before an instance with a recent successful test

# Restore Specification

## Purpose

Define isolated restore verification, persistent restore candidates, and confirmed atomic promotion with recovery.

## Requirements

### Requirement: Persistent restore candidate
`evdb restore <database> --snapshot <id|latest>` SHALL restore the selected backup into a unique candidate directory on the live data filesystem and record an immutable restore id, source identity, snapshot id, manifest hash, engine image, and creation time. It SHALL NOT modify the live data directory.

#### Scenario: Latest snapshot is restored
- **WHEN** the operator selects `latest`
- **THEN** the newest tagged snapshot for that exact database is restored into a new candidate without modifying live data

#### Scenario: Restore target points at live data
- **WHEN** a restore target matches a configured live data path or container
- **THEN** the command refuses to start

### Requirement: Checked restore input
Restore SHALL accept only a completed local backup or an exact Restic snapshot whose files match `backup.json` sizes and hashes and whose host and typed database identity match the target.

#### Scenario: Restored file hash differs
- **WHEN** any restored file does not match its manifest hash
- **THEN** restore verification fails before starting the database

### Requirement: Postgres restore verification
Postgres restore SHALL start a compatible locked Postgres image, load globals before databases, create databases from `template0`, restore each archive with errors treated as fatal, connect to every restored database, and check expected objects.

#### Scenario: Database archive has a restore error
- **WHEN** `pg_restore` reports an SQL or archive error
- **THEN** restore verification fails and keeps the live Postgres database untouched

### Requirement: Redis restore verification
Redis restore SHALL start a compatible locked Redis image from the saved RDB and require it to load successfully. It SHALL compare database count, key count, key types, selected value hashes, and TTL behavior with the backup manifest.

#### Scenario: TTL is lost
- **WHEN** a key that had a finite TTL becomes persistent after restore
- **THEN** restore verification fails

### Requirement: Dragonfly restore verification
Dragonfly restore SHALL start a compatible locked Dragonfly image from the saved RDB and require it to load successfully. It SHALL compare key count, key types, selected value hashes, and TTL behavior with the backup manifest.

#### Scenario: Dragonfly cannot load its RDB
- **WHEN** the verification Dragonfly container rejects the backup file
- **THEN** restore verification fails even if daily file checks passed

### Requirement: Isolated candidate verification
The system SHALL start restore candidates in temporary containers with no Traefik labels, published ports, or access to live data mounts. It SHALL run manifest, engine, and content verification, stop and remove temporary containers and networks after success or failure, and mark only successful candidates promotable.

#### Scenario: Candidate verification succeeds
- **WHEN** every integrity, restore, and engine-specific content check passes
- **THEN** the verification container is removed and the candidate is recorded as promotable

#### Scenario: Candidate verification fails
- **WHEN** any restore or content check fails
- **THEN** the candidate is marked failed, is not promotable, and the live project remains unchanged

#### Scenario: Verification is interrupted
- **WHEN** verification receives an interrupt or reaches its timeout
- **THEN** it stops and removes the temporary Docker resources it created without deleting the backup or live data

### Requirement: Restore verification scheduling
The system SHALL include a job that selects backups due for full restore verification so every durable database can be tested within the configured maximum age. Verification results SHALL be recorded per backup.

#### Scenario: Database has never been restore-verified
- **WHEN** the due check runs and a database has no successful verification record
- **THEN** that database is selected before a database with a recent successful verification

### Requirement: Confirmed restore promotion
`evdb promote <database> <restore-id>` SHALL verify candidate identity and freshness, display the selected backup and expected outage, acquire the database operation lock, and require confirmation or `--yes` before stopping the live project.

#### Scenario: Candidate belongs to another database
- **WHEN** the restore id does not match the selected typed database identity
- **THEN** promotion fails before stopping any service

#### Scenario: Operator declines promotion
- **WHEN** the operator does not confirm the promotion plan
- **THEN** live and candidate data remain unchanged

### Requirement: Atomic data-directory promotion
Promotion SHALL require candidate and live directories on the same filesystem, stop the live project, rename the live directory to a timestamped retained path, atomically rename the candidate into the configured path, and then start the project.

#### Scenario: Filesystems differ
- **WHEN** the candidate cannot be atomically renamed into the live data path
- **THEN** promotion fails before stopping the live project

#### Scenario: Directory swap succeeds
- **WHEN** both same-filesystem renames complete
- **THEN** the selected restored data occupies the configured live path and the previous data remains at its retained path

### Requirement: Post-promotion health gate
Promotion SHALL require container and engine-native health after the directory swap before marking the restore active.

#### Scenario: Promoted database is healthy
- **WHEN** the project starts and passes its engine health check using restored data
- **THEN** promotion records success and reports the retained prior-data path

### Requirement: Automatic failed-promotion recovery
If the promoted database does not become healthy, the system SHALL stop it, move the failed candidate out of the live path, restore the retained prior directory atomically, restart and verify the prior database, and record both promotion and recovery outcomes.

#### Scenario: Restored data fails startup
- **WHEN** the promoted project cannot become healthy
- **THEN** the prior data directory is restored and its service health is verified before the command returns failure

#### Scenario: Prior data recovery fails
- **WHEN** the prior directory cannot be returned to healthy service
- **THEN** neither data directory is deleted and the command reports their exact protected locations and exits nonzero

### Requirement: Retained recovery data
Successful promotion SHALL retain the previous live data directory. The system SHALL provide no automatic purge of retained data, failed candidates, backups, or related 1Password items.

#### Scenario: Promotion completes
- **WHEN** restored data becomes healthy
- **THEN** the prior data remains available for manual recovery and status reports its presence

### Requirement: Engine compatibility
Candidate restore and promotion SHALL use a locked image compatible with the backup format and desired deployment and SHALL reject unsupported cross-engine or major-version combinations.

Compatibility SHALL require exact host and typed database identity and the same engine. It SHALL permit a different patch tag or digest within a supported major path and SHALL reject restoring a newer-major backup with an older-major locked image.

#### Scenario: Redis backup is selected for Dragonfly without supported conversion
- **WHEN** the candidate engine and backup engine combination is not explicitly supported
- **THEN** restore fails before creating or replacing a live data directory

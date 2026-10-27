## MODIFIED Requirements

### Requirement: Independent backup operation state
The system SHALL create and upload one project/role backup at a time. Completed local manifests and tagged Restic snapshots SHALL remain the backup records. A private local catalog SHALL retain known remote snapshots and the latest backup attempt per role for reporting. A failed attempt SHALL NOT erase earlier successful snapshots. A failure for one role in `--all` SHALL NOT prevent later roles from being attempted.

#### Scenario: One project role fails
- **WHEN** one database cannot create or upload a backup during `backup create --all`
- **THEN** its error is reported, remaining durable databases are attempted, and the overall command exits nonzero

#### Scenario: Later upload fails
- **WHEN** a checked local backup completes but upload fails after an earlier snapshot succeeded
- **THEN** the new local folder remains available, history retains the earlier remote snapshot, and the latest failed attempt is recorded separately

### Requirement: Backup manifest
Each completed backup SHALL contain `backup.json` with host, project, role, concrete engine, start and finish times, configured image, engine version, backup format, file names, sizes, SHA-256 hashes, checks performed, purpose, and upload state. It SHALL contain no restore verification or safety-backup transaction fields. Integrity-sensitive operations SHALL verify file hashes; history SHALL inspect metadata and file presence without hashing dump contents and SHALL NOT claim fresh integrity verification.

#### Scenario: Backup file changes after completion
- **WHEN** a completed file no longer matches its recorded size or hash
- **THEN** full backup validation rejects it before upload or restore use

### Requirement: Backup history
`evdb backup list PROJECT/ROLE` and guided history SHALL merge completed local folders with matching snapshots from the persistent local catalog in reverse chronological order. Each item SHALL show time, purpose, local presence, last-known remote availability, backup ID, and snapshot ID when present. History SHALL show when remote inventory was checked and any refresh failure. It SHALL NOT contact Restic/rclone or hash dump contents, even when the catalog is old or absent. History SHALL NOT contain backup-test state.

#### Scenario: Local and remote records refer to one backup
- **WHEN** a local manifest and saved remote snapshot refer to one backup
- **THEN** history presents one item with local presence and last-known remote availability

#### Scenario: Only a remote snapshot remains
- **WHEN** local cleanup removed a backup still recorded in the catalog
- **THEN** history lists that cached remote snapshot without contacting the repository

#### Scenario: No inventory has been checked
- **WHEN** history opens before the first remote inventory refresh
- **THEN** it shows available local records and known uploads, labels remote inventory not checked, and does not query the remote repository

## ADDED Requirements

### Requirement: Persistent backup catalog
The backup module SHALL persist reporting facts privately and atomically, scoped to the configured host and repository. Successful uploads SHALL immediately publish their snapshot facts without an extra remote listing. Latest attempt errors SHALL be secret-redacted. Concurrent local updates SHALL preserve each other's results.

#### Scenario: Operator starts another process after upload
- **WHEN** a successful backup has completed and a new process opens history
- **THEN** the uploaded snapshot appears from disk without a remote query

#### Scenario: Repository changes
- **WHEN** the saved catalog identifies another host or repository
- **THEN** its inventory and attempt records are not used for the current configuration

### Requirement: Remote inventory reconciliation
`evdb backup refresh` SHALL list the host's remote snapshots once and publish the resulting inventory. `backup create --all` SHALL perform the same refresh after all durable roles have been attempted, including after individual failures. Hosts with no durable roles SHALL retain the successful no-backup-due behavior. Refresh failure SHALL retain the last successful inventory and timestamp, record the failure separately, and make the explicit or all-database command exit nonzero. Ordinary reads SHALL never trigger refresh automatically.

#### Scenario: Snapshot was removed remotely
- **WHEN** a successful refresh no longer finds a previously saved snapshot
- **THEN** the catalog stops advertising that snapshot as remotely available

#### Scenario: OneDrive is unavailable
- **WHEN** refresh fails after an earlier successful inventory check
- **THEN** saved backups remain visible with the earlier check timestamp and the latest refresh error

#### Scenario: One backup fails during daily work
- **WHEN** one durable role fails during the all-database run
- **THEN** the remaining roles and one final inventory refresh are still attempted

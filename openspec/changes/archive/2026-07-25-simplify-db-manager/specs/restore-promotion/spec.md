## ADDED Requirements

### Requirement: Persistent restore candidate
`evdb restore <database> --snapshot <id|latest>` SHALL restore the selected backup into a unique candidate directory on the live data filesystem and record an immutable restore id, source identity, snapshot id, manifest hash, engine image, and creation time.

#### Scenario: Latest snapshot is restored
- **WHEN** the operator selects `latest`
- **THEN** the newest tagged snapshot for that exact database is restored into a new candidate without modifying live data

### Requirement: Isolated candidate verification
The system SHALL start restore candidates without published ports or access to live data mounts, run manifest, engine, and content verification, stop and remove the verification container, and mark only successful candidates promotable.

#### Scenario: Candidate verification succeeds
- **WHEN** every integrity, restore, and engine-specific content check passes
- **THEN** the candidate is stopped and recorded as promotable

#### Scenario: Candidate verification fails
- **WHEN** any restore or content check fails
- **THEN** the candidate is marked failed, is not promotable, and the live project remains unchanged

### Requirement: Confirmed restore promotion
`evdb promote <database> <restore-id>` SHALL verify candidate identity and freshness, display the selected backup and expected outage, acquire the instance operation lock, and require confirmation or `--yes` before stopping the live project.

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
If the promoted database does not become healthy, the system SHALL stop it, move the failed candidate out of the live path, restore the retained prior directory atomically, restart and verify the prior database, and record both the promotion and recovery outcomes.

#### Scenario: Restored data fails startup
- **WHEN** the promoted project cannot become healthy
- **THEN** the prior data directory is restored and its service health is verified before the command returns failure

#### Scenario: Prior data recovery fails
- **WHEN** the prior directory cannot be returned to healthy service
- **THEN** neither data directory is deleted and the command reports their exact protected locations and exits nonzero

### Requirement: Retained recovery data
Successful promotion SHALL retain the previous live data directory. This change SHALL provide no automatic purge of retained data, failed candidates, backups, or related 1Password items.

#### Scenario: Promotion completes
- **WHEN** restored data becomes healthy
- **THEN** the prior data remains available for manual recovery and status reports its presence

### Requirement: Engine compatibility
Candidate restore and promotion SHALL use a locked image compatible with the backup format and desired deployment and SHALL reject unsupported cross-engine or major-version combinations.

Compatibility SHALL require exact host and typed database identity and the same engine. It SHALL permit a different patch tag or digest within a supported major path and SHALL reject restoring a newer-major backup with an older-major locked image.

#### Scenario: Redis backup is selected for Dragonfly without supported conversion
- **WHEN** the candidate engine and backup engine combination is not explicitly supported
- **THEN** restore fails before creating or replacing a live data directory

## ADDED Requirements

### Requirement: One-command live restore
`evdb restore PROJECT/ROLE BACKUP` SHALL select and validate a backup, create and verify an isolated same-filesystem restored copy, create and upload a current safety backup, show the selected source and expected outage, require final confirmation, replace live data atomically, start the installed Compose definition, and health-check the database. The operator SHALL NOT manage a restore candidate or run a separate promotion command.

#### Scenario: Restore succeeds
- **WHEN** the selected backup and safety backup are valid, the operator confirms, and restored data becomes healthy
- **THEN** the selected backup becomes live and the command reports the restored and safety backup identities

#### Scenario: Operator declines replacement
- **WHEN** isolated verification and safety backup finish but the operator declines the final outage confirmation
- **THEN** live services and data remain unchanged and temporary restored data is cleaned safely

### Requirement: Safety backup gates replacement
Live data replacement SHALL NOT begin until a new checked backup of current live data has uploaded successfully. The restore activity record SHALL identify that exact safety snapshot.

#### Scenario: Safety upload fails
- **WHEN** current live data is backed up locally but Restic upload fails
- **THEN** restore leaves live service and data untouched and preserves the unuploaded backup

## MODIFIED Requirements

### Requirement: Checked restore input
Restore SHALL accept only a completed local backup or exact Restic snapshot whose files match `backup.json` sizes and hashes and whose host/project/role, concrete engine, and format are compatible with the target.

#### Scenario: Restored file hash differs
- **WHEN** any selected backup file does not match its manifest hash
- **THEN** restore fails before starting an engine container or creating a safety backup

### Requirement: Postgres restore verification
Postgres restore SHALL select a compatible immutable Postgres image digest from installed machine state and backup metadata, load globals before databases, create databases from `template0`, restore every archive with errors treated as fatal, connect to every restored database, and check expected objects.

#### Scenario: Database archive has a restore error
- **WHEN** `pg_restore` reports an SQL or archive error
- **THEN** restore verification fails and keeps live Postgres data untouched

### Requirement: Redis restore verification
Redis restore SHALL select a compatible immutable Redis image digest from installed machine state and backup metadata, start from the saved RDB, require it to load successfully, and compare database count, key count, key types, selected value hashes, and TTL behavior with the backup record.

#### Scenario: TTL is lost
- **WHEN** a key that had a finite TTL becomes persistent after restore
- **THEN** restore verification fails before live data changes

### Requirement: Dragonfly restore verification
Dragonfly restore SHALL select a compatible immutable Dragonfly image digest from installed machine state and backup metadata, start from the saved RDB, require it to load successfully, and compare key count, key types, selected value hashes, and TTL behavior with the backup record.

#### Scenario: Dragonfly cannot load its RDB
- **WHEN** the verification Dragonfly container rejects the backup file
- **THEN** restore verification fails even if file integrity checks passed

### Requirement: Isolated candidate verification
Before live mutation, restore SHALL run the selected backup in a private temporary container and same-filesystem data directory with no Traefik labels, public ports, live data mounts, or shared service identity. It SHALL run manifest, engine, and content verification and SHALL mark only a successful in-process candidate eligible for replacement.

#### Scenario: Isolated verification succeeds
- **WHEN** every integrity, engine restore, and content check passes
- **THEN** the workflow may proceed to a current safety backup while live data remains unchanged

#### Scenario: Isolated verification fails
- **WHEN** any restore or content check fails
- **THEN** temporary Docker resources and candidate data are cleaned, live service remains unchanged, and no replacement confirmation is offered

#### Scenario: Verification is interrupted
- **WHEN** verification receives an interrupt or reaches its timeout
- **THEN** it removes temporary resources it created without deleting the selected backup or live data

### Requirement: Restore verification scheduling
The installed host jobs SHALL select backups due for `backup test` so every durable project/role receives a successful full restore verification within the configured maximum age. Results SHALL remain attached to exact backups.

#### Scenario: Database has never been tested
- **WHEN** due selection finds a durable project/role with no successful backup test
- **THEN** it is selected before a role with a recent successful result

### Requirement: Atomic data-directory promotion
After final confirmation, restore SHALL require candidate and live directories on the same filesystem, stop only the selected role, rename live data to a transaction-owned prior path, atomically rename verified candidate data into the canonical path, and start the same installed Compose definition.

#### Scenario: Filesystems differ
- **WHEN** candidate data cannot be atomically renamed into the live path
- **THEN** restore fails before stopping the live service

#### Scenario: Directory swap succeeds
- **WHEN** both same-filesystem renames complete
- **THEN** verified restored data occupies the canonical path and prior data remains protected until post-restore health succeeds

### Requirement: Post-promotion health gate
After the directory swap, restore SHALL require every expected Compose service, service-contract identity, and engine-native database health before recording success or deleting prior live data.

#### Scenario: Restored database is healthy
- **WHEN** the project starts and all required checks pass
- **THEN** restore records success and may remove prior live data because the safety backup is confirmed

### Requirement: Automatic failed-promotion recovery
If restored data fails startup or health, evdb SHALL stop the selected role, move failed restored data out of the canonical path, return prior live data atomically, restart the installed Compose definition, and verify prior health without prompting. It SHALL delete neither data directory if recovery fails.

#### Scenario: Restored data fails startup
- **WHEN** the restored engine cannot become healthy
- **THEN** prior data is returned to service and the command reports restore failure plus recovery outcome

#### Scenario: Prior data recovery fails
- **WHEN** prior data cannot return to healthy service
- **THEN** evdb reports the exact protected prior and failed paths and preserves both for manual intervention

### Requirement: Retained recovery data
During a live restore transaction, prior data SHALL remain protected until restored health succeeds. After success, evdb SHALL remove the replaced local directory only because the pre-restore safety snapshot uploaded successfully. Failed restored data and prior data from failed recovery SHALL never be removed automatically.

#### Scenario: Healthy restore completes
- **WHEN** restored data passes health and the safety snapshot is confirmed
- **THEN** the transaction removes the replaced live directory and retains the remote safety backup according to normal retention

### Requirement: Engine compatibility
Backup test and restore SHALL require exact host/project/role identity and the same concrete engine. They MAY use a compatible patch tag or digest within a supported major and SHALL reject a newer-major backup with an older-major image, every engine-major migration, and Redis/Dragonfly conversion.

#### Scenario: Redis backup targets Dragonfly
- **WHEN** a Redis backup is selected for a Dragonfly-backed KV role
- **THEN** restore fails before candidate creation and identifies engine conversion as unsupported

## REMOVED Requirements

### Requirement: Persistent restore candidate
**Reason**: Restore candidates are an internal temporary implementation detail, not an operator-managed resource.
**Migration**: Use one `evdb restore PROJECT/ROLE BACKUP` operation or `evdb backup test` for non-destructive verification.

### Requirement: Confirmed restore promotion
**Reason**: Exposing a second promotion command and restore ID made data recovery difficult to understand.
**Migration**: The one restore command performs verification, safety backup, final confirmation, replacement, and health recovery.

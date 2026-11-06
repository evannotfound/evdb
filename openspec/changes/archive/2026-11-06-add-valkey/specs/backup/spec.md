## ADDED Requirements

### Requirement: Checked Valkey backup
Durable Valkey backup SHALL explicitly request a fresh RDB snapshot, wait for successful completion even when AOF rewriting delays it, copy and validate it with the matching Valkey image and valkey-check-rdb, and publish it through the existing checked Restic workflow. Metadata SHALL identify engine valkey, actual Valkey version, and format valkey-rdb-v1. Existing engine formats SHALL remain accepted. Automatic periodic RDB snapshotting SHALL NOT be required.

#### Scenario: AOF rewrite overlaps a backup
- **WHEN** backup requests a snapshot while Valkey is rewriting AOF
- **THEN** it schedules the save and waits for a newer completed successful snapshot before copying, rather than treating scheduling as completion

#### Scenario: Valkey snapshot is invalid
- **WHEN** the generated snapshot fails the native checker
- **THEN** backup fails without publishing a completed backup or uploading the invalid snapshot

#### Scenario: Checked snapshot is recovered
- **WHEN** a checked Valkey RDB is loaded into a separate empty Valkey instance with AOF initially disabled
- **THEN** the backed-up data is available independently of the source AOF directory

## MODIFIED Requirements

### Requirement: Snapshot identity
Every snapshot SHALL use stable host, project, and role tags. It SHALL additionally record concrete engine and backup identity metadata without using mutable image details as retention grouping keys. Changing backup format details SHALL be recorded in `backup.json` rather than by changing stable grouping tags.

#### Scenario: Latest snapshot is queried
- **WHEN** status checks `example-prod-01/kv`
- **THEN** it finds that role's latest snapshot without matching Postgres from the same project or KV from another project

### Requirement: Retention and prune
The system SHALL support the policy of 7 daily, 4 weekly, and 12 monthly snapshots per durable project/role. Forget SHALL run weekly and prune SHALL run monthly. A dry run SHALL be reviewed before deletion is enabled for a repository.

#### Scenario: Weekly retention runs
- **WHEN** the weekly job applies retention
- **THEN** snapshots are grouped by stable host/project/role identity and prune does not run

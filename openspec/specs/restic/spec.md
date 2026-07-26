# Restic Specification

## Purpose

Define verified Restic uploads, stable snapshot identity, serialized repository maintenance, retention, and local testing.

## Requirements

### Requirement: Completed backups only
Restic upload SHALL accept only a complete backup folder whose files pass the checks in `backup.json`.

#### Scenario: Partial folder is passed to upload
- **WHEN** an upload path is a `.partial` folder or has no valid manifest
- **THEN** Restic is not started

### Requirement: Confirmed snapshot
Restic backup SHALL run with JSON output, parse JSON Lines by `message_type`, require exit code zero, and require a final summary containing `snapshot_id`. A snapshot id from a nonzero run SHALL NOT count as success.

#### Scenario: Restic creates an incomplete snapshot
- **WHEN** Restic returns a nonzero exit code and prints a snapshot id
- **THEN** upload is marked failed and the local backup is kept

### Requirement: Snapshot identity
Every snapshot SHALL use stable host, project, and role tags. It SHALL additionally record concrete engine and backup identity metadata without using mutable image details as retention grouping keys. Changing backup format details SHALL be recorded in `backup.json` rather than by changing stable grouping tags.

#### Scenario: Latest snapshot is queried
- **WHEN** status checks `example-prod-01/kv`
- **THEN** it finds that role's latest snapshot without matching Postgres from the same project or KV from another project

### Requirement: Repository locking
All Restic work for one repository SHALL use one host lock. Backup, status, retention, prune, and check commands SHALL NOT race each other.

#### Scenario: Repository check is running
- **WHEN** a backup reaches the same repository
- **THEN** it waits for the configured lock time or fails clearly without using `--no-lock`

### Requirement: Repository format
The system SHALL keep existing repositories at format v1 and SHALL work with a pinned current Restic release. Repository format upgrades are outside this capability.

#### Scenario: Repository reports format v2
- **WHEN** repository validation sees format v2 for an existing production repository
- **THEN** it reports the mismatch and does not continue with maintenance

### Requirement: Retention and prune
The system SHALL support the policy of 7 daily, 4 weekly, and 12 monthly snapshots per durable project/role. Forget SHALL run weekly and prune SHALL run monthly. A dry run SHALL be reviewed before deletion is enabled for a repository.

#### Scenario: Weekly retention runs
- **WHEN** the weekly job applies retention
- **THEN** snapshots are grouped by stable host/project/role identity and prune does not run

### Requirement: Repository checks
The system SHALL support weekly structure checks and deterministic rotating data checks using `n/t` subsets so all repository data is covered over time.

#### Scenario: Data check rotation completes
- **WHEN** all subset numbers from 1 through the configured total have succeeded
- **THEN** the full set of repository packs has been scheduled for reading once

### Requirement: Local test repository
Integration tests SHALL use a new local Restic repository and test password. They SHALL NOT use production OneDrive remotes, the production Restic password, or production rclone configuration.

#### Scenario: Integration tests run
- **WHEN** the Restic integration suite starts
- **THEN** its repository path is inside the temporary test directory

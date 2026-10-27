## Why

Status and backup history wait on OneDrive and hash local dump files even though operators do not need live backup inventory. Save backup facts locally so routine reads remain fast and independent of remote availability.

## What Changes

- Serve status and backup history from a persistent host/repository-scoped catalog and lightweight local manifest reads, without remote calls or dump hashing.
- Record successful uploads and the latest backup attempt locally, retaining previous successful snapshots after failures.
- Reconcile remote snapshots once after the existing daily all-database backup job, including when individual backups fail.
- Add an explicit host-wide backup refresh command and guided action; show when inventory was checked and any refresh failure.
- Keep old information visible when refresh fails; show unknown inventory when no catalog exists and calculate backup age at display time.
- Keep backup storage layout, scheduling frequency, and upload/restore integrity checks unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `backup`: Local catalog, cached history, attempt recording, and scheduled/manual reconciliation.
- `jobs`: Backup freshness and repository assessment use dated local evidence instead of live remote queries.
- `operator-cli`: Explicit refresh and dated cached status/history in CLI and guided operations.

## Impact

Changes affect backup persistence, status collection, CLI/guided presentation, focused unit tests, and operator documentation. No new runtime dependency or service is required. Catalog data is private local machine state, not source configuration.

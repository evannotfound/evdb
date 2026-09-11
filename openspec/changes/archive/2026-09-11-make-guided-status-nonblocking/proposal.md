## Why

The guided root currently shows partial status while Restic loads but still delays numbered input until
the complete repository assessment returns. Operators should be able to navigate immediately while live
runtime and backup state refresh safely in the background.

## What Changes

- Render the guided root and stable numbered choices immediately from configuration, with pending runtime
  and backup labels until assessment results arrive.
- Run one non-overlapping guided status refresh in the background and update the visible overview as local
  runtime and repository results become available.
- Refresh on session start, after backup creation, and on return to root after a 60-second freshness
  window; do not poll continuously or query Restic after every menu step.
- Let a selected database open while runtime is pending and wait only when an action requires live state.
- Cancel background subprocess groups before conflicting mutations or process exit so Restic and rclone
  are not orphaned and shutdown does not wait for an irrelevant refresh.
- Keep direct `evdb status`, `status --json`, their exit codes, and plain output fully synchronous.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `operator-cli`: guided numbered input becomes available before runtime and backup assessment completes,
  with safe live updates and pending-state behavior.
- `jobs`: guided status refresh becomes incremental and cancellable while explicit status remains a
  complete synchronous assessment.

## Impact

- Affected code: `src/evdb/ui.py`, `src/evdb/status.py`, `src/evdb/run.py`, and status-aware database and
  backup interaction points.
- Affected tests: guided input ordering, live terminal updates, refresh TTL/invalidation, cancellation,
  Restic query counts, and unchanged direct JSON/plain contracts.
- Runtime behavior: one short-lived worker thread may own guided status subprocesses; no daemon, persistent
  cache, service, or periodic polling is introduced.
- External contracts: command names, numbered choices, direct status schema, status version, and backup
  repository formats remain unchanged.

## Context

Status currently lists host snapshots through Restic on every collection, then calls history for each role. History hashes local dump files and, when called independently, lists remote snapshots again. The guided session's short-lived memory cache does not help new processes. One daily systemd job already runs `backup create --all`.

## Goals / Non-Goals

**Goals:** Make status, Details, and backup history local-only for backup information; retain dated remote history across processes; update successful uploads immediately; reconcile daily and on explicit request.

**Non-Goals:** Change repository layout, retention, backup frequency, runtime health probes, or upload/restore integrity checks. Add no service, dependency, or configurable cache timeout.

## Decisions

- Keep a private JSON catalog under the existing state root, owned by the backup module. Store host/repository identity, known snapshots, last successful inventory check, last repository success, latest refresh failure, and latest attempt per role. Ignore a catalog belonging to another host/repository. Use existing atomic JSON writes and a short catalog lock; reads never wait for remote locks.
- Successful uploads add snapshot facts and record a successful attempt. Failures update only the latest attempt, preserving previous snapshots. Use the upload confirmation time until a remote listing supplies the snapshot's timestamp. Initialization records successful repository access without pretending to have listed inventory.
- Refresh takes the existing repository lock through listing and catalog publication, with the catalog lock only around the local update. Upload publication and refresh must not lose concurrent results. A successful listing replaces inventory, retaining attempt records; a failed listing retains inventory and its successful-check timestamp and stores a redacted error.
- The existing all-database job refreshes once after all durable roles have been attempted, even if some failed. Report refresh failure separately and return nonzero. No durable roles remains a successful no-op. `backup refresh` and a Backups submenu action perform the same host-wide refresh.
- History merges saved remote snapshots with locally present completed manifests. Reuse manifest validation with hashing disabled for history only; upload and other integrity-sensitive callers keep full verification. Local presence is not a fresh integrity guarantee.
- Status reads one catalog per collection, calculates freshness against the current clock, and includes catalog timestamps and last attempt information. Missing inventory is unknown, not proof that backups are absent. A failed refresh does not change the age/state of an already known backup. Repository availability is explicitly last-known, not a live claim.
- Plain and terminal status/history show inventory age and refresh failure. Details use the same local evidence. An explicit refresh is the only interactive remote inventory query; no stale/missing-cache fallback performs network work.

## Risks / Trade-offs

- External deletion is invisible until reconciliation → label remote availability as cached and show the last successful check.
- Jobs stop running → calculate backup age at read time rather than persisting a healthy badge.
- Catalog is initially absent → display unknown remote inventory and offer `evdb backup refresh`; the next daily job also populates it.
- Multiple backup processes update state → serialize local writes and serialize remote inventory publication with the repository lock; keep normal reads lock-free.

## Migration Plan

No configuration or backup migration is needed. Existing backups remain unchanged. The first explicit or daily refresh imports remote inventory. Removing the catalog only discards reporting metadata; it does not remove backups.

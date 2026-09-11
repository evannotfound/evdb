## Context

The current status collector already separates local host/database probes from one host-wide Restic
snapshot query and can preview a table with loading backup cells. `ui.check_status()` keeps that work in
the foreground, however, so `ui.run()` does not print `Select:` until the remote query returns. The guided
root is therefore visually active but not interactive.

Database identities and numeric positions come from immutable configuration and do not depend on live
status. Runtime-dependent labels and backup freshness may remain pending while the operator selects a
database. Direct `evdb status` and `status --json` are different: their exit status and output contract
require one complete assessment before returning.

## Goals / Non-Goals

**Goals:**

- Make the guided root and numbered input available immediately after configuration loads.
- Update one live overview as local runtime and backup results arrive.
- Run at most one guided refresh and reuse a result for 60 seconds unless an operation invalidates it.
- Stop background subprocess groups promptly on exit or before conflicting work.
- Preserve complete synchronous direct status and exact JSON/plain output contracts.

**Non-Goals:**

- No daemon, persistent status database, continuous timer, or refresh configuration.
- No Restic query after every menu choice.
- No concurrent repository checks or backup operations.
- No full-screen interface, arrow-key input, or change to configured numeric ordering.

## Decisions

### Keep synchronous collection and add an incremental guided boundary

Refactor `status.py` so it can construct a config-only pending result, publish updated snapshots after
local host and database probes, and then finish repository and backup assessment. Existing
`status.collect()` continues through every phase synchronously for direct commands and JSON. The guided
worker calls the same collector with a snapshot callback; domain status rules remain shared.

Starting the existing complete collector in a thread without an initial snapshot was rejected because the
menu would still lack stable data until local probes finish. Making direct status asynchronous was rejected
because callers require a final boolean health result and exit code.

### Own one refresh worker inside the guided UI session

Add a small session object in `ui.py` that owns the latest immutable status snapshot, monotonic completion
time, one non-daemon worker thread, cancellation event, condition, and generation number. It starts on
launch, refreshes after explicit invalidation, and refreshes on return to root only when the completed
snapshot is at least 60 seconds old. A newer generation ignores callbacks from replaced work, and no two
workers run concurrently.

The root starts from the config-only snapshot, renders the database table and numbered actions in one Rich
Live region, subscribes that region to worker snapshots, and begins `_choice()` without waiting. Rich's
locked `Live.update()` performs terminal redraws while the main thread owns line input. Leaving root removes
the renderer subscription while the worker may continue updating the session cache.

Fixed-interval polling was rejected because backup freshness changes rarely, remote queries may be slow,
and polling competes for the same repository lock as backup creation. Refreshing after every step was
rejected for the same reason.

### Treat pending runtime as an explicit UI state

The immediate root uses `checking` for runtime and `loading` for durable backup cells. Selecting a row opens
its submenu immediately. Connection and configuration-only views remain available. Until runtime arrives,
the lifecycle action is labelled `Start/Stop`; selecting Details or a lifecycle action waits only for the
selected database's local result with a transient status indicator.

Details cancels an unfinished repository phase before running its required fresh matching history query,
so it does not wait for one remote query and then start another. Mutating lifecycle, settings, host, add,
and backup operations stop background assessment first and invalidate the affected session state. Backup
creation updates on the next root refresh rather than starting overlapping verification.

### Make subprocess execution cooperatively cancellable

Add a thread-local cancellation context in `run.py`. The background worker enters that context, and
`run()` polls `Popen.communicate()` against both its existing deadline and the cancellation event. On
cancellation it kills the command's existing process group, waits for cleanup, and raises an internal
cancellation exception that bypasses normal status-error conversion. This reaches Docker, systemd, Restic,
and Restic's rclone child without adding cancellation parameters through every domain function.

The worker is non-daemon and is always cancelled and joined by session shutdown. A daemon thread was
rejected because interpreter exit could orphan external commands. Waiting for an uncancellable worker was
rejected because it would move the same latency to navigation or exit.

## Risks / Trade-offs

- [Live redraw interferes with typed input] -> Keep input on the main thread, update through Rich's lock,
  and exercise delayed updates plus typed numeric input in a pseudo-terminal test.
- [Background assessment races a mutation] -> Stop and join the worker before mutation, then invalidate the
  snapshot and start a new refresh at the next root view.
- [Cancellation leaves Restic or rclone running] -> Preserve `start_new_session=True`, kill the process
  group, communicate to reap the child, and test cancellation against a spawned child.
- [Cached status becomes stale] -> Use a fixed 60-second monotonic TTL and explicit invalidation after
  operations that can change runtime or backup state.
- [Pending values escape into automation] -> Construct pending snapshots only for guided UI use; direct and
  JSON status always complete synchronously and retain boolean health fields.

## Migration Plan

Introduce cancellable execution and focused tests, refactor status snapshots while preserving synchronous
collector tests, then wire the guided session and Live root. Existing configuration, status schema,
repository data, and commands require no migration. Reverting the UI session restores foreground status
collection without persisted-state changes.

## Open Questions

None.

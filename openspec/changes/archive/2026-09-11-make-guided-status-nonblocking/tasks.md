## 1. Cancellable Assessment

- [x] 1.1 Add thread-scoped subprocess cancellation that kills and reaps the existing process group, with focused cancellation and ordinary-run tests.
- [x] 1.2 Refactor status collection to publish a config-only snapshot and incremental local/final snapshots while preserving complete synchronous direct and JSON results.

## 2. Guided Background Refresh

- [x] 2.1 Add one guided refresh session with generation-safe caching, a 60-second TTL, non-overlap, explicit invalidation, and clean cancellation/join behavior.
- [x] 2.2 Render the root menu and accept numbered input immediately while Rich updates pending runtime and backup cells in place; preserve plain injected behavior and stable numbering.
- [x] 2.3 Let pending database menus open immediately, wait only for runtime-dependent actions, and stop/invalidate background work around lifecycle, settings, host, add, and backup mutations while keeping one fresh Details history query.

## 3. Validation

- [x] 3.1 Cover input-before-refresh completion, typed-input live updates, TTL and event refresh, cancellation, non-overlap, Details query count, direct JSON/plain contracts, and background failures with focused tests.
- [x] 3.2 Run lint, unit/config tests, disposable Restic checks, integration collection, strict OpenSpec validation, and one-file terminal/plain smoke validation.

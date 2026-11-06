## Context

The KV role already records a concrete engine and selects a small engine module. Shared `kv.py` assumes redis-cli and Redis authentication environment variables. Redis backup owns a reusable RDB procedure. Creation defaults to Dragonfly; existing roles retain their persisted engine. Configure writes mounted configuration and invokes Compose up, which does not notice file-content-only changes.

## Goals / Non-Goals

**Goals:** Add Valkey as the default for new databases, preserve existing engines, provide useful creation choices and AOF-backed local recovery, and use the existing checked remote backup workflow.

**Non-Goals:** Engine migration, changing Valkey persistence mode after creation, automatic version upgrades, real clustering, arbitrary flags, scheduled Valkey RDB snapshots, and changes to existing engines' persistence defaults.

## Decisions

- Add `engines/valkey.py` rather than an engine class hierarchy. Keep the explicit registry. Use `valkey/valkey:9.1.3` and native valkey-server/cli/check-rdb tools.
- Extend shared protocol helpers with explicit engine selection; use VALKEYCLI_AUTH for Valkey and REDISCLI_AUTH for Redis/Dragonfly. Prefer valkey_version over its redis_version compatibility field. Secrets stay out of arguments.
- Share the Redis/Valkey checked RDB procedure in existing `kv.py`, not a new framework. Preserve redis-rdb-v1 and add valkey-rdb-v1. Valkey uses BGSAVE SCHEDULE when an AOF rewrite delays snapshotting; await a newer completed successful save before copying and checking it. A running RDB save is waited out before requesting a fresh one.
- Durable Valkey: appendonly yes, appendfsync everysec, save "", maxmemory-policy noeviction. Cache: appendonly no, save "", allkeys-lru. AOF handles local recovery; explicit backup jobs alone create RDBs. No periodic snapshots or retention mechanism is needed.
- Reuse KV.memory with a 256mb default for Valkey; leave threads Dragonfly-only. Set the nonsecret mutable maxmemory option in the service command so Compose detects its changes. Keep secrets in valkey.conf. Give Valkey a 60-second stop grace period for normal flushing.
- Add mode/memory/http creation kwargs and CLI flags, plus an optional guided KV configuration step and summary. Validate before source writes. Repeated add preserves an existing role and rejects conflicting supplied settings. Hide Valkey mode from editable settings and reject changes/resets that would change it.
- Generate a private HTTP token for each new KV role even when HTTP starts disabled, so enabling the existing optional sidecar later requires no credential migration.
- Existing Redis and Dragonfly definitions and backups remain valid. New Valkey configuration, info, native TLS routing, and optional HTTP access reuse the same role identity and paths.
- Extend existing unit and disposable-container tests for native/HTTP auth, settings application, AOF crash recovery, cache behavior, and checked RDB export/reload. Do not add Node/BullMQ dependencies. Run focused tests with uv and Ruff; no live production services.

## Risks / Trade-offs

- AOF everysec can lose recent writes after a crash; it is not a zero-loss guarantee. Document this and retain independent remote backups.
- Memory limits do not bound process RSS or fork overhead. Explain headroom rather than invent host resource allocation.
- RDB restore with AOF enabled can ignore the imported RDB. Document loading the RDB with AOF disabled, then enabling AOF live and waiting for initialization before persisting that setting.
- Valkey mode is intentionally creation-only to avoid stale-persistence replay and live AOF conversion complexity. No data files are deleted by configuration changes.

## Migration Plan

No migration. Install the updated tool; only newly created KV roles with no engine selection resolve to Valkey. Existing source records keep their engines and images. Existing backup manifests retain their format identifiers.

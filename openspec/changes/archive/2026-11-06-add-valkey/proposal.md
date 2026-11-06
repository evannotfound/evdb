## Why

Make permissively licensed Valkey the default for new KV databases, with established AOF persistence suitable for general application data and BullMQ. Keep existing Redis and Dragonfly deployments unchanged.

## What Changes

- Add pinned Valkey 9.1.3 support and make it the new KV creation default.
- Offer creation-time mode, memory, and HTTP choices. Valkey mode is immutable after creation; memory, image, and HTTP remain configurable.
- Durable Valkey uses AOF with every-second syncing and no eviction. Cache Valkey uses no persistence and allkeys-lru eviction. Neither mode schedules RDB snapshots.
- Create checked RDB snapshots only for durable backup jobs, using the existing Restic workflow.
- Make shared KV commands and version reporting engine-aware, while retaining direct engine modules and sharing the Redis/Valkey RDB procedure.
- No migrations, removal of engines, automatic upgrades, distributed clustering, arbitrary server flags, or BullMQ-specific presets.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `config`: Valkey default, supported settings, and creation-only Valkey durability mode.
- `deploy`: Valkey service, persistence, configuration application, and idempotent creation.
- `backup`: Checked Valkey RDB backups and native version/format identity.
- `http`: Valkey access through the existing optional authenticated sidecar.
- `operator-cli`: Valkey selection and KV creation options in direct and guided interfaces.

## Impact

Touches configuration, database lifecycle wiring, concrete engine modules and shared KV helpers, backup manifests, CLI/guided creation, documentation, and existing unit/container tests. No new production dependencies. Existing engine definitions, credentials, data, and backup formats remain intact.

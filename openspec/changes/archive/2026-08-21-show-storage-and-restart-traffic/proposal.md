## Why

Host details currently report only free space under `/var/lib/evdb`, hiding configured database roots and
the filesystem that actually backs each path. Operators also cannot restart the dedicated Traefik traffic
proxy from the guided Host section or see how much disk and live data one database role uses.

## What Changes

- Show state storage and every configured database root with its exact path, backing mount and source,
  filesystem type, used/total/free capacity, and assigned database roles.
- Show each database role's allocated directory size and backing filesystem in Details without scanning
  every database during routine host status refreshes.
- Show live PostgreSQL logical size and database count, or Redis/Dragonfly dataset memory and key count;
  retain filesystem facts and identify live data as unavailable when a role is stopped.
- Add a confirmed Restart traffic action under Host that performs a real Traefik Compose restart, warns
  about a brief connection interruption, and waits for Traefik health without restarting databases or
  rerunning initialization.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `jobs`: Expand host status from canonical state storage to mount-aware state and database-root storage.
- `operator-cli`: Add the guided Host traffic action and storage presentation behavior.
- `http`: Expand detailed database information with physical and engine-native data usage.
- `deploy`: Define the dedicated proxy restart operation and its health requirement.

## Impact

The change affects host and database status collection, terminal rendering, Traefik Compose lifecycle
helpers, PostgreSQL and Redis-compatible engine information, and focused unit tests. Structured status
gains additive credential-free storage fields; no runtime dependency, direct CLI command, database
restart, configuration schema, or backup format changes.

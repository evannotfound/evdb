## Why

Current public database hostnames include the database role, such as
`test-dev-01.kv-production-host.storage.evanovation.com` and
`test-dev-01.postgres-production-host.storage.evanovation.com`. The role label repeats information that
is already carried by the protocol and port, and there are no users to preserve the old contract for.

## What Changes

- **BREAKING**: Native Postgres, native KV, and KV HTTP endpoints use one canonical project hostname:
  `<project>.<host-id>.<base-domain>`.
- Postgres continues to use port `5432`, KV continues to use port `6379`, and KV HTTP remains an
  external HTTPS proxy contract on port `443`.
- Traefik native routing remains role-qualified internally through separate entrypoints, router names,
  services, Compose projects, and container/network aliases.
- Generated connection URLs and default KV HTTP domains stop emitting `postgres-<host-id>` and
  `kv-<host-id>` host labels.
- Validation permits one project's Postgres and KV roles to share the same hostname on different
  native ports while still rejecting conflicting routes on the same hostname and entrypoint.
- No compatibility aliases, legacy DNS names, or migration fallback behavior are added.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `config`: Derived database and KV HTTP domains change to the project hostname, and validation
  becomes entrypoint-aware instead of requiring every database hostname to be globally unique.
- `deploy`: Native Traefik routing allows the same SNI hostname on separate Postgres and KV
  entrypoints while preserving unique role-qualified backend identities.
- `http`: KV HTTP defaults to the same project hostname used by native database access.

## Impact

- Affects URL construction, default HTTP-domain persistence, config validation, generated Traefik
  labels, docs, OpenSpec requirements, and tests.
- Existing role-qualified production names will be replaced during the later production move rather
  than supported as aliases.
- No new runtime dependencies or external services are introduced.

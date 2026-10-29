## Why

Postgres currently exposes one endpoint that goes through session-mode PgBouncer when enabled. Operators need an explicit transaction-pooled application URL and a direct URL for migrations and session-dependent work.

## What Changes

- **BREAKING**: The existing plain project hostname always routes directly to Postgres, bypassing PgBouncer and its connection limits.
- When PgBouncer is enabled, expose `<project>-pooled.<host-id>.<base-domain>` on the same native ports and explicitly use transaction pooling.
- Show the pooled URL first as the application recommendation, and label the direct URL for migrations and session-dependent work. Both retain TLS and the existing managed credentials.
- Disabling PgBouncer removes only the pooled endpoint; no pooled-to-direct fallback or separate session pool is added.
- Document the transition and validate the additional hostname using existing DNS length rules.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deploy`: Separate direct and transaction-pooled Postgres routes through the existing TLS proxy.
- `http`: Explicit direct and optional pooled Postgres connection details.

## Impact

Touches database endpoint models and validation, generated PgBouncer configuration and Traefik labels, connection details, documentation, and focused tests. No new dependencies, ports, credentials, or source configuration options. Existing users keep session semantics on the plain hostname but must use the new pooled URL to retain pooling.

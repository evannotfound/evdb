## Context

`postgres.files()` currently omits `pool_mode`, so PgBouncer uses session pooling. `postgres.services()` attaches the single project route to either PgBouncer or Postgres. `database.connection()` emits one TLS URL. Both CLI info and guided connection screens display these details.

## Goals / Non-Goals

**Goals:** Give applications a clearly named transaction endpoint while migrations and session-dependent work have a stable direct endpoint. Reuse the current TLS proxy, configured native ports, wildcard certificate, and managed login.

**Non-Goals:** A third session pool, new pool-mode configuration, new ports, credential changes, ORM-specific configuration, or production deployment.

## Decisions

- Keep `Database.domain` direct and add `pooled_domain` with `-pooled` in the project label. Unlike another subdomain level, this fits the existing wildcard certificate. Validate the extra label/hostname length whenever pooling is enabled; project names must end in an environment and number, so a pooled name cannot collide with another valid project name.
- Always put the existing native route on the primary service. Give the PgBouncer container a distinct role-qualified pooled router/service through `docker.route()`, using the same Postgres entrypoint. Set `pool_mode = transaction` explicitly. KV routing remains unchanged.
- Keep the connection dictionary's `url` as the plain/direct URL and add `pooled_url` only when enabled. Both URLs use the same percent-encoded credentials, database, preferred external port, and TLS parameter. Do not introduce a redundant direct URL field.
- Use `connection_details()` for both CLI info and guided output. For Postgres, show the optional pooled URL first, labeled for transaction-mode application traffic, then the direct URL labeled for migrations/sessions. Preserve KV output.
- No fallback: disabling PgBouncer removes its route and URL, while direct access remains unchanged.

## Risks / Trade-offs

- Existing clients on the plain hostname bypass the old session pool after rerender → document that connection limits/queuing no longer apply and that application owners should deliberately adopt the pooled URL only when compatible.
- Transaction pooling does not preserve arbitrary session state → label the direct URL for migrations and session-dependent clients rather than promising every app works through the pool.
- Long project names may not leave room for `-pooled` → reject invalid enabled pooled hostnames through the existing configuration validation path.

## Migration Plan

Normal database start/restart renders the new routes and pool configuration. Existing plain URLs remain valid and retain session semantics. Operators can move compatible application traffic to the new pooled hostname and keep migrations on the plain one. Rolling back requires moving pooled clients back to the plain hostname before restoring the previous release and restarting the database. No data migration is involved.

## Validation

Cover generated routes and transaction mode, disabled pooling, URL encoding and preferred ports, DNS length validation, and both credential display paths with focused unit tests. Extend the disposable Postgres integration to prove backend reuse between transactions while a client remains connected, and direct session state across transactions. Run the focused checks and Ruff; the project has no configured typecheck command.

## Context

evdb terminates native database TLS in one managed Traefik instance. Its generated dynamic file defines
the wildcard certificate store but leaves TLS options at Traefik defaults. Current PostgreSQL clients can
offer the registered `postgresql` ALPN value; Traefik rejects the handshake when its supported list has no
overlap.

## Goals / Non-Goals

**Goals:**

- Negotiate TLS with PostgreSQL clients that offer `postgresql` ALPN.
- Preserve Traefik's existing ALPN behavior for other managed native connections.
- Keep the fix in host-owned Traefik configuration so existing database projects need no rerender.

**Non-Goals:**

- Change certificates, PostgreSQL authentication, SNI rules, or backend services.
- Add an operator setting for ALPN protocols.
- Add client-specific connection workarounds.

## Decisions

Define `tls.options.default.alpnProtocols` in the existing dynamic `tls.yml` with `postgresql` followed by
Traefik's current defaults: `h2`, `http/1.1`, and `acme-tls/1`. Configuring the default option applies to
the existing TCP routers without changing their Docker labels. Retaining all existing values matters
because an explicit ALPN list replaces rather than extends Traefik's defaults.

A named PostgreSQL-only TLS option was considered, but every existing PostgreSQL router would need a new
Docker label. That would require rerendering and recreating database or PgBouncer containers, conflicting
with the installer update contract and adding unnecessary interruption.

## Risks / Trade-offs

- Explicitly listing Traefik defaults means a future Traefik upgrade will not inherit additions to that
  list automatically. The image is pinned, and the focused assertion makes the override visible during an
  intentional upgrade.
- The default option also advertises `postgresql` on the KV entrypoint. SNI still selects the router and
  backend, so this broadens only TLS negotiation and does not route PostgreSQL traffic to KV services.

## Migration Plan

Publish the change in a patch release. Installing the release on an existing host reruns `evdb init --yes`
and regenerates the managed Traefik configuration without rewriting or restarting database roles. A new
host receives the option during initial setup.

## Open Questions

None.

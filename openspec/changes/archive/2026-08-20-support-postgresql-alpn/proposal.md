## Why

PostgreSQL clients that advertise the registered `postgresql` ALPN protocol cannot complete TLS through
evdb's Traefik proxy because its default ALPN list has no matching protocol. The failure occurs before
authentication and affects current TablePlus releases and other clients using PostgreSQL ALPN.

## What Changes

- Configure managed Traefik TLS to negotiate `postgresql` ALPN.
- Preserve Traefik's existing HTTP and ACME ALPN defaults when overriding the list.
- Cover the generated dynamic TLS configuration with focused tests.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deploy`: Require the dedicated native proxy to accept PostgreSQL TLS clients that advertise
  `postgresql` ALPN without changing database routing or lifecycle behavior.

## Impact

This changes the generated Traefik dynamic TLS file and its focused host tests. It adds no configuration
field or dependency and does not rewrite or restart database roles.

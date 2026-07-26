# Native and HTTP routing

## Dedicated native Traefik

evdb owns one `evdb-traefik` Compose project and one dedicated external Docker network. It is the
only evdb service that publishes native host ports 5432 and 6379. Postgres, PgBouncer, Redis,
Dragonfly, and HTTP sidecars use unique project/role service names and network aliases.

Each database contributes a TLS `HostSNI` router for its derived domain. Postgres routes to
PgBouncer when enabled and otherwise to Postgres. KV routes to its selected Redis-compatible
engine. One project may have both roles because Compose project names remain distinct:
`evdb-<project>-postgres` and `evdb-<project>-kv`.

Native Traefik uses a pinned image, concrete Docker healthcheck, and ACME DNS-01 resolver. The
provider credential is a private host file, and persistent `acme.json` is mode `0600`. Dedicated
Traefik does not bind ports 80 or 443. Host setup refuses occupied native ports or an existing
network it cannot prove evdb owns.

## KV HTTP sidecars

HTTP defaults on for KV. Its pinned `serverless-redis-http` sidecar reads token and connection data
from a private mode-`0600` environment file and binds only
`127.0.0.1:<machine-assigned-port>:80`. The stable port is machine-owned state. A sidecar connects
through the unique matching KV service identity, so another project cannot claim its backend.

## External HTTP boundary

The external HTTP proxy owns public ports 80 and 443, HTTPS certificates, and forwarding to the
loopback sidecars. evdb records the intended public domain and local endpoint but does not inspect,
authenticate to, configure, or restart that proxy. Public route and certificate changes belong to
their owner and to a separate production migration.

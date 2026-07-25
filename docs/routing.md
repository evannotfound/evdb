# Database and HTTP routing

## Native database ports

Traefik is the only Compose service that publishes host ports 5432 and 6379. Postgres,
PgBouncer, Redis, and Dragonfly remain internal on the external `traefik-net` network.
Every database container has an instance-specific name.

The shared network and Traefik are release infrastructure. Apply inspects `traefik-net`, creates it
when absent, applies and health-checks Traefik before affected databases, and restores the prior
Traefik definition if a candidate release fails. Primary database and Traefik containers carry a
generated service-contract hash label; plan and status treat a missing or wrong label as drift even
when the image is unchanged.

Traefik reads labels from the matching backend container. TLS `HostSNI` routes send
`<name>.postgres-<host>.<domain>:5432` to that database's PgBouncer or Postgres container and
send `<name>.kv-<host>.<domain>:6379` to that database's Redis or Dragonfly container. Shared
aliases such as `redis` are not used across projects.

## Serverless HTTP

HTTP is enabled by default for Redis and Dragonfly. Generated Compose adds the locked
`serverless-redis-http` image with `SRH_MODE=env` and a 20-connection default. Its private
environment file contains the token and a database-specific backend connection. Container port
80 publishes only as `127.0.0.1:<generated-port>:80`; it has no Traefik labels.

Per-database `http: false` removes the sidecar. Stable loopback ports are allocated and retained
in tool-owned `host.lock.json`; operators do not set or edit them. The derived domain and locked
port form an integration contract for the external proxy owner. Apply does not cause a public
route change.

## External HTTP proxy

An external system owns ports 80 and 443, certificates, public hostnames, and forwarding to
the loopback sidecars. This repository has no proxy credentials, API client, image pin,
route plan, certificate setting, or generated proxy file. External route changes are
coordinated separately during the production move.

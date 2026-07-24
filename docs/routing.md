# Database and HTTP routing

## Native database ports

Traefik is the only Compose service that publishes host ports 5432 and 6379. Postgres,
PgBouncer, Redis, and Dragonfly remain internal on the external `traefik-net` network.
Every database container has an instance-specific name.

Traefik reads labels from the matching backend container. TLS `HostSNI` routes send
`<instance>.postgres-montreal-01.storage.evanovation.com:5432` to that instance's PgBouncer
or Postgres container and send `<instance>.kv-montreal-01.storage.evanovation.com:6379` to
that instance's Redis or Dragonfly container. Shared aliases such as `redis` are not used
across projects.

## Serverless HTTP

When `http.enabled` is true, the KV Compose file adds the pinned
`serverless-redis-http` image with `SRH_MODE=env` and the configured connection limit. Its
private environment file contains the token and an instance-specific backend connection.
Container port 80 publishes only as `127.0.0.1:<http.port>:80`; it has no Traefik labels.

`http.enabled: false` removes the sidecar. The initial config enables all 11 KV sidecars.
The configured domain and loopback port form an integration contract for the external proxy
owner; they do not cause any public route change.

## External HTTP proxy

An external system owns ports 80 and 443, certificates, public hostnames, and forwarding to
the loopback sidecars. This repository has no proxy credentials, API client, image pin,
route plan, certificate setting, or generated proxy file. External route changes are
coordinated separately during the production move.

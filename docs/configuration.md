# Host configuration

## One human-owned file

Each managed host has one human-owned `config/<host>/host.yml`. Separate `postgres.yml` and
`kv.yml` files and migration fields such as `current` and `target` are rejected.

```yaml
host:
  id: example-01
  ssh: deploy@example-01
  domain: storage.example.com
  data_root: /srv/databases
  images:
    postgres: postgres:16
    pgbouncer: edoburu/pgbouncer:v1.25.1-p0
    redis: redis:7.2.5
    dragonfly: docker.dragonflydb.io/dragonflydb/dragonfly:v1.34.1
    http: hiett/serverless-redis-http@sha256:5b0bb9239fce53abf87b2018a7a0deb9ec7bd900c5360738fe5fbeeb426f9150
    traefik: traefik:v3.7.8
  backup:
    repos:
      postgres: rclone:remote:example-01/postgres
      kv: rclone:remote:example-01/kv
  one_password:
    vault: Operations
    system_item: evanovation-db

databases:
  - name: app-prod-01
    type: postgres
  - name: cache-prod-01
    type: dragonfly
```

All shown host fields are required. Every image source must use an explicit non-`latest` tag or an
immutable `@sha256` reference; untagged references and explicit `:latest` are rejected. Postgres,
Redis, and Dragonfly additionally require a tagged positive engine major, so `postgres:stable`,
`redis:0`, and digest-only engine references are invalid. Infrastructure images may remain
digest-only.
`one_password` contains vault and item names, not `op://` references or resolved values. Database
names are safe lowercase identifiers ending in `-dev-N`, `-test-N`, or `-prod-N`.

## Built-in defaults

Host defaults:

| Setting | Default |
| --- | --- |
| Backup retention | 7 daily, 4 weekly, 12 monthly, 12 rotating data parts |
| Minimum free data and backup space | 5 GiB |
| Maximum confirmed backup age | 26 hours |
| Maximum restore verification and candidate age | 30 days |
| HTTP loopback allocation range | 13379 through 13478 |
| Runtime config, state, backup, lock paths | `/etc/evanovation-db`, `/var/lib/evanovation-db`, `/var/lib/evanovation-db/backups`, `/var/lib/evanovation-db/locks` |
| Command, health timeouts | 300 seconds, 120 seconds |
| Backup, restore, maintenance timeouts | 6 hours, 8 hours, 24 hours |

Database defaults:

| Type | Derived behavior |
| --- | --- |
| Postgres | Durable, backups enabled, port 5432, user `default`, database `postgres`, PgBouncer enabled, 100 clients, pool size 20, reserve size 5 |
| Redis | Durable, backups enabled, port 6379, authenticated HTTP enabled |
| Dragonfly | Durable, backups enabled, port 6379, one thread, 256mb maximum memory, authenticated HTTP enabled |
| KV HTTP sidecar | Stable generated loopback port, public derived hostname, 20 maximum connections |

The controller derives environment, container and Compose project names, data paths, native and
HTTP domains, resource policy, backup policy, and convention-based 1Password references. These
values are not per-database source fields.

## Supported overrides

Only real exceptions belong on a database entry:

```yaml
databases:
  - name: direct-prod-01
    type: postgres
    pooler: false
    max_clients: 500
    pool_size: 40
    reserve_size: 8

  - name: transient-prod-01
    type: dragonfly
    mode: cache
    memory: 2gb
    threads: 8
    http: false
```

Supported keys are:

| Key | Applies to | Meaning |
| --- | --- | --- |
| `mode: durable|cache` | Redis, Dragonfly | Cache mode disables durability and backups; durable is the default |
| `pooler` | Postgres | Enable or disable PgBouncer |
| `max_clients`, `pool_size`, `reserve_size` | Postgres | Positive PgBouncer sizing values |
| `memory` | Dragonfly | Positive `kb`, `mb`, or `gb` value |
| `threads` | Dragonfly | Positive thread count |
| `http` | Redis, Dragonfly | Enable or disable the authenticated HTTP sidecar |

Optional host overrides live under `host.backup`: partial `retention`, `min_free_gb`,
`max_age_hours`, and `restore_max_age_days`. `host.http_ports.start` and `.end` may replace the
default unprivileged allocation range. Existing surviving allocations must remain inside an
expanded range; the controller refuses to reallocate them silently.

Per-database images, paths, domains, ports, credentials, raw settings, backup dictionaries,
container names, projects, and resources are unsupported.

## Generated lock

`host.lock.json` sits beside `host.yml`. It is tool-owned and secret-free. Operators may review
and commit it, but must never edit it manually.

The lock records schema version 1, host identity, Linux/amd64 platform, each image's source
reference and immutable `sha256` digest, and HTTP loopback ports keyed by typed selector. Plan and
apply resolve only new or changed tagged sources with `docker manifest inspect --verbose`;
unchanged locks are preserved. An already-digested source reuses its digest without a registry
request. Resolution does not change a local or remote container. Generated Compose normalizes each
image to one `@sha256` suffix without duplicating a source digest.

Plan keeps a newly resolved lock in memory and remains read-only. Confirmed apply atomically
writes mode `0644` lock data before staging the release. New HTTP-enabled KV databases receive
the next free stable port; reorder and later additions do not change surviving assignments.
`evdb validate`, status, and runtime normalization reject a missing, secret-bearing, malformed,
wrong-host, or source-inconsistent lock.

## Selectors

Use a plain database name when it is unique. Postgres and KV products may share a name; in that
case every database command rejects the ambiguous name and lists valid selectors such as
`postgres/app-prod-01` and `dragonfly/app-prod-01`. Typed selectors are required only for this
ambiguity.

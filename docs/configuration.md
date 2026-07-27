# Host configuration

## Authoritative source

Each host owns one readable `/etc/evdb/host.yml`. The normal mutation interface is the installed
host-local command. Source has no passwords, tokens, resolved credentials, live container facts,
release pointers, or generated deployment fields.

```yaml
host:
  id: example-01
  domain: storage.example.com
  data_root: /srv/databases
  backup:
    repos:
      postgres: rclone:remote:example-01/postgres
      kv: rclone:remote:example-01/kv
    retention:
      daily: 7
      weekly: 4
      monthly: 12
      data_parts: 12
  routing:
    acme_email: operations@example.com
    dns_provider: cloudflare
    traefik_image: traefik:v3.7.8

projects:
  app-prod-01:
    postgres:
      image: postgres:16
      pgbouncer:
        enabled: true
        image: edoburu/pgbouncer:v1.25.1-p0
    kv:
      engine: dragonfly
      image: docker.dragonflydb.io/dragonflydb/dragonfly:v1.34.1
      mode: durable
      http:
        enabled: true
        image: hiett/serverless-redis-http@sha256:...
```

Project IDs are lowercase safe identifiers ending in `-dev-N`, `-test-N`, or `-prod-N`. A project
contains at most one `postgres` role and one `kv` role. The stable identity used by commands,
backups, locks, status, and state is `project/role`. KV records either `dragonfly` or `redis` as its
concrete engine.

Public connection hostnames are project-level: `<project>.<host.id>.<host.domain>`. If a project has
both roles, Postgres and KV share that hostname and are distinguished by scheme and port.

## Defaults and overrides

New Postgres roles are durable, use port 5432, enable PgBouncer, and default to user `default` and
database `postgres`. New KV roles default to durable Dragonfly, port 6379, 256mb memory, one thread,
and an authenticated HTTP sidecar with 20 connections. Cache mode disables backups.

Role configuration may override its own image and valid typed settings. Postgres accepts PgBouncer
enablement, image, client, pool, and reserve sizing. KV accepts durability mode, HTTP enablement and
image, HTTP connections, and its primary image. Dragonfly additionally accepts memory and threads.
Redis rejects Dragonfly-only fields. Untagged images, `latest`, invalid majors, unsafe paths, and
settings for the wrong role are rejected before generated or live files change.

Creating KV without `--engine` persists `engine: dragonfly`; defaults that affect an installed role
are explicit enough that a future package default cannot silently change it.

## Canonical paths

```text
/etc/evdb/host.yml
/etc/evdb/host.previous.yml
/etc/evdb/projects/<project>/<role>/compose.yaml
/etc/evdb/traefik/compose.yaml
/etc/evdb/secrets/<project>/<role>/
/var/lib/evdb/state/host.json
/var/lib/evdb/backups/<project>/<role>/
/var/lib/evdb/restores/
/var/lib/evdb/locks/
/var/lib/evdb/rclone/rclone.conf
/var/lib/evdb/activity.jsonl
<data_root>/<project>/<role>/data/
```

Generated Compose and service files are readable conventional YAML but tool-owned. Resolved image
digests, allocated HTTP loopback ports, installed flags, generated hashes, tool version, and
operation results are private machine-owned state. Operators do not copy that state into source.
Configuration writes are atomic, retain one `host.previous.yml`, and append a bounded secret-free
activity record.

## Validation boundaries

The loader rejects the prior database-list schema, separate engine files, checked-in lock files,
runtime copies, and migration-only fields. Omitting an installed role is not removal: status reports
the orphan and mutating commands stop without deleting Compose, secrets, data, or backups.

Changing a Postgres, Redis, or Dragonfly major and converting Redis to Dragonfly or the reverse are
data migrations, not settings changes. They fail before service stop. Conversion of an existing
production source belongs to a separate production migration.

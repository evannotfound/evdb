# Host configuration

## Authoritative source

Each host owns two complete evdb source files and one native rclone file:

```text
/etc/evdb/config.yml
/etc/evdb/secrets.yml
/etc/evdb/rclone.conf
```

`config.yml` is the readable non-secret source for host identity, routing, one backup repository,
projects, roles, concrete engines, images, and explicit settings. Canonical ownership is
`root:evdb` mode `0640` for `config.yml` and `evdb:evdb` mode `0600` for `secrets.yml` and
`rclone.conf`.

`/etc/evdb` is `root:evdb` mode `01770`: group-writable and sticky. Group write access lets the evdb
account create a temporary file beside its own `rclone.conf` and atomically replace it when rclone
persists OAuth refreshes. The service has no write bit on root-owned `config.yml`, and sticky-directory
ownership prevents it from unlinking or renaming that file. `rclone.conf` remains in rclone's native
mutable format so refreshed state survives initialization and installer updates.

The normal mutation interface is the installed host-local command. A representative non-secret
structure is:

```yaml
host:
  id: example-01
  domain: storage.example.com
  data_root: /srv/databases
  backup:
    repository: rclone:remote:evdb/example-01
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
backups, locks, and status is `project/role`. KV records either `dragonfly` or `redis` as its concrete
engine.

Public connection hostnames are project-level: `<project>.<host-id>.<base-domain>`. If a project has
both roles, Postgres and KV share that hostname and are distinguished by scheme and port.

## Defaults and overrides

New Postgres roles use port 5432, enable PgBouncer, and use fixed user `default` and database
`postgres`. New KV roles default to durable Dragonfly, port 6379, and authenticated HTTP. Cache mode
disables backups.

Role configuration may override its own image and valid typed settings. Postgres accepts PgBouncer
settings. KV accepts durability and HTTP settings. Dragonfly additionally accepts memory and threads;
Redis rejects Dragonfly-only fields. Untagged images, `latest`, unsafe paths, and settings for the
wrong role are rejected before generated or live files change.

Creating KV without `--engine` persists `engine: dragonfly`; defaults that affect an installed role
are explicit so a future package default cannot silently change it.

## Files and paths

```text
/etc/evdb/config.yml
/etc/evdb/secrets.yml
/etc/evdb/rclone.conf
/etc/evdb/projects/<project>/<role>/compose.yaml
/etc/evdb/traefik/compose.yaml
/var/lib/evdb/backups/<project>/<role>/
/var/lib/evdb/locks/
<data_root>/<project>/<role>/data/
```

Generated Compose and engine files are conventional tool-owned files derived directly from
`config.yml` and `secrets.yml`. Configured non-`latest` image references are written into Compose as
configured. Local backups and locks are the only mutable runtime files under `/var/lib/evdb`.

Configuration writes validate complete candidates and atomically replace only the changed source
file. Initialization copies a supplied native rclone file only when `/etc/evdb/rclone.conf` is absent;
later runs preserve its bytes. Once configured, initialization also preserves `secrets.yml`; an
additional `--restic-password-file` value is ignored rather than replacing the stored Restic password.

## Validation boundaries

Validation rejects unsafe project IDs, duplicate roles, unsupported engines, unsafe or overlapping
paths, colliding routes, incomplete routing or backup settings, `latest` or unversioned images,
role-specific settings on the wrong engine, missing matching secrets, unexpected secret keys, and
unsafe file ownership or modes.

Changing a Postgres, Redis, or Dragonfly major and converting Redis to Dragonfly or the reverse are
data migrations, not settings changes. Production conversion belongs to a separate change.

The pre-v1 `/etc/evdb/host.yml` layout is not accepted or converted automatically. Reset only approved
disposable hosts; production conversion, including `montreal-01`, is a separate change.

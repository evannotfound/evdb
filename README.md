# evanovation-db

`evdb` is the host-local database manager for Postgres and Redis-compatible KV roles. The
installed command reads `/etc/evdb/host.yml`, operates Docker Compose and Restic locally, and
addresses every database as `<project>/postgres` or `<project>/kv`. SSH is an operator transport,
not an application protocol.

Running `evdb` in a terminal opens the guided menu. Scripts, systemd units, and remote operators
use the same explicit commands. The external HTTP proxy remains outside evdb.

## Install and set up a host

Install an exact published version into its version directory, expose the stable command path, then
run privileged setup on the database host:

```sh
VERSION=1.2.3
UV="$(command -v uv)"
sudo install -d -m 0755 \
  "/opt/evdb/versions/${VERSION}/tools" \
  "/opt/evdb/versions/${VERSION}/bin"
sudo env \
  UV_TOOL_DIR="/opt/evdb/versions/${VERSION}/tools" \
  UV_TOOL_BIN_DIR="/opt/evdb/versions/${VERSION}/bin" \
  "${UV}" tool install "evanovation-db==${VERSION}"
sudo ln -sfn "versions/${VERSION}" /opt/evdb/current
sudo ln -sfn /opt/evdb/current/bin/evdb /usr/local/bin/evdb
sudo /usr/local/bin/evdb host setup
/usr/local/bin/evdb host check
```

Setup checks Python, Docker Compose, Restic, rclone, systemd, DNS inputs, filesystems, and native
ports. It creates the service account, canonical directories, dedicated database network and
Traefik project, private host files, packaged units, and initial host configuration. It does not
install unrelated prerequisites or migrate an existing database layout.

For repository development, invoke tests rather than pointing operator commands at fixtures:

```sh
uv sync --locked
make check
```

Make operator targets always use the authoritative `/etc/evdb/host.yml`. Alternate fixture paths
are test inputs only and are not a supported mutation boundary.

## Host configuration

Configuration is project-first and secret-free:

```yaml
host:
  id: example-01
  domain: storage.example.com
  data_root: /srv/databases
  backup:
    repos:
      postgres: rclone:remote:example-01/postgres
      kv: rclone:remote:example-01/kv
  routing:
    acme_email: operations@example.com
    dns_provider: cloudflare

projects:
  app-prod-01:
    postgres:
      image: postgres:16
    kv:
      engine: dragonfly
      image: docker.dragonflydb.io/dragonflydb/dragonfly:v1.34.1
```

A project has at most one role of each kind. KV defaults to Dragonfly when created and records the
concrete `dragonfly` or `redis` engine. Images belong to each role and resolve to immutable digests
in private machine-owned state. See [configuration](docs/configuration.md).

## Direct commands

```sh
evdb status
evdb status app-prod-01/postgres --json

evdb database list
evdb database add app-prod-01 postgres
evdb database add app-prod-01 kv --engine redis
evdb database info app-prod-01/postgres
evdb database configure app-prod-01/kv --memory 2gb --threads 4
evdb database start app-prod-01/postgres
evdb database stop app-prod-01/postgres
evdb database restart app-prod-01/postgres
evdb database logs app-prod-01/postgres --lines 200

evdb backup create app-prod-01/postgres
evdb backup list app-prod-01/postgres
evdb backup test app-prod-01/postgres latest
evdb backup retention app-prod-01/postgres
evdb backup prune postgres
evdb backup repository-check postgres --rotate

evdb restore app-prod-01/postgres latest
evdb host check
evdb host setup
evdb host update 1.2.3
```

Mutating commands identify the host and project/role, preview their effect, and require a terminal
confirmation or `--yes`. Missing values fail immediately without a TTY. `database info` is the only
credential-bearing view and requires a terminal because it prints complete credentials.

## Recovery model

Backups are checked locally, hashed in `backup.json`, and uploaded with stable host/project/role
tags. `backup test` restores one exact backup into an isolated engine without touching live data.

`restore` is one operation: it verifies the selected data, creates and uploads a safety backup,
shows the expected outage and paths, and then exchanges directories on the same filesystem. It
health-checks the restored role and performs automatic recovery to prior data on failure. There is
no operator-managed candidate or separate data activation command. See [backup](docs/backup.md)
and [restore](docs/restore.md).

## Boundaries

Database removal, engine-major migration, Redis/Dragonfly conversion, password escrow or rotation,
public HTTP proxy changes, and the Montreal cutover are not general commands. They require separate
design and review. In particular, `config/montreal-01` is reference input for a separate production
migration and is never a development or CI target.

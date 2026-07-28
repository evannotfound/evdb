# evdb

**Own your database infrastructure.**

[![Check](https://github.com/evannotfound/evdb/actions/workflows/check.yml/badge.svg)](https://github.com/evannotfound/evdb/actions/workflows/check.yml)
[![Release](https://img.shields.io/github/v/release/evannotfound/evdb)](https://github.com/evannotfound/evdb/releases)
[![License](https://img.shields.io/github/license/evannotfound/evdb)](LICENSE)

evdb turns a Linux server into a platform for Postgres and Redis-compatible databases. (More dbs to come)

Create a database, get a private TLS connection URL, and use it with any standard Postgres or Redis client. evdb handles the infrastructure around it: provisioning, routing, credentials, backups, health checks, and updates.

```sh
sudo evdb database add notes-prod-01 postgres
sudo evdb database info notes-prod-01/postgres
```

```text
postgresql://default:<password>@notes-prod-01.example-01.storage.example.com:5432/postgres?sslmode=require
```

## Installation

evdb supports Ubuntu 22.04 or newer on ARM64 and x86_64. The host requires Docker with Compose,
Restic 0.17 or newer, rclone, systemd, DNS provider credentials for TLS certificates, and free native
ports `5432` and `6379`.

Install the latest standalone release, then initialize the host:

```sh
curl -fsSL https://github.com/evannotfound/evdb/releases/latest/download/install.sh | sudo sh
sudo evdb init
```

Initialization creates or validates `/etc/evdb/config.yml`, private `/etc/evdb/secrets.yml`, and
mutable `/etc/evdb/rclone.conf`. It initializes the host's one Restic repository, including a missing
rclone-backed path, then enables one persistent randomized daily systemd timer that runs
`evdb backup create --all`.

On first init, the guided masked Restic password prompt generates a password when left blank. Direct
init can read one from a private file with `--restic-password-file PATH`; the credential is never
accepted inline or through the environment and is not printed.

See [Host setup and updates](docs/setup.md) for pinned installations and configured-host updates.

## Quick start

Create Postgres and the default Dragonfly-backed KV role, or select Redis explicitly:

```sh
sudo evdb database add notes-prod-01 postgres
sudo evdb database add notes-prod-01 kv
sudo evdb database add cache-prod-01 kv --engine redis
```

Retrieve connection details:

```sh
sudo evdb database info notes-prod-01/postgres
sudo evdb database info notes-prod-01/kv
```

Postgres, Redis-compatible native access, and Redis over HTTPS use standard clients. Native Postgres
and KV connections use TLS and share a project hostname; the scheme and port select the protocol.
`database info` deliberately shows usable credentials in a terminal. Status and machine-readable
output remain credential-free.

## Backups

Backup creation runs engine-native checks, records file sizes and SHA-256 hashes, and reports success
only after Restic confirms the snapshot. Create or inspect one database backup directly:

```sh
sudo evdb backup create notes-prod-01/postgres
sudo evdb backup list notes-prod-01/postgres
sudo evdb backup create --all
```

The automatic timer runs the same all-database command sequentially. Remote snapshots grow
indefinitely because evdb v1 does not delete them. Recovery uses Restic plus manual engine tools; evdb
does not provide a recovery command in v1.

## Commands

Run `evdb` without arguments for the guided terminal interface. Direct commands are available for
scripts, SSH, and systemd:

```sh
sudo evdb init
sudo evdb status

sudo evdb database list
sudo evdb database add notes-prod-01 postgres
sudo evdb database info notes-prod-01/postgres
sudo evdb database configure notes-prod-01/postgres
sudo evdb database start notes-prod-01/postgres
sudo evdb database stop notes-prod-01/postgres
sudo evdb database restart notes-prod-01/postgres
sudo evdb database logs notes-prod-01/postgres

sudo evdb backup create notes-prod-01/postgres
sudo evdb backup create --all
sudo evdb backup list notes-prod-01/postgres
```

Explicit direct commands execute immediately. Guided creation and settings changes use one final
confirmation.

Operational identifiers stay visible in output and errors, including repository URLs, rclone remote
names, paths, image references, snapshot IDs, and unrelated subprocess output. evdb redacts only exact
managed password and token values and their required encoded forms.

## V1 limitations

evdb v1 targets Ubuntu hosts with systemd. A failed creation or settings change leaves readable source
and generated files in place for correction and retry with `database start`. Existing pre-v1 host
files are not migrated automatically. Production migration is a separate approved change.

## Documentation

- [Command reference](docs/commands.md)
- [Host setup and updates](docs/setup.md)
- [Configuration](docs/configuration.md)
- [Database operations](docs/deploy.md)
- [Routing](docs/routing.md)
- [Backups](docs/backup.md)
- [Credentials](docs/secrets.md)

## Development

Development uses Python and `uv`; installed hosts require neither.

```sh
uv sync --locked
make check
make binary
```

For an uncommitted revision on a disposable VPS, use the guarded source workflow in
[Host setup and updates](docs/setup.md#disposable-vps-development). It keeps the installed release
tree intact and rejects `montreal-01`.

## License

[MIT](LICENSE)

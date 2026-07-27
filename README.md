# evdb

**Own your database infrastructure.**

[![Check](https://github.com/evannotfound/evdb/actions/workflows/check.yml/badge.svg)](https://github.com/evannotfound/evdb/actions/workflows/check.yml)
[![Release](https://img.shields.io/github/v/release/evannotfound/evdb)](https://github.com/evannotfound/evdb/releases)
[![License](https://img.shields.io/github/license/evannotfound/evdb)](LICENSE)

evdb turns a Linux server into a platform for Postgres and Redis-compatible databases. (More dbs to come)

Create a database, get a private TLS connection URL, and use it with any standard Postgres or Redis client. evdb handles the infrastructure around it: provisioning, routing, credentials, backups, health checks, restores, updates, and rollback.

```sh
evdb database add notes-prod-01 postgres
evdb database info notes-prod-01/postgres
```

```text
postgresql://default:<password>@notes-prod-01.your-own-infra.com:5432/postgres?sslmode=require
```

## Why evdb

Running a database container is easy. Operating it safely over time is not.

evdb handles the parts that usually require scripts, configuration, and manual recovery work:

- TLS routing for Postgres, Dragonfly, and Redis
- Separate credentials for every database
- Encrypted off-site backups
- Automatic backup verification
- Safer restores with rollback
- Health checks and update recovery
- Stable ports and connection details

You keep control of the server and storage without building the surrounding database platform yourself.

## Installation

evdb supports:

- Ubuntu 22.04 or newer
- ARM64 and x86_64

The host also requires:

- Docker with Compose
- Restic
- rclone
- systemd
- DNS provider credentials
- Free ports `5432` and `6379`

Install the latest release:

```sh
curl -fsSL https://github.com/evannotfound/evdb/releases/latest/download/install.sh | sudo sh
sudo evdb host setup
```

The installer verifies the release checksum and installs an exact version.

See [Host setup and updates](docs/setup.md) for pinned installations, unattended setup, and release security details.

## Quick start

Create a Postgres database:

```sh
evdb database add notes-prod-01 postgres
```

Or create a Redis-compatible database:

```sh
evdb database add notes-prod-01 kv
```

Retrieve its connection details:

```sh
evdb database info notes-prod-01/postgres
evdb database info notes-prod-01/kv
```

evdb prints a complete TLS connection URL:

```text
postgresql://default:<password>@<database-host>:5432/postgres?sslmode=require
rediss://default:<password>@<database-host>:6379/0
```

Set it as `DATABASE_URL` or `REDIS_URL` and use the client your application already has.

`database info` is the only command that prints complete credentials, and it only prints them in an interactive terminal.

## Features

### Standard database connections

Applications connect directly to Postgres, Dragonfly, or Redis using their existing drivers.

There is no evdb SDK, proxy protocol, or application dependency.

### One identity per database

Each database is addressed as:

```text
<project>/postgres
<project>/kv
```

A project can have Postgres, a Redis-compatible database, or both.

Each database receives its own hostname, credentials, storage, container, and connection URL.

### TLS hostname routing

All Postgres databases can share port `5432`, and all Redis-compatible databases can share port `6379`.

evdb uses TLS and the requested hostname to route each connection to the correct database container.

### Private credential storage

Passwords are stored in private files on the host.

They are kept out of:

- Editable configuration
- Internal state
- Logs
- Backup records
- JSON output

### Verified backups

evdb creates encrypted Restic backups and uploads them to the storage provider you configure.

A backup is not treated as successful just because the upload completed. evdb restores it into an isolated container and verifies that the restored database contains the data recorded when the backup was created.

### Safer restores

Before replacing a live database, evdb:

1. Verifies the requested backup
2. Creates and uploads a safety backup of the current database
3. Restores the requested backup
4. Runs health checks against the restored database

If the restored database fails its health checks, evdb restores the previous data.

### Planned updates and rollback

Before applying a change, evdb shows what it will do and asks for confirmation.

Credentials and assigned ports remain stable. If a database deployment or evdb update fails, evdb rolls the change back.

## How it compares

Services such as Supabase, Neon, Amazon RDS, and ElastiCache run databases on infrastructure managed by the provider.

evdb gives applications a similar interface: a standard connection URL. The difference is that the databases run on a Linux server you own or rent.

evdb is MIT licensed and has no per-database or usage fees. You still pay for the server, DNS, and backup storage, and you remain responsible for the underlying host.

evdb is not intended to reproduce every feature of a managed cloud service. It does not provide:

- Supabase Auth or Storage
- Neon serverless scaling or database branching
- AWS high availability or managed service-level agreements

It is designed for small deployments where you want control of the infrastructure without maintaining your own collection of routing, backup, restore, and update scripts.


## Usage

Run `evdb` without arguments to open the guided menu:

```sh
evdb
```

You can also use direct commands:

```sh
evdb status

evdb database add notes-prod-01 postgres
evdb database add notes-prod-01 kv

evdb backup create notes-prod-01/postgres
evdb restore notes-prod-01/postgres latest
```

Commands that make changes show a plan and ask for confirmation before applying it.

## Documentation

- [Command reference](docs/commands.md)
- [Host setup and updates](docs/setup.md)
- [Configuration](docs/configuration.md)
- [Database deployment](docs/deploy.md)
- [Routing](docs/routing.md)
- [Backups](docs/backup.md)
- [Restore and recovery](docs/restore.md)
- [Secrets](docs/secrets.md)

## Development

Development uses Python and `uv`. Installed database hosts do not require either.

```sh
uv sync --locked
make check
make binary
```

## License

[MIT](LICENSE) © Evan Luo

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

evdb handles the hard parts for you:

- TLS routing for Postgres, Dragonfly, and Redis
- Separate credentials for every database
- Encrypted off-site backups
- Automatic backup verification
- Safer restores with rollback
- Health checks and update recovery
- Stable ports and connection details

You keep control of the server and storage without the hassle of building the database infrastructure yourself.

## Installation

evdb supports:

- Ubuntu 22.04 or newer
- ARM64 and x86_64

The host also requires:

- Docker with Compose
- Restic
- rclone
- systemd
- DNS provider credentials for SSL certificates
- Ports `5432` and `6379` to be free

Install the latest release:

```sh
curl -fsSL https://github.com/evannotfound/evdb/releases/latest/download/install.sh | sudo sh
sudo evdb host setup
```

See [Host setup and updates](docs/setup.md) for pinned installations, unattended setup, and release security details.

## Quick start

Create a Postgres database:

```sh
evdb database add notes-prod-01 postgres
```

Or create a key-value database (by default, we use [DragonflyDB](https://www.dragonflydb.io/), a performant Redis alternative. We also support Redis itself.):

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

### Serverless Redis

Besides a regular Redis `rediss://` connection URL, evdb also supports redis over https, just like Neon.

Powered by [serverless-redis-http](https://github.com/hiett/serverless-redis-http), you can use Redis over HTTPS on serverless platforms such as [Vercel](https://vercel.com/evanovation).

### TLS hostname routing

All Postgres databases can share port `5432`, and all Redis-compatible databases can share port `6379`.

evdb uses TLS and the requested hostname SNI to route each connection to the correct database container.

### Private credential storage

Passwords are stored in private files on the host.

In the future, we are planning to support automatic upload and sync to a cloud secret store such as 1Password.

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

evdb gives applications a similar interface: a standard connection URL. The difference is that the databases run on a Linux server you own. 

evdb is not intended to reproduce every feature of a managed cloud service. It does not provide:

- Supabase Auth or Storage
- Neon serverless scaling or database branching
- AWS high availability or managed service-level agreements

It is designed for small deployments or projects where you want control of the infrastructure, low cost, and no managed infrastructure. 


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

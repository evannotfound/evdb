# evdb

Host-local database management for self-hosted Postgres, Dragonfly, and Redis.

[![Check](https://github.com/evannotfound/evdb/actions/workflows/check.yml/badge.svg)](https://github.com/evannotfound/evdb/actions/workflows/check.yml)
[![Release](https://img.shields.io/github/v/release/evannotfound/evdb)](https://github.com/evannotfound/evdb/releases)
[![License](https://img.shields.io/github/license/evannotfound/evdb)](LICENSE)

evdb turns one Linux host into a small database platform. It gives every project a Postgres role, a
Redis-compatible KV role, or both, then manages their containers, routing, credentials, backups,
health, and recovery from one command.

## Features

- **One host, many projects** - address databases as `<project>/postgres` or `<project>/kv`.
- **Postgres, Dragonfly, and Redis** - use durable or cache-oriented roles without separate tooling.
- **Guided and scriptable** - run `evdb` for an interactive menu or use the same direct commands in
  automation.
- **Built-in routing** - expose native database protocols through TLS/SNI and keep optional HTTP
  backends private.
- **Verified backups** - create, upload, retain, and test Restic-backed database snapshots.
- **Recoverable restores** - verify a backup in isolation, take a safety backup, swap data atomically,
  and recover the prior service if health checks fail.
- **Safe changes** - preview mutations, preserve credentials and stable ports, and roll back failed
  database or tool updates.
- **Standalone releases** - install and update without Python, pip, or `uv` on the database host.

## Install

evdb supports Ubuntu 22.04 or newer on ARM64 and x86_64. The host also needs Docker with Compose,
Restic, rclone, systemd, DNS credentials, and free native ports `5432` and `6379`.

```sh
curl -fsSL https://github.com/evannotfound/evanovation-db/releases/latest/download/install.sh | sudo sh
sudo evdb host setup
```

The installer verifies the release checksum and installs an exact version. See [host setup and
updates](docs/setup.md) for pinned installation, non-interactive setup, and the release trust model.

## Use

Run the guided interface:

```sh
evdb
```

Or use direct commands:

```sh
evdb status
evdb database add notes-prod-01 postgres
evdb database add notes-prod-01 kv
evdb backup create notes-prod-01/postgres
evdb restore notes-prod-01/postgres latest
```

Mutating commands show what will change and require confirmation. `database info` is the only view
that prints complete credentials, and it requires a terminal.

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

Development uses Python and `uv`; released database hosts do not.

```sh
uv sync --locked
make check
make binary
```

Tests use disposable containers, temporary host paths, and local Restic repositories. They never
target production configuration.

## License

[MIT](LICENSE) © Evan Luo

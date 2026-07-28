# Command reference

Run `sudo evdb` without arguments in a terminal for the guided interface. Direct commands use the
same operations and are suitable for scripts and systemd.

## Status and information

```sh
sudo evdb status
sudo evdb status app-prod-01/postgres --json
sudo evdb database list
sudo evdb database info app-prod-01/postgres
```

`database info` prints complete credentials and therefore requires a terminal. Other machine-readable
status output is secret-free.

## Database operations

```sh
sudo evdb database add app-prod-01 postgres
sudo evdb database add app-prod-01 postgres --password-file /run/user/1000/evdb-password
sudo evdb database add app-prod-01 kv --engine redis
sudo evdb database configure app-prod-01/kv --memory 2gb --threads 4
sudo evdb database start app-prod-01/postgres
sudo evdb database stop app-prod-01/postgres
sudo evdb database restart app-prod-01/postgres
sudo evdb database logs app-prod-01/postgres --lines 200
```

Postgres creation generates a password by default. `--password-file` imports one non-empty,
single-line initial password from a regular file with no group or other permissions. One trailing
LF or CRLF is removed. The option is creation-only, is rejected for KV roles, and does not support
password rotation. Guided Postgres creation offers the same choice through a masked prompt.

## Backup and restore

```sh
sudo evdb backup create app-prod-01/postgres
sudo evdb backup list app-prod-01/postgres
sudo evdb backup test app-prod-01/postgres latest
sudo evdb backup retention app-prod-01/postgres --dry-run
sudo evdb backup prune postgres
sudo evdb backup repository-check postgres --rotate
sudo evdb restore app-prod-01/postgres latest
```

## Host operations

```sh
sudo evdb host check
sudo evdb host setup
sudo evdb host update 1.2.3
sudo evdb host uninstall
sudo evdb host uninstall --purge
```

Mutating commands preview their effects and require confirmation or `--yes`. Missing required values
fail immediately when no terminal is available.

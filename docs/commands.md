# Command reference

Run `evdb` without arguments in a terminal for the guided interface. Direct commands use the same
operations and are suitable for scripts and systemd.

## Status and information

```sh
evdb status
evdb status app-prod-01/postgres --json
evdb database list
evdb database info app-prod-01/postgres
```

`database info` prints complete credentials and therefore requires a terminal. Other machine-readable
status output is secret-free.

## Database operations

```sh
evdb database add app-prod-01 postgres
evdb database add app-prod-01 kv --engine redis
evdb database configure app-prod-01/kv --memory 2gb --threads 4
evdb database start app-prod-01/postgres
evdb database stop app-prod-01/postgres
evdb database restart app-prod-01/postgres
evdb database logs app-prod-01/postgres --lines 200
```

## Backup and restore

```sh
evdb backup create app-prod-01/postgres
evdb backup list app-prod-01/postgres
evdb backup test app-prod-01/postgres latest
evdb backup retention app-prod-01/postgres --dry-run
evdb backup prune postgres
evdb backup repository-check postgres --rotate
evdb restore app-prod-01/postgres latest
```

## Host operations

```sh
evdb host check
evdb host setup
evdb host update 1.2.3
```

Mutating commands preview their effects and require confirmation or `--yes`. Missing required values
fail immediately when no terminal is available.

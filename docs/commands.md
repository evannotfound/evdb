# Command reference

Run `sudo evdb` without arguments in a terminal for the guided interface. Direct commands use the
same operations and are suitable for scripts and systemd.

## Host commands

```sh
sudo evdb init
sudo evdb init --restic-password-file /root/evdb-restic-password
sudo evdb status
sudo evdb status --json
```

`init` is rerunnable. It validates the host, converges Traefik and networking, initializes or verifies
the one Restic repository, installs the backup service and timer, enables the timer, and finishes with
status. On first init, `--restic-password-file` reads one private, regular, non-symlinked file containing
one non-empty UTF-8 line with an optional trailing LF. Guided init uses a masked prompt where blank
generates the password. The password is not accepted inline or through environment variables and is
never printed. On a configured host the option is ignored and the existing `secrets.yml` value is
preserved. The release installer invokes `evdb init --yes` after updating an already configured host.

## Database commands

```sh
sudo evdb database list
sudo evdb database add app-prod-01 postgres
sudo evdb database add app-prod-01 postgres --password-file /run/user/1000/evdb-password
sudo evdb database add app-prod-01 kv --engine redis
sudo evdb database info app-prod-01/postgres
sudo evdb database configure app-prod-01/kv --memory 2gb --threads 4
sudo evdb database start app-prod-01/postgres
sudo evdb database stop app-prod-01/postgres
sudo evdb database restart app-prod-01/postgres
sudo evdb database logs app-prod-01/postgres --lines 200
```

Postgres creation generates a password by default. `--password-file` imports one non-empty,
single-line initial password from a regular file with no group or other permissions. One trailing
LF is removed. The option is creation-only, is rejected for KV roles, and does not support
password rotation. Guided Postgres creation offers the same choice through a masked prompt.

`database info` deliberately prints complete connection credentials in a terminal and has no JSON
mode. `status --json` is credential-free.

## Backup commands

```sh
sudo evdb backup create app-prod-01/postgres
sudo evdb backup create --all
sudo evdb backup list app-prod-01/postgres
```

`backup create --all` processes durable roles sequentially, continues after an individual failure,
and exits nonzero if any role failed. Cache-mode KV has backups disabled.

Explicit direct commands execute immediately. Guided database creation and settings changes show one
summary and require one final Create or Save confirmation. Output redacts exact managed credentials,
while repository URLs, rclone remote names, paths, image references, snapshot IDs, and unrelated tool
errors remain visible.

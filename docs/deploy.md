# Deployment assets

## Roles

- `base` creates the dedicated account and required `/opt`, `/etc`, and `/var/lib` paths.
- `app` stages a Git-SHA release and changes `current` only after checks pass.
- `backup` renders runtime JSON, state paths, one-time rclone seed, and systemd units.
- `postgres` renders Postgres and PgBouncer Compose and private files.
- `kv` renders Redis or Dragonfly Compose and HTTP sidecar private files.
- `traefik` renders the sole native-port publisher.

Membership in the Docker group is root-equivalent. The code release is root-owned; only
state and required secret files are writable by `evanovation-db`.

## Guards

Playbooks default to the `test` group. `ansible/hosts.yml` has no test group, so production
must be named with `-e target=production`. Production file changes are disabled unless
`-e apply=yes` is present. Compose projects are rendered but are never started by these
roles. The external HTTP proxy is not inspected or changed.

Safe local render:

```sh
uv run ansible-playbook -i ansible/test-hosts.yml ansible/databases.yml
```

Future production plan, with no managed-file or API writes:

```sh
uv run ansible-playbook -i ansible/hosts.yml ansible/databases.yml \
  -e target=production
```

A later approved change may add `-e apply=yes`; do not do that during `build-db-system`.
Restore execution additionally requires `restore_group` and `restore_name`.

## Compose and images

Separate templates exist for Postgres, Redis, Dragonfly, and Traefik. Instance files retain
project, data path, domain, settings, current engine, and instance-specific names while using
pinned target images. Before changing a digest, test backup compatibility, load an existing
backup into the candidate image, render every Compose file, and review the route labels.
Image changes and container restarts are production migration work.

## Scheduled work

Systemd installs per-instance backup timers plus daily status and restore selection, weekly
retention/check, and monthly prune. Every timer is persistent, randomized, time-limited, and
low priority. `evdb_enable_timers` defaults to false; units are installed stopped and
disabled unless an operator explicitly changes that variable during an approved migration.

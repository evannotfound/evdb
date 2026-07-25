# evanovation-db

`evdb` manages Postgres, Redis, and Dragonfly databases from an operator's controller over
SSH. Operators maintain one `config/<host>/host.yml`; the controller owns the generated
`host.lock.json`, deployment releases, Compose input, and host runtime.

This repository does not manage the external HTTP proxy. Its systemd timers remain disabled
until a separately approved production move enables them.

## Controller setup

The controller needs Python 3.10 or newer, OpenSSH, 1Password CLI, Docker CLI for image
manifest resolution, and Ansible Core 2.17 through 2.20. It needs batch SSH access to the
configured host and non-interactive sudo for the fixed host runtime command. See
[`docs/setup.md`](docs/setup.md) for controller and host prerequisites.

For repository development:

```sh
uv sync --locked
uv run evdb --config tests/fixtures/config/minimal validate
make help
```

To expose an installed `evdb` from a long-lived checkout while retaining its Ansible assets:

```sh
uv tool install --editable --with "PyYAML>=6.0" .
export EVANOVATION_DB_CONFIG=/path/to/evanovation-db/config/my-host
evdb validate
```

Without `EVANOVATION_DB_CONFIG`, pass `--config <directory-or-host.yml>`. There is no default
production target.

## Configuration

Most databases need only a typed name:

```yaml
databases:
  - name: example-prod-01
    type: postgres
  - name: cache-prod-01
    type: dragonfly
```

Names end in `-dev-N`, `-test-N`, or `-prod-N`; supported types are `postgres`, `redis`, and
`dragonfly`. Host images, backup repositories, SSH destination, base domain, data root, and
1Password ownership live once in the same `host.yml`.

The adjacent `host.lock.json` is generated, secret-free, reviewable, and safe to commit.
Never edit it manually. It holds Linux/amd64 image digests and stable HTTP loopback ports.
See [`docs/configuration.md`](docs/configuration.md) for the full minimal schema, built-in
defaults, supported overrides, and digest behavior.

## Plan and apply

`validate` checks the current source and generated lock. `plan` reads desired, active-release,
and live state without writing source, lock, secrets, releases, services, containers, or data.
`apply` shows the plan and asks for confirmation; `--yes` is reserved for deliberate
non-interactive use.

```sh
uv run evdb --config config/my-host validate
uv run evdb --config config/my-host plan
uv run evdb --config config/my-host apply
```

On first apply or a protocol upgrade, the controller separately asks to install or update the
stable bootstrap runtime before it replans through that runtime. Routine apply does not run
Ansible. It writes the resolved lock only after confirmation, stages an immutable release, creates
the shared Docker network when absent, applies Traefik before databases, and activates only after
all affected health and service-contract checks pass. Normal remote calls and systemd jobs use the
active release's `current/src` and `current/runtime`; rollback changes both together.

## Create and inspect

```sh
evdb create postgres example-prod-01
evdb create dragonfly cache-prod-01
evdb show example-prod-01
```

`create` atomically adds the minimal source entry, ensures convention-based 1Password fields,
then uses the normal plan and apply path. Pending source and managed items remain after a
cancelled or failed apply so rerunning converges.

**Credential output:** `evdb show <database>` always prints complete usable credentials to the
terminal on every successful run, including the password-bearing connection URL and an HTTP token
when enabled. No other command or generated source, lock, runtime JSON, state, release, history,
or log artifact does. `show` fails without partial detail output if a required field cannot be
read. Protected service secret files remain outside releases.

Plain names work when unique. If two types share a name, use the typed selector shown by the
error, for example `postgres/example-prod-01` or `dragonfly/example-prod-01`.

## Operate

```sh
evdb status
evdb status example-prod-01
evdb status --json
evdb start example-prod-01
evdb stop example-prod-01
evdb restart example-prod-01
evdb logs example-prod-01 --lines 200
```

Start, stop, and restart require confirmation unless `--yes` is supplied. They use the active
release's Compose definition and preserve data, source config, service secrets, backup history,
and release history. Status exits nonzero for unhealthy or stale state and reports other
databases even if one engine check fails.

## Back up and recover

```sh
evdb backup example-prod-01
evdb backups example-prod-01
evdb backup-check example-prod-01
evdb backup-check example-prod-01 SNAPSHOT_ID

evdb restore example-prod-01 --snapshot latest
evdb promote example-prod-01 RESTORE_ID
```

`backup-check` performs manifest, hash, isolated engine, and content checks. `restore` creates
and verifies a persistent candidate without touching live data. `promote` requires an outage,
atomically swaps same-filesystem directories, health-checks restored data, and retains prior or
failed data for recovery. There is no purge or in-place restore command. See
[`docs/backup.md`](docs/backup.md) and [`docs/restore.md`](docs/restore.md).

## Releases

```sh
evdb releases
evdb rollback
evdb rollback RELEASE_ID
```

Rollback defaults to the previous successful active release, shows service and image changes,
requires confirmation, and health-checks before activation. It changes deployment code,
configuration, Compose, and locked images only. It never restores or modifies database data;
use restore and promote when older data is required.

## Development checks

```sh
make check
```

The integration suite uses disposable containers and local Restic repositories only. Production
migration remains the separate [`docs/move-prod.md`](docs/move-prod.md) procedure.

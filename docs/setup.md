# Controller setup

## Requirements

The operator controller needs:

- A long-lived repository checkout, Python 3.10 or newer, and `uv`.
- OpenSSH with batch authentication to the `host.ssh` destination.
- Ansible Core 2.17 through 2.20 on `PATH` for first install and protocol upgrades.
- Docker CLI on `PATH`; plan and apply use `docker manifest inspect` to resolve changed image
  tags for Linux/amd64. They do not start or change local containers.
- 1Password CLI on `PATH`, authenticated through the desktop app or a service account with
  `write_items` for the complete create/apply workflow.

The managed host needs `/usr/bin/python3`, Docker with Compose, Restic, rclone, and passwordless
non-interactive sudo for the controller's fixed remote runtime command. SSH and sudo access are
operational prerequisites; `evdb` does not provision controller authentication.

## Development environment

```sh
uv sync --locked
uv run evdb --config tests/fixtures/config/minimal validate
uv run pytest tests/unit/test_controller.py tests/config/test_make.py
```

Repository commands use `uv run evdb`. Developer-only Compose, Ansible, systemd, and full test
checks remain available through `make check` and its component targets.

## Installed command

The controller's Ansible assets live in this checkout, so install the command in editable mode
from a stable path:

```sh
uv tool install --editable --with "PyYAML>=6.0" .
```

Ensure `ansible-playbook` is installed separately on `PATH`, then select one host explicitly:

```sh
export EVANOVATION_DB_CONFIG=/path/to/evanovation-db/config/my-host
evdb validate
evdb status
```

Alternatively, pass `--config /path/to/config/my-host` before the subcommand. The value may be
the host directory or its `host.yml`. `evdb` has no implicit production config.

## First apply

Create `config/<host>/host.yml` as described in [configuration.md](configuration.md). A first
apply resolves the desired lock in memory and attempts the fixed SSH runtime. If the runtime is
absent or incompatible, the controller displays the host and asks permission to install or
update only that internal runtime. No database project is started by this bootstrap step.

After bootstrap, the controller uses the bootstrap runtime to replan and asks separately before
applying the production plan. Confirmed apply writes `host.lock.json`, resolves deployment secrets
locally, and sends staged runtime, release, Compose, and protected inputs through the versioned
remote operation. It does not run Ansible again. `--yes` accepts both confirmations and must be
used deliberately.

The remote protocol uses `BatchMode=yes`, a bounded connect timeout, a fixed `sudo -n` command,
and validated JSON on stdin/stdout. Secret values never appear in SSH arguments. Protocol
version mismatch is one of the two explicit bootstrap conditions, along with a missing runtime.
SSH failures, malformed responses, invalid active config, and remote operation failures do not
trigger Ansible and fail closed.

## Boundaries

Normal remote operations and systemd jobs read code and generated JSON from the active
`/opt/evanovation-db/current` release; they do not read source YAML on the host. The stable
bootstrap code reads only the always-refreshed normalized
`/opt/evanovation-db/host-runtime/runtime`, never preserved legacy JSON under `/etc`. Bootstrap
atomically refreshes that runtime and removes stale instance JSON without replacing active unit
files. Protected files remain outside releases under `/etc/evanovation-db/secrets` and
`/var/lib/evanovation-db/rclone`.

Bootstrap and release activation preserve existing timer state. Timer enablement or disablement is
a separate production migration. The external HTTP proxy remains outside this repository, along
with certificates, public ports 80/443, and route changes.

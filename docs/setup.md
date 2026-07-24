# Local setup

## Requirements

- Python 3.10 or newer and `uv`
- Ansible Core 2.17 through 2.20 for deployment checks
- `systemd-analyze` for unit verification
- Docker, Compose, Restic, and rclone only for the integration tests that exercise them
- 1Password CLI only for a future secret-resolving deployment

Install the locked development environment and run the deployment-focused checks:

```sh
uv sync --locked
uv run pytest tests/config tests/integration/test_deploy.py
uv run ansible-playbook --syntax-check -i ansible/test-hosts.yml ansible/backup.yml
uv run ansible-playbook --syntax-check -i ansible/test-hosts.yml ansible/databases.yml
uv run ansible-playbook --syntax-check -i ansible/test-hosts.yml ansible/restore.yml
systemd-analyze verify systemd/*.service systemd/*.timer
```

`ansible/test-hosts.yml` uses the local connection and `/tmp/evanovation-db-test`. It does
not resolve production secrets, manage systemd, or start Compose projects.

## Configuration

Humans edit `config/<host>/host.yml`, `postgres.yml`, and `kv.yml`. Host config defines
paths, repositories, retention, pinned infrastructure images, and `op://` references.
Every instance records `id`, `env`, `engine`, `container`, `project`, `data`,
`domain`, durability, current and pinned target facts, backup policy, secrets, and engine
settings. Read-only Docker inspection records the current database, PgBouncer, HTTP, and
Traefik resource contract as `unlimited`; generated Compose therefore adds no CPU, memory,
cpuset, or PID limit. KV instances also define `http.enabled`, `port`, `domain`, pinned image,
token reference, and `max_connections`.

Validate source YAML and render runtime JSON with:

```sh
uv run evanovation-db validate --source config/montreal-01
uv run evanovation-db validate --source config/montreal-01 --output /tmp/evdb-config
```

Runtime Python reads JSON. Ansible writes `host.json` plus group-prefixed instance files
under `/etc/evanovation-db/instances`; the prefixes preserve products that exist in both
Postgres and KV groups.

## Commands

```text
evanovation-db backup <postgres|kv> <instance>
evanovation-db restore-check <postgres|kv> <instance> [--folder PATH | --snapshot ID]
evanovation-db restore-due [--run]
evanovation-db status [--json]
evanovation-db maintain <init|forget|prune|check> <postgres|kv>
```

## Releases

The app role stages root-owned code in `/opt/evanovation-db/releases/<git-sha>`. It checks
the Python files and rendered config before switching `/opt/evanovation-db/current`.
Configuration and secrets remain outside releases, under `/etc/evanovation-db`; mutable
state remains under `/var/lib/evanovation-db`.

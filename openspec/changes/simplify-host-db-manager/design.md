## Context

The repository currently implements a workstation controller that owns source configuration, resolves 1Password values, compares desired state with a remote active release, sends a versioned JSON request over SSH, stages code and generated assets under `/opt/evanovation-db/releases`, health-gates affected Compose projects, and switches a `current` symlink. Backup and restore code then depends on that active release to find configuration, images, and Compose files.

This is disproportionate to the actual topology. Each database and all of its persistent data live on one VPS. Backup schedules must continue when an operator workstation or future central monitor is offline. Database recovery means restoring verified data, not changing a host-wide software release. The current production host also already uses one Compose file per database directory; the proposed release system has not been deployed there.

The redesign makes the installed host command authoritative and keeps the validated engine backup, Restic, isolated restore, Docker health, routing, locking, and subprocess behavior. Implementation and tests remain local and disposable. Moving `montreal-01` is a separate change and MUST NOT occur while implementing this change.

## Goals / Non-Goals

**Goals:**

- Give operators one host-local `evdb` command with guided menus and matching scriptable commands.
- Organize configuration, files, commands, status, and backup identity around projects with one Postgres role and one KV role.
- Make `kv` a stable role with Dragonfly as its default implementation and Redis as an explicit implementation.
- Keep generated Compose visible and conventional while making each database independently operable.
- Keep safety transactions local to one settings change, restore, or tool update.
- Preserve checked backups, off-site retention, restore testing, atomic live-data replacement, and automatic failed-restore recovery.
- Make a dedicated database Traefik self-contained for native TCP routing and certificates without taking ownership of public HTTP.
- Support idempotent host setup and exact-version tool updates without Ansible or a controller runtime.
- Preserve a stable, secret-free status document that a future central monitor can ingest.
- Reorganize the package by direct work and concrete engines rather than controller/deployment framework layers.

**Non-Goals:**

- Database removal, purge, or retirement.
- PostgreSQL, Redis, or Dragonfly major-version migration.
- Redis-to-Dragonfly or Dragonfly-to-Redis data conversion.
- Password export, escrow, rotation, or total-host-loss recovery beyond local host-owned files.
- Public HTTP proxy routes, ports 80/443, or their certificates.
- A resident evdb API daemon, centralized mutation, distributed reconciliation, or global locking.
- Automatic database image updates.
- Production cutover, changes to `montreal-01`, or migration of its current `.env`, Compose, Traefik, timer, or data layout.

## Decisions

### The host-local command is the product

The installed `evdb` executable reads `/etc/evdb/host.yml`, owns generated files and operation state, and invokes Docker, Restic, rclone, and systemd locally. Operators may SSH to the host and run the same command; SSH is transport chosen by the operator, not an application protocol. Systemd jobs call the same public non-interactive commands.

This removes the local controller, versioned stdin/stdout protocol, controller-side Docker image resolution, bootstrap runtime, active runtime, and code bundled with database definitions. A future monitor can initially run `ssh HOST sudo evdb status --json` and later wrap the same status collector with HTTPS without changing database operations.

The alternative was retaining a thin controller. It was rejected because configuration, secrets, schedules, live Docker state, and recovery already belong to the host, while controller/runtime version negotiation created more failure modes than useful isolation.

### Project and database identity

The human and machine identity of a database is `<project>/<role>`, where role is `postgres` or `kv`. Project IDs retain the required `-dev-N`, `-test-N`, or `-prod-N` suffix. A project may contain zero or one of each role. A KV role selects `dragonfly` or `redis`; omission during creation resolves to Dragonfly and the command persists `engine: dragonfly` explicitly.

An illustrative host configuration is:

```yaml
host:
  id: example-01
  domain: storage.example.com
  data_root: /srv/databases
  backup:
    repos:
      postgres: rclone:remote:example-01/postgres
      kv: rclone:remote:example-01/kv
  routing:
    acme_email: operations@example.com
    dns_provider: cloudflare

projects:
  code-share-prod-01:
    postgres:
      image: postgres:16
      pgbouncer:
        enabled: true
        image: edoburu/pgbouncer:v1.25.1-p0
    kv:
      engine: dragonfly
      image: docker.dragonflydb.io/dragonflydb/dragonfly:v1.34.1
      mode: durable
      http:
        enabled: true
        image: hiett/serverless-redis-http@sha256:...
```

Commands, not manual YAML editing, are the normal mutation interface. The file remains concise and readable. Image sources are per database so projects can update independently. Creation starts from built-in defaults, resolves the selected defaults, and writes important persistent choices such as engine and source image explicitly so future default changes do not alter existing databases.

Tool-owned state under `/var/lib/evdb/state` records resolved image digests, stable HTTP loopback ports, schema versions, and operation results. Live observations remain observations and are never written into source configuration.

### Guided and scriptable CLI share one command model

Running `evdb` on a terminal opens a numbered menu showing host health, databases, backups, restore, and host checks. Database selection shows current values, whether each is default or customized, live health, and relevant actions. The settings editor exposes only settings valid for the selected role and engine, supports reset to default, collects multiple changes, previews restart and outage effects, and applies once.

Grouped commands are canonical:

```text
evdb status [DATABASE] [--json]
evdb database list
evdb database add PROJECT postgres
evdb database add PROJECT kv [--engine dragonfly|redis]
evdb database info PROJECT/ROLE
evdb database configure PROJECT/ROLE ...
evdb database start|stop|restart|logs PROJECT/ROLE
evdb backup create|list|test PROJECT/ROLE
evdb restore PROJECT/ROLE BACKUP
evdb host check
evdb host setup
evdb host update VERSION
```

On a TTY, missing human inputs prompt. Without a TTY, missing inputs fail rather than hang. Explicit arguments and `--yes` call the same domain functions as guided flows. Secret-bearing `database info` is terminal-only and has no JSON form. All other JSON and logs are secret-free.

The alternative was a full-screen TUI or a large flat command list. Numbered prompts were chosen for reliable SSH behavior and standard-library implementation; grouped commands preserve discoverability and automation.

### Direct per-database settings transaction

Database add and configure do not create a host-wide plan or release. They acquire the host and database locks, build candidate source/state/secret/Compose files in private sibling paths, resolve immutable image digests, validate the candidate with `docker compose config --quiet`, show one concrete preview, and require confirmation.

For a durable existing database, a service change that recreates the primary container first creates and uploads a checked safety backup. Engine changes and source images with an incompatible major are rejected. On confirmation, the command atomically installs source and generated files, runs the selected Compose project, and requires every primary and sidecar plus engine-native health.

The transaction retains the exact prior source, generated files, and secret-file metadata until health succeeds. If candidate health fails, it restores the previous files, starts the previous same-engine Compose definition, and verifies prior health. Recovery is automatic because prompting after failure is unsafe for unattended commands and broken SSH sessions. After success, only one `host.previous.yml` and an audit entry remain; temporary transaction files are removed.

Creation is idempotent. An existing matching role is shown rather than duplicated. Failed new creation stops candidate services and does not claim the database is installed; any newly created empty data and secret paths are removed only when their identity and ownership prove they were created by that transaction.

### Generated Compose YAML and canonical paths

Python dictionaries are the canonical rendering model. Canonical JSON encoding is used for hashes, while PyYAML writes readable `compose.yaml` files. Every file passes Docker Compose validation before installation. There are no Jinja templates, shared symlinks, secret-bearing `.env` files, or user-edited Compose files.

```text
/etc/evdb/host.yml
/etc/evdb/host.previous.yml
/etc/evdb/projects/<project>/postgres/compose.yaml
/etc/evdb/projects/<project>/postgres/pgbouncer.ini
/etc/evdb/projects/<project>/kv/compose.yaml
/etc/evdb/traefik/compose.yaml
/etc/evdb/secrets/

/var/lib/evdb/state/
/var/lib/evdb/backups/
/var/lib/evdb/restores/
/var/lib/evdb/locks/
/var/lib/evdb/rclone/
/var/lib/evdb/activity.jsonl

<data_root>/<project>/<role>/data/
```

Compose project names are `evdb-<project>-postgres`, `evdb-<project>-kv`, and `evdb-traefik`. The role remains stable if a future explicit KV engine migration is added. Concrete engine modules remain visible in backup records and implementation. Service and network aliases are unique per project/role so one external Docker network cannot route to another project's shared `postgres` or `redis` name.

### Dedicated native database Traefik

evdb owns one dedicated Traefik Compose project and external Docker network. It is the only evdb component publishing host ports 5432 and 6379. Database Compose files contribute Docker labels for unique TLS `HostSNI` routers and backends. Postgres routes to PgBouncer when enabled and otherwise to Postgres; KV routes to its selected Redis-compatible engine.

Traefik uses a pinned image, a Docker healthcheck, and ACME DNS-01 because the dedicated proxy does not own ports 80 or 443. Its DNS-provider credential and `acme.json` are private host files; `acme.json` is persistent and mode `0600`. Setup validates that ports are available and refuses to displace an existing proxy. The production migration must explicitly move current listeners and certificate state.

The serverless Redis HTTP sidecar remains part of the KV Compose project, defaults enabled, binds only `127.0.0.1:<stable-port>:80`, and uses a host-generated private environment file. evdb records the intended public domain and loopback endpoint but never reads or changes the external HTTP proxy.

### Backup identity and one-command restore

Backup folders, `backup.json`, Restic tags, locks, status, and history use host/project/role identity. Backup records additionally preserve the concrete engine, engine version, image, format, file hashes, facts, and upload result. Existing checked engine procedures, repository format v1, retention, local safety, repository locking, and periodic full restore tests remain.

`evdb backup test` is the human name for isolated end-to-end restore verification. `evdb restore PROJECT/ROLE BACKUP` performs the complete live recovery workflow:

1. Select and validate the exact local backup or Restic snapshot.
2. Restore it into a private same-filesystem candidate with no live mounts, routes, or public ports.
3. Run manifest, engine, and content verification.
4. Create and upload a new checked backup of current live data.
5. Show the selected backup, safety backup, expected outage, and paths, then require confirmation.
6. Stop the database, rename live data aside, rename verified data into the canonical path, and start the current Compose definition.
7. Require container, sidecar, contract, and engine health.
8. On failure, move failed data aside, restore the prior directory atomically, restart, and verify it.
9. On success, remove the replaced directory only after the safety backup upload and restored health are confirmed.

Restore requires matching host/project/role and concrete engine. Same-engine compatible patch images are allowed; newer-major backup data cannot be opened by an older major. Redis/Dragonfly conversion and all major migrations are rejected.

### One installed package, setup path, and update transaction

Ansible is removed. The package is published with an exact semantic version and canonical systemd assets. Initial installation uses `uv` to install an exact package version, followed by `sudo evdb host setup`. Setup is idempotent: it checks Python, Docker Compose, Restic, rclone, DNS routing inputs, and port availability; creates the service account, Docker membership, directories, ownership, initial configuration, dedicated network, Traefik files, and systemd units; and finishes with `host check`. It does not install or upgrade unrelated host prerequisites.

Managed tool versions live separately from database assets:

```text
/opt/evdb/current -> versions/<version>
/opt/evdb/previous -> versions/<previous-version>
/opt/evdb/versions/<version>/
/usr/local/bin/evdb -> /opt/evdb/current/bin/evdb
```

`evdb host update VERSION` requires an exact version, acquires the host lock, installs the published candidate through `uv`, and runs the candidate read-only against current configuration, state, Compose, backup records, and units. It previews any compatible config/state/unit migration, snapshots affected files, atomically switches the tool symlink, refreshes units without changing timer enablement, and runs `host check`. Failure restores the previous tool, files, and loaded units. One previous tool version is retained.

A tool update does not regenerate Compose or restart databases. New code must continue to operate existing installed definitions or reject the update before switching. A service-definition or engine migration is a separate explicit database operation.

### Package organization follows direct work

```text
src/evanovation_db/
  cli.py
  interactive.py
  config.py
  database.py
  compose.py
  backup.py
  restore.py
  status.py
  secrets.py
  images.py
  docker.py
  restic.py
  files.py
  lock.py
  run.py
  log.py
  errors.py
  engines/
    postgres.py
    redis.py
    dragonfly.py
    kv.py
```

`interactive.py` owns numbered input/output but no files or subprocesses. Workflow modules expose direct functions used by both CLI modes. Engine modules contain concrete health, backup, and restore behavior; `engines/kv.py` contains only genuinely shared Redis-protocol helpers. No service/provider/adapter/plugin hierarchy is introduced.

## Risks / Trade-offs

- [Host-owned configuration is lost with a failed VPS] -> A future monitor may mirror secret-free configuration, but credential escrow and rebuild policy remain explicitly out of scope; Restic/rclone bootstrap material requires separate operational handling.
- [A self-updating CLI can replace its own executable incorrectly] -> Install into a candidate version directory, validate with the candidate executable, atomically switch a symlink, retain one previous version, and restore units and files on failure.
- [Removing releases loses arbitrary deployment rollback] -> Preserve one previous settings transaction for automatic failed-health recovery; use checked backups for data recovery and explicit package versions for tool recovery.
- [Per-database images allow version inconsistency] -> Show image and major in every settings view and status result, require explicit updates, pin digests, and block unsupported major changes.
- [Safety backups make settings changes slower] -> Require them only for durable changes that recreate the primary service; the added latency is preferable to changing a persistent database without a current recovery point.
- [Dedicated Traefik conflicts with existing listeners] -> Setup checks port ownership and refuses to proceed; production listener cutover belongs to the separate migration change.
- [ACME DNS credentials expand host secret scope] -> Use a provider-scoped token in a private host file, keep `acme.json` mode `0600`, and never expose either through status or logs.
- [Generated YAML may vary by serializer version] -> Hash the canonical Python structure through sorted JSON rather than hashing YAML presentation.
- [One-command restore can hide its internal destructive step] -> Complete isolated verification and safety backup first, show the exact source/outage/paths, and require a final confirmation immediately before stopping live service.
- [A disconnected interactive session could interrupt work] -> Keep operations synchronous and interruption-safe, defer signals around atomic swaps, and never ask for recovery decisions after mutation starts.

## Migration Plan

1. Implement and test the new host-owned schema, package layout, CLI contract, Compose renderer, and direct operations against temporary paths without changing production.
2. Move retained engine, backup, Restic, restore, status, locking, and redaction behavior into the new direct modules with fixture and integration parity.
3. Replace controller, remote runtime, release, planning, and Ansible tests with disposable host setup, package update, settings recovery, and guided CLI tests.
4. Remove obsolete modules, playbooks, release paths, old command entry point, and release-focused documentation only after all retained behavior has moved.
5. Validate generated Compose, dedicated Traefik, two projects per shared native port, HTTP isolation, backups, restore, failed restore recovery, setup idempotence, and failed tool update entirely on disposable infrastructure.
6. Create a separate OpenSpec production migration that inventories current Montreal projects and credentials, resolves existing project-name collisions, stages the dedicated proxy cutover, preserves data and timers, and defines a host rollback procedure.

Implementation of this change has no production rollback because it MUST NOT modify production. Code changes can be reverted normally until the separate migration is approved.

## Open Questions

- Initial host-local credential generation, private service files, and deliberate terminal-only display are part of this change. Credential import for production migration, rotation, external export or escrow, and total-host-loss recovery require a separate design.
- The package registry namespace, release signing policy beyond registry hashes, and DNS provider credential provisioning mechanism must be selected before production migration, but do not change the internal setup and update contracts.

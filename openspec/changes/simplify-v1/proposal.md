## Why

evdb's pre-v1 implementation carries recovery, release migration, machine-state, drift, maintenance,
and defensive rollback systems that make basic database creation and backup setup difficult to operate
and review. The first release should instead make its core personal-host workflow obvious: initialize
one Ubuntu host, run configured database containers reliably, and create automatic remote backups.

## What Changes

- **BREAKING** Replace `/etc/evdb/host.yml`, the separate secret tree, and machine-owned deployment
  state with root-owned `/etc/evdb/config.yml` and `/etc/evdb/secrets.yml`; use a configured native
  rclone file in place rather than copying it into evdb storage.
- **BREAKING** Run the host-local command and backup unit as root without an evdb service account,
  but run Restic and its rclone child as the non-root owner of the configured native rclone file.
  Store runtime files under `/var/lib/evdb` and fix database data at
  `/var/lib/evdb/databases/<project>/<role>/data` without a configurable data root.
- **BREAKING** Remove live restore, isolated backup testing, remote retention, prune, repository checks,
  safety backups, operation transactions, automatic rollback, drift contracts, and their commands,
  status fields, documentation, and scheduled jobs.
- Keep Postgres, PgBouncer, Dragonfly, Redis, Redis-over-HTTP, native TLS routing, direct lifecycle
  commands, checked backup creation, Restic upload, and backup history.
- Make each concrete engine own its defaults, validation, generated services and files, health checks,
  connection information, and backup implementation; keep database orchestration direct and shared.
- Replace per-database and maintenance timers with one automatically enabled daily systemd timer that
  backs up every durable database sequentially.
- Use one Restic repository per host and initialize its missing rclone path during `evdb init` before
  enabling scheduled backups.
- **BREAKING** Replace `host setup`, `host check`, `host update`, and `host uninstall` with top-level
  `evdb init`, `evdb status`, and installer-driven updates.
- Replace versioned `/opt/evdb` releases with one checksummed standalone executable installed
  atomically at `/usr/local/bin/evdb`; embed the two canonical systemd units in that executable.
- Redesign the guided terminal flow around a compact database overview and progressive database,
  backup, and host details instead of one wide all-fields table.
- Show repository URLs, remote names, paths, images, and subprocess errors; redact only exact password
  and token values, which remain deliberately visible through the database information view.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `config`: Replace the split source/state layout with `config.yml`, `secrets.yml`, direct image
  references, and generated files derived without machine deployment state.
- `deploy`: Simplify database creation, configuration, lifecycle, engine ownership, generated files,
  and failure behavior while retaining all concrete engines and routing.
- `operator-cli`: Remove recovery and host-maintenance commands and introduce a progressively disclosed,
  readable guided workflow with narrower output and exact-value redaction.
- `backup`: Retain checked creation, upload, history, and bounded local cleanup while removing restore
  verification, safety backups, and remote retention behavior.
- `restic`: Use one automatically initialized host repository and remove maintenance operations.
- `restore`: Remove the live restore and restore-candidate capability from v1.
- `jobs`: Replace broad health, transaction, drift, logging, and timer contracts with direct runtime
  status and one scheduled all-database backup job.
- `host-setup`: Replace transactional setup, in-app updates, and uninstall with rerunnable initialization
  and installer-owned updates.
- `release-distribution`: Let the verified installer update configured hosts and refresh initialization
  instead of delegating updates to the installed application.

## Impact

- Core modules affected: `config.py`, `database.py`, `host.py`, `backup.py`, `status.py`, `cli.py`,
  `interactive.py`, `ui.py`, `compose.py`, `restic.py`, `restore.py`, `secrets.py`, `images.py`, engine
  modules, and low-level command error handling.
- Runtime assets affected: canonical config and secret paths, fixed `/var/lib/evdb` runtime and data
  paths, external rclone configuration, packaged systemd units, release installation, and the
  standalone command contract.
- Tests and documentation will be reduced to the retained host initialization, engine lifecycle,
  routing, backup, installer, and guided/direct CLI behavior.
- The existing pre-v1 on-disk schema is not migrated. Disposable hosts may be reset explicitly;
  production migration, including `montreal-01`, remains a separate change.

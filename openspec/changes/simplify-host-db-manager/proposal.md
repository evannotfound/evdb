## Why

The current unshipped manager wraps Docker Compose and database recovery in a controller-owned plan, apply, immutable release, remote-runtime, and rollback system that is difficult to understand and maintain. The product should instead match its real operational boundary: one host-local CLI owns each VPS, performs direct database operations, and treats verified backups as the database recovery history.

## What Changes

- **BREAKING** Move the authoritative configuration to `/etc/evdb/host.yml` on each managed host and organize it by project, with at most one `postgres` role and one `kv` role per project.
- **BREAKING** Replace engine-first database identities with `<project>/postgres` and `<project>/kv`; `kv` defaults to Dragonfly and may explicitly select Redis.
- **BREAKING** Replace the controller and its plan/apply/create/show/releases/rollback/promote vocabulary with one host-local `evdb` CLI organized around databases, backups, restore, status, and host administration.
- Add a guided numbered menu when `evdb` runs interactively while preserving the same grouped commands and explicit arguments for scripts, systemd, SSH, and future control-plane calls.
- Make each command complete its own operation: add or configure one database, create or test one backup, or perform one checked live restore without a separate apply or promotion command.
- Keep generated Docker Compose as readable per-database `compose.yaml` files under `/etc/evdb/projects/<project>/<role>` with distinct project identities and previous-setting recovery for failed health checks.
- Add a dedicated evdb Traefik project for native Postgres and KV ports, unique TLS SNI routing, and ACME DNS-01 certificates; keep serverless Redis HTTP sidecars loopback-only and public HTTP routing external.
- Allow image selection per database, pin resolved images, take safety backups for durable service changes, and reject engine-major upgrades and Redis/Dragonfly replacement as unsupported migrations.
- Preserve checked Postgres, Redis, and Dragonfly backups, Restic retention, isolated backup testing, and atomic data replacement, but collapse restore preparation and promotion into one `evdb restore` workflow.
- **BREAKING** Remove Ansible, generated inventories, the local controller, custom SSH JSON protocol, dual bootstrap/current runtimes, host-wide deployment bundles, release manifests, release history, and deployment rollback.
- Add idempotent `evdb host setup` and exact-version `evdb host update <version>` workflows around a published Python package, versioned tool installations, canonical systemd units, and automatic tool-update recovery.
- Retain a secret-free, versioned `evdb status --json` contract for SSH automation and a future read-only central monitor.
- Defer database removal, engine-major migrations, Redis/Dragonfly conversion, password disaster recovery, public HTTP proxy changes, centralized mutation, and production cutover.

## Capabilities

### New Capabilities

- `operator-cli`: Guided menus, grouped commands, confirmations, current-setting display, non-interactive equivalents, and terminal-safe output contracts.
- `host-setup`: Host-local installation, idempotent setup, prerequisite checks, service ownership, systemd assets, published package updates, and failed-update recovery.

### Modified Capabilities

- `config`: Replace the controller-owned database list and generated lock with host-owned project/role configuration, per-database images, stable ports, canonical paths, and command-owned updates.
- `deploy`: Replace plan/apply/releases/rollback and remote deployment with direct per-database Compose operations, failed-setting recovery, and dedicated native Traefik routing.
- `backup`: Use project/role identities and grouped commands while retaining checked engine backups, local history, isolated testing, and safety-backup behavior.
- `restore`: Replace exposed restore candidates and promotion with one confirmed restore workflow that tests data, creates a safety backup, swaps atomically, and recovers automatically.
- `http`: Express HTTP as a default KV capability with a loopback sidecar, project/role identity, host-owned secrets, and an unchanged external public-proxy boundary.
- `jobs`: Run status and scheduled work from the installed host CLI without active releases or a remote runtime, and expose stable secret-free monitoring output.
- `restic`: Change snapshot grouping from engine/database selectors to stable host/project/role identity while preserving repository format, locking, retention, and checks.

## Impact

- Reorganizes `src/evanovation_db` around `cli.py`, `interactive.py`, direct database/Compose/backup/restore/status modules, and concrete engine modules.
- Removes `controller.py`, `remote.py`, `planning.py`, `deployment.py`, `lifecycle.py`, `details.py`, the split restore promotion workflow, and the Ansible tree after their retained behavior is moved.
- Replaces both installed command entry points with one host-local `evdb` command and changes configuration, generated files, state, service units, and documentation paths.
- Preserves Docker Compose, Docker, Traefik, Restic, rclone, systemd, engine-native backup tools, and disposable integration testing.
- Requires a published versioned Python package and `uv` for initial installation and exact-version host updates.
- Requires a separate production migration change to inventory and move the existing Montreal Compose projects, `.env` files, shared Traefik listeners, data paths, timers, and credentials without modifying production during this change.

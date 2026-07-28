## 1. Remove Recovery and Maintenance Surface

- [ ] 1.1 Remove the top-level restore command, guided restore flow, result formatting, and command tests.
- [ ] 1.2 Delete live restore orchestration, restore candidates, engine restore functions, restore errors, restore staging paths, and restore unit and integration tests.
- [ ] 1.3 Remove backup test, due selection, materialization, verification records, safety-backup purposes, and restore-based engine checks while retaining checks performed during backup creation.
- [ ] 1.4 Remove retention, prune, repository-check commands and Restic implementations, approval state, maintenance logging, and their tests.
- [ ] 1.5 Delete backup-test, status, retention, prune, and repository-check service and timer files and update package and release unit expectations.
- [ ] 1.6 Remove recovery and maintenance claims, commands, files, and examples from README, Makefile, and topic documentation.

## 2. Replace Configuration and State

- [ ] 2.1 Add simple configuration models for host, routing, one backup repository, projects, roles, and concrete engine settings without transaction or deployment-state models.
- [ ] 2.2 Implement strict `/etc/evdb/config.yml` loading, validation, dumping, and atomic writing with current project/role selectors and explicit non-latest images.
- [ ] 2.3 Implement strict mode-`0600` `/etc/evdb/secrets.yml` loading and atomic writing for Restic, DNS, Postgres, KV, and HTTP credentials under matching project/role keys.
- [ ] 2.4 Move native mutable rclone configuration to `/etc/evdb/rclone.conf`, seed it only when absent, and preserve subsequent bytes across initialization and updates.
- [ ] 2.5 Replace canonical path derivation with config, secrets, rclone, project-generated, Traefik, local backup, lock, and data paths defined by the new layout.
- [ ] 2.6 Remove machine state, resolved image state, installed flags, HTTP port state, Compose hashes, operation records, activity logs, transaction paths, and compatibility loaders.
- [ ] 2.7 Update all configuration fixtures and tests to the new two-file schema and add rejection tests for `host.yml` and pre-v1 machine state without adding a migration path.

## 3. Move Concrete Engine Ownership

- [ ] 3.1 Define one direct engine-module lookup with no abstract base class, protocol, adapter, inheritance, or callback container.
- [ ] 3.2 Move Postgres defaults, validation, Compose services, password and PgBouncer file rendering, health, information, and checked backup behavior into `engines/postgres.py`.
- [ ] 3.3 Move Redis defaults, validation, Compose services, native config rendering, health, information, and checked RDB backup behavior into `engines/redis.py`.
- [ ] 3.4 Move Dragonfly defaults, validation, Compose services, flags rendering, health, information, and checked DFS backup behavior into `engines/dragonfly.py`.
- [ ] 3.5 Reduce `engines/kv.py` to Redis-protocol commands genuinely shared by Redis and Dragonfly and keep HTTP sidecar generation with the selected concrete engine.
- [ ] 3.6 Move generic Docker inspect, exec, Compose write, validation, and lifecycle commands into one direct Docker module and remove service-contract labels and image resolution.
- [ ] 3.7 Reduce `database.py` to selection, source updates, generated file installation, lifecycle, bounded logs, information dispatch, and one native health wait loop.
- [ ] 3.8 Make add persist explicit settings and generated or supplied credentials before rendering and starting; make start and restart rerender from current source before Compose.
- [ ] 3.9 Make failed creation or settings health leave readable source and generated files in place, surface the concrete failure, and support correction followed by `database start` without automatic rollback.
- [ ] 3.10 Add focused unit and pinned-container coverage for all three engines, PgBouncer private access, HTTP, lifecycle retry, and source-to-generated-file behavior.

## 4. Simplify Backup and Restic

- [ ] 4.1 Replace role repository mappings with one `host.backup.repository` and one repository lock while preserving host/project/role/engine/backup/purpose snapshot tags.
- [ ] 4.2 Merge retained Restic initialization, upload, snapshot parsing, and history operations into `backup.py` and show repository URLs in failures.
- [ ] 4.3 Require Restic 0.17 or newer and implement `cat config` handling where only its documented missing-repository exit runs format-v1 initialization.
- [ ] 4.4 Prove that initialization through an rclone local remote creates an initially absent repository path without `rclone mkdir`, fallback commands, or production credentials.
- [ ] 4.5 Simplify backup manifests and history by removing verification and machine-state fields while retaining native checks, sizes, SHA-256 hashes, upload state, and snapshot identity.
- [ ] 4.6 Preserve private partial folders, role and repository locking, failed-upload local backups, and cleanup of all but the two newest uploaded local backups.
- [ ] 4.7 Implement `backup create --all` to process durable roles sequentially, continue after each failure, succeed with no durable roles, and return nonzero when any role fails.
- [ ] 4.8 Update backup and Restic unit and integration tests for one repository, merged history, all-role execution, cache exclusion, missing repository initialization, and visible diagnostic identifiers.

## 5. Make Initialization and Scheduling Automatic

- [ ] 5.1 Replace `host setup` and `host check` with top-level rerunnable `evdb init` and existing top-level `status`.
- [ ] 5.2 Reduce host initialization to prerequisite checks, account and directory creation, source setup, network and Traefik convergence, Restic initialization, unit installation, timer activation, and final status.
- [ ] 5.3 Remove host transaction snapshots, rollback helpers, candidate compatibility checks, in-app release download and update, and host uninstall and purge behavior.
- [ ] 5.4 Add exactly one low-priority `evdb-backup.service` running `evdb backup create --all` and one daily persistent randomized `evdb-backup.timer`.
- [ ] 5.5 Make initialization install, daemon-reload, and `enable --now` the two backup units only after the repository is ready; reruns SHALL converge without database restart.
- [ ] 5.6 Update `install.sh` to accept configured hosts, retain checksum/archive/version validation and atomic current/previous selection, then run `evdb init --yes` after configured updates.
- [ ] 5.7 Remove duplicate release-installation code from Python and update installer, release archive, systemd, setup, and configured-update tests for the smaller contract.

## 6. Rebuild Status, Errors, and Guided UX

- [ ] 6.1 Replace status machine-state, drift, transaction, operation-history, restore-test, and maintenance assessments with direct source, infrastructure, timer, Docker, engine, disk, repository, and latest-backup observations.
- [ ] 6.2 Update credential-free status JSON and its versioned tests to the smaller host and database contract.
- [ ] 6.3 Merge guided navigation and terminal presentation into one UI module that calls domain operations directly without an `Actions` callback dataclass.
- [ ] 6.4 Render the root as one compact numbered database table with Database, Engine, Status, and Backup plus Add, Host, and Exit choices.
- [ ] 6.5 Add key/value database Details, Connection, Settings, Start/Stop, Restart, Backups, and Logs screens with full images and errors disclosed only in the relevant view.
- [ ] 6.6 Render host details and backup history as compact text, add blank-line rhythm between every screen, result, error, and prompt, and keep numbered SSH-safe input.
- [ ] 6.7 Remove the generic confirmation and raw result-dictionary layers; retain one final guided Create or Save confirmation and execute explicit direct commands immediately.
- [ ] 6.8 Replace heuristic sanitizer and custom structured JSONL logging with exact managed-credential redaction while preserving repository URLs, paths, images, remotes, snapshots, and original unrelated stderr.
- [ ] 6.9 Catch only expected application failures in the CLI, preserve tracebacks for unexpected faults, and make the VPS helper return remote command exit codes without printing `CalledProcessError` command representations.
- [ ] 6.10 Add terminal snapshot assertions at 60, 100, and 160 columns proving readable identities, intentional whitespace, progressive details, and no root image digest or unintended ellipsis-only cells.

## 7. Delete Superseded Structure and Documentation

- [ ] 7.1 Delete `restore.py`, `restic.py`, `compose.py`, `images.py`, `secrets.py`, `interactive.py`, and `log.py` after retained behavior has moved and remove every import and test tied to them.
- [ ] 7.2 Keep low-level files only where they have one coherent responsibility and add architecture tests that reject transaction models, machine state, removed commands, removed units, and removed modules.
- [ ] 7.3 Update README, setup, configuration, deployment, routing, backup, secrets, command, development, and release documentation for `config.yml`, `secrets.yml`, one repository, one timer, installer updates, and v1 limitations.
- [ ] 7.4 State explicitly that remote snapshots grow without automatic retention and that recovery uses manual engine tools until a later restore capability exists.
- [ ] 7.5 Update release notes and public feature language to remove restore verification, rollback, maintenance, and cross-distribution implications.

## 8. Validate the V1 Boundary

- [ ] 8.1 Run Ruff check and format checks across production, tests, and tools.
- [ ] 8.2 Run all unit and configuration tests, local Restic/rclone repository integration, and integration collection without production resources.
- [ ] 8.3 Run pinned Postgres/PgBouncer, Redis, Dragonfly, HTTP, and Traefik integration coverage on disposable containers.
- [ ] 8.4 Build the standalone executable and smoke-test version, init and parser contracts, status JSON, database lifecycle, and backup create/list commands.
- [ ] 8.5 Run installer shell syntax, archive-layout, first-install, configured-update, init-refresh, and failed-refresh tests.
- [ ] 8.6 Validate the `simplify-v1` OpenSpec change and run an independent code and terminal UX review with no unresolved findings.
- [ ] 8.7 Before remote reset, obtain explicit approval for disposable Toronto files and confirm the development helper still rejects `montreal-01` before subprocess execution.
- [ ] 8.8 On approved Toronto disposable state only, initialize the absent shared repository, create retained engine roles, prove native health and HTTP, run manual and all-role backups, verify timer activation and remote snapshots, inspect readable errors, and deactivate the development command.

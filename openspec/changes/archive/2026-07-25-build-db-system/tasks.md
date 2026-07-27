## 1. Project Setup

- [x] 1.1 Add `pyproject.toml` for Python 3.10+, the `evdb` command, Ruff, and pytest
- [x] 1.2 Add the agreed `src`, `config`, `compose`, `ansible`, `systemd`, `tests`, and `docs` folders as real files need them
- [x] 1.3 Add a Makefile with `check`, `plan`, `deploy-backup`, `deploy-db`, `backup`, `restore-check`, and `status` targets
- [x] 1.4 Add README and AGENTS guidance covering scope, simple naming, no production writes, and development commands
- [x] 1.5 Add CI for lint, unit tests, integration tests, config checks, Compose checks, Ansible syntax, systemd checks, and secret scanning

## 2. Config

- [x] 2.1 Add `Host` and `Instance` config types with JSON loading in `config.py`
- [x] 2.2 Add checks for required fields, engine values, backup rules, HTTP settings, current and target settings, and safe data paths
- [x] 2.3 Check ids and projects within Postgres or KV, check host-wide names and routes, and allow Traefik's shared host ports
- [x] 2.4 Add checks for `op://` secret references and pinned target image versions and digests
- [x] 2.5 Add source YAML loading and JSON rendering for development and the Ansible controller
- [x] 2.6 Record the 14 Postgres, 7 Dragonfly, and 4 Redis instances from read-only production facts
- [x] 2.7 Record current Docker image ids and safe target database and HTTP images without changing an engine or data path
- [x] 2.8 Add exact 1Password references without reading or storing secret values
- [x] 2.9 Add config tests for all valid and invalid cases, including the four Redis instances whose current Compose template says Dragonfly and all 11 initial HTTP routes

## 3. Shared Python Code

- [x] 3.1 Add direct error types for config, commands, backups, restores, and Restic failures
- [x] 3.2 Implement `run.py` with argument arrays, timeouts, streamed output, checked return codes, and no shell
- [x] 3.3 Add secret redaction for arguments, stdout, stderr, exceptions, and structured logs
- [x] 3.4 Implement `docker.py` for inspect, exec, copy, run, stop, and remove operations
- [x] 3.5 Implement `lock.py` with instance and repository `fcntl` locks and clear lock timeouts
- [x] 3.6 Implement `files.py` with private folders, free-space checks, SHA-256, atomic JSON writes, and safe renames
- [x] 3.7 Implement `manifest.py` for reading, writing, and checking `backup.json`
- [x] 3.8 Add unit tests for command handling, timeouts, redaction, Docker commands, locks, files, and manifests

## 4. Backup Flow

- [x] 4.1 Implement `backup/main.py` to load one instance, take its lock, and select the matching engine
- [x] 4.2 Create `.partial` backup folders and complete them only after every required file and check succeeds
- [x] 4.3 Write engine facts, file sizes, hashes, checks, times, and upload state to `backup.json`
- [x] 4.4 Keep at least two uploaded local backups and never remove an unuploaded backup automatically
- [x] 4.5 Stop a new backup before configured free-space limits are crossed
- [x] 4.6 Add tests proving one failed instance does not block another instance
- [x] 4.7 Add tests for interrupted runs, stale partial folders, changed files, failed uploads, and low disk space

## 5. Postgres

- [x] 5.1 Discover connectable non-template databases through `pg_database`
- [x] 5.2 Stream one `pg_dump -Fc` archive per database from the matching Postgres container
- [x] 5.3 Create private `globals.sql` with `pg_dumpall --globals-only` and preserved role password hashes
- [x] 5.4 Reject empty files and require `pg_restore --list` to read every database archive
- [x] 5.5 Record Postgres version, database names, object counts, sizes, and hashes in the manifest
- [x] 5.6 Seed a local Postgres cluster with several databases, schemas, tables, data, and roles for integration tests
- [x] 5.7 Add Postgres backup tests for multiple databases, globals, damaged archives, command failures, and unusual database names

## 6. Dragonfly

- [x] 6.1 Implement `SAVE RDB <new-name>` with a unique name that did not exist before the run
- [x] 6.2 Copy only the new RDB, check its size and hash, and remove only the source file created by the run
- [x] 6.3 Record Dragonfly version, key counts, sampled key facts, and TTL facts without storing raw values
- [x] 6.4 Keep Dragonfly checks independent from `redis-check-rdb`
- [x] 6.5 Seed Dragonfly 1.34.1 with strings, sets, hashes, and TTLs for integration tests
- [x] 6.6 Add tests proving old `dump.rdb` and fresh DFS files cannot be mistaken for the new RDB
- [x] 6.7 Add tests for failed save, missing file, empty file, copy failure, cleanup, and private Dragonfly RDB records

## 7. Redis

- [x] 7.1 Record `LASTSAVE`, start `BGSAVE`, and poll persistence state until a newer successful save is complete
- [x] 7.2 Pass Redis authentication through a protected environment value instead of command arguments
- [x] 7.3 Copy the configured RDB and check it with tools from the matching Redis image
- [x] 7.4 Record Redis version, database counts, key counts, sampled key facts, and TTL facts without storing raw values
- [x] 7.5 Seed Redis with strings, sets, hashes, several databases, and TTLs for integration tests
- [x] 7.6 Add tests for fast saves, failed saves, unchanged timestamps, damaged RDB files, auth redaction, and timeouts

## 8. Serverless HTTP

- [x] 8.1 Add per-instance `enabled`, `port`, `domain`, `image`, `token`, and `max_connections` HTTP config
- [x] 8.2 Enable the sidecar for all 11 initial KV instances with unique loopback ports and standard intended domains
- [x] 8.3 Add the pinned `serverless-redis-http` sidecar to both Redis and Dragonfly Compose output
- [x] 8.4 Bind each sidecar only to its unique `127.0.0.1:133xx` port and connect it to the instance-specific backend name
- [x] 8.5 Render `SRH_TOKEN` and `SRH_CONNECTION_STRING` through a private environment file built from 1Password references
- [x] 8.6 Treat the domain and loopback port as a read-only contract for an external proxy
- [x] 8.7 Assert that Ansible has no external proxy credentials, API calls, image checks, or route writes
- [x] 8.8 Record the missing `oai-co-prod-02` route and two legacy domains as informational `move-prod` follow-ups
- [x] 8.9 Test invalid and valid tokens, Redis and Dragonfly support, loopback-only binding, and per-instance enablement
- [x] 8.10 Test two enabled sidecars and prove each reaches only its own seeded backend

## 9. Restic

- [x] 9.1 Implement Restic JSON Lines parsing and require a zero exit code plus final `summary.snapshot_id`
- [x] 9.2 Upload only complete backup folders and save the confirmed snapshot id and time in local state
- [x] 9.3 Add stable host, engine, and instance tags and group retention by those stable tags
- [x] 9.4 Serialize work per repository and handle lock waits without `--no-lock`
- [x] 9.5 Implement 7 daily, 4 weekly, and 12 monthly retention with separate weekly forget and monthly prune commands
- [x] 9.6 Implement format-v1 checks, weekly structure checks, and rotating `n/t` data checks
- [x] 9.7 Add tests for nonzero runs with snapshot ids, bad JSON, missing summaries, lock conflicts, retention groups, and check rotation
- [x] 9.8 Run all Restic integration tests against temporary local repositories only

## 10. Restore Tests

- [x] 10.1 Implement `restore/main.py` to check manifest files and reject live paths and containers
- [x] 10.2 Add temporary Docker resource naming, limits, cleanup, and interrupt handling shared by restore modules
- [x] 10.3 Restore Postgres globals and databases into a fresh matching container with errors treated as fatal
- [x] 10.4 Check Postgres connections, database names, roles, schemas, tables, and expected object counts after restore
- [x] 10.5 Restore Redis RDB files into matching containers and compare counts, types, value hashes, and TTL behavior
- [x] 10.6 Restore Dragonfly RDB files into matching containers and compare counts, types, value hashes, and TTL behavior
- [x] 10.7 Save restore results and implement selection of the instance most overdue for a restore test
- [x] 10.8 Add tests for damaged files, wrong images, failed startup, failed data checks, timeout cleanup, and live-path refusal

## 11. CLI, Status, and Jobs

- [x] 11.1 Implement `cli.py` with grouped `backup` and `restore-check` commands plus `status` and `validate`
- [x] 11.2 Add concise human output, useful exit codes, and optional JSON output for automation
- [x] 11.3 Implement `status.py` for local backup, upload, snapshot, failure, and restore-test state
- [x] 11.4 Mark confirmed snapshots older than about 26 hours and restore tests older than 30 days as stale
- [x] 11.5 Add structured logs with host, instance, command, step, result, duration, and redacted error fields
- [x] 11.6 Add per-instance backup, daily status, due restore, weekly maintenance, and monthly prune systemd units
- [x] 11.7 Set persistent timers, randomized delays, execution limits, and low CPU and I/O priority
- [x] 11.8 Keep every timer disabled unless an explicit enable flag is supplied
- [x] 11.9 Add CLI, status, log, stale-state, Make target, and systemd unit tests

## 12. Compose and Ansible

- [x] 12.1 Add separate Postgres, Redis, Dragonfly, and Traefik Compose templates with pinned images
- [x] 12.2 Preserve current ids, projects, data paths, ports, resources, networks, and Traefik TCP TLS/SNI labels
- [x] 12.3 Make Traefik the only publisher of host ports 5432 and 6379 and use instance-specific backend names
- [x] 12.4 Test at least two Postgres and two KV domains through the shared ports and prove SNI isolation
- [x] 12.5 Use `POSTGRES_PASSWORD_FILE`, a private Redis config, and a private Dragonfly flag file for supported secrets
- [x] 12.6 Add the base role for the service account, directories, permissions, and documented Docker trust
- [x] 12.7 Add the app role for root-owned versioned releases and checked current-link changes
- [x] 12.8 Add the backup role for rendered JSON, secrets, mutable rclone config seeding, state folders, and disabled systemd units
- [x] 12.9 Add Postgres, KV, and Traefik roles that render files without applying production changes by default
- [x] 12.10 Add backup, databases, and restore playbooks with explicit target and production apply guards
- [x] 12.11 Keep 1Password resolution on the controller with `no_log` and make check mode work without secret values
- [x] 12.12 Add Compose render checks, Ansible syntax checks, systemd verification, and disposable-host deployment tests

## 13. Docs and Final Checks

- [x] 13.1 Document local setup, config fields, commands, test requirements, and release layout
- [x] 13.2 Document backup files, status, Restic work, local history, and failure handling
- [x] 13.3 Document Postgres, Redis, and Dragonfly restore tests and their limits
- [x] 13.4 Document native Traefik routes, serverless HTTP, tokens, and the external proxy ownership boundary
- [x] 13.5 Document secrets, rclone token ownership, reconnect, and safe 1Password handling
- [x] 13.6 Document Ansible, Compose, systemd, image updates, and the production guard
- [x] 13.7 Write `move-prod.md` with the missing HTTP route and legacy domain moves, without running any production step
- [x] 13.8 Run secret scanning and confirm no production value, dump, rclone config, password, or generated secret is tracked
- [x] 13.9 Run the full `make check` suite on the supported local platform and fix every failure caused by this change
- [x] 13.10 Confirm Git and production are unchanged except for the new repository files and read-only fact gathering

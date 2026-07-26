## 1. Contract and Test Foundations

- [x] 1.1 Add project-first host config fixtures with Postgres-only, KV-only, and combined project roles
- [x] 1.2 Add invalid fixtures for duplicate roles, missing environment suffixes, engine/setting mismatches, unsafe paths, image majors, and secret-bearing source
- [x] 1.3 Add canonical path fixtures rooted entirely in temporary `/etc`, `/var/lib`, `/opt`, and data directories
- [ ] 1.4 Add CLI parser contract tests for every grouped interactive and non-interactive command
- [ ] 1.5 Add disposable host helpers that never target `montreal-01`, production Restic remotes, real DNS, cron, or systemd
- [ ] 1.6 Update package metadata to expose one `evdb` command and declare required runtime dependencies and package assets

## 2. Project-First Configuration

- [x] 2.1 Replace database-list source models with host, project, Postgres-role, and KV-role models
- [x] 2.2 Implement `<project>/postgres` and `<project>/kv` identity parsing and ambiguity handling
- [x] 2.3 Enforce one Postgres and one KV role per project and the required dev/test/prod suffix
- [x] 2.4 Default new KV roles to Dragonfly and persist the concrete engine explicitly
- [x] 2.5 Move primary and sidecar image sources into each database role with immutable digest validation
- [x] 2.6 Preserve concise defaults for PgBouncer, durability, HTTP, pool sizing, Dragonfly memory and threads, and connection limits
- [x] 2.7 Implement typed role-specific override validation without accepting raw deployment fields
- [x] 2.8 Implement canonical `/etc/evdb`, `/var/lib/evdb`, `/opt/evdb`, and project-first data path derivation
- [x] 2.9 Implement private machine state for resolved digests, allocated HTTP ports, schema versions, and operation results
- [x] 2.10 Implement atomic host YAML updates with one `host.previous.yml` and secret-free activity records
- [x] 2.11 Reject controller-owned databases lists, separate engine files, checked-in locks, migration fields, and release runtime input
- [x] 2.12 Add round-trip and collision tests for project config, machine state, defaults, explicit images, and stable ports

## 3. Direct Module Organization

- [ ] 3.1 Establish direct `cli`, `interactive`, `database`, `compose`, `backup`, `restore`, `status`, `secrets`, and `images` module APIs
- [x] 3.2 Create concrete `engines/postgres.py`, `engines/redis.py`, `engines/dragonfly.py`, and shared Redis-protocol `engines/kv.py`
- [ ] 3.3 Move Postgres health, backup, and restore behavior into the Postgres engine module with parity tests
- [ ] 3.4 Move Redis health, backup, and restore behavior into the Redis engine module with parity tests
- [ ] 3.5 Move Dragonfly health, backup, and restore behavior into the Dragonfly engine module with parity tests
- [x] 3.6 Keep subprocess execution, file writes, locking, Docker helpers, redaction, and structured logging as small shared modules
- [ ] 3.7 Remove engine dispatch through generic controller, lifecycle, deployment, or promotion abstractions

## 4. Generated Compose and Native Routing

- [x] 4.1 Implement canonical Python-dictionary rendering and canonical JSON service hashes
- [x] 4.2 Write readable per-role `compose.yaml` files with deterministic content and no Jinja templates or symlinks
- [x] 4.3 Generate distinct `evdb-<project>-postgres` and `evdb-<project>-kv` Compose projects
- [x] 4.4 Remove secret-bearing `.env` values from Compose and mount only private host secret files
- [x] 4.5 Generate unique service names, network aliases, SNI routers, and backends for every project role
- [x] 4.6 Generate Postgres with optional PgBouncer and route native traffic to the correct backend
- [x] 4.7 Generate Dragonfly or Redis KV services with durable/cache settings and optional HTTP sidecar
- [x] 4.8 Generate dedicated `evdb-traefik` Compose with pinned image, ports 5432/6379, Docker provider, and concrete healthcheck
- [x] 4.9 Add ACME DNS-01 resolver configuration, private provider credential input, and mode-0600 persistent `acme.json`
- [x] 4.10 Validate every candidate YAML with `docker compose config --quiet` before installation
- [x] 4.11 Add tests proving Postgres and KV from one project remain separate Docker Compose projects
- [ ] 4.12 Add disposable routing tests for multiple Postgres and KV roles over shared native ports
- [ ] 4.13 Add setup validation that refuses occupied native ports or an unsafe existing evdb network

## 5. Database Operations and Settings Recovery

- [x] 5.1 Implement idempotent database add for Postgres and default or explicit KV engines
- [x] 5.2 Stage candidate config, state, secret, and Compose files in private transaction paths
- [x] 5.3 Implement per-database image resolution and reject mutable latest or invalid image references
- [x] 5.4 Implement complete change previews with old/new settings, affected services, outage, and safety-backup effect
- [x] 5.5 Implement direct start, stop, restart, bounded logs, and engine-native health operations
- [x] 5.6 Require host and project-role locks around conflicting add, configure, lifecycle, backup, and restore work
- [ ] 5.7 Create and upload a safety backup before durable primary-container recreation
- [x] 5.8 Atomically install a confirmed settings transaction and perform at most one Compose restart
- [x] 5.9 Restore prior source, state, generated files, and same-engine service definitions automatically after candidate failure
- [x] 5.10 Verify prior health after settings recovery and preserve diagnostic transaction paths when recovery fails
- [x] 5.11 Block PostgreSQL, Redis, and Dragonfly major changes before service stop
- [x] 5.12 Block Redis/Dragonfly implementation changes as unsupported migrations
- [x] 5.13 Record bounded secret-free settings activity without retaining deployment releases
- [x] 5.14 Add interrupted creation, unhealthy candidate, recovered prior service, and failed recovery tests
- [x] 5.15 Detect manually omitted installed roles, preserve every service and persistent asset, and block mutation pending a future retirement workflow

## 6. Guided and Scriptable CLI

- [ ] 6.1 Implement TTY detection and a numbered root menu with host summary and domain actions
- [ ] 6.2 Implement project-role database listing and guided database selection
- [ ] 6.3 Implement context-aware Postgres, Redis, and Dragonfly settings menus with current/default/custom values
- [ ] 6.4 Implement keep, reset-to-default, discard, and multi-setting save behavior
- [ ] 6.5 Implement guided add, lifecycle, logs, backup, backup test, restore, host check, setup, and update flows
- [ ] 6.6 Implement exact grouped command arguments for all guided operations
- [ ] 6.7 Make non-TTY missing arguments fail immediately with concise examples
- [ ] 6.8 Implement consistent host/target/effect confirmations and `--yes` semantics
- [ ] 6.9 Implement `database info` with settings, live facts, paths, backups, native URLs, and HTTP details
- [ ] 6.10 Restrict credential-bearing info to terminal output and reject JSON or partial secret output
- [ ] 6.11 Add injected input/output tests for invalid choices, EOF, cancellation, defaults, resets, and repeated navigation
- [ ] 6.12 Prove guided and explicit settings commands produce identical domain requests and one restart

## 7. Host-Owned Secrets and HTTP API

- [ ] 7.1 Implement private host credential file generation and reading interfaces needed by database services
- [ ] 7.2 Keep password escrow, export, rotation, and total-host-loss recovery outside public contracts
- [ ] 7.3 Generate Redis config, Dragonfly flags, PgBouncer users, Postgres password files, and HTTP environment files with mode 0600
- [ ] 7.4 Preserve stable HTTP loopback ports in machine state and bind sidecars only to 127.0.0.1
- [ ] 7.5 Persist the intended public HTTP domain without reading or changing the external proxy
- [ ] 7.6 Pin serverless Redis HTTP images per KV role and preserve enabled-by-default behavior
- [ ] 7.7 Test token authentication, backend isolation, URL encoding, redaction, and loopback-only publication

## 8. Backup and Restic Identity

- [ ] 8.1 Move backup orchestration and backup-record validation into direct `backup.py` APIs
- [ ] 8.2 Change local backup paths, locks, records, and status to host/project/role identity
- [ ] 8.3 Record concrete engine, image, version, format, purpose, facts, files, hashes, and upload result in `backup.json`
- [ ] 8.4 Change Restic tags and retention grouping to stable host/project/role identity
- [x] 8.5 Preserve checked Postgres dumps and globals and Redis confirmed BGSAVE behavior, and use complete native Dragonfly DFS generations
- [ ] 8.6 Implement `backup create`, `backup list`, and `backup test` with guided backup-time selection
- [ ] 8.7 Preserve two uploaded local backups, all unuploaded backups, free-space checks, and independent operation results
- [ ] 8.8 Preserve repository format v1, repository locks, weekly forget, monthly prune, and rotating checks
- [ ] 8.9 Attach full backup-test results to exact local and remote backup identities
- [ ] 8.10 Add local Restic integration tests for two roles in one project and cross-role selection rejection

## 9. One-Command Restore

- [ ] 9.1 Collapse exposed restore candidate and promotion APIs into one direct restore workflow
- [ ] 9.2 Select exact local or Restic backups by host/project/role and concrete-engine compatibility
- [ ] 9.3 Restore into a private same-filesystem candidate without live mounts, routes, aliases, or public ports
- [ ] 9.4 Preserve Postgres, Redis, and Dragonfly manifest, engine, and content verification
- [ ] 9.5 Create and upload a current live safety backup before final outage confirmation
- [ ] 9.6 Display selected backup, safety snapshot, data paths, and expected outage immediately before mutation
- [ ] 9.7 Stop only the selected role and atomically exchange verified and live data directories
- [ ] 9.8 Start installed Compose and require all service-contract and engine health checks
- [ ] 9.9 Automatically return prior data and verify it when restored data fails
- [ ] 9.10 Preserve prior and failed data paths when automatic recovery fails
- [ ] 9.11 Remove replaced local data only after safety upload and restored health both succeed
- [ ] 9.12 Reject all engine-major and Redis/Dragonfly conversion restores before candidate creation
- [ ] 9.13 Add interruption tests for download, verification, confirmation, directory swap, startup, and recovery

## 10. Status, Activity, and Scheduled Jobs

- [ ] 10.1 Replace release-aware status with host-local source, generated-file, Docker, engine, routing, backup, and transaction assessment
- [ ] 10.2 Render concise project-role human status with installed image and concrete KV engine
- [ ] 10.3 Emit a versioned secret-free `status --json` document suitable for SSH polling
- [ ] 10.4 Continue assessing other databases when one Docker or engine check fails or times out
- [ ] 10.5 Report dedicated Traefik, ACME state, native listeners, disk space, package version, and timer health
- [ ] 10.6 Report configuration differences in human terms without desired/deployed/release vocabulary
- [ ] 10.7 Preserve backup freshness, upload failure, and backup-test freshness as separate status signals
- [ ] 10.8 Update structured activity and journald fields to host/project/role identities with redaction
- [ ] 10.9 Update canonical backup, status, backup-test, retention, prune, and repository-check systemd units
- [ ] 10.10 Preserve persistent scheduling, randomized delay, timeouts, low priority, and timer enablement across setup and update
- [ ] 10.11 Add status schema, partial assessment, stale recovery, transaction residue, and secret scanning tests

## 11. Host Setup and Tool Updates

- [ ] 11.1 Implement `host setup` prerequisite checks for Python, Docker Compose, Restic, rclone, systemd, paths, DNS input, and ports
- [ ] 11.2 Create the non-login evdb account, Docker membership, canonical directories, and required ownership idempotently
- [ ] 11.3 Implement initial guided and explicit host config creation without modifying unrelated software
- [ ] 11.4 Install canonical systemd units and preserve timer state on repeated setup
- [ ] 11.5 Create and validate the dedicated network, Traefik assets, DNS credential boundary, and ACME storage
- [ ] 11.6 Implement versioned `/opt/evdb/versions`, `current`, `previous`, and stable command paths
- [ ] 11.7 Implement exact-version package candidate installation through uv
- [ ] 11.8 Run candidate read-only compatibility checks against config, state, Compose, backup records, and units
- [ ] 11.9 Preview and transact compatible config, state, and unit migrations without changing database definitions
- [ ] 11.10 Atomically activate the candidate and run host check without restarting databases
- [ ] 11.11 Restore prior tool, config, state, units, and loaded definitions after failed update
- [ ] 11.12 Retain one previous version and remove older inactive tool installations safely
- [ ] 11.13 Add setup idempotence, missing prerequisite, occupied port, incompatible state, successful update, and failed update tests

## 12. Remove Superseded Architecture

- [ ] 12.1 Remove the workstation controller and its package entry point
- [ ] 12.2 Remove the custom SSH JSON protocol and protocol-version bootstrap behavior
- [ ] 12.3 Remove planning, desired-state comparison, generic apply, release history, and deployment rollback
- [ ] 12.4 Remove host-wide release staging, manifests, active pointer, dual runtime, and bundled source copies
- [ ] 12.5 Remove lifecycle and details wrappers after behavior moves to direct database modules
- [ ] 12.6 Remove restore promotion and persistent candidate commands after one-command restore parity
- [ ] 12.7 Remove Ansible modules, generated inventory, playbooks, roles, variables, tests, and documentation
- [ ] 12.8 Remove obsolete release, controller, protocol, planning, and Ansible test fixtures
- [ ] 12.9 Verify no production configuration, data, Compose, timer, Docker, Restic, DNS, or systemd state changed during implementation

## 13. Documentation and Final Verification

- [ ] 13.1 Rewrite README around host setup, guided CLI, project roles, direct commands, backups, and restore
- [ ] 13.2 Document project-first YAML, per-database images, defaults, generated files, machine state, and canonical paths
- [ ] 13.3 Document dedicated native Traefik, DNS-01, SNI domains, and external HTTP proxy boundary
- [ ] 13.4 Document setup, exact-version update, one previous tool, failed-update recovery, and package release requirements
- [ ] 13.5 Document backup policy, backup test, one-command restore, safety backup, outage, and failure recovery
- [ ] 13.6 Document unsupported removal, major migration, KV conversion, password recovery, and production migration boundaries
- [ ] 13.7 Update Make targets and tests to remove controller/Ansible/release commands and use the host CLI
- [ ] 13.8 Run Ruff, unit tests, config/docs checks, and targeted disposable integration suites through uv
- [ ] 13.9 Run the full repository check without production credentials or services and resolve change-caused failures
- [ ] 13.10 Verify OpenSpec implementation coverage against all nine capability specs before requesting production migration work

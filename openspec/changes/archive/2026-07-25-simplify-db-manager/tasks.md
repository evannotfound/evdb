## 1. Concise Configuration Model

- [x] 1.1 Add minimal host and database source-model fixtures covering Postgres, Redis, Dragonfly, defaults, and supported overrides
- [x] 1.2 Replace the migration-oriented `Host` and `Instance` parsing with concise source dataclasses and a complete normalized runtime model
- [x] 1.3 Load one `config/<host>/host.yml` containing host settings and all database entries
- [x] 1.4 Derive container names, projects, paths, domains, durability, backup rules, engine settings, HTTP settings, and 1Password references
- [x] 1.5 Add safe defaults for Postgres/PgBouncer, Redis, Dragonfly, HTTP, backup freshness, paths, and operation timeouts
- [x] 1.6 Add explicit overrides for cache mode, PgBouncer, pool sizing, Dragonfly memory and threads, and HTTP enablement
- [x] 1.7 Reject unsupported engines, unsafe names, duplicate typed identities, derived collisions, invalid overrides, and migration-only fields with concise errors
- [x] 1.8 Reject old `postgres.yml` and `kv.yml` source layouts instead of adding a compatibility path
- [x] 1.9 Add typed selector resolution that accepts unique names and explains ambiguous `<type>/<name>` choices
- [x] 1.10 Add unit tests for source parsing, all defaults, each override, validation failures, typed duplicate names, and secret-safe errors

## 2. Generated Lock and Runtime Rendering

- [x] 2.1 Define the versioned, secret-free `host.lock.json` schema for platform image digests and stable HTTP ports
- [x] 2.2 Implement atomic lock loading and writing with source compatibility and duplicate-allocation checks
- [x] 2.3 Resolve configured image tags to platform-specific immutable digests without changing production containers
- [x] 2.4 Preserve existing locked image digests and update only entries whose configured source image changed
- [x] 2.5 Seed current Montreal HTTP ports and allocate the next free stable port for new HTTP-enabled KV databases
- [x] 2.6 Render normalized runtime JSON with protected remote secret-file paths while keeping source references and values out of runtime output
- [x] 2.7 Add tests for deterministic normalization, digest changes, stable ports across reorder/add/remove operations, atomic writes, and lock drift

## 3. Montreal Configuration Migration

- [x] 3.1 Convert the 25 Montreal databases into one concise `host.yml` with only real per-database exceptions
- [x] 3.2 Create the initial generated lock with current image digests and the 11 existing HTTP port assignments
- [x] 3.3 Add a golden comparison proving normalized names, engines, containers, projects, paths, domains, ports, database settings, and secret references match the current contract
- [x] 3.4 Delete the human-authored `postgres.yml` and `kv.yml` after the golden comparison passes
- [x] 3.5 Remove migration-specific engine counts, current/target equality, current image-id, and unlimited-resource validation

## 4. Local Controller and Remote Protocol

- [x] 4.1 Add the short `evdb` console entry point while retaining one internal host-runtime entry point
- [x] 4.2 Define a versioned JSON request/response envelope for fixed remote operations and structured failures
- [x] 4.3 Implement local SSH transport with subprocess argument arrays, bounded timeouts, stdin JSON, stdout JSON, and no shell strings
- [x] 4.4 Add remote dispatch that validates protocol version, operation name, selector, and payload before invoking host-local code
- [x] 4.5 Fail closed with a useful upgrade message when controller and remote protocol versions differ
- [x] 4.6 Generate internal Ansible inventory and normalized variables from `host.yml` so operators never invoke playbooks directly
- [x] 4.7 Add transport tests for argument safety, timeouts, malformed JSON, remote failures, unreachable hosts, and protocol mismatch

## 5. 1Password Management

- [x] 5.1 Extend the secret module with a local 1Password client that uses subprocess argument arrays and JSON parsing
- [x] 5.2 Implement convention-based system, Postgres, and KV item and field names matching existing Montreal items
- [x] 5.3 Preflight desktop or service-account authentication and reject Connect-only authentication for write workflows before any change
- [x] 5.4 Create missing database password and HTTP token fields idempotently using concealed fields and stdin templates
- [x] 5.5 Read requested database credentials locally without obtaining secret values from the managed host
- [x] 5.6 Transfer resolved deployment secrets through protected stdin/content paths without putting them in arguments or normal output
- [x] 5.7 Add tests proving retries do not duplicate or rotate existing items and secrets never appear in arguments, config, locks, logs, state, or errors

## 6. Database Details and Connection Access

- [x] 6.1 Add `evdb show <database>` and gather its non-secret derived, active-release, live-engine, and backup facts
- [x] 6.2 Render Postgres host, port, username, database, TLS mode, engine/image versions, paths, container, release, health, backup, and 1Password item details
- [x] 6.3 Build and display a complete usable `postgresql://` URL using the current local 1Password password and correct percent encoding
- [x] 6.4 Render Redis and Dragonfly native host, port, TLS, engine/image versions, paths, container, release, health, backup, and 1Password item details
- [x] 6.5 Build and display a complete usable `rediss://` URL and, when enabled, the public HTTPS endpoint and current HTTP token
- [x] 6.6 Fail without partial credential output when a required 1Password field cannot be resolved
- [x] 6.7 Ensure only `show` emits requested credentials and that status, plan, logs, histories, JSON output, and structured logs remain secret-free
- [x] 6.8 Add URL round-trip tests with reserved characters and command tests for running, stopped, HTTP-disabled, ambiguous, and missing-credential cases

## 7. Plan, Apply, and Create

- [x] 7.1 Define the active release manifest and desired-versus-deployed comparison for create, update, restart, pending, and drift actions
- [x] 7.2 Implement read-only `evdb plan` with no source, lock, secret, release, service, container, or data writes
- [x] 7.3 Block removal of a deployed database from source with guidance instead of stopping or deleting it implicitly
- [x] 7.4 Implement confirmed `evdb apply` plus deliberate non-interactive `--yes`
- [x] 7.5 Stage normalized config, lock, generated Compose, code, secret files, and release manifest before changing services
- [x] 7.6 Compute affected projects and avoid restarting databases whose locked image and generated service definition are unchanged
- [x] 7.7 Validate staged artifacts and require container plus engine-native health before activation
- [x] 7.8 Implement `evdb create <type> <name>` with atomic canonical YAML update, idempotent 1Password setup, plan display, confirmation, and normal apply
- [x] 7.9 Preserve pending desired config and managed secret items after failed create/apply so reruns converge
- [x] 7.10 Add unit and disposable-host tests for no-op plans, creates, updates, declined confirmation, `--yes`, blocked removals, failed applies, and idempotent retries

## 8. Routine Lifecycle Operations

- [x] 8.1 Add confirmed start, stop, and restart controller commands for a selected database project
- [x] 8.2 Add remote project-state operations using the active release's exact Compose file and locked image
- [x] 8.3 Add bounded log streaming for a selected project without reading or exposing protected secret files
- [x] 8.4 Serialize lifecycle, backup, apply, rollback, and promotion work with compatible host, repository, and instance locks
- [x] 8.5 Verify stop and restart preserve database data, source config, runtime config, secret files, backup history, and release history
- [x] 8.6 Add disposable Docker tests for lifecycle commands, selectors, operation conflicts, timeouts, and log redaction

## 9. Database and Host Observability

- [x] 9.1 Extend host-local status with Docker state and bounded Postgres, Redis, and Dragonfly native health checks
- [x] 9.2 Compare live containers with active release definitions and locked images to distinguish planned changes from unplanned drift
- [x] 9.3 Add SSH reachability, active release, source-lock consistency, timer state, and free data/backup filesystem checks
- [x] 9.4 Preserve independent latest backup, upload, restore-verification, and operation-error facts for every durable database
- [x] 9.5 Render a compact `evdb status [database]` table with failure details and meaningful healthy, stale, drifted, stopped, and failed states
- [x] 9.6 Add versioned `evdb status --json` with the same assessment, secret-free fields, and matching nonzero health semantics
- [x] 9.7 Continue assessing independent databases after one engine timeout or failure
- [x] 9.8 Add status tests for healthy, stopped, stale, drifted, low-disk, unreachable, partial-timeout, timer, and JSON cases

## 10. Operator Backup Workflows

- [x] 10.1 Route `evdb backup <database>` over SSH into the existing checked dump, manifest, and Restic upload path
- [x] 10.2 Add `evdb backups <database>` that merges local completed folders and tagged Restic snapshots by manifest/snapshot identity
- [x] 10.3 Show backup source, timestamp, snapshot id, local/remote availability, and full verification result in reverse chronological order
- [x] 10.4 Add `evdb backup-check <database> [backup]` using existing manifest hashes and isolated engine/content restore checks
- [x] 10.5 Default backup-check to the latest exact database backup and reject missing, ambiguous, cross-host, or cross-database snapshots
- [x] 10.6 Preserve independent success and failure state and continue other eligible databases during scheduled multi-database work
- [x] 10.7 Add tests for merged history, remote-only snapshots, upload failure retention, exact selection, integrity failure, restore failure, cleanup, and redaction

## 11. Immutable Releases and Rollback

- [x] 11.1 Store code, normalized secret-free config, Compose, lock, and a versioned manifest under each unique release directory
- [x] 11.2 Record source/lock hashes, controller version, predecessor, engine majors, plan, timestamps, and outcome without secrets
- [x] 11.3 Activate the staged release pointer only after every affected service passes health checks
- [x] 11.4 Restore prior Compose definitions and locked images automatically when staged service health fails
- [x] 11.5 Record high-severity recovery state and preserve both releases when automatic service recovery also fails
- [x] 11.6 Add release history and `evdb rollback [release]` defaulting to the previous successful active release
- [x] 11.7 Show rollback changes, require confirmation, health-check restored projects, and activate only after success
- [x] 11.8 Block engine-major rollback that cannot safely open the existing data format before stopping a live database
- [x] 11.9 Prove release rollback never renames, restores, deletes, or otherwise changes database data directories
- [x] 11.10 Add disposable tests for release audit, no-op activation, successful rollback, health-gate failure, automatic recovery, recovery failure, and major-version rejection

## 12. Restore Candidates and Promotion

- [x] 12.1 Extend restore code to create a unique persistent same-filesystem candidate directory and immutable restore record
- [x] 12.2 Select and verify the latest or exact snapshot for the selected host and typed database identity
- [x] 12.3 Restore Postgres, Redis, and Dragonfly candidates with locked compatible images and no published ports or live data mounts
- [x] 12.4 Run existing manifest, engine, content, key-type, value, and TTL checks and mark only successful candidates promotable
- [x] 12.5 Add confirmed `evdb promote <database> <restore-id>` plus deliberate non-interactive `--yes`
- [x] 12.6 Verify identity, compatibility, freshness, operation locks, same filesystem, and complete candidate state before stopping live service
- [x] 12.7 Atomically retain the live data directory, move the candidate into the configured path, start the project, and require engine-native health
- [x] 12.8 Automatically stop failed promoted data, restore the retained directory, restart prior service, and verify recovery health
- [x] 12.9 Preserve all candidate and prior data paths and record exact recovery instructions when automatic recovery fails
- [x] 12.10 Keep prior data after successful promotion and provide no purge or in-place restore operation in this change
- [x] 12.11 Add end-to-end disposable tests for successful candidates, rejected candidates, declined promotion, atomic swap, successful promotion, failed promotion recovery, failed recovery, and compatibility rejection

## 13. Ansible, Compose, and Scheduled Jobs

- [x] 13.1 Change Ansible roles to consume one normalized controller input instead of loading `host.yml`, `postgres.yml`, and `kv.yml` independently
- [x] 13.2 Update Compose templates to consume normalized direct fields and locked images rather than migration `target` dictionaries
- [x] 13.3 Stage runtime JSON and protected secret files with existing owner/mode guarantees before release validation
- [x] 13.4 Add changed-project Compose application and engine health gates without starting unchanged projects
- [x] 13.5 Update systemd units to invoke the internal host runtime and normalized config while keeping timers disabled by default
- [x] 13.6 Update disposable Ansible and Compose tests for the concise source model, generated lock, release layout, and no-production-write guards

## 14. Documentation and Final Validation

- [x] 14.1 Rewrite the README around the `evdb` create, show, status, plan/apply, backup, backup-check, restore/promote, and rollback workflows
- [x] 14.2 Replace configuration documentation with the minimal one-file schema, built-in defaults, and concise override examples
- [x] 14.3 Document that `evdb show` intentionally prints complete credentials and that other commands and artifacts remain secret-free
- [x] 14.4 Document controller SSH, write-capable 1Password authentication, generated lock ownership, release recovery, downtime, and retained-data behavior
- [x] 14.5 Update Make targets and command tests so routine operator work uses `evdb` rather than direct Ansible or host-local module commands
- [x] 14.6 Run Ruff checks and formatting, the full unit suite, disposable Docker integrations, Compose validation, Ansible syntax/check mode, and systemd verification
- [x] 14.7 Run OpenSpec strict validation, secret scanning, and `git diff --check`
- [x] 14.8 Confirm implementation and tests perform no deployment, Restic write, Docker change, cron change, or systemd change on `montreal-01`

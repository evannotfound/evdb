## Context

`build-db-system` created reliable backup, Restic, isolated restore-check, Compose, Ansible, and systemd foundations, but its source configuration is a migration manifest. Each instance repeats observed and desired images, image digests, container and project names, paths, domains, resources, backup flags, secret references, and engine defaults. The CLI runs on the database host and does not create or control database deployments.

The intended product is a small operator tool. A user should configure a host once, describe most databases with a name and type, and run concise commands locally. Production work still needs immutable images, protected secrets, validation, health gates, locks, and recoverable state, but those controls do not need to be exposed as repetitive source configuration.

The implementation must continue to use subprocess argument arrays, keep production Python on the standard library, keep secret values out of Git and command arguments, and avoid production writes while this change is implemented and tested. Public HTTP remains owned by the external proxy.

## Goals / Non-Goals

**Goals:**

- Make one `host.yml` the only human-owned database configuration for a host.
- Make a name and engine type sufficient for a normal database entry.
- Provide a local `evdb` control plane over SSH for creation, deployment, routine lifecycle work, monitoring, backup verification, recovery, and release rollback.
- Show complete database connection details and usable credential-bearing URLs on explicit operator request.
- Preserve reproducibility through generated image locks and immutable deployment releases.
- Create and retrieve convention-based credentials through a write-capable local 1Password session.
- Reuse the existing backup, manifest, Restic, isolated restore, Compose, Ansible, and systemd implementation where it remains appropriate.
- Make failed deployments and failed restore promotions recover automatically without deleting the prior release or data directory.

**Non-Goals:**

- Deploying this redesign to `production-host` or changing any running production container.
- Providing a web dashboard, persistent control-plane service, or public API.
- Managing the external HTTP proxy.
- Purging database data, backup history, or 1Password items.
- Supporting destructive in-place data restore.
- Preserving the unshipped three-file source schema or old command syntax.
- Adding replication, failover, point-in-time WAL recovery, or zero-downtime restore promotion.

## Decisions

### One concise source file

`config/<host>/host.yml` will contain the host identity, SSH destination, base domain, data root, host-level image versions, backup policy, 1Password vault settings, and a `databases` list. A normal entry contains only `name` and `type`. Optional fields express real exceptions such as cache mode, disabled PgBouncer, unusual pool settings, Dragonfly memory or threads, and disabled HTTP access.

The loader will derive container names, Compose project names, data paths, domains, default database settings, durability, backup policy, HTTP settings, and 1Password references. Internal installation, state, backup, and lock paths remain code defaults unless a future host demonstrates a real need for overrides.

This is preferred over YAML anchors, profiles, or inheritance because those mechanisms expose implementation structure and still require operators to understand the expanded schema. It is also preferred over one file per instance because the current host has many nearly identical entries and host-wide review is useful.

### Generated lock and observed state

`host.lock.json` will be a tool-owned, secret-free file beside `host.yml`. It will record the platform-specific digest resolved for each host-level image and stable assigned values that cannot be recomputed safely, initially HTTP loopback ports. Existing production ports will seed the first lock; new ports will use the next free value in the configured range. Operators may review and commit the lock but never edit it manually.

Observed Docker facts will not be source configuration. `evdb plan` will obtain the active release manifest and live status over SSH and compare them with the normalized desired model. This replaces the public `current` and `target` sections.

A generated lock allows explicit version tags in `host.yml` to keep routine upgrades readable while the lock and release manifest retain exact reproducibility. Immutable digest sources are also accepted and reused without registry resolution. Untagged implicit latest and explicit `:latest` sources are rejected. Resolving tags on every start is avoided because mutable tags would make rollback nondeterministic.

### Local controller and small remote runtime

The public `evdb` command will run on the operator's workstation. It will read source YAML, manage the lock, invoke `op`, and connect to the configured host with `ssh` argument arrays. The normal fixed command loads code and config through the active `current` release. A separate stable bootstrap runtime reads an always-refreshed normalized machine-owned runtime under `/opt/evdb/host-runtime/runtime` only for first install or an explicitly detected protocol upgrade; it never reads preserved legacy JSON from `/etc`. Generic protocol, SSH, malformed-response, configuration, and operation errors never trigger it. Both exchange versioned JSON over stdin/stdout.

Ansible remains an internal bootstrap mechanism because its roles safely manage ownership, modes, stable bootstrap code, and the dedicated bootstrap runtime. Release activation owns canonical systemd units so bootstrap cannot disturb loaded current-release definitions. The controller will generate normalized input and inventory rather than making the operator invoke playbooks directly. The remote runtime continues to read JSON and therefore retains no production YAML dependency.

This is preferred over running the public CLI directly on production because local 1Password access and source control are controller concerns. It is preferred over a daemon or web service because SSH already supplies authentication, transport security, and auditability without another exposed service.

### Convention-based 1Password ownership

Host configuration will name a vault and the system item containing Restic and rclone fields. Database item names will be derived as `<name>-postgres` for Postgres and `<name>-kv` for Redis or Dragonfly, preserving the current naming convention. Database items contain a concealed password and, when HTTP is enabled, a concealed HTTP token.

`evdb create` will look up the item first and create only missing fields or items. JSON item templates and secret material will pass through stdin; secret values will never appear in process arguments, source or lock files, logs, or exceptions. The controller will support a desktop-authenticated CLI or service account with `write_items`. It will reject Connect-only authentication for write workflows with a direct remediation message.

Items are not deleted when config writes, applies, rollbacks, or retirements fail. Idempotent lookup makes a partially completed create safe to rerun.

### Database details and connection URLs

`evdb show <database>` will combine derived configuration, active release facts, and live engine status into one detail view. For Postgres it will show engine and image versions, state, hostname, port, username, database name, TLS requirement, 1Password item, data path, container name, backup summary, and a complete `postgresql://` URL. For Redis and Dragonfly it will show the corresponding native TLS `rediss://` URL and, when enabled, the public HTTP endpoint and token.

The command intentionally reveals the full connection URL and HTTP token every time, matching the existing production manager behavior selected by the operator. It will resolve credentials from 1Password on the local controller, percent-encode URL components, and print them directly to the terminal. Credentials will not be requested from the remote host, included in SSH or child-process arguments, emitted to structured logs, or persisted in state. If local 1Password authentication cannot resolve a required field, the command will fail rather than print a misleading partial URL.

This explicit details command is the only normal output allowed to contain credentials. Status, plan, logs, backup history, release history, and all JSON machine output remain secret-free.

### Desired-state creation and application

`evdb create <type> <name>` will validate the identifier, atomically add a minimal entry to `host.yml`, ensure the 1Password item, show the same plan used by `evdb apply`, and require confirmation before applying. `--yes` will support deliberate non-interactive use. If remote application fails, the desired entry and secret item remain so the command can converge on a later run.

`evdb plan` will be read-only and show create, update, restart, and drift actions for databases and `host/<id>` release infrastructure. `evdb apply` will resolve the lock, stage normalized config, secrets, Compose files, code, canonical systemd units, and a release manifest, validate the staged release, create `traefik-net` when absent, apply and health-check Traefik before affected databases, and require every primary and sidecar plus engine health before switching the active release. Routine apply does not run Ansible. Removal of a deployed database from source config will be rejected because destructive or retirement semantics are outside this change.

Before installing candidate secret files, apply snapshots each target's bytes, mode, ownership, and existence in memory. Validation, health, activation, and recovery failures restore those files before prior Compose is restarted. Existing deployed database credential and protected config files cannot change in this release; credential rotation is a separate workflow. Missing files and secrets for new databases can be installed, while the existing Restic overwrite and rclone preservation policies remain unchanged.

Start, stop, and restart are operational state changes and do not rewrite desired config. Stopping or rolling back never removes data, secrets, or backups.

### Immutable release activation and rollback

Each deployment release will contain code, normalized secret-free config, generated Compose, canonical systemd units, the image lock, and a manifest describing every expected primary and sidecar container, engine major versions, the shared network, and Traefik. Every generated managed service carries its project service hash as a managed label computed before label insertion, avoiding circular hashes while detecting non-image drift. Mutable data, secret files, logs, and operation state stay outside releases.

Apply stages a new release without replacing the current release or loaded unit files. After changed projects are healthy, one activation transaction installs canonical units, refreshes active runtime files, switches the active symlink, and reloads systemd without enabling, disabling, starting, or stopping timers. It snapshots and restores prior unit files, runtime files, and the pointer if activation fails. A failed health check restores candidate secrets before restoring prior Compose definitions and images and leaves the prior release active.

`evdb rollback [release]` defaults to the prior active release, shows the database and image changes, requires confirmation, reapplies its Compose definitions, verifies health, and only then switches the active pointer. It never changes database data. Rollback is blocked when it would start an engine major version that cannot read the existing data format.

### Concise but complete status

`evdb status [database]` will combine controller reachability with host-local facts: running state, engine ping, container health, desired/release/image drift, disk space, latest local backup, latest confirmed Restic snapshot, last full restore verification, and recorded operation errors. Human output is a compact table with details only for failures. JSON output preserves structured data for future alerting, and unhealthy or stale state returns nonzero.

This extends the existing state-file status rather than creating a monitoring database. Systemd timers continue to automate backups and restore checks; a persistent metrics or notification service can consume JSON later.

### Backup history and verification

The current checked backup format, manifests, hashes, independent per-instance state, and Restic upload remain authoritative. Latest local completion, successful upload, successful restore summary, per-backup verification records, and current errors remain independent. `evdb backup` invokes that path remotely. `evdb backups` combines local completed folders and tagged Restic snapshots into a chronological list. `evdb backup-check` selects the latest or requested backup, verifies its exact typed identity, manifest, hashes, and engine-major compatibility, restores it into an isolated container, and records the result without erasing older verification records.

Calling the full existing restore test a backup check makes the operator intent clear while preserving the stronger behavior. Repository maintenance remains available but is not part of the primary workflow.

### Restore candidates and promotion

`evdb restore <database> --snapshot <id|latest>` will restore into a unique staging directory under the same data filesystem as the live database. It will start a network-isolated candidate with the locked compatible engine image, perform the existing engine-specific verification, stop the candidate, and record a restore identifier and immutable metadata.

`evdb promote <database> <restore-id>` will acquire the instance operation lock, verify the candidate again, show the selected backup and expected outage, and require confirmation. It will stop the live project, rename the live data directory to a timestamped retained path, atomically rename the candidate into the configured data path, and start and health-check the project. If startup or health checks fail, it will stop the candidate, swap the prior directory back, and restore service before returning failure.

Successful promotion retains the prior data directory for manual recovery and later explicit cleanup. No command in this change purges it. Same-filesystem staging is required so directory swaps are atomic.

### Typed identifiers only when necessary

Commands accept a plain database name when it is unique. If the same name exists for multiple types, the CLI requires `<type>/<name>` and lists the matching choices. This keeps common commands short without losing support for products that have both Postgres and KV databases.

## Risks / Trade-offs

- [Generated lock can drift from source] -> Validate the source/lock pair before plan or apply, update it atomically, and include its hash in every release manifest.
- [Mutable image tags can resolve differently later] -> Resolve platform-specific digests before apply and use only locked references in generated Compose and rollback releases.
- [1Password writes can partially succeed] -> Make item creation idempotent, never delete items automatically, and keep secret values out of error text.
- [Connection details can leak through terminal capture or scrollback] -> Reveal credentials only from the explicit `show` command, print a concise view once, and never duplicate its output in logs or remote traffic.
- [The current environment uses Connect variables that do not support all management commands] -> Preflight authentication and require desktop or service-account write access for create while allowing read-only commands where supported.
- [SSH or controller interruption can leave a staged release] -> Stage under a unique release id, activate only after health checks, and make apply safe to rerun.
- [Compose rollback can be unsafe across engine major versions] -> Record engine majors in manifests and block incompatible rollback before stopping a database.
- [Restore promotion requires downtime] -> Show the outage in the promotion plan, keep the swap small, and restore the prior directory automatically on failure.
- [External HTTP routes depend on stable loopback ports] -> Seed existing assignments in the generated lock and never reallocate a surviving database's port.
- [Canonical YAML writes may alter formatting] -> Keep the schema small, write atomically, preserve key order, and treat comments as documentation rather than configuration state.
- [A broad redesign can regress proven backup behavior] -> Retain existing backup and restore-check internals and add lifecycle tests around them instead of rewriting their data paths.

## Migration Plan

1. Add the new source parser and normalizer alongside fixtures that prove concise entries expand to the current 25-instance deployment contract.
2. Convert production source config to one `host.yml`, delete the two instance files, and seed `host.lock.json` with current image digests and HTTP ports.
3. Add the local controller, remote JSON protocol, 1Password manager, and status/lifecycle commands against fakes and disposable hosts.
4. Adapt Ansible and Compose rendering to normalized input, then add staged release activation and rollback on disposable Docker projects.
5. Extend isolated restore code with persistent candidates and promotion, including forced failure recovery tests.
6. Rewrite operator documentation and run the complete local validation suite and secret scan.
7. Leave production unchanged. A later approved production move will install the controller-compatible runtime, import the current deployment as the initial release, verify a no-change plan, and only then enable apply and timers.

Implementation rollback is a source revert before production adoption. After adoption, the initial imported release and retained current data provide the operational rollback point.

## Open Questions

No design question blocks implementation. Before a production move, confirm the checked-in SSH destination, the controller's write-capable 1Password authentication mode, and the external proxy's current port mappings against the seeded lock.

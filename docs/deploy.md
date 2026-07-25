# Apply and releases

## Operator workflow

```sh
evdb validate
evdb plan
evdb apply
evdb releases
evdb rollback RELEASE_ID
```

`plan` compares normalized desired state with the active release and live containers. It reports
create, update, restart, pending, and drift actions. Removing a deployed database from source is
blocked; this repository has no retirement or purge workflow.

`apply` prints that plan and requires confirmation unless `--yes` is supplied. It resolves the
lock, gathers protected files from local 1Password, and sends a fixed versioned apply operation
over SSH. Routine apply does not run Ansible or replace bootstrap runtime, active config, units,
or protected files before the staged release is validated. Operators do not invoke playbooks or
host-local Python commands for routine work.

## Release activation

Every apply stages a unique immutable release under `/opt/evanovation-db/releases`. A release
contains active runtime code, normalized secret-free JSON, generated Compose JSON, canonical
systemd units, generated Ansible input, `host.lock.json`, and a manifest with source and lock
hashes, every expected service/container, engine majors, shared `traefik-net` and Traefik
infrastructure, service contract labels, and controller/runtime versions.

Database data, protected secret files, logs, operation state, restore records, and promotion
journals remain outside releases. Apply validates staged files and Compose, starts only projects
whose active service definition, locked image, or running state requires it, and checks every
primary and sidecar plus engine-native health. The shared network is inspected and created only
when absent; changed Traefik is applied and must report healthy Docker status before databases.
The active release pointer changes only after every affected service is healthy.

Candidate secret targets are snapshotted in memory before installation. If validation, health,
activation, or recovery fails, exact prior files are restored and new files removed before prior
Compose starts. Changed credential or protected database config files for deployed instances are
blocked because rotation is a separate workflow. If a changed project fails, apply restores the
prior release's definitions and locked images and verifies health. If automatic recovery also
fails, both releases are retained and the audit records high-severity recovery steps.

## Release rollback

`evdb rollback` defaults to the previous successful active release. Supplying a release ID is
safer for scripted or Make-based work. The command displays service and image changes, requires
confirmation, starts or stops the target definitions, health-checks them, and changes the active
pointer only on success.

Rollback is deployment recovery, not data recovery. It never renames, replaces, restores, or
deletes database data directories and never selects a backup. It is blocked before stopping a
database when the target engine type, data path, or major version cannot safely use the current
data. Use `evdb restore` and `evdb promote` for older data.

## Routine lifecycle

`evdb start`, `stop`, `restart`, and `logs` use the active release's exact Compose file, project,
container, and locked image. Lifecycle commands retain data, source and runtime config, protected
files, backup history, and release history. Logs are bounded to 1 through 1000 lines and redact
credential fields, URLs, and `op://` references.

## Internal deployment boundary

Ansible remains an internal bootstrap and file ownership mechanism. It runs only for first install
or an explicitly detected protocol upgrade. Generated controller inventory places the real host in
the `production` group and targets `production`, so the playbook production guard is exercised;
disposable tests explicitly use `managed` or `test` groups.

Bootstrap refreshes stable code and its dedicated normalized
`/opt/evanovation-db/host-runtime/runtime`; it does not replace or reload active unit files.
Canonical units install from a healthy release in the same transaction that switches active
runtime and pointer. Failure restores prior unit bytes, modes, runtime, pointer, and loaded
definitions. No activation command enables, disables, starts, or stops timers. Systemd and normal
remote operations execute `/opt/evanovation-db/current/src` with
`/opt/evanovation-db/current/runtime`.

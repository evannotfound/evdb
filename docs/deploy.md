# Direct database operations

## One role at a time

There is no host-wide desired-state operation. Each command validates, previews, confirms, changes,
and health-checks one concrete target:

```sh
evdb database add app-prod-01 postgres
evdb database configure app-prod-01/postgres --max-clients 200
evdb database start app-prod-01/postgres
evdb database stop app-prod-01/postgres
evdb database restart app-prod-01/postgres
evdb database logs app-prod-01/postgres --lines 200
```

Add and configure acquire host and project/role locks, stage source, state, secret, and Compose
candidates in private sibling paths, resolve immutable image digests, validate candidate YAML, and
show the exact service and outage effects. Confirmation installs the files atomically and performs
at most one Compose restart.

An existing matching role makes add idempotent. A failed new role is stopped and is not marked
installed. Only empty files and directories proven to belong to that transaction may be removed.

## Settings recovery

Before a durable primary-container recreation, evdb creates, checks, and uploads a safety backup.
Failure to upload stops the settings operation before the service changes. Sidecar-only changes and
cache-mode KV do not claim a data recovery point.

Until candidate health succeeds, the transaction retains exact prior source, generated files,
secret-file metadata, and resolved state. Startup or health failure restores the prior same-engine
files, starts that Compose definition, and verifies prior health without prompting. Successful work
keeps one `host.previous.yml` and a bounded secret-free activity record, not selectable service
definition history.

## Lifecycle behavior

Start, stop, and restart address only the selected `project/role` Compose project. They preserve
source, generated files, credentials, data, backup history, and package history. Start and restart
require the expected primary, sidecars, service-contract labels, image digest, and engine-native
health. Logs are bounded and redact credentials and credential-bearing URLs.

Removing a role from YAML is unsupported and non-destructive. Host check reports it, and mutation
stops until source is restored. Major engine changes and Redis/Dragonfly conversion are also
rejected before any container stops.

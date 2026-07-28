## Context

evdb currently has about ten thousand lines of production Python and twelve systemd units. Basic
database creation crosses source configuration, machine state, image resolution, secret staging,
generated Compose contracts, transaction snapshots, recovery logic, status drift checks, and timer
reconciliation. Backup creation assumes that separate Postgres and KV Restic repositories already
exist even though repository initialization is not part of setup or the CLI. The guided home screen
then exposes nearly every observed and stored field in one wide table, causing the values operators
need first to be truncated.

The product is pre-v1 personal tooling for Ubuntu hosts. It needs to retain Postgres, PgBouncer,
Dragonfly, Redis, Redis-over-HTTP, TLS routing, direct lifecycle control, and remote backups, but it
does not need an enterprise recovery or deployment framework yet. Production migration remains a
separate change and no implementation or validation may mutate `montreal-01`.

## Goals / Non-Goals

**Goals:**

- Make one rerunnable initialization command prepare the host, initialize the remote repository, and
  activate automatic backups without follow-up systemd or rclone directory commands.
- Make `/etc/evdb/config.yml` and `/etc/evdb/secrets.yml` the two readable evdb sources, with
  rclone's mutable native file beside them.
- Let concrete engine modules own database-specific behavior while generic database and backup
  modules perform only shared orchestration.
- Fail with the original operational context and keep rerunnable declarative files rather than
  attempting layered automatic recovery.
- Present a compact first screen and disclose detailed settings, images, credentials, backup records,
  and errors only after the operator selects the relevant database or host view.
- Remove code, files, requirements, and tests for capabilities outside the v1 boundary rather than
  retaining dormant compatibility layers.

**Non-Goals:**

- No live restore, isolated restore-based backup test, safety backup, remote retention, prune,
  repository check, database retirement, engine migration, or password rotation.
- No automatic migration from the current pre-v1 config, state, repository, unit, or tool layout.
- No non-systemd scheduler or support commitment beyond Ubuntu for v1.
- No automatic rollback of failed database changes, initialization, or installer-driven host refresh.
- No credential display outside the deliberate database information view.

## Decisions

### Use two evdb source files and one native rclone file

The canonical files are:

```text
/etc/evdb/config.yml
/etc/evdb/secrets.yml
/etc/evdb/rclone.conf
```

`config.yml` contains host identity, routing, one backup repository URL, projects, roles, concrete
engines, images, and explicit settings. `secrets.yml` contains the Restic password, DNS values,
database passwords, and HTTP tokens under matching project and role keys. Both loaders require a
complete supported schema; the secret file is mode `0600` and is never emitted by status.

`rclone.conf` remains separate because rclone writes refreshed OAuth state in its own INI format.
evdb copies an initial native file only when absent and then never regenerates or translates it.
Keeping mutable OAuth state synchronized through YAML was rejected because it would create a custom
two-way configuration protocol.

Generated Compose and engine files live together under
`/etc/evdb/projects/<project>/<role>`. Runtime state under `/var/lib/evdb` is limited to local backups
and locks. There is no machine-state, activity, transaction, restore, or mutable-rclone subtree.

### Remove deployment state and render directly from configuration

Configured non-`latest` image references are written directly into generated Compose. evdb does not
resolve a second platform digest, persist image state, attach service contract hashes, or compare a
generated definition with a machine-owned copy. Existing containers remain stable because routine
commands do not pull newer images; changing an image is an explicit settings edit.

Database presence comes from `config.yml`; generated file presence and Docker observations describe
runtime state. `database add` writes the selected role and generated credentials, renders its files,
runs `docker compose up -d`, and waits for native health. `start` and `restart` rerender from the
current source before invoking Compose. A failure leaves the readable source and generated files in
place, reports the failing command, and can be retried with `database start` after correction.

Candidate transactions, fingerprints, prior-file snapshots, safety backups, and automatic rollback
were rejected because they dominate the current orchestration. Atomic replacement remains appropriate
for writing one YAML or generated file; it is not expanded into a recovery framework.

### Put database-specific work in concrete engine modules

`postgres.py`, `redis.py`, and `dragonfly.py` each own their defaults, role-specific validation,
Compose services, generated environment or native configuration files, health check, information
fields, and checked backup creation. `kv.py` retains only shared Redis-protocol commands genuinely
used by Redis and Dragonfly. No abstract base class, protocol, adapter, or inheritance hierarchy is
introduced; a small direct engine lookup selects the module.

`database.py` owns only project/role selection, source updates, generated file installation, generic
Compose lifecycle, bounded logs, and the health wait loop. Generic Docker and Compose subprocess
helpers stay together. Restic upload and history move into `backup.py`; `compose.py`, `images.py`,
`secrets.py`, `restic.py`, and `restore.py` are removed after retained behavior moves.

Configuration models remain simple immutable data values in `models.py`. Transaction-only data
classes such as `Change`, `Candidate`, `FileState`, and callback containers such as `Actions` are
removed.

### Initialize one Restic repository per host

`config.yml` contains one `host.backup.repository`, for example
`rclone:onedrive-evanovation:evdb/toronto-01`. Every snapshot is distinguished by host, project,
role, concrete engine, and backup tags. One repository lock serializes Restic work.

Initialization requires Restic 0.17 or newer. `evdb init` runs `restic cat config` with the configured
password and rclone file. Exit zero means the repository is ready; Restic's documented missing-repo
exit code triggers `restic init --repository-version 1`; every other result fails with its original
repository URL and stderr. Restic through rclone creates the missing remote path, so evdb does not run
an independent `rclone mkdir` or ask the operator to prepare directories.

Backup creation retains native engine checks, private partial directories, manifests, file sizes and
SHA-256 hashes, confirmed Restic snapshot IDs, merged local/remote history, and cleanup that keeps the
two newest uploaded local backups. Remote snapshots are not deleted in v1 and repository growth is
documented explicitly.

### Keep systemd but install one automatic backup schedule

Ubuntu is the v1 platform, so systemd is the existing host scheduler rather than an abstraction to
hide. The package contains only:

```text
evdb-backup.service  -> evdb backup create --all
evdb-backup.timer    -> daily, persistent, randomized
```

`evdb init` installs, reloads, and enables the timer with `--now` after the repository is ready. The
service runs durable databases sequentially, records each result, continues after an individual
failure, and exits nonzero if any database failed. A new database needs no timer instance or escaped
unit name and is included automatically in the next run. Per-database, status, test, retention,
prune, and repository-check timers and generated drop-ins are removed.

### Make initialization rerunnable and updates installer-owned

Top-level `evdb init` replaces `host setup`. On a new host it collects or accepts the required source
values, creates the service identity and canonical files, initializes networking and Traefik,
initializes Restic, installs the two units, enables the timer, and checks the resulting runtime. On an
existing host it reads the canonical files and repeats those direct convergence steps without
replacing existing credentials or rclone state.

`host check` becomes `status`; in-app update and uninstall commands are removed. The verified shell
installer keeps checksums, archive validation, atomic `current` selection, and one `previous` release.
It may update a configured host and then invokes `evdb init --yes` to refresh host assets. If refresh
fails, the installer reports the error and leaves the selected release available; it does not run a
second compatibility and rollback implementation inside Python.

### Use progressive terminal disclosure

Running `evdb` renders one compact database table with `#`, `Database`, `Engine`, `Status`, and
`Backup`. Numbered rows open a database directly; add, host, and exit follow the rows. The overview
does not include image digests, configuration hashes, full errors, credentials, or separate
local/upload/test columns.

The database screen uses key/value text and a numbered menu for Details, Connection, Settings,
Start/Stop, Restart, Backups, and Logs. Full images belong in Details, credentials only in Connection,
and backup create/history under Backups. Host details and backup history use compact text rather than
another wide table. Screens, previews, results, errors, and prompts are separated by blank lines.
The interface stays SSH-safe and numbered without cursor-addressed navigation.

Direct commands remain available for scripts and systemd. Explicit direct commands execute without a
generic confirmation layer; guided creation and settings use one final confirmation. Only expected
application errors are converted to concise `evdb:` messages. Unexpected programming errors retain a
traceback.

### Redact exact credentials, not operational identifiers

Repository URLs, rclone remote names, paths, image references, snapshot IDs, and subprocess stderr are
ordinary diagnostic information and remain visible. The runner replaces only exact database, HTTP,
Restic, DNS, and rclone credential values and their required encoded forms. Broad key-name, URL, and
repository redaction is removed. Database Connection deliberately prints its current credentials in a
TTY, while status and non-interactive output remain credential-free.

## Risks / Trade-offs

- [Removing restore and backup tests weakens recovery confidence] -> Retain engine-native creation
  checks and manifest hashes, state the limitation plainly, and add restore only as a later focused
  capability.
- [One repository serializes all uploads] -> The single daily service is already sequential and the
  personal-host workload favors simplicity over parallel maintenance.
- [Remote snapshots grow indefinitely] -> Document that v1 performs no remote deletion and leave
  manual Restic retention outside evdb until a smaller retention design is requested.
- [Failed changes can leave configured but unhealthy roles] -> Keep files readable, show the exact
  failing command, expose health in the selected database view, and make start rerender and retry.
- [The new schema cannot operate current hosts] -> Do not add compatibility branches; reset only
  approved disposable hosts and keep production migration separate.
- [Secrets now share one YAML file] -> Require root/evdb ownership and mode `0600`, write atomically,
  and never include the file or values in machine output.
- [Installer refresh can fail after selecting a release] -> Report the direct initialization error and
  let the operator repair config or rerun init; the previous verified release remains on disk.

## Migration Plan

1. Archive the completed Postgres creation fix so its behavior is represented in the main specs.
2. Remove restore, restore-based testing, maintenance commands, units, docs, and tests while preserving
   checked backup creation.
3. Introduce the new config/secrets models and paths, then move engine ownership and direct lifecycle
   behavior onto them.
4. Replace role repositories and timers with one initialized host repository and one all-database
   backup timer.
5. Replace host setup/update flows and the guided CLI, then remove superseded modules and state.
6. Run local unit, configuration, Restic, container, installer, binary, terminal-width, and OpenSpec
   validation.
7. With separate approval, reset only the disposable Toronto pre-v1 files, initialize the missing host
   repository, create all retained engine roles, prove automatic backups and readable errors, and
   restore the stable command after development validation.

There is no automatic rollback to the old on-disk schema. Source control reverts the implementation;
existing hosts must retain their old executable and files until explicitly reset or migrated.

## Open Questions

None. The v1 engines, backup boundary, repository layout, systemd scheduler, update path, secret
layout, rclone exception, and credential-output boundary are fixed by this change.

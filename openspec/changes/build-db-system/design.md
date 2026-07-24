## Context

`montreal-01` currently has 25 managed database instances under `/home/ubuntu/databases`: 14 Postgres, 7 Dragonfly, and 4 Redis. The current scripts back up only 6 Postgres and 4 KV instances. They delete prior local files before upload is known to be good, handle each engine as one batch, do not check Restic results well enough, and cannot produce a safe Dragonfly RDB.

Every instance Compose file is a link to one shared template for its engine group. The current KV template says Dragonfly for all instances even though four running containers still use Redis. This means the files on disk do not fully describe what is running.

All 11 KV instances run a `serverless-redis-http` sidecar on a unique loopback port. Nginx Proxy Manager owns public ports 80 and 443 and currently routes ten HTTPS domains to those ports. `oai-co-prod-02` has no public route, and two routes still use the old `kv-na01.storage.evanovation.com` suffix. The target config enables public HTTP for all 11 and uses the `kv-montreal-01.storage.evanovation.com` suffix.

The repository is currently empty apart from project setup. This change builds the full system locally. It can read production facts over SSH, but it does not deploy to production or change production files, jobs, repositories, containers, images, or secrets. A later `move-prod` change will do that work.

Production uses Ubuntu 22.04 ARM64, Python 3.10, Docker 29, Compose 2.40, Restic 0.12.1, rclone 1.68.1, two existing Restic repositories on OneDrive, Traefik TCP TLS/SNI routing, and Nginx Proxy Manager HTTPS routing. The new runtime should need only Python's standard library.

## Goals / Non-Goals

**Goals:**

- Build one clear system for config, backup, restore tests, Restic, status, deployment, and scheduled jobs.
- Record all 25 managed instances without committing secrets.
- Support Postgres, Redis, and Dragonfly as separate engines.
- Support an optional authenticated serverless HTTP sidecar for each KV instance, with all 11 initially enabled and public.
- Preserve shared Postgres and KV ports through Traefik and shared HTTPS through Nginx Proxy Manager.
- Prove backup and restore behavior with disposable local containers and a local Restic repository.
- Build and test Ansible, Compose, and systemd files without running them on production.
- Keep file, function, class, and command names short, direct, and natural.

**Non-Goals:**

- Deploying any part of this repository to `montreal-01`.
- Replacing cron, changing Compose ownership, restarting databases, changing images, upgrading Restic, rotating secrets, or touching the OneDrive repositories.
- Changing any running Redis instance to Dragonfly.
- Moving database data or redesigning Traefik.
- Replacing Nginx Proxy Manager or taking over its unrelated proxy hosts.
- Adding PITR, WAL tools, replication, failover, Kubernetes, a dashboard, a daemon, or a plugin system.
- Choosing the production alert service. Status and exit codes are built here; alert wiring is part of `move-prod`.

## Decisions

### One build change, separate production move

This change builds all code, config, templates, tests, and docs in one pass. Production migration remains a separate change after local checks pass.

This keeps the new system internally consistent without mixing development with a live cutover. The alternative was to build and deploy one part at a time, but that would make local completion depend on production and would blur the user's requested boundary.

### Organized, direct package layout

The code is grouped by real work rather than by framework layers:

```text
src/evanovation_db/
  cli.py
  config.py
  docker.py
  run.py
  lock.py
  files.py
  manifest.py
  restic.py
  status.py
  backup/{main,postgres,redis,dragonfly}.py
  restore/{main,postgres,redis,dragonfly}.py
```

Related deployment and test files use the same names:

```text
config/montreal-01/{host,postgres,kv}.yml
compose/{postgres,redis,dragonfly,traefik}.yml.j2
ansible/{hosts,backup,databases,restore}.yml
ansible/roles/{base,app,backup,postgres,kv,traefik,http}/
tests/{unit,integration,config,fixtures}/
```

Module names give functions their context, so names stay short: `postgres.backup()`, `redis.restore()`, `restic.upload()`, `manifest.write()`, and `docker.exec()`. The implementation will not add service, provider, adapter, manager, factory, or plugin classes unless a concrete need appears.

The alternative flat layout was shorter but would mix engine backup and restore work in large files. A deeper framework layout would add names without adding useful boundaries.

### Source YAML and runtime JSON

Humans edit three small YAML files for `montreal-01`. Ansible and local config tools render one JSON file per instance. Production Python reads only JSON.

Each instance keeps two simple sections:

- `current`: what Docker reports now, including engine, image name, image id, data path, and Compose file.
- `target`: what this repository will render later, including pinned image and the same engine and data path.

KV config also has a short `http` section with `enabled`, `public`, `port`, `domain`, `image`, `token`, and `max_connections`. These are per-instance choices. All 11 start enabled and public, but future instances can choose otherwise.

This handles the four Redis containers whose shared Compose template now says Dragonfly. It also lets validation reject accidental engine or data-path changes before any future production run.

YAML parsing is allowed on the controller and in development. It is not a production Python dependency.

### Simple runtime data

The Python config uses small dataclasses for values that are passed across modules, such as `Host`, `Instance`, and `Backup`. It does not build a general object model for Docker or database engines.

Runtime state uses plain JSON files written atomically:

```text
/var/lib/evanovation-db/
  backups/<instance>/<utc-time>/
  state/<instance>.json
  locks/
  rclone/rclone.conf
```

Each completed backup folder contains `backup.json`. Postgres files are `globals.sql` and `databases/<safe-db-name>.dump`. Redis and Dragonfly use `dump.rdb`.

### Safe command runner

`run.py` owns subprocess execution. It accepts argument arrays, a timeout, a small allowed environment, and optional stdout file. It never uses `shell=True`. It returns a small result value or raises a direct error.

Secrets are collected before a command runs and removed from logged arguments, output, and errors. Large binary output, such as `pg_dump`, streams directly to a private file instead of being held in memory.

`docker.py` contains the few Docker operations the engine modules need: inspect, exec, copy, run, stop, and remove. This avoids repeated command building without introducing a Docker framework or SDK dependency.

### Backup folder flow

Every run follows one flow:

```text
lock instance
  -> check free space
  -> create <time>.partial with mode 0700
  -> create engine files
  -> run engine checks
  -> write file hashes and backup.json
  -> rename to <time>
  -> upload with Restic
  -> record snapshot id
  -> clean old uploaded folders
```

An unuploaded folder is never removed automatically. At least two uploaded folders remain. A free-space limit stops a new run before the filesystem is put at risk.

### Postgres backup and restore

The Postgres module runs the tools inside the matching Postgres container so client and server major versions match. It uses the configured cluster superuser over the local container socket and does not need the password in command arguments.

It queries `pg_database` for connectable non-template databases, streams one `pg_dump -Fc` archive per database, and creates `globals.sql` with `pg_dumpall --globals-only`. The globals file keeps role password hashes and is treated as a secret file.

Daily checks require nonempty files and a readable archive table of contents. A real restore remains the stronger check. Restore starts a fresh matching container, loads globals first, creates databases from `template0`, uses `pg_restore --exit-on-error`, connects to every database, and compares expected catalog counts.

### Dragonfly backup and restore

Dragonfly uses `SAVE RDB <new-name>`. The name contains the instance and run id and did not exist before the command. `SAVE` is synchronous, so success plus the exact new file avoids the stale `dump.rdb` bug. The file is copied out, checked for size and hash, and only the run's named source file is removed.

`redis-check-rdb` is not used for Dragonfly because Dragonfly 1.34.1 can write private RDB records and a zero checksum. The strong check is loading `dump.rdb` into a fresh container using the recorded Dragonfly image.

### Redis backup and restore

Redis records `LASTSAVE`, waits until a later timestamp can be observed, starts `BGSAVE`, and polls `INFO persistence` until the save finishes successfully and `LASTSAVE` advances. It copies the configured RDB and checks it with tools from the matching Redis image.

Redis authentication is passed through `REDISCLI_AUTH` in the exec environment, not `redis-cli -a`. Restore mounts the saved file into a fresh matching Redis container.

For Redis and Dragonfly, backup records database count, key count, sampled key types, hashes of selected values, and TTL facts without storing raw values. Restore checks those facts and allows for elapsed TTL time.

### Restic and rclone

`restic.py` parses JSON Lines and accepts an upload only when Restic exits zero and the final `summary` record has `snapshot_id`. Exit code 3 or any other nonzero result is a failure even if a snapshot id was printed.

Snapshots use a fixed host and stable `host:`, `engine:`, and `instance:` tags. Backup format stays in `backup.json`, so retention groups do not split when formats change. One local `fcntl` lock serializes all work for each repository.

The current 7 daily, 4 weekly, and 12 monthly policy is preserved. Forget is weekly, prune is monthly, structure checks are weekly, and data checks rotate through deterministic `n/t` subsets. Existing production repositories remain format v1.

Tests use only temporary local repositories. This change does not open or lock the OneDrive repositories.

The rclone config is different from a normal static secret because rclone updates its OAuth token. Ansible seeds `/var/lib/evanovation-db/rclone/rclone.conf` only when absent, sets mode 0600, and leaves it alone afterward. Reconnect and escrow refresh are documented for the later production move.

### Secrets and Compose

Git stores only `op://` references. The Ansible controller resolves them with `no_log`, writes required files as mode 0600, and never puts values in a release folder.

Postgres uses `POSTGRES_PASSWORD_FILE`. Redis uses a private mounted config. Dragonfly uses a private mounted flag file and `--flagfile`; this avoids putting `--requirepass` in the Compose command. The HTTP bridge uses a private environment file because its image expects `SRH_TOKEN` and `SRH_CONNECTION_STRING` in the environment.

Compose templates are separate for Postgres, Redis, and Dragonfly. They preserve the existing project names, paths, resources, network, and Traefik TCP TLS/SNI labels. The four current Redis instances render as Redis. Traefik behavior is copied, not redesigned.

### Native database routing

Traefik is the only service that publishes host ports 5432 and 6379. Postgres, PgBouncer, Redis, and Dragonfly stay internal.

```text
<instance>.postgres-montreal-01.storage.evanovation.com:5432
  -> Traefik TLS HostSNI
  -> <instance>-pgbouncer-1:5432

<instance>.kv-montreal-01.storage.evanovation.com:6379
  -> Traefik TLS HostSNI
  -> <instance>-redis-1:6379
```

The instance-specific backend name is required. A shared `redis` alias is unsafe because all projects join the same external `traefik-net` network. Local tests run at least two instances through each shared port and prove that SNI reaches the correct seeded database.

### Serverless Redis HTTP

Every enabled KV HTTP sidecar runs in its instance's Compose project, connects to that instance's unique Redis or Dragonfly container name, and publishes container port 80 only on its configured `127.0.0.1:133xx` port. It has no Traefik labels.

Nginx Proxy Manager remains in host network mode and owns public ports 80 and 443:

```text
https://<instance>.kv-montreal-01.storage.evanovation.com
  -> Nginx Proxy Manager :443
  -> 127.0.0.1:133xx
  -> serverless-redis-http
  -> <instance>-redis-1:6379
```

The same KV hostname therefore supports native TLS on port 6379 and HTTPS on port 443. All 11 target instances are public initially. `http.public: false` keeps a sidecar local, and `http.enabled: false` removes it.

The `http` Ansible role reads desired routes from KV config. It uses the Nginx Proxy Manager API to list and plan routes. Write calls require the same explicit production apply guard as database deployment. The role never edits NPM's generated `data/nginx/proxy_host` files and never changes unrelated proxy hosts.

The target route set adds `oai-co-prod-02` and moves the two old `kv-na01.storage.evanovation.com` domains to matching `kv-montreal-01.storage.evanovation.com` domains during `move-prod`. This change only builds and tests that target state.

### Releases and Ansible

The `app` role installs a root-owned release at `/opt/evanovation-db/releases/<git-sha>` and changes `current` only after checks pass. The `base` role creates the service account and required directories. Other roles render only their own files.

The service account owns state and secret files and belongs to the Docker group. Documentation calls out that Docker access is root-equivalent.

Production is never the default target. Make and Ansible require an explicit target, and any production apply also requires an explicit production flag. The integration suite creates disposable local inventory and does not use `montreal-01`. HTTP route tests use a local HTTPS listener and NPM API fixtures or a pinned disposable NPM instance; they never call the production NPM API.

### Systemd jobs and status

Systemd has per-instance backup units plus status, restore, and maintenance units. Timers use `Persistent=true`, randomized delays, timeouts, and low CPU and I/O priority. They are installed disabled unless an enable flag is supplied.

`status` reads local state and reports backup, upload, snapshot, and restore times for every durable instance. It exits nonzero when a confirmed snapshot is older than about 26 hours or a restore test is older than 30 days. A later production change will connect these failures to the selected alert service.

### Test layers

Unit tests cover config, command execution, redaction, locks, files, manifests, Restic JSON, status, and engine command building.

Integration tests use disposable Postgres, Redis, and Dragonfly containers. They seed strings, sets, hashes, databases, tables, roles, and TTLs; then run backup and restore checks. They also cover stale files, partial folders, failed uploads, one-instance failure, generated Compose, Ansible, systemd units, shared Traefik ports, HTTP auth, NPM host routing, and cross-instance isolation.

CI runs Ruff, pytest, secret scanning, Compose validation, Ansible syntax checks, and the local integration suite where Docker is available.

## Risks / Trade-offs

- [Risk] The production facts can change while this system is being built. -> Keep the facts collection script read-only and require a fresh comparison in `move-prod`.
- [Risk] Docker group access gives the service account control of the host. -> Use a dedicated account, root-owned code, narrow writable paths, and document the trust boundary.
- [Risk] Full restore tests use CPU, memory, disk, and Docker. -> Limit temporary containers, run tests one at a time, and remove resources on every exit path.
- [Risk] Dragonfly has no cheap independent checker for every valid RDB. -> Use direct file checks daily and a real Dragonfly load for restore proof.
- [Risk] Local unuploaded backups can grow after a long remote failure. -> Keep them, stop before a free-space limit, and return a visible failure instead of deleting recovery data.
- [Risk] Preserved Postgres role hashes make `globals.sql` sensitive. -> Keep folders private, encrypt off-site copies with Restic, redact logs, and never include globals in test output.
- [Risk] A stale rclone token escrow might not rebuild a blank host. -> Keep the live file mutable and document reconnect plus manual escrow refresh before production migration.
- [Risk] Nginx Proxy Manager's API can change between releases. -> Record the current NPM version, test the exact API contract locally, and refuse route writes when the version is not approved.
- [Risk] Shared Docker aliases can send a KV route to the wrong instance. -> Use only instance-specific backend names and test two projects on the same network.
- [Trade-off] Standard-library-only production code means direct subprocess and JSON handling. -> Keep wrappers small and test command boundaries heavily.
- [Trade-off] Building deployment files before using them cannot prove all production details. -> Test disposable hosts now and require a separate production plan and fresh facts later.

## Migration Plan

This change has no production migration. Its completion path is:

1. Build the repository and config from read-only facts.
2. Pass unit, integration, config, Compose, Ansible, systemd, Traefik route, and serverless HTTP checks locally.
3. Build a versioned release and deploy it only to disposable local infrastructure.
4. Verify local backup and restore cycles for all three engines and a local Restic repository.
5. Finish operator docs, including a draft production move and rollback plan.
6. Create a separate `move-prod` change. Re-read production before that proposal is approved.

Rollback during this change is deleting disposable local resources. There is no production rollback because production is not changed.

## Open Questions

- Which alert service will receive production failures and missed-backup alerts? Decide in `move-prod`.
- What are the final 1Password item paths for each managed instance? Exact references must be present before production migration.
- Which pinned image digests should be approved after local restore tests? The build records and tests candidates; production use is approved later.

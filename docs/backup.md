# Backup operations

## Commands

```sh
evdb backup create app-prod-01/postgres
evdb backup create --all
evdb backup list app-prod-01/postgres
```

`backup create` runs on the authoritative host for one durable project/role. It reports a remote
recovery point only after engine validation, manifest hashing, and Restic upload all succeed.
Cache-mode KV has backups disabled. `backup create --all` processes every durable role sequentially,
continues after an individual failure, and exits nonzero if any role failed.

Each run starts in a private `<time>.partial` folder under
`/var/lib/evdb/backups/<project>/<role>`. Completion requires nonempty engine files, size and
SHA-256 checks, and a valid `backup.json`; only then is the folder renamed. The record includes
host, project, role, concrete engine, configured image, engine version, format, timestamps, purpose,
files, checks, hashes, and upload state.

Partial folders and files are root-owned mode `0700` and `0600`. After validation, completed
directories become mode `0500` and regular files mode `0400` under the owner of the configured rclone
file. Root-owned traverse-only backup ancestors let that user read a known completed path without
listing backup directories or reaching root-only database data. These modes prevent accidental writes;
because the completed tree is user-owned, they do not make it immutable against that owner.

Postgres stores globals and one custom archive for every connectable non-template database. Redis
waits for a successful new BGSAVE and checks the RDB. Dragonfly creates one uniquely named native
DFS generation, copies its summary and every numbered shard, and removes only those temporary
source files. Backup records contain bounded facts and hashes, not credentials.

## One host repository

`/etc/evdb/config.yml` defines one Restic repository for the host. All snapshots use host, project,
role, concrete engine, backup, and purpose tags. `evdb init` checks that repository and initializes a
missing format-v1 repository through rclone before enabling automatic backups. Restic creates an
absent remote path as part of initialization; no separate remote-directory command is required.
Every repository operation runs as the rclone configuration owner, and Restic launches
`/usr/bin/rclone` under that same identity. Manual rclone and evdb therefore share one mutable OAuth
configuration. Simultaneous manual and scheduled token refreshes can still race, but no second config
can diverge.

`backup list` merges valid local folders and matching tagged Restic snapshots in reverse chronological
order. Each item shows purpose, local and remote availability, backup ID, and snapshot ID. A
remote-only snapshot remains visible after local cleanup.

## Scheduling and limits

`evdb init` installs and automatically enables one persistent randomized daily timer. Its service runs
`evdb backup create --all` at low CPU and I/O priority, so databases added later are included without
new unit instances.

All but the two newest uploaded local backups are cleaned up. Incomplete and failed-upload local
backups remain available for diagnosis. Remote snapshots are never deleted by evdb v1 and therefore
grow indefinitely.

Recovery uses `backup list`, Restic, and the matching Postgres, Redis, or Dragonfly tools manually.
Backup manifests retain the engine, image, file, size, and hash information needed for that work.

Backup output replaces exact managed credential values and their required encoded forms. Repository
URLs, rclone remote names, local paths, image references, backup IDs, snapshot IDs, and unrelated
Restic output remain visible.

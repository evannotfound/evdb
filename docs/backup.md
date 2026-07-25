# Backup operations

## Operator workflow

```sh
evdb backup app-prod-01
evdb backups app-prod-01
evdb backup-check app-prod-01
evdb backup-check app-prod-01 SNAPSHOT_ID
```

`backup` runs the checked backup and Restic upload path on the managed host. It reports the
completed backup time and exact snapshot ID only after dump validation, manifest hashing, and
upload succeed. Cache-mode KV databases reject backups because durability is disabled.

`backups` merges completed local folders and tagged Restic snapshots in reverse chronological
order. Each row shows local, remote, or combined source, time, backup ID, snapshot ID, and that
backup's latest full verification state. Verification records remain attached to older backups
when later backups are checked. A remote-only snapshot remains visible after local retention.

`backup-check` defaults to the newest restorable backup for that typed database. An optional
argument selects an exact backup folder ID or Restic snapshot ID from `backups`. Selection rejects
missing, ambiguous, cross-host, and cross-database snapshots.

## Verification

Every backup first uses a private `<utc-time>.partial` directory and becomes complete only after
engine checks, sizes, SHA-256 hashes, and `backup.json` are valid. Postgres stores globals and one
custom archive per connectable non-template database. Redis and Dragonfly store `dump.rdb`.

Full backup-check verifies exact host and typed database identity plus manifest hashes, then
restores into an isolated compatible engine container with no published ports or live data mounts.
The locked current image may differ by patch tag or digest from the backup image, but the engine
must match and its major version cannot be older than the backup's. Postgres checks restored
databases and catalog facts. Redis and Dragonfly compare database and key counts, key types,
sampled value hashes, and TTL behavior without persisting raw values.
Temporary containers and download staging are removed after success, failure, timeout, or
interruption. Live data is never changed.

If upload fails after a checked local backup, the local folder remains and failure is recorded.
The latest local completion, last successful upload, last successful verification, per-backup
verification records, and current operation error are independent. A current upload failure does
not make a still-recent prior successful snapshot stale. A failed scheduled database does not stop
other eligible databases. Repository and instance locks serialize conflicting work.

Backup command output, history, manifests, state, and structured logs are secret-free. Treat local
backup folders as sensitive database contents even though they contain no controller credentials.

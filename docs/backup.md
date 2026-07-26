# Backup operations

## Commands

```sh
evdb backup create app-prod-01/postgres
evdb backup list app-prod-01/postgres
evdb backup test app-prod-01/postgres latest
evdb backup retention app-prod-01/postgres
evdb backup prune postgres
evdb backup repository-check postgres --rotate
```

`backup create` runs on the authoritative host for one durable project/role. It reports a remote
recovery point only after engine validation, manifest hashing, and Restic upload all succeed.
Cache-mode KV rejects backup creation.

Each run starts in a private `<time>.partial` folder under
`/var/lib/evdb/backups/<project>/<role>`. Completion requires nonempty engine files, size and
SHA-256 checks, and a valid `backup.json`; only then is the folder renamed. The record includes
host, project, role, concrete engine, source and locked image, engine version, format, timestamps,
purpose, content facts, files, checks, and upload result.

Postgres stores globals and one custom archive for every connectable non-template database. Redis
waits for a successful new BGSAVE and checks the RDB. Dragonfly creates one uniquely named native
DFS generation, copies its summary and every numbered shard, and removes only those temporary
source files. Backup records contain bounded facts and hashes, not raw sampled values or
credentials.

## History and testing

`backup list` merges local folders and stable Restic snapshots in reverse time order. Every row
shows purpose, local and remote availability, snapshot identity, and that exact backup's latest
verification. A remote-only backup remains visible after local retention.

`backup test` selects an exact local backup or Restic snapshot, verifies its identity and hashes,
and restores it into an isolated same-engine container with no live mounts, routes, aliases, or
published ports. Postgres checks restored catalogs. Redis and Dragonfly compare database and key
counts, key types, sampled hashes, and TTL behavior. Temporary resources are removed after success,
failure, timeout, or interruption; live data never changes.

## Retention and failures

The policy defaults to 7 daily, 4 weekly, and 12 monthly snapshots per durable project/role.
Retention runs without prune; prune is a separate monthly repository operation. Repository checks
rotate through configured data subsets. All grouping uses stable host/project/role tags rather than
mutable image metadata.

At least two newest uploaded local backups are retained, and unuploaded backups are never deleted
automatically. A failed new upload remains separate from the most recent confirmed snapshot. A
failed backup test remains attached to its selected backup. One role's failure does not overwrite
another role's result or stop independent scheduled work.

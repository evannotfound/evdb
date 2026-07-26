# Live restore

## One checked operation

Review history and select an exact local backup, Restic snapshot, or `latest`:

```sh
evdb backup list app-prod-01/postgres
evdb restore app-prod-01/postgres latest
evdb restore app-prod-01/postgres SNAPSHOT_ID
```

The restore command performs the complete recovery workflow. There is no persistent operator-owned
candidate and no second command to activate data.

1. Validate exact host, project, role, concrete engine, format, sizes, and hashes.
2. Materialize data in a private candidate beside the live data directory.
3. Start an isolated compatible engine with no live mounts, routes, aliases, or public ports.
4. Run engine and content checks and remove the verification container.
5. Create, check, and upload a safety backup of current live data.
6. Show the selected source, safety snapshot, live and candidate paths, and expected outage.
7. Require final confirmation immediately before service stop.
8. Exchange verified and live directories atomically, restart installed Compose, and require health.

The candidate and live parent must use the same filesystem. Live data is moved to a protected prior
path before the verified candidate is renamed into the canonical path. Successful health and a
confirmed safety snapshot are both required before prior local data is removed.

## Compatibility

Restore accepts only the same host/project/role and concrete engine. A supported same-major patch
image may verify a backup. A newer-major backup cannot be opened by an older major, and every
engine-major migration or Redis/Dragonfly conversion is rejected before candidate creation.

Postgres restores globals before databases, creates non-default databases from `template0`, treats
archive errors as fatal, and checks every restored database and expected object count. Redis and
Dragonfly requires the complete native DFS summary and shard set to load and compares keyspace,
types, hashes, and TTL behavior.

## Automatic recovery

If restored data cannot start or pass service and engine health, evdb stops the selected role,
moves failed restored data aside, returns prior data atomically, restarts installed Compose, and
verifies prior health without asking another question. This automatic recovery is safe for broken
SSH sessions and unattended callers.

If recovery also fails, neither prior nor failed data is deleted. The error identifies both
protected paths and the transaction record for manual intervention. A safety-upload failure or a
declined final confirmation leaves live service and data unchanged.

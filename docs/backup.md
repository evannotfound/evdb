# Backups and status

Each backup operates on one named instance under an instance lock. It writes to a private
`<utc-time>.partial` directory and renames that directory only after engine checks, sizes,
SHA-256 hashes, and `backup.json` are complete.

Postgres backups contain `globals.sql` and one custom archive under `databases/` for every
connectable non-template database. Redis and Dragonfly backups contain `dump.rdb`.
`backup.json` records host, group, instance, engine, times, version, engine facts, files,
checks, and upload state. Treat the entire folder as sensitive.

Restic receives only complete, checked folders. A successful upload requires a zero exit
code and a final JSON summary with `snapshot_id`. Snapshots use stable `host:`, `engine:`,
and `instance:` tags. Work for each repository is serialized by a local lock.

The configured policy retains 7 daily, 4 weekly, and 12 monthly snapshots per instance.
Weekly maintenance applies `forget` and a repository structure check; monthly maintenance
runs prune. Weekly maintenance also selects a deterministic rotating data subset. A specific
subset can be run with `maintain check --part N`.
Production repositories remain format v1.

Local history keeps at least two uploaded backups and never automatically removes an
unuploaded backup. Low free space stops a new run. A failed instance job does not prevent
another instance's independent job from running.

`evanovation-db status` reports local backup, confirmed upload and snapshot, restore result,
and current failure state. A snapshot older than about 26 hours or restore proof older than
30 days is stale and makes status exit nonzero. Journald receives concise structured command
records; credentials are redacted.

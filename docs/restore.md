# Restore checks

Restore checks accept a complete local backup or an explicit Restic snapshot whose manifest
sizes and hashes still match. Snapshot files are staged privately and removed after the test.
They refuse configured live data paths and use temporary Docker names and storage without
Traefik labels or published host ports. Temporary resources are removed after success,
failure, timeout, or interruption.

Postgres restore loads globals first, creates databases from `template0`, restores custom
archives with fatal errors, then checks connections and recorded catalog objects using the
matching Postgres image.

Redis restore starts the matching Redis image from `dump.rdb`. Dragonfly restore starts the
recorded Dragonfly image because `redis-check-rdb` cannot validate every Dragonfly-private
record. Both checks compare database and key counts, key types, selected value hashes, and
TTL behavior without recording raw values.

Run a named check or select the most overdue durable instance:

```sh
uv run evanovation-db restore-check postgres vercount-prod-01
uv run evanovation-db restore-check kv vercount-prod-01
uv run evanovation-db restore-check postgres vercount-prod-01 --snapshot SNAPSHOT_ID
uv run evanovation-db restore-due
uv run evanovation-db restore-due --run
```

These checks prove that a specific backup loads and matches recorded facts. They do not
provide point-in-time recovery, replication, failover, application-level validation, or a
production cutover. A successful local test also does not validate production DNS, TLS,
storage performance, or available host capacity.

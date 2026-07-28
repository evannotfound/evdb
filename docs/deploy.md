# Direct database operations

## One role at a time

Database commands operate on one `project/role` at a time:

```sh
evdb database list
evdb database add app-prod-01 postgres
evdb database add app-prod-01 kv
evdb database add cache-prod-01 kv --engine redis
evdb database info app-prod-01/postgres
evdb database configure app-prod-01/postgres --max-clients 200
evdb database start app-prod-01/postgres
evdb database stop app-prod-01/postgres
evdb database restart app-prod-01/postgres
evdb database logs app-prod-01/postgres --lines 200
```

`database add` validates the identity, writes explicit role settings to `config.yml`, generates or
securely accepts credentials in `secrets.yml`, renders role-local files, runs Compose, and waits for
native health. KV defaults to durable Dragonfly with HTTP enabled; `--engine redis` selects Redis.
An existing matching role is not duplicated.

`database configure` validates the complete source, atomically writes the new values, renders that role
once, runs Compose once, and waits for health. Explicit direct commands execute immediately. The guided
interface shows changed values and asks once before Create or Save.

## Failure and retry

A failed creation or settings health check leaves the readable source and generated files in place and
reports the concrete failing operation. Correct `config.yml` or `secrets.yml`, then run
`evdb database start PROJECT/ROLE`; start rerenders from the current source before retrying Compose and
native health.

## Lifecycle behavior

Start, stop, and restart address only the selected `project/role` Compose project. They preserve
source, generated files, credentials, data, and backup history. Start and restart rerender from the
current source and wait for container and engine health. Routine commands do not pull a changed image;
image changes are explicit settings edits.

Logs are bounded. Exact managed credential values and required encoded forms are replaced, while the
original unrelated log text, paths, image references, and service identifiers remain visible.

Role removal, password rotation, major-version planning, and Redis/Dragonfly conversion are outside
the v1 command surface.

# Host-owned credentials

## Private files

Credentials are generated and read on the authoritative host. Source YAML, machine-owned state,
generated Compose, backup records, status, activity, logs, and errors remain secret-free.

Database files live under `/etc/evdb/secrets/<project>/<role>` with mode `0600`. Postgres receives a
password file and PgBouncer users file. Redis receives a private configuration containing its
password. Dragonfly receives private flags. HTTP-enabled KV receives a token and environment file.
Restic and DNS provider credentials are separate private host files. Mutable rclone configuration
under `/var/lib/evdb/rclone` is seeded only when absent so refreshed OAuth state survives setup and
tool updates.

Credential values never belong in command arguments. Subprocess errors and bounded logs redact
known password, token, environment, and URL values.

Postgres creation may instead read the initial `default` user password from `--password-file` or a
masked guided prompt. Password files must be regular, non-symlink files with mode `0600` or stricter;
the supplied value is validated before staging and is never retained in source YAML, machine state,
activity, previews, result output, or generated Compose. Supplying a different password for an
existing role is rejected because rotation remains a separate operation.

PgBouncer runs as the numeric owner of evdb's managed configuration and secret roots. This lets the
unprivileged sidecar read its `0640` configuration and `0600` userlist without widening file modes,
running as root, or copying credentials into environment variables.

## Deliberate terminal output

```sh
evdb database info app-prod-01/postgres
evdb database info app-prod-01/kv
```

`database info` deliberately prints complete credentials only to a terminal. Postgres includes a
percent-encoded `postgresql://` URL with TLS requirements. KV includes a percent-encoded
`rediss://` URL and, when HTTP is enabled, its intended HTTPS endpoint and current token. Missing
or unsafe files cause the command to fail without partial credential output. The command has no
JSON mode.

All machine-readable output, especially `evdb status --json`, excludes passwords, tokens,
credential-bearing URLs, secret paths with content, and DNS provider values.

## Deferred recovery policy

Credential import for an existing host, rotation, external export or escrow, and total-host-loss
password recovery are not general evdb workflows. They require separate operational design and the
approved production migration. Local setup never treats checked-in references or development
fixtures as production credentials.

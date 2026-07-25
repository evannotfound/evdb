# 1Password and secret output

## Controller authentication

The complete controller workflow requires 1Password CLI authenticated through either:

- A desktop-authenticated account that can read and write the configured vault.
- A service account with `write_items` for that vault.

1Password Connect supports the controller's `op read` credential path, so read-only credential
resolution such as `evdb show` can use it. Connect cannot create or edit items. `evdb create` and
any credential-creation path preflight write access and reject Connect-only authentication before
changing source or the remote host. Use desktop authentication or a write-capable service account
for normal operator work.

The host config stores only vault and system item names. Database item names are derived as
`<name>-postgres` for Postgres and `<name>-kv` for Redis or Dragonfly. Every item has a concealed
`password`; HTTP-enabled KV items also have a concealed `http-token`. The system item supplies
concealed `restic-password` and `rclone-config` fields.

Create is idempotent. Existing concealed values are reused and never rotated. Missing fields are
generated and passed to `op item create` or `op item edit` through a JSON stdin template, never
through process arguments.

## Deliberate show output

**`evdb show <database>` always prints complete current credentials to the terminal on every
successful run.** Postgres output includes a complete percent-encoded `postgresql://` URL. Redis
and Dragonfly output includes a complete `rediss://` URL and, when HTTP is enabled, the current
HTTP token. Treat terminal output, scrollback, recordings, and transcripts accordingly.

`show` obtains non-secret deployment facts over SSH, validates the complete response, then reads
credentials directly from local 1Password. Credential values are never requested from the host or
sent over SSH by `show`. If any required field is unavailable, it exits nonzero without printing a
partial URL, endpoint, or token.

No other command output displays credentials. Source YAML, generated locks, normalized runtime
JSON, release files and manifests, state, backup and release histories, structured logs, errors,
SSH arguments, and remote protocol output remain credential-free. Protected runtime files needed
by services are separate from releases and are never normal command output.

## Protected host files

Confirmed apply resolves deployment values locally and transfers protected content through stdin
with redaction. The host writes mode `0600` service files under `/etc/evanovation-db/secrets` and
the mutable rclone config under `/var/lib/evanovation-db/rclone`.

Postgres receives a password file through `POSTGRES_PASSWORD_FILE`; PgBouncer uses a private users
file. Redis reads a private config with `requirepass`. Dragonfly reads a private flag file. HTTP
sidecars use a private environment file. The rclone config is seeded only when absent so refreshed
OAuth state survives apply.

Never put resolved values in Git, source or lock files, tests, tickets, Compose JSON, release
directories, or command arguments.

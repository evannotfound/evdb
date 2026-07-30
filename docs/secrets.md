# Host-owned credentials

## Private files

Credentials are generated and read on the authoritative host. `/etc/evdb/secrets.yml` contains the
Restic password, DNS values, database passwords, and HTTP tokens under matching host or project/role
keys. It is `root:root` mode `0600` and never appears in status output.

Generated private files live with each role under `/var/lib/evdb/projects/<project>/<role>`. Postgres
receives a password file and PgBouncer users file. Redis receives private native configuration,
Dragonfly receives private flags, and HTTP-enabled KV receives a token and environment file. Generated
Compose references private paths without embedding credential content.

The native rclone configuration remains at the private root-owned path recorded in
`host.backup.rclone_config`. evdb passes that path to Restic and never copies, replaces, chowns, or
regenerates it. Direct and scheduled commands both run as root, so OAuth refreshes retain one owner.

Credential values never belong in command arguments. Postgres creation may read the initial `default`
user password from `--password-file` or a masked guided prompt. Password files must be regular,
non-symlink files with mode `0600` or stricter; the supplied value is validated before source or
services change and never belongs in process arguments or output. Supplying a different password for
an existing role is rejected because rotation is outside v1.

First host initialization follows the same boundary for Restic. Direct init accepts
`--restic-password-file PATH`; the private, non-symlinked regular file must contain one non-empty UTF-8
line, with one trailing LF allowed. Guided init uses a masked prompt and generates a password when it
is left blank. There is no inline or environment input, and neither supplied nor generated values are
printed. Configured init preserves the existing `secrets.yml`; a replacement password-file option is
ignored without reading the file.

PgBouncer keeps the image's unprivileged identity and receives the generated files' numeric host group
as a supplementary group. Root-owned role directories remain private, generated files are mode
`0640`, mounts are read-only, and credentials are not embedded in environment values.

## Deliberate terminal output

```sh
evdb database info app-prod-01/postgres
evdb database info app-prod-01/kv
```

`database info` deliberately prints complete credentials only to a terminal. Postgres includes a
percent-encoded `postgresql://` URL with TLS requirements. KV includes a percent-encoded `rediss://`
URL and, when HTTP is enabled, its intended HTTPS endpoint and current token. Missing or unsafe files
cause the command to fail without partial credential output. The command has no JSON mode.

All machine-readable output, especially `evdb status --json`, excludes database, HTTP, DNS, Restic,
and rclone credentials.

## Exact redaction boundary

Displayed errors, logs, and subprocess output replace only exact managed password and token values and
their required encoded forms. Repository URLs, rclone remote names, paths, image references, backup
IDs, snapshot IDs, and unrelated stdout or stderr remain visible. This narrow boundary preserves the
operational identifiers needed to diagnose a failed command.

Credential import for an existing host, rotation, and external export or escrow require separate
operational design. Development fixtures and checked-in references are never production credentials.

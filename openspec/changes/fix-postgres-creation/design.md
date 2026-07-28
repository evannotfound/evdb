## Context

The released Postgres definition bind-mounts `pgbouncer.ini` with mode `0640` and
`pgbouncer-users` with mode `0600`. Both are owned by the host `evdb` service identity, while
`edoburu/pgbouncer:v1.25.1-p0` declares `USER postgres`. On `toronto-01` the primary Postgres
container became healthy, but PgBouncer repeatedly exited with `Permission denied` while opening its
configuration. The health gate therefore failed the initial add.

Creation currently generates a token-safe random password inside database staging. The domain layer
has a test-only generator seam, but the CLI has no secure operator input. Postgres username and
database remain fixed as `default` and `postgres`, and PgBouncer intentionally serves that single
managed login.

Development releases are built natively, but the workspace is ARM64 and the disposable VPS is
x86_64. Copying a local PyInstaller binary is therefore not viable. The production release boundary
must remain intact: installed hosts do not gain Python or uv prerequisites merely because a disposable
test host can run a source checkout.

## Goals / Non-Goals

**Goals:**

- Make the default PgBouncer sidecar healthy without root or relaxed host secret permissions.
- Support a generated or securely supplied initial password for the fixed Postgres login.
- Preserve secret-free arguments, Compose, previews, state, activity, status, and logs.
- Exercise the actual pinned Postgres and PgBouncer images in disposable integration coverage.
- Provide a short source-to-test-VPS loop that does not require tags or releases.

**Non-Goals:**

- No password rotation for an initialized database.
- No custom Postgres username or default database, multi-user PgBouncer authentication, external dump
  import command, or general migration workflow.
- No automatic recovery of transactions abandoned by process termination.
- No database timer reconciliation, rclone configuration validation, or repair of unrelated host
  configuration.
- No source-based production install or relaxation of exact-version host updates.

## Decisions

### Run PgBouncer as the managed-file owner

Set the generated PgBouncer service `user` to the numeric UID:GID that owns evdb's managed project
and secret roots. The service remains non-root and can read the two bind-mounted files without changing
their `0640` and `0600` host modes. The generated runtime identity participates in the existing
service contract hash.

The canonical host setup guarantees that project and secret roots belong to the evdb service account.
Alternate fixture paths use their actual owner, preserving disposable test behavior without requiring
a host `evdb` account.

Alternatives considered:

- World- or group-readable authentication files weaken the existing private-file contract.
- Chowning files to the image's internal `postgres` UID couples writable host files to image metadata
  and prevents the evdb service account from replacing an existing file safely.
- Compose `secrets` with a host file cannot solve ownership: Docker Compose silently ignores requested
  UID, GID, and mode for file-backed secrets because they are bind mounts.
- Running the container as root violates least privilege and PgBouncer's runtime expectations.

### Treat a supplied password as creation input, not a setting

Add an optional initial password to `database.prepare_add()` and carry it into the existing staged
`Credentials` and secret rendering path. Absence continues to use cryptographic generation. The
password is not source configuration and does not become a configurable Postgres setting.

For an existing matching role, compare a supplied password to the installed credential without
displaying either value. Equal input preserves idempotency; unequal input fails before staging because
changing the password file would not alter an initialized PostgreSQL role and would desynchronize
PgBouncer.

Alternative considered: add password to `database configure`. PostgreSQL consumes
`POSTGRES_PASSWORD_FILE` only during initial data-directory creation, so a file-only settings update
would be incorrect. Safe rotation needs a separate transaction that changes the live role and rolls
back both database and sidecar credentials.

### Use a password file for direct commands and a masked guided prompt

Add `--password-file PATH` only to `database add`. The CLI reads one value, removes one trailing line
ending, and rejects empty, NUL-containing, or multiline input. Guided Postgres add uses a masked
`getpass`-style prompt; blank input means generate. Preview and completion output report only whether
the credential was supplied or generated.

An inline `--password`, an environment variable, and an ordinary input prompt were rejected because
they expose the value through process inspection, shell history, inherited environment, or terminal
echo. Standard input was not selected for the first version because it conflicts with sudo and the
existing confirmation stream; callers can provide a private file or use guided input.

PgBouncer userlist rendering will follow PgBouncer quoting rules rather than JSON string escaping so
valid one-line passwords containing spaces, quotes, or backslashes authenticate correctly.

### Test the complete pooler authentication path

Add the pinned PgBouncer image to disposable container fixtures. A focused integration test starts
Postgres and the generated PgBouncer service with private files owned by the test identity, waits for
both services, and connects through PgBouncer using a supplied password containing representative
special characters. Unit tests retain fast coverage for generated Compose, modes, redaction,
idempotency, and invalid input.

### Keep source activation explicitly disposable

Document a one-time checkout under `/srv/evdb-dev`, uv installation, and `uv sync --locked`. Add a
guarded development helper or Make targets that require an explicit host, refuse `montreal-01`, and
rsync only `src/`, `tests/`, and named project metadata. They must not sync the whole workspace.

The fast path invokes `/srv/evdb-dev/.venv/bin/evdb` explicitly. End-to-end systemd testing may
temporarily redirect `/usr/local/bin/evdb` to that executable because units use the stable command;
`/opt/evdb/current` remains untouched and deactivation restores the canonical link. Host update is
forbidden while the temporary link is active because its stable-link invariant intentionally rejects
that state.

## Risks / Trade-offs

- [An arbitrary numeric UID may not satisfy an undocumented image filesystem assumption] -> Run the
  pinned image in integration and verify startup, readiness, and an authenticated query before remote
  validation.
- [Special-character passwords may parse differently in Postgres and PgBouncer] -> Define one-line
  input, implement PgBouncer-native quoting, and test the same exact value through both services.
- [A password file can remain on disk after creation] -> Document private file ownership and leave
  lifecycle control with the operator; evdb never copies its path or content to durable metadata.
- [A development activation can make host update fail] -> Preserve the release tree, make activation
  explicit and reversible, and reject update in documentation until the canonical link is restored.
- [The abandoned transaction on `toronto-01` blocks validation] -> Treat the approved test Postgres
  data as disposable and clean or recreate only that role before the end-to-end add.

## Migration Plan

1. Add unit and integration coverage that reproduces the PgBouncer permission failure.
2. Implement managed-owner runtime identity and secure initial-password staging.
3. Wire direct and guided CLI input, then update command and secret documentation.
4. Add and document the guarded disposable-VPS sync and activation flow.
5. Run local checks and the pinned container integration test.
6. Sync the source checkout to `toronto-01`, activate it for the test window, recreate the approved
   disposable Postgres role, and verify healthy authenticated access and transaction cleanup.
7. Restore `/usr/local/bin/evdb` to the installed release after validation.

Rollback is a normal code revert before release. On the disposable VPS, restore the canonical stable
link and recreate the test Postgres role with the prior released tool if necessary; no production host
or release tree is modified by this validation.

## Open Questions

None. Password rotation, arbitrary role migration, and durable abandoned-transaction recovery require
separate proposals.

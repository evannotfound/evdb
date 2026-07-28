## 1. Reproduce PgBouncer Creation

- [ ] 1.1 Add the pinned PgBouncer image digest to disposable container fixtures.
- [ ] 1.2 Add a focused integration test that starts Postgres and PgBouncer from generated private files and reproduces the current unprivileged file-access failure.
- [ ] 1.3 Add unit assertions for the PgBouncer runtime user, private bind mounts, service contract, and secret-free generated Compose.

## 2. Repair Private PgBouncer Access

- [ ] 2.1 Resolve the numeric owner of canonical managed project and secret roots, with actual-path ownership for alternate test configurations.
- [ ] 2.2 Run the generated PgBouncer service as that non-root UID:GID and include it in the existing service contract.
- [ ] 2.3 Preserve `0640` PgBouncer configuration and `0600` authentication-file modes without copying credential content into environment variables or Compose.
- [ ] 2.4 Update PgBouncer userlist rendering to use native quoting for one-line passwords containing spaces, quotes, and backslashes.
- [ ] 2.5 Make the pinned integration test pass an authenticated query through healthy PgBouncer using private managed files.

## 3. Add Initial Password Domain Support

- [ ] 3.1 Add shared validation for non-empty single-line database passwords and reject NUL, carriage return, and embedded line feed values.
- [ ] 3.2 Extend `database.prepare_add()` and staging to accept an optional initial Postgres password while preserving random generation by default.
- [ ] 3.3 Keep the supplied password only in staged `Credentials` and protected secret handling, never source configuration, state, activity, previews, or result dictionaries.
- [ ] 3.4 Preserve idempotency when an existing Postgres role receives the same supplied password and reject a different value as unsupported rotation before staging.
- [ ] 3.5 Add database and secret tests for generated input, supplied input, invalid values, same-password reruns, different-password rejection, and redaction.

## 4. Wire Secure Operator Input

- [ ] 4.1 Add `--password-file PATH` to `database add` for Postgres only and reject its use with KV creation.
- [ ] 4.2 Read one password value from a private file, remove one trailing line ending, validate it, and avoid retaining its path or content in operation output.
- [ ] 4.3 Add a masked guided Postgres password prompt where blank input selects generation and the credential is never echoed or redisplayed.
- [ ] 4.4 Thread supplied/generated credential state through the existing terminal presenter without showing the value.
- [ ] 4.5 Add parser, direct-command, guided-flow, TTY, invalid-file, cancellation, and secret-redaction tests.

## 5. Add the Disposable VPS Development Loop

- [ ] 5.1 Add a standard-library development helper that requires an explicit host and remote checkout path and invokes `ssh` and `rsync` with subprocess argument arrays.
- [ ] 5.2 Make sync transfer only `src/`, `tests/`, and named project metadata before running remote `uv sync --project PATH --locked`.
- [ ] 5.3 Refuse `montreal-01` before any subprocess call and add tests proving no copy, link, evdb, or host mutation is attempted.
- [ ] 5.4 Add explicit activation and deactivation commands that leave `/opt/evdb/current` untouched and restore `/usr/local/bin/evdb` to `/opt/evdb/current/bin/evdb`.
- [ ] 5.5 Document one-time uv/checkout setup, fast explicit-path execution, reversible systemd activation, sudo behavior, architecture independence, and the prohibition on host update while development activation is active.
- [ ] 5.6 Update README development links and command examples without making uv or Python a production prerequisite.

## 6. Validate Locally and on Toronto

- [ ] 6.1 Run `uv run ruff check src tests tools` and `uv run ruff format --check src tests tools`.
- [ ] 6.2 Run `uv run pytest tests/unit tests/config` and the targeted Postgres/PgBouncer integration test.
- [ ] 6.3 Build the PyInstaller executable and smoke-test `evdb --version` plus database-add argument parsing.
- [ ] 6.4 Bootstrap and sync the locked source checkout on `toronto-01` without copying credentials or replacing `/opt/evdb/current`.
- [ ] 6.5 Clean or recreate only the approved disposable `test-dev-01/postgres` role, activate the development command for the test window, and create Postgres with a supplied special-character password.
- [ ] 6.6 Verify primary and PgBouncer health, authenticated access through PgBouncer, secret-free output, matching-add idempotency, different-password rejection, and removal of the completed settings transaction.
- [ ] 6.7 Restore `/usr/local/bin/evdb` to `/opt/evdb/current/bin/evdb` and report the separate hostname, rclone remote, timer, and abandoned-transaction follow-ups without changing their scope.

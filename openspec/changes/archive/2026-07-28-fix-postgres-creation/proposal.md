## Why

Default Postgres creation cannot become healthy because the unprivileged PgBouncer container cannot
read evdb's private bind-mounted configuration and user file. Debugging this class of host-local
failure also currently requires publishing and installing a release, while migrations that retain an
application credential cannot choose evdb's initial managed password securely.

## What Changes

- Make generated PgBouncer services able to read their private evdb-owned files without weakening
  host secret permissions or exposing credentials through Compose, environment, logs, or arguments.
- Allow Postgres creation to use either a generated password or an operator-supplied initial password
  from a masked guided prompt or password file.
- Keep Postgres username `default` and database `postgres`; password rotation, arbitrary role import,
  and general external database migration remain out of scope.
- Add a disposable-VPS development workflow that syncs an explicit source checkout, uses a locked uv
  environment, and can temporarily run host commands without producing a GitHub Release.
- Add an integration test that starts the pinned Postgres and PgBouncer images with generated files
  and proves an authenticated connection through the healthy pooler.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deploy`: Postgres creation supports a generated or securely supplied initial password and requires
  the unprivileged PgBouncer service to become usable with private managed files.
- `operator-cli`: Direct and guided Postgres creation accept secure initial password input without a
  password-bearing command argument.
- `release-distribution`: Disposable test hosts may run an explicitly activated locked source checkout
  while production installation and updates remain standalone exact-version releases.

## Impact

- Affected code: `src/evdb/compose.py`, `src/evdb/secrets.py`, `src/evdb/database.py`,
  `src/evdb/cli.py`, and `src/evdb/interactive.py`.
- Affected tests: Compose, secrets, database transaction, CLI, guided-flow, and container integration
  coverage for PgBouncer and custom passwords.
- Development operations: a guarded sync/activation workflow and documentation for disposable VPSs;
  `/opt/evdb/current` remains the recoverable installed release.
- Security: managed secret files retain private modes, supplied passwords never appear in command
  arguments or machine output, and special-character handling is validated end to end.

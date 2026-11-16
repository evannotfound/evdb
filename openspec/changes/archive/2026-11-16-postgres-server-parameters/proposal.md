## Why

Some applications, including GlitchTip, require PostgreSQL startup settings such as
`max_locks_per_transaction=512`. evdb currently owns the generated PostgreSQL Compose
definition but has no supported way to persist or apply such server parameters.

## What Changes

- Add a generic, per-Postgres-role parameter map for PostgreSQL server settings.
- Render configured parameters as PostgreSQL `-c key=value` arguments while retaining evdb's fixed service contract.
- Validate parameter names and values sufficiently to keep the configuration typed, secret-free, and argument-array based.
- Expose parameter configuration through the existing direct database settings flow.
- Rerender and restart the affected role when parameters change; do not support replacing the full container command.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `config`: Postgres roles may persist validated server parameters.
- `deploy`: Generated Postgres Compose applies configured server parameters during startup.
- `operator-cli`: Direct Postgres configuration can add or replace individual parameters.

## Impact

- Affects Postgres source parsing, serialization, validation, generated Compose, and direct configuration.
- Existing roles without parameters retain their current generated Compose and behavior.
- No new runtime dependency or arbitrary shell/custom-command execution is introduced.

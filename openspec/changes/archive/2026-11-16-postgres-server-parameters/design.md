## Context

evdb stores each Postgres role as a typed `Postgres` model and renders an independent
Compose document from that model. The current renderer relies on the official image's
default command and has no source field for PostgreSQL server settings. GlitchTip
requires `max_locks_per_transaction=512` because its partitioned tables need more
PostgreSQL locks than the default.

Configuration is strict and source-owned, while lifecycle changes rerender Compose
before starting or restarting a role. The new setting therefore needs to survive
rerenders, upgrades, and retries rather than being a manual edit to generated YAML.

## Goals / Non-Goals

**Goals:**

- Persist arbitrary supported PostgreSQL GUC name/value pairs per Postgres role.
- Apply them as PostgreSQL `-c name=value` arguments in generated Compose.
- Configure or replace one parameter through the existing direct `database configure`
  command, with the normal validation, render, Compose, and health flow.
- Keep existing roles and generated Compose unchanged when no parameters are configured.

**Non-Goals:**

- Do not support replacing the complete container command or entrypoint.
- Do not add application-specific profiles such as a GlitchTip mode.
- Do not add parameters to KV engines or change PostgreSQL backup and upgrade policy.
- Do not expose secrets or accept shell syntax in parameter values.

## Decisions

### Store a parameter mapping on Postgres settings

Add an optional `parameters` mapping to the Postgres source role. The runtime model
will keep the mapping in deterministic key order so round trips and generated Compose
are stable. Empty mappings remain omitted from serialized source to preserve the
existing minimal configuration.

Parameter names must be valid PostgreSQL GUC names. Values are scalar text values;
numeric and boolean YAML scalars are normalized to their PostgreSQL text form. NUL
and line-break characters are rejected. Settings owned by evdb's connection contract,
including the server port and data/config file locations, are rejected rather than
allowing a parameter to invalidate routing or storage assumptions.

### Render only `-c` arguments

When parameters exist, the Postgres primary service receives a list containing one
`-c` and `name=value` pair per parameter. This matches the official Postgres image's
argument contract while preserving its entrypoint, initialization behavior, health
checks, mounts, and evdb-managed routing. No shell command is constructed.

### Use repeated direct CLI options

`database configure PROJECT/postgres --parameter NAME=VALUE` will add or replace one
parameter. Repeating the option updates several parameters in one operation. The
existing `--reset parameters` form clears the complete mapping. The database
configuration path will validate the candidate before writing source or invoking
Compose, then use the existing one-render/one-start/health operation.

## Risks / Trade-offs

- [A PostgreSQL parameter can still make the server fail to start] → Validate names,
  reject evdb-owned settings, and rely on the existing Compose and native health
  checks before reporting success.
- [Generic parameters could become an undocumented second configuration system] →
  Keep the map limited to PostgreSQL GUC arguments and explicitly exclude full
  commands, shell syntax, secrets, and application-specific behavior.
- [A parameter may be valid on one PostgreSQL major but not another] → Persist it
  with the role and let the normal startup/health path fail visibly after an image
  change; do not silently discard it during upgrades.

## Context

The host-local command now owns one human-readable `host.yml`, generated Compose files, and private
machine state under `/var/lib/evdb/state`. The config module has kept most safety checks close to the
source and state schemas, but it also retains older convenience paths and mutable-looking fields that
are not part of the documented source contract.

The biggest practical issue is not the number of dataclasses; those models match real source and state
objects used by compose, backup, restore, status, secrets, and the guided settings UI. The issue is
that parser-only validation and `require_valid()` have drifted apart. Programmatic construction during
initial setup can currently accept values that YAML loading would reject later.

## Goals / Non-Goals

**Goals:**

- Keep one clear validation contract for both loaded YAML and constructed config objects.
- Remove compatibility and future-oriented surfaces that are not current product behavior.
- Make machine state fail closed when writer-owned fields are absent or malformed.
- Preserve current safety behavior: duplicate-key rejection, old-schema rejection, secret-free source
  and state, stable HTTP ports, path derivation, image digest resolution, and orphan protection.
- Keep production Python dependency-light and PyInstaller-friendly.

**Non-Goals:**

- No production migration, state repair command, or live host mutation.
- No schema-library dependency, framework-style config layer, or module split solely for size.
- No database removal, engine migration, major-version migration, password rotation, or HTTP proxy
  change.
- No removal of documented source fields or generated/state fields that status, backup, restore, or
  deploy currently use.

## Decisions

### Keep dataclasses and direct validation

`Config`, `Host`, role settings, and state dataclasses remain plain frozen dataclasses. `validate()`
becomes the complete invariant check for values created by either YAML parsing or code. Parser helpers
remain responsible for raw input shape, required YAML fields, and default construction.

Alternative considered: replace the module with Pydantic or another schema library. That would reduce
some scalar type checks, but duplicate YAML-key handling, strict source/state separation, image source
rules, path overlap checks, route collisions, state compatibility, secret scanning, and port allocation
would all remain custom code. It also adds runtime packaging surface to the one-file executable.

### Treat fixed policy as fixed policy

Postgres username/database and command timeout values are used by runtime code but are not accepted in
source YAML or serialized back to `host.yml`. They will be represented as module constants rather than
mutable-looking model fields. This prevents programmatic changes from being silently dropped by
`dump()`.

Alternative considered: make them real source fields. That expands the human schema for no current
operator need and cuts against the concise host-local model.

### Make state schema strict and derived values derived

Machine state will require the fields that `state_dict()` writes for the current version. Missing
writer-owned fields indicate corrupt or stale state, not a normal defaulting path. `ImageState.major`
will be removed because it is derived from the image source and can drift from it; restore compatibility
will derive the major from `ImageState.source`.

Alternative considered: keep permissive loading for resilience. This makes partial JSON look valid and
can hide the exact state drift that write operations are expected to fail closed on.

### Remove undocumented compatibility shorthands

Source loading will accept the documented `host.yml` path and explicit mapping sections for role
overrides. Boolean YAML shorthands such as `http: false` and `pgbouncer: false`, directory source
loading, and `.yaml` aliases are removed from the supported surface. Guided and scriptable commands can
still set those booleans through typed settings and will persist explicit mappings.

Alternative considered: keep the conveniences because they are small. Small parser conveniences become
implicit public schema and add branches that tests and docs do not otherwise need.

## Risks / Trade-offs

- [Risk] A stale local test or operator script passes a directory or `.yaml` file to `load()`. -> Use
  the documented exact `host.yml` path in tests and CLI examples.
- [Risk] Existing partial state files become invalid. -> This change is pre-production for the new
  state schema; production migration remains separate and must seed valid current state.
- [Risk] Tightening validation exposes existing programmatic construction bugs. -> Add tests through
  host setup and direct `require_valid()` before changing persistence code.
- [Trade-off] Manual validation remains verbose. -> It keeps validation close to project-specific
  invariants without introducing another abstraction layer.

## Migration Plan

Implementation happens locally only. Existing valid documented `host.yml` files continue to load. Any
machine state written by older local development versions should be regenerated or removed during local
testing; the separate production migration must seed a valid state file for production adoption.

Rollback during implementation is a source revert before production adoption.

## Open Questions

None.

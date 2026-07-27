## Why

`src/evdb/config.py` has a few compatibility and future-oriented surfaces that do not match the
current host-local product. More importantly, parsed YAML and programmatically constructed config do
not share one complete validation contract, so initial setup can accept values that a later reload
rejects.

## What Changes

- Make configuration validation the complete authority for parsed and constructed `Config` values,
  including host routing, backup repositories, retention, role/settings shape, and image-major rules.
- **BREAKING** Tighten machine-owned state parsing for the current schema instead of synthesizing
  missing writer-owned fields from partial JSON.
- Remove redundant image-major state and derive compatibility from the configured source image.
- Remove fixed policy from mutable models where it is not source-owned, including Postgres user/name
  and operation timeout maps.
- Remove dead or undocumented compatibility paths: the unused config writer helper, unused directory
  constants, boolean YAML role shorthands, and directory/`.yaml` source loading shortcuts.
- Keep the existing one-file host schema, old-schema rejection, duplicate-key YAML loader, stable port
  allocation, path derivation, operation state, and no-new-dependency approach.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `config`: Validation, machine-state schema strictness, and source parsing semantics become simpler
  and stricter while preserving the current host-local configuration model.

## Impact

- Affects `src/evdb/config.py`, image/state consumers, restore compatibility checks, host setup
  validation, tests, and config documentation if examples mention removed shorthand behavior.
- Existing valid `host.yml` files remain valid when they use documented mapping syntax and exact
  `host.yml` paths.
- Existing partial or stale machine state that omits current writer-owned fields is rejected instead of
  repaired implicitly; production migration remains a separate OpenSpec change.
- No runtime dependency is added.

## 1. Config Model Cleanup

- [x] 1.1 Remove unused config constants and the unused standalone `write_config()` helper.
- [x] 1.2 Replace mutable-looking Postgres user/database fields and host timeout maps with fixed runtime constants.
- [x] 1.3 Remove undocumented source-loading compatibility for directory inputs, `.yaml` aliases, and boolean role-section shorthands.

## 2. Validation Contract

- [x] 2.1 Make `validate()` cover host routing, backup repository shape, retention values, HTTP port range, role/settings type shape, image sources, image majors, and role-specific settings.
- [x] 2.2 Keep parser helpers focused on raw YAML shape, defaults, and type conversion without duplicating full invariant checks.
- [x] 2.3 Narrow secret scanning so source and state reject secret values and open operation data without rejecting valid schema keys or project IDs by substring.

## 3. Machine State

- [x] 3.1 Remove stored image major state and derive engine majors from image sources at restore compatibility checks.
- [x] 3.2 Tighten current-version machine-state loading to require all writer-owned fields emitted by `state_dict()`.
- [x] 3.3 Keep stable HTTP port allocation, compose hashes, installed flags, tool version, and operation records intact.

## 4. Tests and Validation

- [x] 4.1 Update unit tests and fixtures for exact `host.yml` loading, strict state schema, fixed runtime defaults, and unified validation.
- [x] 4.2 Run OpenSpec validation for `simplify-config-core`.
- [x] 4.3 Run focused unit tests for config, host setup, database transactions, restore compatibility, compose, backup, and status.
- [x] 4.4 Run Ruff and formatting checks.

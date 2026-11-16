## ADDED Requirements

### Requirement: Postgres server parameters
Each Postgres role MAY persist a `parameters` mapping of PostgreSQL GUC names to
scalar values. evdb SHALL normalize accepted scalar values to deterministic text,
reject invalid names, NUL or line-break characters, and settings owned by evdb's
storage or network contract. An empty mapping SHALL be omitted when the source is
serialized.

#### Scenario: GlitchTip lock setting round trips
- **WHEN** a Postgres role contains `parameters.max_locks_per_transaction: 512`
- **THEN** loading and dumping the source retains the parameter and its normalized
  value is available to the Postgres renderer

#### Scenario: Invalid server parameter is rejected
- **WHEN** a Postgres role contains an invalid parameter name or a value containing
  a NUL or line break
- **THEN** configuration validation fails before source or generated files change

#### Scenario: Existing role has no parameters
- **WHEN** an existing Postgres role has no `parameters` field
- **THEN** it loads with an empty parameter mapping and its serialized source remains
  compatible with the previous minimal role shape

## ADDED Requirements

### Requirement: Direct Postgres parameter configuration
`evdb database configure PROJECT/postgres` SHALL accept repeatable
`--parameter NAME=VALUE` options. Each option SHALL add or replace one parameter in
the role's mapping, and `--reset parameters` SHALL clear the complete mapping. The
command SHALL use the existing atomic source write, Compose convergence, and health
behavior.

#### Scenario: Configure the GlitchTip setting
- **WHEN** an operator runs `database configure glitchtip-prod-01/postgres
  --parameter max_locks_per_transaction=512`
- **THEN** the role source records the parameter and the restarted service becomes
  healthy with that setting rendered

#### Scenario: Configure several parameters together
- **WHEN** an operator supplies multiple `--parameter NAME=VALUE` options
- **THEN** evdb validates and applies them in one configuration operation

#### Scenario: Reject a malformed option
- **WHEN** an operator supplies `--parameter` without a valid `NAME=VALUE` pair or
  targets a KV role
- **THEN** the command fails before source, generated files, or services change

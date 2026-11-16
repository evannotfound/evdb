## ADDED Requirements

### Requirement: Render Postgres server parameters
Generated Postgres Compose SHALL pass each configured server parameter to the primary
Postgres container as an argument pair `-c` and `name=value`. evdb SHALL preserve the
official image entrypoint and SHALL NOT accept a complete custom command override.

#### Scenario: Render max locks for GlitchTip
- **WHEN** a Postgres role configures `max_locks_per_transaction` as `512`
- **THEN** its primary service command contains `-c` and
  `max_locks_per_transaction=512`

#### Scenario: Parameter changes follow normal lifecycle convergence
- **WHEN** an operator changes a role's parameters and configures that role
- **THEN** evdb writes the validated source, rerenders that role, starts the generated
  Compose project, and waits for the existing native health check

#### Scenario: Unconfigured Postgres remains unchanged
- **WHEN** a Postgres role has no server parameters
- **THEN** its generated primary service has no additional command arguments

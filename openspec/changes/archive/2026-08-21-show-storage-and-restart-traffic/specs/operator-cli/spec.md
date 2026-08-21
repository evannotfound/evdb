## ADDED Requirements

### Requirement: Guided host actions
Selecting Host from the guided root SHALL show mount-aware state and database-root storage, routing
infrastructure, repository, timer, and current host errors followed by Restart traffic and Back actions.
Restart traffic SHALL warn that active database connections may briefly drop and require confirmation
before changing Traefik runtime state.

#### Scenario: Operator reviews custom storage
- **WHEN** the host configures several database roots
- **THEN** the Host view shows every exact path, backing mount and source, capacity, and assigned roles

#### Scenario: Operator declines traffic restart
- **WHEN** the operator selects Restart traffic and declines confirmation
- **THEN** Traefik and every database remain unchanged

#### Scenario: Operator confirms traffic restart
- **WHEN** the operator confirms Restart traffic
- **THEN** the Host view reports completion only after the dedicated Traefik container becomes healthy

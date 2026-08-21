## ADDED Requirements

### Requirement: Dedicated traffic proxy restart
The guided Host interface SHALL provide a confirmed Restart traffic operation that takes the exclusive
host operation lock, restarts only the installed Traefik Compose service, and waits for the owned Traefik
container to become healthy within the host health timeout. It SHALL preserve source, generated files,
database services, database data, credentials, and certificate state and SHALL NOT rerun initialization.

#### Scenario: Traffic restart succeeds
- **WHEN** the operator confirms Restart traffic and Traefik becomes healthy
- **THEN** evdb reports completion without restarting any database Compose project

#### Scenario: Traffic restart remains unhealthy
- **WHEN** Traefik does not become healthy before the timeout
- **THEN** evdb reports the failed host operation and does not claim that traffic restart completed

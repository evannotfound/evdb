## MODIFIED Requirements

### Requirement: Complete Postgres connection details
The Postgres information view SHALL show hostname, port, configured username, configured database name,
TLS requirement, and a complete percent-encoded `postgresql://` URL containing the current host-owned
password. The hostname SHALL be the project hostname `<project>.<host-id>.<base-domain>`.

#### Scenario: Default Postgres info is shown
- **WHEN** the operator requests an installed Postgres role created with defaults
- **THEN** the URL uses username `default`, database `postgres`, its project SNI hostname, port 5432, and required TLS

#### Scenario: Custom Postgres info is shown
- **WHEN** the operator requests a role created with username `app user` and database name `app/data`
- **THEN** the displayed fields preserve those values and the URL percent-encodes them without changing the credential identity

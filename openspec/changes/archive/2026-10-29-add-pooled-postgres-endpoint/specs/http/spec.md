## MODIFIED Requirements

### Requirement: Complete Postgres connection details
The Postgres information and guided connection views SHALL show username, database name, and complete percent-encoded `postgresql://` URLs containing the current host-owned password, hostname, preferred configured Postgres port, and TLS requirement. The direct URL SHALL use `<project>.<host-id>.<base-domain>` and be labeled for migrations and session-dependent work. When PgBouncer is enabled, the views SHALL show an additional `<project>-pooled.<host-id>.<base-domain>` URL first, labeled as transaction-pooled application access. Both URLs SHALL use the same credentials, database, port, and TLS parameters.

#### Scenario: Postgres info is shown
- **WHEN** the operator requests an installed Postgres role with PgBouncer enabled
- **THEN** CLI info and guided connection details show the pooled application URL first and the direct migration/session URL second, both requiring TLS

#### Scenario: PgBouncer is disabled
- **WHEN** the operator requests Postgres connection details with PgBouncer disabled
- **THEN** the views show the direct URL and no pooled URL

#### Scenario: Custom identity and port are used
- **WHEN** a Postgres role uses URL punctuation in its identity or password and a non-default preferred native port
- **THEN** both URLs decode to the configured identity and password and use the preferred port

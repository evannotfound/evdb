## MODIFIED Requirements

### Requirement: Derived deployment values
The system SHALL derive type-qualified Compose project names, unique service and network aliases, data and generated-file paths, project SNI domains, default settings, backup behavior, HTTP behavior, and secret-file paths from host settings plus each project role. Public native database domains SHALL use `<project>.<host-id>.<base-domain>` for both Postgres and KV roles. Default KV HTTP domains SHALL use the same project hostname. Compose project identity SHALL remain distinct when one project has both roles.

#### Scenario: Two roles share one project
- **WHEN** one project has Postgres and KV
- **THEN** generated project names are `evdb-<project>-postgres` and `evdb-<project>-kv`, both roles derive `<project>.<host-id>.<base-domain>` as their public native hostname, and Docker does not merge their Compose metadata

#### Scenario: KV HTTP default uses project hostname
- **WHEN** an HTTP-enabled KV role has no explicit HTTP domain override
- **THEN** its intended public HTTPS endpoint uses `<project>.<host-id>.<base-domain>`

### Requirement: Configuration validation
Validation SHALL reject unsafe project IDs, missing required environment suffixes, duplicate roles, unsupported KV engines, unsafe paths, colliding project/service/native-route/HTTP identities, unversioned, `latest`, or invalid image sources, invalid role-specific overrides, unsupported engine-major changes, and secret values in source or machine state. Explicit version tags SHALL be permitted only when they resolve to immutable digests in machine state.

#### Scenario: Project suffix is absent
- **WHEN** a project ID does not end in `-dev-N`, `-test-N`, or `-prod-N`
- **THEN** validation fails before candidate rendering

#### Scenario: Project roles share a public hostname
- **WHEN** one project contains both Postgres and KV roles using the derived project hostname
- **THEN** validation accepts the shared hostname because the native routes use different ports and entrypoints

#### Scenario: Enabled KV HTTP domains collide
- **WHEN** two enabled KV HTTP sidecars are configured with the same intended public HTTPS hostname
- **THEN** validation fails before generated files or live services change

## ADDED Requirements

### Requirement: Host database root catalog
Fresh guided initialization SHALL collect one or more database roots, beginning with
`/var/lib/evdb/databases`, and SHALL show the ordered catalog in review. Direct initialization SHALL
accept repeated `--data-root PATH`. Initialization SHALL validate and prepare every configured root.
Operators MAY edit the catalog in `config.yml` and rerun initialization; evdb SHALL leave filesystem
mounting and startup ordering to the operator.

#### Scenario: Guided setup accepts one root
- **WHEN** the operator accepts the canonical first root and adds no others
- **THEN** source records `host.data_roots` with only `/var/lib/evdb/databases`

#### Scenario: Guided setup adds another root
- **WHEN** the operator adds `/data`
- **THEN** review shows both ordered paths and initialization prepares both roots

#### Scenario: Source omits the root catalog
- **WHEN** initialization loads `config.yml` without `host.data_roots`
- **THEN** it rejects the unsupported source instead of synthesizing a compatibility default

### Requirement: Database root selection during creation
Guided database creation SHALL select one configured root and include it in confirmation. When exactly
one root exists, guided and direct creation SHALL use it automatically. When multiple roots exist,
guided creation SHALL ask and direct creation SHALL require `--data-root PATH`. Creation SHALL reject any
path outside the host catalog and SHALL persist the exact selection on the role.

#### Scenario: Guided creation has multiple roots
- **WHEN** the host catalog contains `/var/lib/evdb/databases` and `/data`
- **THEN** the operator chooses one before confirming database creation

#### Scenario: Direct creation omits a required selection
- **WHEN** direct database creation omits `--data-root` while multiple roots exist
- **THEN** creation fails before source or generated files change

## ADDED Requirements

### Requirement: Host database root selection
Fresh guided initialization SHALL ask for one host-wide database data root and default it to
`/var/lib/evdb/databases`. Direct initialization SHALL accept `--data-root PATH`. The redacted review
SHALL show the selected root, and initialization SHALL prepare it before database roles are rendered.
evdb SHALL leave filesystem mounting and startup ordering to the operator.

#### Scenario: Guided setup accepts the default
- **WHEN** the operator accepts the database data root default
- **THEN** source records `host.data_root: /var/lib/evdb/databases`

#### Scenario: Guided setup selects a mounted-disk directory
- **WHEN** the operator enters `/mnt/database-volume/evdb` whose immediate parent is safe and present
- **THEN** review shows that path and initialization prepares it as the host database root

#### Scenario: Source omits the data root
- **WHEN** initialization loads `config.yml` without `host.data_root`
- **THEN** it rejects the unsupported source instead of synthesizing a compatibility default

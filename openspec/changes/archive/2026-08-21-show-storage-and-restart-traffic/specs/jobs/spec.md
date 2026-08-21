## MODIFIED Requirements

### Requirement: Host status
Status SHALL include host identity, running evdb version, Traefik and network health, native listeners,
one backup timer state, repository availability, source validation, and storage assessment. Storage SHALL
cover `/var/lib/evdb` and every configured database root with exact path, backing mount point and source,
filesystem type, used/total/free capacity, and assigned project/roles. It SHALL NOT assess machine-state
compatibility, tool-version match, generated contract hashes, transaction directories, restore state, or
maintenance timers.

#### Scenario: Disk space is below policy
- **WHEN** free space for state storage or a configured database root is below the configured minimum
- **THEN** host status identifies the affected path and backing filesystem and marks the host unhealthy

#### Scenario: Several database roots are configured
- **WHEN** roles select database roots backed by different mounted filesystems
- **THEN** host details show every root, its actual mount facts and capacity, and the roles assigned to it

#### Scenario: Backup timer is inactive
- **WHEN** the one packaged backup timer is not loaded, enabled, and active
- **THEN** host details report the inactive timer without listing per-database unit names

### Requirement: Structured status output
`evdb status --json` SHALL emit one credential-free JSON object containing integer `version`, boolean
`healthy`, a host object, a databases object keyed by exact project/role, and an errors array. Host fields
SHALL cover identity, tool version, infrastructure, state storage, configured database-root storage,
repository, and the one timer. Database fields SHALL cover project, role, engine, running, health,
configured image, latest backup, and bounded error. Output SHALL omit machine state, contract comparison,
operation history, restore, backup tests, and maintenance units.

#### Scenario: Automation requests JSON
- **WHEN** status runs with `--json`
- **THEN** stdout contains exactly one parseable document with additive mount-aware storage facts and no terminal presentation or credentials

#### Scenario: Human contract changes incompatibly
- **WHEN** a future release removes or changes a required structured field
- **THEN** the top-level status version changes

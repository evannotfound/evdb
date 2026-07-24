## ADDED Requirements

### Requirement: Versioned release
Ansible SHALL install each build under `/opt/evanovation-db/releases/<git-sha>` and switch `/opt/evanovation-db/current` only after the release, config, and checks are complete.

#### Scenario: Release check fails
- **WHEN** a new release cannot pass its install checks
- **THEN** the current link remains on the prior release

### Requirement: Production paths
Rendered config SHALL live under `/etc/evanovation-db`, secret files under `/etc/evanovation-db/secrets`, and local backup state under `/var/lib/evanovation-db`.

#### Scenario: Secret file is deployed
- **WHEN** Ansible writes a resolved secret
- **THEN** the file is owned by the service account with mode `0600`

### Requirement: Controller-side secrets
Ansible SHALL resolve committed `op://` references on the trusted controller with secret output hidden. Resolved values SHALL NOT be stored in release folders, Ansible facts, logs, or generated Compose checked into Git.

#### Scenario: Ansible runs in check mode
- **WHEN** required 1Password access is unavailable
- **THEN** the playbook can still check non-secret templates without inventing or printing secret values

### Requirement: Mutable rclone config
Ansible SHALL seed the live rclone config from 1Password only when it is absent. Once created, the live file SHALL be owned by the service account and SHALL NOT be overwritten during normal deploys because rclone updates its OAuth token.

#### Scenario: Live token has changed
- **WHEN** Ansible runs after rclone refreshed its token
- **THEN** the existing live config remains unchanged

### Requirement: Service account
The deployed jobs SHALL run as a dedicated service account with access to Docker and only the files required by this system. The documentation SHALL state that Docker access is root-equivalent.

#### Scenario: Backup service starts
- **WHEN** systemd starts a backup unit
- **THEN** it runs as the service account rather than as an interactive user

### Requirement: Compose output
The repository SHALL render separate Postgres, Redis, Dragonfly, and Traefik Compose files. Generated database Compose SHALL preserve current ids, projects, data paths, domains, ports, resources, and Traefik TCP TLS/SNI behavior while using pinned images.

#### Scenario: Redis config is rendered
- **WHEN** one of the four current Redis instances is rendered
- **THEN** its Compose uses Redis and does not silently change it to Dragonfly

### Requirement: Shared database ports
Only Traefik SHALL publish host database ports 5432 and 6379. Postgres, PgBouncer, Redis, and Dragonfly containers SHALL remain internal. Traefik SHALL use TLS `HostSNI` routes to send Postgres traffic to the matching PgBouncer and KV traffic to the matching Redis or Dragonfly container by an instance-specific backend name.

#### Scenario: Two Postgres instances share port 5432
- **WHEN** clients connect to two configured Postgres domains on host port 5432
- **THEN** TLS SNI sends each connection to that domain's PgBouncer and database

#### Scenario: Two KV instances share port 6379
- **WHEN** clients connect to two configured KV domains on host port 6379
- **THEN** TLS SNI sends each connection to that domain's Redis or Dragonfly container

#### Scenario: Shared Redis alias is used
- **WHEN** a Traefik KV route points to a shared name such as `redis:6379`
- **THEN** validation fails and requires the instance-specific backend name

### Requirement: Local deployment test
Ansible, rendered Compose, JSON config, and systemd units SHALL be tested on disposable local infrastructure before the change is complete.

#### Scenario: Full local deploy runs
- **WHEN** the deployment integration test finishes
- **THEN** the test host can run config validation and local backup and restore commands without production credentials

### Requirement: Production guard
Production SHALL NOT be a default Ansible target. Any future production run SHALL require the production inventory and an explicit apply flag. This change SHALL NOT run those playbooks against `montreal-01`.

#### Scenario: Deploy command has no target
- **WHEN** a deploy target is omitted
- **THEN** the command uses disposable local infrastructure or stops instead of selecting production

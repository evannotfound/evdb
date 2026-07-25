## ADDED Requirements

### Requirement: Local operator command
The system SHALL provide an `evdb` command that reads local source configuration and manages the configured host over SSH without requiring the operator to invoke Ansible or host-local Python commands directly.

#### Scenario: Operator requests status
- **WHEN** the operator runs `evdb status`
- **THEN** the command connects to the host declared in `host.yml` and prints the remote status result locally

### Requirement: Safe SSH transport
The controller SHALL use subprocess argument arrays and a fixed versioned remote command. Normal operations SHALL execute code and runtime configuration through `/opt/evanovation-db/current`. A separate `/opt/evanovation-db/host-runtime` mode SHALL read an atomically refreshed normalized runtime from `/opt/evanovation-db/host-runtime/runtime` only after explicit confirmation for first install or protocol upgrade. It SHALL NOT read preserved `/etc` legacy JSON, use a shell command string, or place secret values in SSH arguments.

#### Scenario: Remote operation is invoked
- **WHEN** the controller sends an operation to the host
- **THEN** operation data is validated JSON over stdin/stdout and the process argument list contains no secret value

#### Scenario: Active runtime is unavailable
- **WHEN** the fixed active runtime is explicitly absent or reports an incompatible protocol
- **THEN** confirmed bootstrap installs stable code, atomically refreshes its dedicated normalized runtime, removes stale bootstrap instance JSON, and release-state plus apply use it until the candidate release activates

#### Scenario: Existing release uses an older runtime schema
- **WHEN** protocol mismatch triggers confirmed bootstrap on a host with active timers and legacy active runtime JSON
- **THEN** bootstrap leaves current release units, pointer, `/etc` runtime, and timer state unchanged until a healthy candidate activates transactionally

#### Scenario: Remote response is malformed
- **WHEN** SSH fails, output is malformed, active configuration is invalid, or a remote operation fails
- **THEN** the controller fails closed without invoking Ansible or treating the error as a bootstrap condition

### Requirement: Read-only plan
`evdb plan` SHALL compare normalized desired configuration with the active remote release and live state without changing source, lock, secrets, releases, services, containers, or data.

#### Scenario: New database is planned
- **WHEN** source contains a database absent from the active release
- **THEN** plan reports a create action and performs no remote write

#### Scenario: Deployed database was removed from source
- **WHEN** the active release contains a database absent from source
- **THEN** plan reports the removal as blocked and directs the operator to a future retirement workflow

### Requirement: Confirmed apply
`evdb apply` SHALL show the plan, require interactive confirmation unless `--yes` is supplied, stage a release, apply only its required service changes, and activate it only after validation and health checks succeed.

#### Scenario: Operator declines apply
- **WHEN** the operator does not confirm the displayed production plan
- **THEN** no lock, secret, release, service, container, or data change occurs

#### Scenario: Apply succeeds
- **WHEN** all staged configuration and changed services validate and become healthy
- **THEN** the new release becomes active and unchanged database projects are not restarted

### Requirement: Idempotent database creation
`evdb create <type> <name>` SHALL atomically add a minimal source entry, ensure convention-based 1Password fields exist, display the resulting plan, and use the normal apply path after confirmation. Rerunning an interrupted create SHALL converge without duplicating configuration or secret items.

#### Scenario: New database is created
- **WHEN** the operator confirms creation of a valid absent database
- **THEN** source contains one minimal entry, 1Password contains one managed item, and the healthy deployment is active

#### Scenario: Remote apply fails after local creation
- **WHEN** configuration and secrets were created but remote deployment fails
- **THEN** desired configuration and the secret item remain intact and the command can be rerun safely

### Requirement: Secure 1Password writes
Create workflows SHALL require a write-capable desktop or service-account 1Password session, send item templates and secret material through stdin, and suppress secret-bearing output and logs.

#### Scenario: Connect-only authentication is active
- **WHEN** a create workflow detects Connect-only environment variables without write-capable authentication
- **THEN** it fails before source or remote changes and explains the required authentication mode

#### Scenario: Existing managed item is found
- **WHEN** the convention-based item and required fields already exist
- **THEN** create reuses them without rotating or revealing their values

### Requirement: Routine lifecycle commands
The controller SHALL provide start, stop, restart, and logs commands for a selected database. These operations SHALL retain data, configuration, secrets, and backups.

#### Scenario: Database is stopped
- **WHEN** the operator confirms a stop command
- **THEN** the database project stops while its data, release configuration, secrets, and backups remain present

#### Scenario: Logs are requested
- **WHEN** the operator requests logs for a database
- **THEN** the controller streams that project's remote logs without exposing protected secret files

### Requirement: Unambiguous selectors
Commands SHALL accept a plain name when it resolves to one database and SHALL require `<type>/<name>` when more than one configured database shares the name.

#### Scenario: Plain name is ambiguous
- **WHEN** an operator selects a name used by Postgres and KV databases
- **THEN** the command performs no operation and lists the valid typed selectors

### Requirement: Production guardrails
Write operations SHALL identify the target host, show affected databases and host infrastructure, serialize conflicting operations with locks, and require explicit confirmation or `--yes`. Generated inventory for the real target SHALL retain production group membership and target production so mutable extra variables cannot downgrade the guard. They SHALL fail closed when controller and remote protocol versions are incompatible.

#### Scenario: Protocol versions differ
- **WHEN** the local controller cannot safely communicate with the deployed runtime
- **THEN** the operation stops before any remote write and requests installation of a compatible release

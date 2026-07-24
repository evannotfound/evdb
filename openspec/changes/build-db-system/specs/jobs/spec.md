## ADDED Requirements

### Requirement: Direct commands
The CLI SHALL provide `backup <instance>`, `restore-check <instance>`, `status`, and `validate` commands. Commands SHALL use short option and function names that match the terms used by operators.

#### Scenario: Named backup runs
- **WHEN** an operator runs `evanovation-db backup vercount-prod-01`
- **THEN** only that instance is backed up and the command returns success only after its required checks and upload succeed

### Requirement: Safe command execution
External commands SHALL use argument arrays without `shell=True`, enforce timeouts, check return codes, and redact secret values from logs and errors.

#### Scenario: Tool prints a secret in an error
- **WHEN** stderr contains a configured secret value
- **THEN** the stored and displayed error replaces that value with a redaction marker

### Requirement: Useful status
Status SHALL report the latest completed local backup, Restic snapshot id and time, upload result, latest restore-test result, and current failure for every durable instance.

#### Scenario: Backup is too old
- **WHEN** a durable instance has no confirmed snapshot newer than about 26 hours
- **THEN** status marks it stale and exits nonzero

#### Scenario: Restore test is too old
- **WHEN** a durable instance has no successful restore test in 30 days
- **THEN** status marks its restore test stale

### Requirement: Structured logs
Commands SHALL write concise structured logs with host, instance, command, step, result, duration, and error fields. Logs SHALL be useful through journald and readable during local tests.

#### Scenario: Instance backup fails
- **WHEN** an engine command fails
- **THEN** one error record identifies the instance and failed step without exposing credentials

### Requirement: Systemd jobs
The repository SHALL include systemd units for per-instance backup, daily status, due restore tests, weekly checks and retention, and monthly prune. Timers SHALL use persistent scheduling, randomized delays, execution timeouts, and low CPU and I/O priority.

#### Scenario: Host was offline at backup time
- **WHEN** a persistent timer becomes active after the host returns
- **THEN** systemd schedules the missed work instead of waiting a full day

### Requirement: Disabled by default
Deployment SHALL install timers disabled unless an operator explicitly enables them. No production timer or cron entry SHALL change in this change.

#### Scenario: System is deployed to a test host
- **WHEN** the Ansible backup playbook finishes without an enable flag
- **THEN** the units exist but no timer is enabled or started

### Requirement: Make targets
The Makefile SHALL provide `check`, `plan`, `deploy-backup`, `deploy-db`, `backup`, `restore-check`, and `status` targets with direct names and required arguments documented.

#### Scenario: Instance argument is missing
- **WHEN** an instance Make target is run without `NAME`
- **THEN** it stops with a short message showing the required form

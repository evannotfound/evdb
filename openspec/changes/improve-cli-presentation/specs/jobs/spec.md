## MODIFIED Requirements

### Requirement: Structured logs
Commands SHALL write concise structured logs with host, project, role, concrete engine when relevant, command, step, result, duration, and redacted error fields. Logs SHALL be useful through journald and readable during local tests. Routine structured log records SHALL be emitted as JSONL when stderr is not an interactive terminal and SHALL NOT appear as raw JSON in ordinary interactive terminal stderr.

#### Scenario: Backup fails
- **WHEN** an engine backup command fails in a non-interactive job or with stderr redirected
- **THEN** one error record identifies project/role and failed step without exposing credentials

#### Scenario: Systemd captures command logs
- **WHEN** a systemd timer or service runs an evdb command with non-terminal stderr
- **THEN** stderr receives parseable structured JSONL records suitable for journald ingestion

#### Scenario: Operator uses guided CLI in a terminal
- **WHEN** stdin, stdout, and stderr are interactive terminals and routine status or mutation events are recorded
- **THEN** evdb does not print raw structured JSON records into the human menu session

#### Scenario: Operator redirects terminal stderr for debugging
- **WHEN** an operator runs an interactive command with stderr redirected to a file or pipe
- **THEN** evdb writes the same redacted structured JSONL records to that redirected stream

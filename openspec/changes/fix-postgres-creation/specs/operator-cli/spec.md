## ADDED Requirements

### Requirement: Secure initial Postgres password input
Direct Postgres creation SHALL accept an optional `--password-file PATH` whose content becomes the
initial password for the fixed `default` login. Guided Postgres creation SHALL offer a masked password
prompt where blank input selects generation. evdb SHALL NOT accept a password value as a command
argument, environment variable, ordinary echoed prompt, preview field, log field, or machine-readable
output. A supplied password SHALL be non-empty and contain no NUL, carriage return, or embedded line
feed after one trailing line ending is removed.

#### Scenario: Script supplies a password file
- **WHEN** automation runs `evdb database add app-prod-01 postgres --password-file PATH` with a valid private file
- **THEN** evdb reads the password from the file, protects it from subprocess output, and does not place it in the process arguments or operation preview

#### Scenario: Guided creation keeps the entered password hidden
- **WHEN** an operator enters a password in the guided Postgres add flow
- **THEN** the terminal does not echo or redisplay the value and the confirmed operation uses it as the initial managed password

#### Scenario: Guided creation requests generation
- **WHEN** an operator leaves the guided Postgres password prompt blank
- **THEN** evdb generates the initial managed password without requiring another credential input

#### Scenario: Password file has invalid content
- **WHEN** the selected password file is empty or contains a NUL or embedded line break
- **THEN** evdb rejects creation before changing source, state, secrets, Compose, containers, routes, or data

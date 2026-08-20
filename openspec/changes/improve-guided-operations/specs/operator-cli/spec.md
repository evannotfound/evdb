## ADDED Requirements

### Requirement: Guided host initialization
Fresh `evdb init` in an interactive terminal SHALL use an append-only sequence for host identity, DNS,
backup storage, and Restic password input. Each step SHALL explain the requested value, show a useful
default or example where applicable, validate the answer before advancing, and retry the same step after
an invalid answer. The flow SHALL finish with a credential-redacted summary whose fields can be edited
before one Apply confirmation.

#### Scenario: Operator corrects an invalid domain
- **WHEN** the operator enters an invalid base domain during guided initialization
- **THEN** evdb explains the valid domain form and repeats the domain prompt without discarding earlier answers

#### Scenario: Operator reviews setup
- **WHEN** every guided setup section is complete
- **THEN** evdb shows host, wildcard domain, DNS provider and variable names, repository, and credential source without displaying any credential value

### Requirement: Guided provider and repository selection
Guided initialization SHALL search and select canonical DNS providers from the catalog bundled for the
pinned Traefik and lego versions. It SHALL present the selected provider's documented credential
variables and help URL and collect selected secret values with masked input. For rclone storage it SHALL
validate the native configuration, list its configured remotes, select one remote, and collect a safe
repository path instead of accepting a free-form repository URL. It SHALL also offer an explicit local
repository mode.

#### Scenario: Operator selects an rclone repository
- **WHEN** a valid rclone file contains remotes `archive:` and `offsite:` and the operator selects `offsite:` with path `evdb/example-01`
- **THEN** the review shows repository `rclone:offsite:evdb/example-01`

#### Scenario: Provider search has no match
- **WHEN** the operator searches for a DNS provider not present in the bundled catalog
- **THEN** evdb reports no supported match and repeats provider selection without accepting the search text as a provider code

### Requirement: Grounded contextual presentation
Human output SHALL use task-specific sections, key/value summaries, numbered choices, concise append-only
progress, and actionable errors. Tables SHALL be used only for comparable database, backup, provider, or
remote rows and SHALL not wrap single records or setup sections in decorative tables. Generic nested
Python values SHALL not be used as human-facing renderers.

#### Scenario: Database details contain nested settings
- **WHEN** an operator opens one database's Details view
- **THEN** settings, images, paths, health, and backup facts appear in named sections rather than as dictionary representations or a table of unrelated fields

#### Scenario: Initialization waits for ACME
- **WHEN** Traefik is obtaining the wildcard certificate
- **THEN** evdb prints one clear waiting message and a later success or credential-redacted failure without a spinner or rewritten terminal line

### Requirement: Actionable command guidance
Public argparse help and missing-input errors SHALL name accepted forms, defaults where relevant, and at
least one valid example for setup, database creation, database selection, and backup creation. Help SHALL
not disclose secrets or expand the complete DNS provider catalog into every ordinary usage error.

#### Scenario: Non-interactive setup omits a repository
- **WHEN** a non-interactive fresh `evdb init` omits its repository input
- **THEN** evdb exits without prompting and shows the accepted rclone and local repository forms with examples

## MODIFIED Requirements

### Requirement: Secure initial Postgres password input
Direct Postgres creation SHALL accept optional `--username`, `--database-name`, and
`--password-file PATH` creation inputs. Guided creation SHALL generate the default managed identity
without requesting a password unless the operator enters Advanced configuration. Advanced creation SHALL
collect username and database name as visible validated values and collect and confirm the password with
masked input. The validated password SHALL be stored only in `secrets.yml` and derived private role
files. No inline password argument, environment input, echoed prompt, preview, status, or machine output
SHALL be accepted.

#### Scenario: Script supplies a complete Postgres identity
- **WHEN** automation adds Postgres with `--username app_user`, `--database-name app_db`, and a valid private password file
- **THEN** evdb persists the two non-secret identity values and stores the password under the matching `secrets.yml` role without placing it in process arguments or output

#### Scenario: Guided creation accepts defaults
- **WHEN** the operator creates Postgres without opening Advanced configuration
- **THEN** evdb uses username `default`, database `postgres`, and a generated password without showing a password prompt

#### Scenario: Guided advanced passwords differ
- **WHEN** the two masked advanced password entries differ
- **THEN** evdb reports the mismatch and repeats password entry before showing the creation summary

#### Scenario: Password file is invalid
- **WHEN** the file is empty, non-private, symlinked, non-regular, or contains NUL or embedded line breaks
- **THEN** creation fails before source or services change

### Requirement: Deliberate confirmations
Fresh guided initialization, guided database creation, and guided settings editing SHALL present one
concise redacted summary and require one final Apply, Create, or Save confirmation. Fully supplied direct
database lifecycle, backup, and initialization commands SHALL execute without a generic confirmation.
The installer MAY use `init --yes` only for an already configured host and SHALL not use it to bypass
missing fresh-host input.

#### Scenario: Operator declines guided initialization
- **WHEN** the operator rejects the final Apply prompt
- **THEN** source, credentials, containers, repositories, routes, units, and data remain unchanged

#### Scenario: Operator declines guided creation
- **WHEN** the operator rejects the final Create prompt
- **THEN** source, generated files, credentials, containers, routes, and data remain unchanged

#### Scenario: Operator runs an explicit backup
- **WHEN** the operator invokes `evdb backup create PROJECT/ROLE`
- **THEN** backup starts without repeating a confirmation of the already explicit command

## MODIFIED Requirements

### Requirement: User-run repository subprocesses
Every Restic repository operation SHALL run as a derived non-root repository operator using that
account's primary and supplementary groups. For rclone repositories the operator SHALL be the owner of
`host.backup.rclone_config`; evdb SHALL set explicit `HOME`, `USER`, `LOGNAME`, `RCLONE_CONFIG`, and a
user-writable Restic cache, invoke trusted absolute Restic and rclone executables, and configure Restic to
launch that rclone path. For local repositories the operator SHALL be the owner of the repository or its
existing immediate parent; evdb SHALL omit `RCLONE_CONFIG`, the rclone program option, and any rclone
child. The Restic password SHALL be supplied through an inherited Linux memory-file descriptor, not
arguments, environment variables, or a persistent file.

#### Scenario: Scheduled rclone repository inspection runs
- **WHEN** the root backup service checks an rclone repository
- **THEN** Restic and its rclone child run with the rclone file owner's identity and canonical mutable configuration

#### Scenario: Scheduled local repository inspection runs
- **WHEN** the root backup service checks a local repository
- **THEN** Restic runs with the local repository operator's identity and an environment containing no rclone configuration

#### Scenario: Restic reads its password
- **WHEN** evdb starts any Restic operation
- **THEN** Restic reads the password from an inherited memory descriptor that is closed when the operation ends

### Requirement: Automatic host repository initialization
`evdb init` SHALL perform a read-only `restic cat config` preflight for the reviewed repository before
canonical mutation and SHALL initialize the repository only after Apply. Success SHALL reuse the
repository, the documented missing-repository exit SHALL run `restic init --repository-version 1`, and
every other result SHALL fail with the repository URL and original credential-redacted error. Restic
through rclone SHALL create a missing remote path without a separate rclone directory operation. Restic
in local mode SHALL create only the missing repository leaf under its validated existing parent.

#### Scenario: Configured rclone path does not exist
- **WHEN** Restic 0.17 or newer reports that the configured rclone repository is missing
- **THEN** preflight marks it for creation and post-confirmation initialization creates format v1 at that exact path

#### Scenario: Configured local path does not exist
- **WHEN** the local repository leaf is absent under its validated non-root parent
- **THEN** post-confirmation Restic initialization creates the leaf as the derived repository operator

#### Scenario: Repository already exists
- **WHEN** `restic cat config` succeeds with the reviewed password
- **THEN** initialization preserves the existing repository and does not run `restic init`

#### Scenario: Repository access is denied
- **WHEN** repository preflight fails for a reason other than the documented missing-repository exit
- **THEN** fresh guided setup returns to review without writing canonical source or hiding the repository and error

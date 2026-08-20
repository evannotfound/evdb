## ADDED Requirements

### Requirement: Disposable host source workflow
The repository SHALL document a development workflow that syncs only explicit source, test, and
locked project files to a disposable non-production VPS, creates an editable environment with
`uv sync --locked`, and invokes or explicitly activates that checkout without publishing a release.
The workflow SHALL reject the known production host, SHALL NOT copy ignored files or credentials,
and SHALL leave `/opt/evdb/current` unchanged. Any temporary stable-command activation SHALL be
reversible to `/opt/evdb/current/bin/evdb` and SHALL be identified as incompatible with host update
until deactivated.

#### Scenario: Developer tests an uncommitted source revision
- **WHEN** a developer syncs the explicit development paths to an approved disposable VPS
- **THEN** the remote locked editable environment runs that source revision on the VPS architecture without a tag or GitHub Release

#### Scenario: Developer targets the production host
- **WHEN** the development sync or activation workflow is given `production-host`
- **THEN** it refuses before copying files, changing links, invoking evdb, or modifying host state

#### Scenario: Development activation is removed
- **WHEN** the operator deactivates the disposable host checkout
- **THEN** `/usr/local/bin/evdb` again resolves to `/opt/evdb/current/bin/evdb` and the installed release files remain unchanged

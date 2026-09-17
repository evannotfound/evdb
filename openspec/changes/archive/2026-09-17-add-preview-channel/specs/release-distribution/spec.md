## MODIFIED Requirements

### Requirement: Tagged standalone releases
Each stable evdb release SHALL originate from an exact semantic-version Git tag and SHALL publish a
standalone executable whose reported version exactly matches the source version and tag. A stable
release SHALL NOT be published when these versions differ. Preview publication SHALL use the separate
rolling preview channel.

#### Scenario: Release tag differs from the application
- **WHEN** a release workflow for tag `v1.2.3` builds an executable reporting `1.2.4`
- **THEN** the workflow fails before publishing a GitHub Release

### Requirement: Consistent version identity
evdb SHALL expose `evdb --version`, and source package metadata, release executables, and runtime
status SHALL use the same exact SCM-derived version. Stable release tags SHALL match that semantic
version; preview versions SHALL retain the SCM development identity, such as `1.2.4.dev17+gabc1234`,
or the exact stable version when building a tagged commit. No machine-state tool-version field SHALL
be required.

#### Scenario: Operator checks the installed version
- **WHEN** the operator runs `evdb --version`
- **THEN** the command prints the exact version represented by the active release

#### Scenario: Rolling tag is present
- **WHEN** SCM derives the version with a `preview` tag in repository history
- **THEN** only tags matching `v[0-9]*` participate in Git describe version discovery

### Requirement: Public initial installer
Each release SHALL include an anonymously fetchable installer. It SHALL detect supported architecture,
resolve latest stable by default, accept a positional exact version, or accept exclusive `--preview`
selection. It SHALL verify checksum and executable version and atomically replace `/usr/local/bin/evdb`
without Python or uv. A new host SHALL be directed to `sudo evdb init`. A configured host SHALL be
updated and then refreshed automatically with `evdb init --yes`.

#### Scenario: New host installs latest
- **WHEN** a host with no managed installation runs the public installer
- **THEN** the latest stable version is selected and the operator is directed to `sudo evdb init`

#### Scenario: Operator pins installation
- **WHEN** the installer receives exact version `1.2.3`
- **THEN** it downloads only that tag and rejects an executable reporting another version

#### Scenario: Configured host updates
- **WHEN** `config.yml` and a managed executable already exist
- **THEN** the installer installs the verified requested executable and runs `evdb init --yes`

#### Scenario: Configured refresh fails
- **WHEN** post-selection initialization returns nonzero
- **THEN** the installer returns nonzero with that error and leaves the verified selected executable installed

#### Scenario: Operator opts into preview
- **WHEN** the installer receives only `--preview`
- **THEN** it selects commit-qualified binary and checksum assets from one manifest snapshot and
  requires the executable to report the manifest's exact version

#### Scenario: Preview download is invalid or unavailable
- **WHEN** preview metadata is malformed, an asset is unavailable, a checksum differs, or a reported
  version differs from the selected version
- **THEN** installation fails before replacing an existing command or refreshing the host

### Requirement: Autonomous repository-grounded release notes
For each semantic-version release, the workflow SHALL attempt to generate the GitHub Release body with
a non-interactive OpenCode command that receives the exact target tag. OpenCode SHALL identify the
latest previous non-draft, non-prerelease GitHub release, excluding the rolling preview, inspect actual
changes through the target, use repository context as needed, describe user-visible behavior and required
operator action, and omit internal-only work. The notes SHALL use clear feature, improvement, bugfix,
and breaking-change sections, omit empty sections, and let the number of bullets reflect the number of
distinct notable changes. If no notable user-visible changes remain, the notes SHALL instead contain
exactly `No notable changes.`

#### Scenario: OpenCode generates release notes
- **WHEN** the configured model and repository tools complete successfully for a tagged release
- **THEN** the workflow publishes the validated OpenCode-written Markdown as that GitHub Release's body

#### Scenario: Initial release has no predecessor
- **WHEN** GitHub has no previous non-draft, non-prerelease release before the target tag
- **THEN** OpenCode inspects history through the target and summarizes the initial usable product

#### Scenario: Release has no notable user-visible changes
- **WHEN** inspection finds only internal, test, CI, or documentation changes without user-visible
  effects
- **THEN** OpenCode writes `No notable changes.` without empty change sections

## ADDED Requirements

### Requirement: Rolling preview publication
Successful main-push checks and native Ubuntu 22.04 amd64 and arm64 builds SHALL publish checksummed,
attested standalone executables to one `preview` GitHub prerelease. It SHALL be excluded from latest
stable selection. Publication SHALL be serialized, reject older workflow runs after a newer run has
reserved publication, upload complete commit-qualified assets before changing the installer manifest,
and retain the preceding build for overlapping downloads. The README SHALL document preview opt-in,
stable default selection, exact positional installation, and how to return to stable.

#### Scenario: Both architectures succeed
- **WHEN** a main push passes checks and both native executable checks
- **THEN** publication updates the rolling prerelease with both architectures, checksums, provenance,
  installer, and exact source commit and version identity

#### Scenario: One architecture fails
- **WHEN** either architecture build or release check fails
- **THEN** publication does not select that build

#### Scenario: Older workflow finishes late
- **WHEN** an older run attempts publication after a newer run has reserved the channel
- **THEN** it exits without modifying the preview

#### Scenario: Installer overlaps publication
- **WHEN** an installer fetched the preceding manifest before the new manifest is published
- **THEN** its commit-qualified binary and checksum remain available and cannot be mixed with new assets

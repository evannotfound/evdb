# Release Distribution Specification

## Purpose

Define standalone release publication, integrity, installation, and production runtime boundaries.

## Requirements

### Requirement: Tagged standalone releases
Each evdb release SHALL originate from an exact semantic-version Git tag and SHALL publish a
standalone executable whose reported version exactly matches the source version and tag. A release
SHALL NOT be published when these versions differ.

#### Scenario: Release tag differs from the application
- **WHEN** a release workflow for tag `v1.2.3` builds an executable reporting `1.2.4`
- **THEN** the workflow fails before publishing a GitHub Release

### Requirement: Supported Linux release architectures
Every release SHALL provide native Linux archives for ARM64 and x86_64 built on Ubuntu 22.04 runners.
The release and installer SHALL reject unsupported operating systems and architectures explicitly.

#### Scenario: ARM64 host selects a release
- **WHEN** installation runs on an Ubuntu 22.04 `aarch64` host
- **THEN** it selects the native `evdb_linux_arm64.tar.gz` release archive

#### Scenario: Unsupported host requests installation
- **WHEN** installation runs on an unsupported operating system or machine architecture
- **THEN** it exits before downloading or changing managed tool paths and names the unsupported value

### Requirement: Complete release archive
Each architecture archive SHALL contain exactly one executable at `bin/evdb` and every canonical,
non-empty systemd service and timer under `units/`. The executable SHALL run without a host Python
installation or Python package manager.

#### Scenario: Release archive is inspected
- **WHEN** the release workflow assembles an architecture archive
- **THEN** it verifies the expected executable, canonical units, file types, modes, and absence of
  unexpected members before publication

### Requirement: Release integrity and provenance
Every architecture archive SHALL have a matching SHA-256 file and a GitHub artifact attestation tied
to the release workflow and source revision. Installation and host update SHALL verify the checksum
before reading archive members or placing executable content.

#### Scenario: Downloaded archive is corrupted
- **WHEN** an archive digest differs from its release checksum
- **THEN** installation fails without creating or changing a managed version, active link, or stable
  command

### Requirement: Consistent version identity
evdb SHALL expose `evdb --version`, and source package metadata, release tags, release archives,
machine-owned tool state, and runtime status SHALL use the same exact semantic version.

#### Scenario: Operator checks the installed version
- **WHEN** the operator runs `evdb --version`
- **THEN** the command prints the exact version represented by the active release and exits successfully

### Requirement: Autonomous repository-grounded release notes
For each semantic-version release, the workflow SHALL attempt to generate the GitHub Release body with
a non-interactive OpenCode command that receives the exact target tag. OpenCode SHALL identify the
latest previous non-draft GitHub release, inspect actual changes through the target, use repository
context as needed, describe user-visible behavior and required operator action, and omit internal-only
work. The notes SHALL use clear feature, improvement, bugfix, and breaking-change sections, omit empty
sections, and let the number of bullets reflect the number of distinct notable changes. If no notable
user-visible changes remain, the notes SHALL instead contain exactly `No notable changes.`

#### Scenario: OpenCode generates release notes
- **WHEN** the configured model and repository tools complete successfully for a tagged release
- **THEN** the workflow publishes the validated OpenCode-written Markdown as that GitHub Release's body

#### Scenario: Initial release has no predecessor
- **WHEN** GitHub has no previous non-draft release before the target tag
- **THEN** OpenCode inspects history through the target and summarizes the initial usable product

#### Scenario: Release has no notable user-visible changes
- **WHEN** inspection finds only internal, test, CI, or documentation changes without user-visible
  effects
- **THEN** OpenCode writes `No notable changes.` without empty change sections

### Requirement: Autonomous release investigation
The changelog command SHALL configure its model directly and run without a dedicated project agent or
generated evidence file. OpenCode SHALL be able to use GitHub metadata, Git, shell commands, repository
search, and file reads to investigate the release. The notes job SHALL provide only read-scoped GitHub
contents and pull-request authority, SHALL NOT receive release assets or publication permissions, and
SHALL upload only the generated notes file.

#### Scenario: OpenCode needs release context
- **WHEN** the command needs to determine the release range or understand a user-visible effect
- **THEN** it selects and runs the investigative commands needed instead of relying on a precomputed
  commit list or patch

#### Scenario: Notes generation attempts GitHub publication
- **WHEN** a command attempts to create or modify a GitHub release from the notes job
- **THEN** the job's read-only GitHub permission prevents publication

### Requirement: Secret-safe model configuration
Release CI SHALL obtain the OpenAI-compatible base URL and API key from GitHub Actions secrets and
expose them to OpenCode through environment interpolation. It SHALL NOT place resolved credentials in
source, command arguments, generated notes, artifacts, or installed evdb files. The notes job's
ephemeral GitHub token SHALL be limited to read access.

#### Scenario: OpenCode calls the configured model
- **WHEN** the release workflow invokes `openai/gpt-5.6-sol` through the custom endpoint
- **THEN** OpenCode resolves the endpoint and API key from the release environment without persisting
  either value in the repository or release

### Requirement: Release-note fallback
OpenCode installation, provider access, GitHub inspection, generation, tool use, and output validation
SHALL be non-blocking after the existing release safety gates pass. If any release-note stage fails or
no valid notes file exists, the workflow SHALL publish the same checked and attested release artifacts
with GitHub-generated notes and SHALL identify the selected fallback without exposing credentials.

#### Scenario: Notes generation is unavailable
- **WHEN** OpenCode cannot produce a valid release-note file
- **THEN** the workflow publishes the release with GitHub automatic notes and unchanged verified assets

#### Scenario: OpenCode output is invalid
- **WHEN** the generated notes are missing, empty, non-UTF-8, unsafe as a regular file, or exceed the
  configured size limit
- **THEN** validation rejects the file and the workflow uses GitHub automatic notes

#### Scenario: OpenCode output has invalid structure or exposes a credential
- **WHEN** the generated notes use unsupported or empty change sections, order sections incorrectly,
  or contain an exact release credential value
- **THEN** validation rejects the file and the workflow uses GitHub automatic notes

### Requirement: Release-only OpenCode dependency
OpenCode, Node.js, provider configuration, and model credentials SHALL be release-CI dependencies only.
Standalone archives, the public installer, installed commands, setup, status, scheduled jobs, and host
updates SHALL remain operable without those dependencies.

#### Scenario: Managed host installs OpenCode-authored release notes
- **WHEN** an operator installs a release whose notes were generated by OpenCode
- **THEN** installation and all host-local evdb operations require no OpenCode executable, Node.js, or
  model credential

### Requirement: Tag-derived application version
The build system SHALL derive the evdb application and package version from Git metadata without a
manually maintained release-version literal. An exact semantic-version release tag SHALL produce that
exact release version, and built artifacts SHALL retain the resolved version without requiring Git or
version-derivation tooling at runtime.

#### Scenario: Exact tag is built
- **WHEN** release CI builds tag `v1.2.3`
- **THEN** package metadata, `evdb --version`, runtime status, and the standalone executable use version
  `1.2.3`

#### Scenario: Untagged development revision is built
- **WHEN** a developer installs or builds evdb from a revision after the latest release tag
- **THEN** the package reports an SCM-derived development version without modifying a tracked version
  source file

#### Scenario: Built artifact runs without repository metadata
- **WHEN** a wheel, source archive, or standalone executable runs outside its original Git checkout
- **THEN** it reports the version embedded during its build without invoking Git or a Python package
  versioning tool

### Requirement: Public initial installer
Each release SHALL include an installer that can be fetched anonymously from GitHub Releases. The
installer SHALL detect the supported architecture, resolve the latest release by default or accept an
exact version, verify the release checksum and executable version, stage the complete archive, install
it under `/opt/evdb/versions/<version>`, and establish `/opt/evdb/current` and
`/usr/local/bin/evdb` without requiring Python or `uv`.

#### Scenario: New host installs the latest release
- **WHEN** an operator pipes the public latest `install.sh` release asset to a root shell on a supported
  host with no managed evdb installation
- **THEN** the exact downloaded version is installed and selected, and the installer directs the
  operator to run `sudo evdb host setup`

#### Scenario: Operator pins initial installation
- **WHEN** the installer receives exact version `1.2.3`
- **THEN** it downloads only release tag `v1.2.3` and rejects an archive reporting any other version

#### Scenario: Unconfigured managed installation already exists
- **WHEN** the installer finds an existing managed current version but no host configuration
- **THEN** it verifies the existing managed links, stages the requested release, switches the active
  tool version, records the prior active version as previous, and directs the operator to
  `evdb host setup`

#### Scenario: Configured managed installation already exists
- **WHEN** the installer finds an existing managed current version and any host configuration entry
- **THEN** it leaves all versions and links unchanged and directs the operator to fix configuration and
  use `evdb host update`

### Requirement: Development and production tool boundary
The repository SHALL continue to use `uv` and its lock file for development, checks, and release
builds. Installed production commands, setup, systemd jobs, and host updates SHALL NOT require or
invoke Python, pip, pipx, `uv`, or a Python package registry.

#### Scenario: Host has no Python development tools
- **WHEN** a release archive is installed on a supported host with the documented database-tool
  prerequisites but without Python or `uv`
- **THEN** evdb setup, status, scheduled jobs, and exact-version updates remain operable

### Requirement: Public project entry point
The README SHALL present evdb to public self-hosters with a concise product description, core
features, short installation and representative usage, supported requirements, focused documentation
links, development commands, release status, and MIT licensing. Detailed configuration, command,
recovery, update, and migration procedures SHALL remain in topic documentation rather than the
README.

#### Scenario: New user evaluates evdb
- **WHEN** a self-hoster opens the repository README
- **THEN** they can understand the supported databases, safety model, host requirements, installation,
  first command, documentation paths, and license without reading implementation details

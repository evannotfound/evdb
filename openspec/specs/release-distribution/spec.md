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

### Requirement: Complete release executable
Each architecture asset SHALL be one executable with exactly the two canonical systemd unit templates
embedded. The executable SHALL run and install those units without host Python or a Python package
manager.

#### Scenario: Release executable is inspected
- **WHEN** the release workflow builds an architecture executable
- **THEN** it verifies the reported version and that both embedded units are available and non-empty

### Requirement: Release integrity and provenance
Every architecture executable SHALL have a matching SHA-256 file and GitHub artifact attestation tied to
the release workflow and source revision. Initial installation and configured-host installer updates
SHALL verify the checksum before executing or installing the candidate.

#### Scenario: Downloaded executable is corrupted
- **WHEN** an executable digest differs from its checksum
- **THEN** installation fails without creating or selecting the candidate release

### Requirement: Consistent version identity
evdb SHALL expose `evdb --version`, and source package metadata, release tags, release executables, and
runtime status SHALL use the same exact semantic version. No machine-state tool-version field SHALL be
required.

#### Scenario: Operator checks the installed version
- **WHEN** the operator runs `evdb --version`
- **THEN** the command prints the exact version represented by the active release

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
OpenCode, Node.js, provider configuration, and model credentials SHALL remain release-CI dependencies
only. Standalone executables, the installer, initialization, status, scheduled backup, database, and
backup commands SHALL operate without those dependencies.

#### Scenario: Managed host installs OpenCode-authored release notes
- **WHEN** an operator installs that release
- **THEN** no host-local evdb operation requires OpenCode, Node.js, or model credentials

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
Each release SHALL include an anonymously fetchable installer. It SHALL detect supported architecture,
resolve latest by default or accept an exact version, verify checksum and executable version, and
atomically replace `/usr/local/bin/evdb` without
Python or `uv`. A new host SHALL be directed to `sudo evdb init`. A configured host SHALL be updated
and then refreshed automatically with `evdb init --yes`.

#### Scenario: New host installs latest
- **WHEN** a host with no managed installation runs the public installer
- **THEN** the exact downloaded version is selected and the operator is directed to `sudo evdb init`

#### Scenario: Operator pins installation
- **WHEN** the installer receives exact version `1.2.3`
- **THEN** it downloads only that tag and rejects an executable reporting another version

#### Scenario: Configured host updates
- **WHEN** `config.yml` and a managed executable already exist
- **THEN** the installer installs the verified requested executable and runs `evdb init --yes`

#### Scenario: Configured refresh fails
- **WHEN** post-selection initialization returns nonzero
- **THEN** the installer returns nonzero with that error and leaves the verified selected executable installed

### Requirement: Development and production tool boundary
The repository SHALL use `uv` and its lock file for development, checks, and release builds. Installed
commands, initialization, systemd jobs, and installer-driven updates SHALL NOT require or invoke
Python, pip, pipx, `uv`, or a Python package registry.

#### Scenario: Host has no Python development tools
- **WHEN** a standalone release runs on a supported host without Python or `uv`
- **THEN** initialization, status, database, backup, scheduling, and installer updates remain operable

### Requirement: Disposable host source workflow
The repository SHALL retain a workflow that syncs only explicit source, test, and locked project files
to an operator-selected disposable VPS and runs a native `uv sync --locked` environment. It SHALL copy
no ignored files or credentials and SHALL leave the installed `/usr/local/bin/evdb` unchanged.
Development commands SHALL invoke the checkout's executable explicitly rather than temporarily replacing
the installed command.

#### Scenario: Developer tests an uncommitted source revision
- **WHEN** explicit development files are synced to an approved VPS
- **THEN** its architecture-native environment runs the source revision without a release

#### Scenario: Development command completes
- **WHEN** a checkout command exits
- **THEN** `/usr/local/bin/evdb` remains the installed release executable

### Requirement: Public project entry point
The README SHALL preserve the project headline, slogan, three badges, and concise description of evdb
as Linux tooling for Postgres and Redis-compatible databases. It SHALL provide one linear public guide
that presents host requirements and official prerequisite installation links before any evdb command,
then covers DNS and rclone preparation, evdb installation, guided initialization, first Postgres
creation, connection information, CLI usage, automatic backups, development commands, and licensing.
It SHALL describe Linux with systemd as the host requirement and Ubuntu as the tested path without an
Ubuntu-version or architecture matrix. It SHALL identify CLI help as the detailed command reference and
SHALL NOT depend on a separate `docs/` tree. It SHALL state that evdb does not provide restore or remote
snapshot pruning and SHALL NOT claim private networking, live restore, restore verification, remote
retention, or automatic rollback.

#### Scenario: New self-hoster follows the README
- **WHEN** a new self-hoster opens the repository README
- **THEN** they can prepare required software, DNS credentials, DNS routing, and remote storage before
  installing evdb, initialize the host, create and connect to a first Postgres database, and find later
  operations through the CLI usage section and command help

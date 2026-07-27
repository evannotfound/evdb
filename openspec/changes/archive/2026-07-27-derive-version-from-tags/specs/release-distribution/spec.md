## ADDED Requirements

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

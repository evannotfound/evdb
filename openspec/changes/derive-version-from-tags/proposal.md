## Why

Releases currently require a manual edit to `evdb.__version__` before creating a tag, so an otherwise
valid release can fail after the tag is pushed when that edit is forgotten. The semantic-version tag
already identifies the release and should be the single version source.

## What Changes

- Derive package and application versions from Git tags during development and builds.
- Generate a runtime version module so wheels and standalone binaries do not require Git or versioning
  tooling after they are built.
- Fetch full tag history in release jobs and retain exact tag-to-binary version validation.
- Allow untagged development builds to report an SCM-derived development version without weakening
  strict semantic-version validation at release and update boundaries.
- Remove the manually maintained version literal from `evdb.__init__`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `release-distribution`: Make the exact semantic-version Git tag the release version source while
  preserving consistent package, executable, archive, state, and status version identity.

## Impact

- Changes Python build configuration, generated-file ignores, package version imports, release checkout
  depth, version tests, and the development lock.
- Adds `setuptools-scm` as a build-only dependency; installed wheels and standalone binaries gain no
  runtime dependency.
- Changes untagged developer builds from a release-like literal to a PEP 440 development version.

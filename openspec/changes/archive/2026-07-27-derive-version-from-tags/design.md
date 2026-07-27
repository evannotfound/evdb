## Context

The package currently stores a release version literal in `evdb.__init__`, derives Python metadata from
that attribute, and checks the value against the pushed tag in release CI. This catches mismatches but
still requires an easy-to-forget source edit. Release builds already run from an exact Git tag, use
`uv` and setuptools, and install the project before building the standalone executable.

The runtime cannot depend on Git metadata or Python packaging tools because releases are one-file
executables installed on hosts without Python. Wheel and source archive builds must also retain a
version after leaving a Git checkout.

## Goals / Non-Goals

**Goals:**

- Make the semantic-version Git tag the only release version input.
- Preserve matching package metadata, CLI output, status output, release archives, and installed state.
- Embed the resolved version in wheels, source archives, and PyInstaller executables.
- Keep version derivation tooling out of production runtime dependencies.
- Give untagged developer checkouts an identifiable development version.

**Non-Goals:**

- Automate tag creation, pushing, or GitHub Release publication outside the existing workflow.
- Publish Python packages to a registry.
- Relax exact semantic-version validation for tags, updates, or installed version directories.

## Decisions

### Derive versions with setuptools-scm

Add `setuptools-scm` to the isolated build requirements and configure it to write
`src/evdb/_version.py`. `evdb.__init__` imports the generated value instead of containing a literal.
An exact `vX.Y.Z` tag resolves to `X.Y.Z`; commits after a tag resolve to a PEP 440 development version.

The generated module is preferred over reading package metadata or invoking Git at runtime. It is
included in source archives and wheels, and PyInstaller can analyze it as ordinary Python code. The
tool remains build-only and is absent from the installed runtime.

Alternatives considered were a release script that edits and commits the literal, and a workflow step
that rewrites source before building. A release script can still be bypassed, while workflow rewriting
makes local package metadata and source archives dependent on CI behavior. SCM derivation keeps one
authoritative input across all build paths.

### Give release jobs complete tag metadata

The check and architecture build jobs will use full-history checkouts. Shallow tag checkouts can hide
the nearest tag or annotated tag metadata from SCM version discovery. Release-note and publication jobs
already fetch full history.

### Keep release validation stricter than development versions

The existing semantic-version regex continues to validate release tags, requested updates, release
directories, and downloaded binaries. Untagged development versions can contain PEP 440 `.dev` and
local metadata, so the general package-consistency test will compare metadata, CLI, and status values
without treating a development build as a valid release identifier. Tagged CI continues to require the
executable value to equal the exact tag without its leading `v`.

## Risks / Trade-offs

- [A shallow or tagless source checkout cannot derive a useful version] -> Release jobs fetch full
  history, normal development uses a Git checkout, and built source archives carry generated version
  metadata.
- [Generated source can become stale in a working tree] -> Ignore `_version.py`; each `uv` package
  build regenerates it from current SCM state.
- [Development versions are not strict release SemVer] -> Keep them out of release and update inputs,
  while retaining exact release gates in CI and runtime installation code.
- [Changing build tooling can affect standalone packaging] -> Build and smoke-test editable metadata,
  wheel, source archive, and PyInstaller outputs before completing the change.

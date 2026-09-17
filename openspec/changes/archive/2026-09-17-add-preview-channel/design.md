## Context

Stable releases already build standalone executables natively with uv and PyInstaller, checksums,
and GitHub attestations. The installer uses POSIX shell and verifies bytes before execution and atomic
replacement. SCM defaults produce development versions but must ignore the new rolling tag.

## Goals / Non-Goals

**Goals:** Publish one opt-in preview from successful main builds, preserve stable behavior, retain
SCM identity, and prevent mixed downloads and publication regression.

**Non-Goals:** Historical preview installation, automatic channel tracking, new host dependencies,
timestamp-generated versions, or changes to tagged release-note content or generation tooling.

## Decisions

- Use a separate main-push workflow with the existing native runner matrix, checks, build command,
  checksum validation, and attestation action. Capture the source version once and compare each
  executable exactly against it. Accept clean SCM development versions and exact stable versions
  when main is itself tagged; never invent a version to force a prerelease suffix.
- Configure `scm.git.describe_command` with `--match v[0-9]*` using setuptools-scm 9's supported
  configuration. Keep the default development version scheme.
- Publish commit-qualified executables and checksum assets. A bounded one-line `preview.txt` contains
  the commit, exact embedded version, workflow run number, and attempt. Asset names include the run
  and attempt so rebuilding a commit cannot mutate bytes already selected by an installer.
  Installers read the manifest once and validate
  each field before constructing asset URLs. This avoids a JSON parser dependency on managed hosts.
- Serialize the publication job without canceling active publication. Use GitHub's `queue: max` so
  a late older build cannot evict a newer waiting publisher. Store a monotonically
  increasing workflow run number in the release title before mutations, and reject older publishers.
  This also protects interrupted publication. Keep the current and immediately preceding build's
  assets. Upload binaries and checksums before switching the manifest; retain the existing installer
  URL at `releases/download/preview/install.sh`.
- Stable downloads keep their filenames and latest/exact-tag selection. `--preview` is exclusive
  with positional versions. Preview validates the manifest version and requires an exact executable
  match; it performs no ordering comparison between semantic and PEP 440 versions.
- Stable changelog generation selects the previous non-draft, non-prerelease release and ignores
  the rolling preview, so preview publication cannot truncate the stable release comparison.

## Risks / Trade-offs

- GitHub asset replacement deletes before uploading: the small installer/manifest replacement window
  can return an HTTP failure. Fail before replacement and let the operator rerun; a fetched manifest
  never combines binary and checksum generations. Retaining the preceding build covers normal overlap.
- A download spanning more than one subsequent publication can lose its old assets: fail safely and
  rerun for the current preview. Historical installation is not promised.
- Interrupted publication reserves its run number: a rerun of that run or a newer run can finish it;
  older runs cannot roll back the channel. A draft is used for first publication.
- Native arm64 and attestation execution require GitHub Actions: local tests exercise installer,
  validation, SCM tag behavior, and publication ordering with disposable fixtures.

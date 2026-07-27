## Context

The tagged release workflow checks the source version, builds and validates ARM64 and AMD64 archives,
attests them, and publishes a GitHub Release. GitHub-generated notes do not inspect implementation
details, so they often emphasize commit mechanics instead of operator-visible behavior.

The first release-note implementation generated a deterministic evidence file and used a dedicated
OpenCode agent that could not run Git or shell commands. That boundary also prevented the model from
following useful context, selecting investigative commands, or adapting its analysis to the release.
The desired workflow is intentionally more autonomous: give OpenCode the target tag and normal tools,
then let it decide how to inspect the repository.

## Goals / Non-Goals

**Goals:**

- Produce release notes grounded in actual diffs and relevant repository context.
- Let OpenCode discover the previous non-draft release and investigate commits, pull requests, source,
  configuration, and documentation with normal tools.
- Use clear feature, improvement, bugfix, and breaking-change sections whose length reflects the
  release rather than a fixed editorial limit.
- Keep provider credentials out of source and retain a read-only boundary around GitHub access.
- Fall back to GitHub automatic notes without blocking an otherwise valid release.
- Cover an initial release with no previous published release.

**Non-Goals:**

- Let the notes job publish releases or access built release assets.
- Install OpenCode, Node.js, Python, or model credentials on managed database hosts.
- Replace artifact checksums, attestations, version gates, or output validation.
- Generate changelogs for untagged development builds.

## Decisions

### Run one autonomous OpenCode command

Release CI installs the exact tested OpenCode CLI version and invokes `.opencode/commands/changelog.md`
with the target tag as its argument. The command selects `openai/gpt-5.6-sol` with the `high` variant
directly, so no project-specific release-note agent is needed. OpenCode uses its built-in default agent
and may run `gh`, Git, shell commands, searches, and repository reads as needed.

The command identifies the latest previous non-draft GitHub release, inspects the range through the
target tag, and follows relevant implementation context before writing `release-notes.md`. Commit and
pull request text provide context, but retained claims must be supported by the actual changes. For an
initial release, the command summarizes the usable product instead of enumerating setup commits.

### Categorize notes by user-visible effect

The output uses `Features`, `Improvements`, `Bugfixes`, and `Breaking changes` in that order and omits
empty sections. Each distinct notable change receives one bullet. Commits that implement one feature
are combined, while unrelated user-visible effects are separated even when they share a commit. There
is no fixed bullet or word limit. A release with no notable user-visible changes contains exactly
`No notable changes.` instead of empty categories.

Required operator action belongs in the relevant bullet rather than a vague upgrade section. A short
opening summary is optional when the release has a clear theme.

### Give GitHub read access without publication authority

The notes job keeps `contents: read` and `pull-requests: read`, checks out full history without
persisted credentials, and passes its ephemeral job token as `GH_TOKEN`. OpenCode can therefore
inspect releases and pull requests with `gh`, but the token cannot publish or modify repository
contents. The notes job receives no release assets, OIDC permission, attestation permission, or
write-scoped publication token.

Normal shell access intentionally broadens the trust boundary compared with the dedicated-agent
design. Shell commands run in the OpenCode process environment and can inspect the model credential;
this is an accepted consequence of unrestricted command access. The job remains disposable, external
plugins are disabled with `--pure`, output containing an exact credential value is rejected, only
`release-notes.md` is uploaded, and publication happens later in a separate write-scoped job.

### Inject provider configuration only for the release process

The workflow passes `RELEASE_LLM_URL` and `RELEASE_LLM_KEY` from GitHub Actions secrets. A
release-scoped `OPENCODE_CONFIG_CONTENT` configures the built-in OpenAI provider with environment
interpolation, the `gpt-5.6-sol` model limits, and its `high` reasoning variant. Resolved provider
values are not committed or passed in command arguments.

### Validate output and preserve publication fallback

After OpenCode exits, a small helper requires `release-notes.md` to be a regular, non-empty UTF-8 file
within a conservative size limit. The notes job uploads valid output, and the publish job downloads and
validates it again. Successful validation selects `gh release create --notes-file`; any setup,
generation, transfer, or validation failure selects `--generate-notes` instead.

Artifact assembly, attestation, and release publication remain separate from note generation. Both
publication paths use the same verified tag, title, installer, archives, checksums, and attestations.
Immediately before publication, the workflow verifies that the tag still resolves to the immutable
workflow event commit.

## Risks / Trade-offs

- **Autonomous commands inspect untrusted repository content.** The notes job is disposable and has
  read-only GitHub authority, but normal shell access is an intentionally broader trust decision.
- **The model selects the wrong range or misstates a change.** The prompt requires published-release
  discovery and actual diff inspection; invalid or failed output falls back to GitHub notes.
- **The endpoint, GitHub API, model, or OpenCode is unavailable.** Generation remains non-blocking and
  the release uses GitHub automatic notes with unchanged verified assets.
- **A pinned OpenCode version becomes stale.** Its exact version remains visible in the workflow and is
  updated only with corresponding command and workflow tests.
- **Repository investigation adds latency.** Releases are infrequent, and the notes job has a bounded
  timeout.

## Migration Plan

1. Replace the deterministic command and dedicated agent with an autonomous command.
2. Remove generated release evidence while retaining output validation.
3. Give the notes job read-only GitHub metadata access and pass the target tag to OpenCode.
4. Update tests and exercise generated-note and fallback publication paths.
5. Roll back by publishing with `--generate-notes`; release artifacts and installed hosts require no
   migration.

## Open Questions

None.

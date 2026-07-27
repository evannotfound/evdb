## Why

GitHub's automatic release notes expose raw pull request and commit metadata but do not reliably
explain operator-visible changes, safety implications, or required action. The release workflow should
let OpenCode investigate each release directly so its judgment is informed by the repository rather
than constrained by a precomputed summary.

## What Changes

- Run a non-interactive OpenCode command using the custom OpenAI-compatible endpoint and
  `openai/gpt-5.6-sol` at high reasoning effort.
- Pass only the target release tag and let OpenCode use GitHub release metadata, Git, shell commands,
  and repository tools to discover and inspect the relevant changes autonomously.
- Configure the model on the command and use OpenCode's built-in agent instead of maintaining a
  dedicated release-note agent or deterministic input file.
- Write release notes under clear change categories, with the number of bullets determined by the
  number of notable user-visible changes.
- Publish validated OpenCode notes when generation succeeds and preserve GitHub-generated notes as a
  non-blocking fallback when generation or validation fails.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `release-distribution`: Require autonomous repository-aware release-note generation, secret-safe
  provider configuration, output validation, and deterministic publication fallback.

## Impact

- Changes `.github/workflows/release.yml` and its configuration tests.
- Adds one autonomous command under `.opencode/` without a project-specific agent.
- Keeps a small release-note output validator under `tools/` but removes generated release evidence.
- Adds an exact OpenCode CLI release dependency to release CI only; installed evdb binaries and
  managed hosts remain independent of OpenCode, Node.js, Python, and model credentials.
- Requires the existing model endpoint secrets and an ephemeral read-only GitHub token in the notes
  job.

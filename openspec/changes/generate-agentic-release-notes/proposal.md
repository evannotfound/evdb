## Why

GitHub's automatic release notes expose raw pull request and commit metadata but do not reliably
explain operator-visible changes, safety implications, or upgrade requirements. The release workflow
should use the same repository-aware OpenCode agent pattern proven by OpenCode itself so each evdb
release has concise notes grounded in the actual implementation.

## What Changes

- Generate release notes with a non-interactive OpenCode agent using the custom OpenAI-compatible
  endpoint and `openai/gpt-5.6-sol` at high reasoning effort.
- Give the agent a deterministic semantic-version commit range with hardened patches and allow it to
  inspect relevant repository files before writing user-facing Markdown.
- Restrict the release-note agent to repository reads and writing one generated notes file; deny all
  shell commands, unrelated edits, external paths, and network tools.
- Keep endpoint credentials in GitHub Actions secrets and inject release-only provider configuration
  through environment interpolation without committing resolved credentials or changing developers'
  normal OpenCode configuration.
- Publish the generated file as the GitHub Release body when generation succeeds, and preserve
  GitHub-generated notes as a non-blocking fallback when OpenCode, the endpoint, or output validation
  fails.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `release-distribution`: Require repository-aware agentic release-note generation, constrained tool
  access, secret-safe provider configuration, output validation, and deterministic fallback during
  tagged release publication.

## Impact

- Changes `.github/workflows/release.yml` and its configuration tests.
- Adds a release-note OpenCode agent and command under `.opencode/` plus a deterministic changelog
  input helper under `tools/`.
- Adds an exact OpenCode CLI release dependency to release CI only; installed evdb binaries and
  managed hosts remain independent of OpenCode, Node.js, Python, and model credentials.
- Requires GitHub Actions secrets for the OpenAI-compatible base URL and API key.

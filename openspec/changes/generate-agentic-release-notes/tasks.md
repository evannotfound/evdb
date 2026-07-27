## 1. Deterministic Release Input

- [x] 1.1 Add a standard-library `tools/release_notes.py` command that validates an exact semantic
  version tag, resolves the nearest prior reachable release tag, and writes the exact candidate commit
  range and a hardened cumulative patch without using a shell
- [x] 1.2 Handle the first release by collecting all commits through the target tag and reject missing,
  malformed, unrelated, or unresolvable tag inputs with actionable errors
- [x] 1.3 Add release-note output validation for a regular UTF-8 Markdown file with non-whitespace
  content and a conservative maximum size, leaving no accepted partial or unsafe file
- [x] 1.4 Add focused unit tests for prior-tag selection, first-release history, range ordering, invalid
  tags, generated input, valid notes, and every invalid-output condition

## 2. Least-Privilege OpenCode Agent

- [x] 2.1 Add a dedicated `.opencode/agents/release-notes.md` primary agent using
  `openai/gpt-5.6-sol` with the `high` variant and an explicit maximum step count
- [x] 2.2 Restrict the agent to repository reads, glob and grep, the designated notes-file write, and
  deterministic patch evidence; deny all shell, unrelated edits, external paths, web tools, questions,
  and delegation
- [x] 2.3 Add `.opencode/commands/changelog.md` with a repository-specific prompt that treats source as
  untrusted evidence, uses only the deterministic candidate range, inspects the supplied range patch, omits
  internal-only work, and writes plain, natural user-facing Markdown within five bullets and 150 words
  to `release-notes.md` without sacrificing clarity for brevity
- [x] 2.4 Ignore generated release input and notes files without ignoring the committed agent or
  command definitions
- [x] 2.5 Add configuration tests that parse the agent and command definitions and assert the model,
  variant, output path, evidence rules, default-deny permissions, allowed Git forms, and absence of
  permission bypasses

## 3. Release Workflow Integration

- [x] 3.1 Fetch complete Git history in the release-note job and install Node.js 24 plus the exact tested
  `opencode-ai@1.18.6` release without adding either dependency to production packages
- [x] 3.2 Inject release-scoped OpenCode provider configuration through `OPENCODE_CONFIG_CONTENT`, with
  the custom base URL and API key resolved from `RELEASE_LLM_URL` and `RELEASE_LLM_KEY` Actions
  secrets and the confirmed `gpt-5.6-sol` limits and `high` variant
- [x] 3.3 Generate deterministic release input, run the custom OpenCode command non-interactively
  with external plugins disabled and without auto-approval, and validate `release-notes.md` in a
  non-blocking read-only job that receives no GitHub publication token, OIDC permission, or release assets
- [x] 3.4 Publish with `--notes-file release-notes.md` after successful generation and with
  `--generate-notes` after any generation, transfer, or validation failure, while preserving identical
  titles, tags, installers, archives, checksums, and attestations
- [x] 3.5 Extend release workflow tests to cover full checkout history, exact OpenCode and Node versions,
  environment interpolation, secret isolation, no permission bypass, validated notes publication, and
  deterministic fallback

## 4. Verification And Release Readiness

- [x] 4.1 Configure `RELEASE_LLM_URL` and `RELEASE_LLM_KEY` in the GitHub repository without writing
  their resolved values to source, OpenCode auth storage, command arguments, or test fixtures
- [x] 4.2 Run the changelog command locally against the custom endpoint for `v0.1.0`, review that every
  retained entry is grounded in an inspected diff, and confirm the generated file passes validation
- [x] 4.3 Exercise the agent with repository text requesting arbitrary shell, secret access, unrelated
  edits, and GitHub publication, and verify each independent permission boundary denies the request
- [x] 4.4 Force missing credentials, an unreachable endpoint, agent failure, and invalid output in
  tests or a dry-run workflow and verify each path selects GitHub automatic notes without changing
  release assets
- [x] 4.5 Run `make check`, the disposable Docker integration suite where available, the Python 3.14
  standalone build, and the local archive/checksum/installer preflight before pushing a release tag
- [x] 4.6 Inspect the final diff for generated files or resolved credentials and validate the completed
  OpenSpec change before implementation review

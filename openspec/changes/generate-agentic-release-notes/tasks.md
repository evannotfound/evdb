## 1. Autonomous Changelog Command

- [x] 1.1 Configure `.opencode/commands/changelog.md` with the release model and target-tag argument,
  and let OpenCode discover the previous non-draft release with GitHub and repository tools
- [x] 1.2 Remove the dedicated release-note agent and generated `release-input.md` path
- [x] 1.3 Categorize output under non-empty feature, improvement, bugfix, and breaking-change sections
  with one bullet per distinct notable change and no fixed editorial limit

## 2. Release Workflow

- [x] 2.1 Pass the target tag and ephemeral read-only GitHub token to OpenCode while retaining the
  separate, write-scoped publication job
- [x] 2.2 Reduce `tools/release_notes.py` to output validation and remove deterministic Git evidence
  generation
- [x] 2.3 Preserve validated note transfer, exact tag verification, and GitHub automatic-note fallback

## 3. Tests And Specification

- [x] 3.1 Update command, validator, and workflow tests for autonomous investigation and remove dedicated
  agent and deterministic-input assertions
- [x] 3.2 Align the proposal, design, and release-distribution requirements with the autonomous workflow
- [x] 3.3 Run focused tests and the full repository test suite
- [x] 3.4 Review the final diff for unintended generated files, credentials, or unrelated changes

## Context

The tagged release workflow currently checks the source version, builds and validates ARM64 and
AMD64 archives, attests them, and publishes a GitHub Release with `--generate-notes`. Those notes are
derived from GitHub metadata and do not inspect the implementation, so they often emphasize commit
mechanics instead of operator-visible behavior, compatibility, and data-safety effects.

OpenCode's own repository solves the same problem by giving a non-interactive OpenCode agent a
deterministic release range, requiring it to inspect real diffs, and using the resulting Markdown as
the release body. The available OpenAI-compatible endpoint supports tool calling and exposes
`gpt-5.6-sol` with a 353,000-token context window and a `high` reasoning variant. Release automation
must not expose its API key, broaden repository mutation, or add OpenCode to installed evdb hosts.

## Goals / Non-Goals

**Goals:**

- Produce release notes grounded in the exact tagged source and actual diffs, using one natural opening
  sentence, at most five bullets and 150 words, and plain language that stays clear rather than terse.
- Run a real OpenCode agent that can investigate candidate commits before deciding what is notable.
- Constrain the agent to repository reads, deterministic patch evidence, and one generated output.
- Keep provider credentials out of source, process arguments, logs, generated notes, and artifacts.
- Fall back to GitHub automatic notes without blocking an otherwise valid release.
- Cover the first release, which has no prior semantic-version tag.

**Non-Goals:**

- Let the agent create tags, releases, commits, pull requests, or release assets.
- Let the agent modify source, invoke builds or tests, access external directories, or use web tools.
- Install OpenCode, Node.js, Python, or model credentials on managed database hosts.
- Replace artifact checksums, attestations, version gates, or human review of release output.
- Generate changelogs for untagged development builds.

## Decisions

### Run OpenCode as the release-note editor

Release CI will install an exact tested OpenCode CLI version, initially `opencode-ai@1.18.6`, and run
a project command with the dedicated release-note agent. The agent will use
`openai/gpt-5.6-sol` with the `high` variant and will write `release-notes.md`.

This is preferred over one direct Responses API call because OpenCode can use repository tools to
verify commit messages against source changes. A direct completion would require sending a bounded
preselected patch and could miss effects outside that selection. GitHub automatic notes remain the
fallback rather than the primary editor.

### Separate deterministic range selection from agent judgment

A standard-library development tool will validate the release tag, find the nearest prior reachable
semantic-version tag, verify it still resolves to the immutable workflow event commit, and write a
structured input containing the exact range, candidate commits, changed paths, and one cumulative
range patch. It invokes hardened Git forms with subprocess argument arrays rather than a shell. For
the first release it will include all commit metadata through the tagged commit and diff the empty
tree to that commit, avoiding repeated historical churn. Release CI will fetch full history before
running this tool.

The agent command will treat this input as the authoritative candidate set and inspect the cumulative
range patch for every retained claim. It cannot invoke Git or any shell, rebuild the range, or infer entries
from unrelated history. It will omit internal-only work and produce only user-facing Markdown. This
follows OpenCode's raw-input-plus-editor pattern while keeping range selection and diff collection
deterministic and unit-testable.

### Use a dedicated least-privilege agent

The release-note agent will permit repository read, glob, and grep operations. Edit permission will
deny all paths except `release-notes.md`. All Bash access, external-directory access, web tools,
questions, task delegation, and unrelated writes will be denied, and non-interactive execution will
use `--pure` and will not use `--auto` or another permission bypass.

Repository files, commit messages, and patches will be identified as untrusted evidence rather than
instructions. Generated input and output paths will be ignored by Git. The agent step will not receive
`GITHUB_TOKEN`, so even a model or prompt failure cannot publish or mutate GitHub state.

The alternative was to run the default build agent with broad permissions, as a trusted maintainer
might do interactively. It was rejected because CI processes contributor-authored repository content
and needs a much narrower authority boundary.

### Inject provider configuration only for the release process

The workflow will pass `RELEASE_LLM_URL` and `RELEASE_LLM_KEY` from GitHub Actions secrets. A
release-scoped `OPENCODE_CONFIG_CONTENT` value will configure the built-in OpenAI provider with
`baseURL: "{env:RELEASE_LLM_URL}"`, `apiKey: "{env:RELEASE_LLM_KEY}"`, the `gpt-5.6-sol` model
limits, and its `high` reasoning variant.

No resolved URL or key will be written to the worktree or OpenCode auth storage. Keeping provider
configuration in the release environment avoids changing normal local OpenCode sessions or requiring
the CI key-file pattern used on the operator's workstation.

### Validate output and preserve deterministic publication

After OpenCode exits, the helper will require a regular UTF-8 Markdown file with non-whitespace
content and a conservative maximum size. A separate read-only job with no persisted checkout token,
OIDC permission, release assets, or publication token will run OpenCode with external plugins disabled
and upload validated notes. The publish job will revalidate a downloaded notes artifact. Successful
validation makes `gh release create` use `--notes-file`; any installation, configuration, provider,
generation, upload, download, or validation failure selects `--generate-notes` instead.

Artifact assembly, attestation, and GitHub Release publication remain separate from the agent. Both
publication branches use the same verified tag, title, installer, archives, checksums, and
attestations. Immediately before publication, a mandatory full-history check verifies that the tag
still resolves to the immutable workflow event commit; mismatch or cancellation blocks publication
rather than selecting the notes fallback. The release job logs which note source it selected without
printing model credentials.

## Risks / Trade-offs

- [Repository text attempts prompt injection] -> Mark all source evidence as untrusted, restrict tools
  independently of the prompt, disable permission auto-approval, and withhold GitHub credentials.
- [Git inspection invokes repository-defined helpers] -> Collect patches before the model runs using
  subprocess argument arrays, `--no-ext-diff`, and `--no-textconv`; deny all agent shell access.
- [The model omits or misstates a change] -> Supply exact candidate commits, require actual diff
  inspection, prohibit unsupported claims, retain human review, and keep notes informational rather
  than part of the artifact trust chain.
- [Endpoint, model, or OpenCode is unavailable] -> Validate output and publish GitHub automatic notes
  without blocking checked and attested binaries.
- [Pinned OpenCode becomes stale] -> Keep the exact version visible in the workflow and update it only
  after local command and permission tests pass.
- [Full repository inspection adds release latency] -> Run one agent only after deterministic checks;
  accept the bounded latency because releases are infrequent and the endpoint is unmetered.

## Migration Plan

1. Add the deterministic input/validation tool, dedicated agent, and changelog command with local
   fixture tests.
2. Configure the repository's `RELEASE_LLM_URL` and `RELEASE_LLM_KEY` Actions secrets.
3. Update and test the release workflow, including success and forced-fallback configurations.
4. Generate and review `v0.1.0` notes locally against the custom endpoint before pushing the tag.
5. Roll back by removing the OpenCode generation step and publishing with the existing
   `--generate-notes`; no release artifacts, installed hosts, or persistent state require migration.

## Open Questions

None. The endpoint, model ID, context limits, high reasoning variant, tool-call support, and
fallback policy have been confirmed.

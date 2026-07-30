---
description: Generate repository-grounded notes for an evdb release
model: openai/gpt-5.4
variant: high
---

Generate `release-notes.md` for the evdb release tagged `$ARGUMENTS`.

If `release-notes.md` already exists, ignore its contents completely. Do not preserve, merge, or reuse
text from it.

Before writing:

- Work autonomously. Use `gh`, Git commands, repository search, and any other available tools as needed.
- Use GitHub release metadata to find the latest non-draft release before the target tag. The target
  release may not exist yet.
- Inspect the commits and real diffs from the previous release to the target. Read relevant source,
  configuration, and documentation when needed to understand the user-visible effect.
- Use pull request descriptions and commit messages as context, but treat the implementation as
  authoritative. Ground every retained claim in an inspected change.
- If there is no previous release, treat this as the initial release and summarize the usable product
  instead of listing setup commits.

Rules:

- Write sections in this order: `## Features`, `## Improvements`, `## Bugfixes`,
  `## Breaking changes`.
- Include only sections with at least one entry and leave a blank line between a heading and its bullets.
- Use `-` Markdown bullets, one for each distinct notable change.
- Let the number of bullets reflect the release. Combine commits that deliver one change, and separate
  unrelated user-visible changes even when they share a commit.
- There is no fixed bullet or word limit for the document. Do not reduce the number of bullets merely
  to make the release look shorter.
- Keep only user-visible or operator-visible changes. Internal work belongs only when it produces a
  real user outcome, such as fixing a race, improving compatibility, or making backups safer.
- Put required operator action directly in the relevant `## Breaking changes` bullet.
- Do not add an opening summary by default. Use one short sentence only when the release has a clear
  overall theme and the sentence does not repeat the bullets.
- Start each bullet with a capital letter and end it with a period.
- Prefer direct verbs such as `Add`, `Fix`, `Keep`, `Prevent`, `Restore`, `Support`, and `Use`.
- Do not use raw commit prefixes, trailing pull request numbers, commit hashes, contributor roll calls,
  generated-by notices, repetitive bold labels, vague filler, or empty sections.
- If there are no notable user-visible changes, write exactly `No notable changes.`

**Importantly, these release notes are for evdb users, who are at least slightly technical. They
operate databases through the CLI, configure hosts, create and verify backups, restore data, and
update installations. Be thorough when investigating user-visible effects because they may not be
obvious. A dependency upgrade may fix a real backup failure. A refactor may prevent a race condition
or make restores safer. Pull request descriptions and commit messages often explain the intended
outcome, while the actual diff confirms it.**

**Investigate deeply, write lightly. Your research does not belong in the release notes. Understanding
every effect does not mean describing every implementation detail. Use editorial judgment to keep only
the shortest useful conclusion for users.**

Writing style:

- Write plain, direct Markdown like a maintainer briefly telling users what changed.
- Focus on writing the least words needed. Users will skim the changelog.
- Keep each bullet to one observable change and one short sentence. Most bullets should be roughly
  8-16 words; exceed that only for a necessary condition, command, configuration key, or operator action.
- Prefer one clear clause. Avoid semicolons, long lists, and chains of `while`, `so that`, or `keeping`.
- State the primary outcome. Omit secondary effects when they do not change what users need to know.
- Do not list every affected command, package, output, code path, or internal subsystem.
- Omit development-only behavior unless it directly affects released software or its users.
- Use technical names only when users recognize or act on them, such as an evdb command, database
  engine, configuration key, or backup backend.

Examples below show the desired editing style. Do not copy an example unless the inspected release
actually contains that change.

Bad:

- Release builds now derive and embed their version from the exact Git tag, keeping package metadata,
  `evdb --version`, runtime status, and standalone executables aligned. Builds from untagged revisions
  report an SCM-derived development version.

Good:

- Use the exact Git tag as the release version.

Bad:

- Restore validation was refactored to compare database metadata before replacing the active data
  directory, preventing backups from unrelated roles or hosts from being restored.

Good:

- Prevent restoring backups from another database or host.

Bad:

- Status collection now preserves partial service results when one health check fails, allowing
  operators to inspect the remaining healthy databases.

Good:

- Keep healthy databases visible when one status check fails.

Bad:

- Updated backup scheduling internals to avoid multiple overlapping processes operating on the same
  repository.

Good:

- Prevent overlapping backups in the same repository.

Example structure:

```markdown
## Features

- Add Dragonfly support for Redis-compatible databases.

## Improvements

- Keep backup progress visible during long uploads.

## Bugfixes

- Prevent failed restores from replacing healthy data.

## Breaking changes

- Require explicit database roles in host configuration.
```

Write no other file and do not publish the release.

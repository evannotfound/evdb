---
description: Generate repository-grounded notes for an evdb release
model: openai/gpt-5.6-sol
variant: high
---

Generate `release-notes.md` for the evdb release tagged `$ARGUMENTS`.

1. Use GitHub release metadata to find the latest non-draft release before the target tag. The target
   release may not exist yet. If there is no previous release, treat this as the initial release.
2. Work autonomously. Use `gh`, Git commands, repository search, and any other available tools as
   needed to understand the release. Inspect the commits and real diffs from the previous release to
   the target, then read relevant source, configuration, and documentation to understand their effects.
   Commit messages and pull request context are useful leads, but the implementation is authoritative.
3. Retain only user-visible or operator-visible behavior. Prioritize backup and restore safety,
   compatibility, configuration changes, installation and upgrade effects, security, and required
   operator action. Omit internal refactors, test-only changes, CI mechanics, documentation-only
   corrections, and implementation details unless they materially affect operators.
4. Ground every statement in an inspected change. Do not repeat unsupported commit or pull request
   claims. For an initial release, summarize the usable product rather than listing setup commits.
5. Organize the notes under `## Features`, `## Improvements`, `## Bugfixes`, and
   `## Breaking changes`, in that order. Include only sections with at least one entry. Put required
   operator action directly in the relevant bullet. A short opening summary is optional when the
   release has a clear theme.
6. Use `-` Markdown bullets, one for each distinct notable change. Let the number of bullets reflect
   the release: combine commits that implement one feature, and separate unrelated user-visible
   changes even when they share a commit. There is no fixed bullet or word limit.
7. Write plain, direct Markdown for technical users who will skim it. Start each bullet with a capital
   letter and prefer what changed for users over internal implementation details. Avoid raw commit
   prefixes, trailing pull request numbers, commit hashes, contributor roll calls, generated-by notices,
   unnecessary jargon, vague filler, and empty sections.
8. If there are no notable user-visible changes, write exactly `No notable changes.`

Write no other file and do not publish the release.

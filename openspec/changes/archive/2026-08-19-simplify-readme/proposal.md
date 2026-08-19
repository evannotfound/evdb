## Why

The public documentation is verbose but still puts evdb commands before the software and external
services a new operator must prepare. A single linear README should take a user from prerequisites to
their first database, then provide concise CLI guidance for later operations.

## What Changes

- Remove the `docs/` topic-documentation tree and its README links.
- Rewrite the README around prerequisites, DNS and remote-storage preparation, evdb installation,
  guided initialization, one first Postgres database, and advanced CLI usage.
- Keep the existing headline, slogan, badges, and core Linux-server description while replacing the
  command-first introduction with a concise summary of what evdb manages.
- Describe the host as Linux with systemd, with Ubuntu as the tested path, without a version or
  architecture matrix.
- Link to official Docker, Restic, rclone, and Traefik provider documentation instead of reproducing
  third-party installation instructions.
- Keep the consequential backup boundaries clear: evdb does not restore databases or prune remote
  snapshots.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `release-distribution`: Replace the README-plus-topic-documentation entry point with one concise,
  linear README and CLI help as the public usage reference.

## Impact

- Public documentation changes are limited to `README.md` and removal of `docs/`.
- The `release-distribution` specification changes to match the reduced documentation surface.
- Runtime behavior, CLI behavior, release assets, configuration, and managed hosts do not change.

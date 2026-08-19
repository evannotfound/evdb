## Context

The README currently opens with evdb commands, compresses prerequisites into one sentence, and then
duplicates details from eight topic documents. The topic documents preserve implementation detail but
do not form a clear first-use path. The CLI already exposes its command hierarchy and option help.

## Goals / Non-Goals

**Goals:**

- Make the README a linear guide from host preparation through the first working database.
- Keep installation concise by linking authoritative third-party instructions.
- Retain enough CLI and backup information for safe routine operation.
- Remove the separate public topic-documentation tree.

**Non-Goals:**

- Change evdb runtime, CLI, installation, routing, or backup behavior.
- Reproduce complete Docker, Restic, rclone, Traefik, recovery, or migration documentation.
- Document every CLI option in the README.

## Decisions

The README will preserve the existing title, slogan, three badges, and opening Linux-server sentence.
The command-first product example will become a short list of managed concerns so no evdb command
appears before prerequisites.

The first-use path will present host requirements, official prerequisite links, DNS and rclone setup,
evdb installation, guided initialization, and one Postgres example in that order. Dragonfly, Redis,
configuration, lifecycle, and backup commands will remain discoverable in the later CLI usage section.

The host requirement will say Linux with systemd and identify Ubuntu as the tested path. Exact Ubuntu
versions and release architectures are installer details and will not be part of the onboarding flow.

The README will name the missing external setup explicitly: clients need DNS records resolving
`*.<host-id>.<base-domain>` to the server, Traefik needs provider credential variables in a private
file, and backups need a normal-user rclone configuration and repository target.

The README will retain only backup limitations that materially affect data recovery: restore is manual
and remote snapshots are not pruned. Detailed internals, file modes, update pinning, migration notes,
and development VPS procedures will not move from `docs/` into the README.

## Risks / Trade-offs

- [Removing topic documents reduces operational detail] -> Keep normal workflows in the README, expose
  command options through CLI help, and retain behavioral contracts in OpenSpec.
- [Third-party installation links require leaving the README] -> Prefer current authoritative guidance
  over embedding long, distribution-specific commands that become stale.
- [Linux wording may imply broad distribution support] -> State that systemd is required and Ubuntu is
  the tested path without promising a distribution matrix.

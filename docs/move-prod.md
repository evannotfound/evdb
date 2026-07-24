# Draft production move

This is a plan for a separate OpenSpec change. Do not execute it while building this system.

## Required route changes

| Instance | Current HTTP route | Target HTTP route | Action |
| --- | --- | --- | --- |
| `oai-co-prod-02` | none | `oai-co-prod-02.kv-montreal-01.storage.evanovation.com` | create |
| `redefine-version-prod-01` | `redefine-version-prod-01.kv-na01.storage.evanovation.com` | `redefine-version-prod-01.kv-montreal-01.storage.evanovation.com` | update |
| `stock-selector-prod-01` | `stock-selector-prod-01.kv-na01.storage.evanovation.com` | `stock-selector-prod-01.kv-montreal-01.storage.evanovation.com` | update |

The other eight public routes are expected to remain unchanged. This is informational input
for the external proxy owner; `evanovation-db` does not inspect or apply these changes.

## Preflight

1. Create and approve a new `move-prod` change.
2. Re-read all 25 running containers, images, paths, Compose projects, domains, and ports.
3. Compare live facts with source config and stop on any engine or data-path difference.
4. Confirm fresh local backups, Restic snapshots, and successful restore checks for every
   durable instance.
5. Approve exact database, HTTP, and Traefik image digests.
6. Have the external proxy owner confirm its route and certificate plan separately.
7. Refresh 1Password references and rclone bootstrap escrow without exposing values.
8. Capture current symlink, Compose files, timer and cron state, and DNS for rollback.
   Review the Ansible diff separately from the external proxy plan.

## Staged move

1. Install the versioned app, JSON config, secrets, Compose files, and disabled units.
2. Validate config from the staged release before changing `current`.
3. Move one non-critical instance per engine, preserving engine, project, data path, and
   container identity. Prove native TLS/SNI isolation against a second instance.
4. Prove each HTTP sidecar is loopback-only, authenticated, and connected to its own backend.
5. Have the external proxy owner apply and verify its three route changes independently;
   leave legacy names available until DNS and clients are confirmed.
6. Migrate remaining instances in approved batches. Replace cron and enable timers only
   after backup, upload, status, and restore checks pass.
7. Observe at least one complete backup cycle before retiring old files or routes.

## Rollback

Disable the new timers, restore the prior `current` symlink, and restore prior Compose
ownership without moving data. The external proxy owner handles its own rollback and legacy
DNS if needed. Keep every backup created before or during the move. Do not
upgrade Restic format, prune repositories, rotate secrets, or delete old Compose files as
part of rollback.

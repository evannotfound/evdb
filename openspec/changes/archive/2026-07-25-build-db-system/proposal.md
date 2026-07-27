## Why

Evanovation's database setup is spread across host files, shared Compose templates, old backup scripts, and incomplete backup lists. We need one tested system that can describe, back up, restore, and manage the 25 supported database instances before production is moved to it.

## What Changes

- Build a Python 3.10+ command-line tool for config checks, backups, restore tests, status, and Restic work.
- Record the 14 Postgres, 7 Dragonfly, and 4 Redis instances currently managed under `/home/ubuntu/databases` without storing secret values.
- Create checked backup files for Postgres, Redis, and Dragonfly with hashes, metadata, safe local history, and independent failure handling.
- Create isolated restore tests that check database contents, key types, values, and TTL behavior.
- Add an optional serverless Redis HTTP sidecar to each KV instance, with all 11 enabled on unique loopback ports in the initial config.
- Preserve shared native database ports through Traefik TLS/SNI and leave public HTTP routing to the external proxy owner.
- Add Restic upload, snapshot tracking, retention, prune, and repository check support while keeping repository format v1.
- Add Ansible, Compose templates, and systemd units for the finished system and test them on disposable local hosts.
- Add local integration tests, CI, secret scanning, and operator documentation.
- Keep production unchanged. Deployment to `montreal-01`, replacement of cron, Restic upgrades, image changes, secret rotation, container restarts, and Compose takeover will be handled by a later production move.

## Capabilities

### New Capabilities
- `config`: Store and check host and instance settings, secret references, current engine types, images, ports, names, paths, resources, domains, and backup rules.
- `backup`: Create and check independent Postgres, Redis, and Dragonfly backup files with manifests, hashes, locks, and safe local history.
- `restore`: Restore backup files into isolated containers and check that the restored data is usable.
- `http`: Run authenticated serverless Redis HTTP sidecars and record the domain and loopback port contract for an external proxy.
- `restic`: Upload completed backups, record snapshot IDs, apply retention, prune repositories, and run repository checks.
- `jobs`: Provide direct CLI commands, status checks, logs, locks, timeouts, and systemd jobs for backup and maintenance work.
- `deploy`: Build versioned releases, render Compose and JSON config, resolve 1Password references, and deploy the system through Ansible without making production the default target.

### Modified Capabilities

None.

## Impact

- Adds the `evdb` Python package, configuration, tests, CI, documentation, Ansible roles, Compose templates, and systemd units.
- Uses Docker, Docker Compose, Traefik, Restic, rclone, Ansible, and the 1Password CLI at defined system boundaries.
- Production Python uses the standard library. Pytest, Ruff, Ansible, and any config tooling remain development or controller dependencies.
- Does not change production during this change. Read-only production facts may be used to build the checked-in config.

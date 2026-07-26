# Production migration boundary

Production conversion is a separate OpenSpec change. Do not execute production migration while
implementing or testing the host-local manager.

That change must inventory every live project, concrete engine, image, Compose identity, data path,
native domain, HTTP endpoint, credential file, timer, cron entry, Restic repository, and external
proxy route. It must resolve project/role naming collisions and compare all live facts with the
proposed host source before mutation.

The migration requires reviewed recovery points and explicit ownership boundaries:

1. Confirm a fresh checked backup, exact Restic snapshot, and recent isolated backup test for every
   durable role.
2. Record current Compose, data, listener, DNS, external HTTP route, credential, and schedule state.
3. Define the dedicated native Traefik cutover without taking ports from an existing listener.
4. Define credential import without writing secrets to Git, command arguments, logs, or migration
   artifacts.
5. Stage one role at a time, preserving concrete engine, major version, data, and public contracts.
6. Prove native SNI and loopback HTTP isolation before moving another role.
7. Enable packaged timers only after backup, status, and restore checks pass.
8. Keep prior files, schedules, routes, and every migration safety backup until observation and
   rollback windows close.

The production rollback must restore prior service definitions, routing ownership, and schedule
state without moving or deleting database data. The external HTTP proxy owner handles its own
route, certificate, and DNS recovery. Repository format upgrades, prune, engine migration, secret
rotation, and old-file deletion do not belong in the cutover.

Repository development and CI must not load, validate, or invoke commands against the production
configuration. They use disposable temporary paths and local test repositories only.

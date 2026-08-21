## Context

`status._host()` currently measures only free space for `Paths.state`, even though configuration now
contains an ordered `Host.data_roots` catalog and every role selects one root. The guided Host choice is
a read-only screen, while database roles already have lifecycle actions. `database.info()` knows the role
data path and delegates live facts to concrete engine modules, but it does not measure directory usage or
engine-native data totals.

## Goals / Non-Goals

**Goals:**

- Identify every managed storage path and the filesystem that currently backs it.
- Keep frequent root-menu status checks bounded while providing richer on-demand database details.
- Report physical role storage separately from engine-native logical or in-memory data.
- Provide one deliberate guided action that actually restarts Traefik and verifies its health.

**Non-Goals:**

- No expected-device configuration, mount creation, mount repair, or startup ordering.
- No per-table, per-key, or per-logical-database breakdown.
- No direct host restart command or change to database lifecycle behavior.
- No storage quotas, cleanup, forecasting, or background metric collection.

## Decisions

### Read the current Linux mount table and retain separate storage concepts

Host assessment reads `/proc/self/mountinfo`, decodes its escaped fields, and selects the longest mount
point containing each managed path. It reports mount point, source, filesystem type, and
`shutil.disk_usage()` capacity without exposing mount options. The canonical state path remains
`host.storage`; an additive `host.database_storage` list contains one item per configured root plus exact
role assignments. Existing structured status fields remain present, so the status version does not
change. Host health includes every configured storage location, and failures remain partial and
path-specific.

Using only the configured path was rejected because it cannot reveal that an intended custom mount is
currently backed by `/`. Calling `findmnt` was rejected because the kernel mount table provides the
required facts without adding another host tool dependency.

### Measure role files only in database Details

Database Details recursively sums allocated blocks under the selected role's data directory without
following symlinks. It combines that value with the same backing-filesystem facts used by host status.
The root menu does not scan role trees, because a large PostgreSQL data directory could make every guided
refresh slow.

Filesystem allocation and engine data are labeled separately. PostgreSQL reports the sum of
`pg_database_size` for connectable non-template databases and their count. Redis and Dragonfly report
dataset memory and total keys from `INFO memory` and `INFO keyspace`. A stopped role still reports path,
directory allocation, and filesystem capacity while explicitly marking live data unavailable.

### Restart only the installed Traefik service

The Host submenu presents Restart traffic, warns that active database connections may briefly drop, and
requires confirmation. The host operation takes the exclusive host lock, requires the installed Traefik
Compose file, invokes `docker compose restart traefik`, and polls the owned container's health within the
existing health timeout. It does not render files, run initialization, renew certificates, or touch
database Compose projects.

A Compose `up` was rejected because an unchanged service may not restart. Full initialization was
rejected because it performs unrelated DNS, certificate, repository, and systemd convergence.

## Risks / Trade-offs

- [Two configured paths use the same filesystem] -> Show both exact paths and assignments; repeated
  capacity values truthfully describe their shared backing filesystem.
- [A configured mount is absent] -> Show the actual fallback mount and source; evdb does not infer the
  intended device or repair it.
- [A role contains many files] -> Restrict recursive allocation measurement to an explicit Details view.
- [Live engine metrics fail] -> Preserve filesystem facts and report live data unavailable with a bounded
  error instead of hiding the rest of Details.
- [Traefik restart interrupts clients] -> Require confirmation, restart only Traefik, and wait for health
  before reporting completion.

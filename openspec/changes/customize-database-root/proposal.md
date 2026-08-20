## Why

Database files are fixed under `/var/lib/evdb/databases`, which prevents an operator from distributing
roles across dedicated storage paths. The host needs an allowlisted root catalog and an explicit
creation-time placement for each database role.

## What Changes

- **BREAKING**: require a non-empty ordered `host.data_roots` list and an exact `data_root` selection on
  every Postgres and KV role; no compatibility values are synthesized.
- Let fresh guided setup collect one or more roots, beginning with `/var/lib/evdb/databases`.
- Let guided database creation choose an allowlisted root. Direct creation uses the sole root
  automatically or requires an explicit selection when multiple roots exist.
- Derive every database bind source as `<selected-root>/<project>/<role>/data`.
- Validate each root, reject duplicates and overlaps, and reject role selections outside the catalog.
- Allow operators to edit the catalog in `config.yml` and rerun initialization; referenced roots cannot
  be removed.
- Reject changed role data binds when generated Compose records another root. evdb does not move data.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `config`: Define an allowlisted root catalog and explicit immutable placement for every database role.
- `host-setup`: Collect, validate, and prepare all configured roots and select placement during creation.
- `deploy`: Allow role data outside `/var/lib/evdb` while keeping all other production paths fixed.

## Impact

This changes the host and role source schema, initialization and database-creation CLI, path derivation,
generated Compose safety checks, and focused tests. It adds no runtime dependency and does not change
backup staging or repositories.

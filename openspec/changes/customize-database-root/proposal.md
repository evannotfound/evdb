## Why

Database files are fixed under `/var/lib/evdb/databases`, which prevents an operator from placing them
on a dedicated mounted disk during host setup. The host needs one explicit storage root while preserving
the existing project and role layout.

## What Changes

- **BREAKING**: require `host.data_root` in `config.yml`; fresh setup writes
  `/var/lib/evdb/databases` by default.
- Add guided and direct initialization input for one host-wide database data root.
- Derive every database bind source as `<data_root>/<project>/<role>/data`.
- Validate the selected root as a safe normalized absolute path and reject overlap with other managed or
  local repository paths.
- Reject a changed data bind source when an existing generated Compose file records another root. evdb
  does not move database data.
- Keep mount configuration and startup ordering under operator control.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `config`: Make the host database data root explicit, safe, and immutable after database generation.
- `host-setup`: Collect and prepare the host-wide database data root during initialization.
- `deploy`: Allow database data outside `/var/lib/evdb` while keeping all other production paths fixed.

## Impact

This changes the host source schema, initialization CLI and review, database path derivation, generated
Compose safety checks, documentation, and focused configuration and database tests. It adds no runtime
dependency and does not change backup staging or repositories.

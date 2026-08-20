## Context

`Paths.databases` currently fixes database files under `<state>/databases`, and `Database.data` derives
every engine bind source from it. Generated assets, backup staging, and locks also derive from `Paths`,
but they must remain in the canonical state tree when database files move to dedicated storage.

The source configuration is strict and human-owned. evdb does not retain configuration history or move
database data, while generated Compose already records the last bind source used for each managed role.

## Goals / Non-Goals

**Goals:**

- Store one explicit host-wide data root and use it for every database role.
- Make fresh guided and direct setup support a mounted-disk directory.
- Reject unsafe roots and prevent an existing generated role from silently switching to an empty path.
- Keep the canonical root as the setup default.

**Non-Goals:**

- No per-project or per-role data paths.
- No mount creation, persistence, startup ordering, or availability monitoring.
- No data copy, relocation, import, compatibility default, or source migration.
- No change to generated assets, backup staging, locks, or repositories.

## Decisions

### Put the root on Host and keep Paths as the canonical default

Add required `Host.data_root: Path` and serialize it as `host.data_root`. `Database.data` uses this field,
while `Paths.databases` remains the default selected by fresh initialization. This keeps operator policy
in source configuration without making all runtime paths configurable.

### Validate a dedicated safe directory

The value must be a normalized absolute path with no symlinked or non-directory existing component. It
must not be a broad filesystem ancestor or overlap evdb configuration, generated assets, Traefik,
backups, locks, or a local Restic repository. For a custom root, the immediate parent must already exist;
initialization creates or converges only the selected leaf as a private managed directory.

The setup review states the selected path. The operator is responsible for mounting storage before evdb
or Docker starts; evdb does not imply that a custom directory is a persistent mount.

### Treat the root as immutable after role generation

Fresh setup and source loading require the field. Existing-host initialization accepts only the stored
value. Before replacing an existing role Compose file, database rendering compares the current primary
data bind with the desired bind. A different source fails before creating the new data directory or
rewriting Compose. This catches both supported command misuse and direct source edits without adding a
machine-state file.

## Risks / Trade-offs

- [A configured disk is not mounted when Docker starts] -> State clearly that mounts and startup ordering
  are operator-managed; evdb validates the directory but does not claim mount availability.
- [A source edit points at an empty directory] -> Compare the generated primary data bind before any
  render mutation and reject a changed source.
- [Existing source lacks the required field] -> Reject it under the existing strict-schema policy; the
  operator must provide current source explicitly.
- [Generated Compose is absent] -> Allow rendering because no prior managed bind exists to preserve.

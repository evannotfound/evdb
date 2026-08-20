## Context

`Paths.databases` supplies the canonical database root. The first implementation moved that choice to
one `Host.data_root`, but one host-wide value cannot place roles on different disks. Generated assets,
backup staging, and locks must remain in the canonical state tree regardless of role placement.

## Goals / Non-Goals

**Goals:**

- Store an ordered allowlist of host database roots and one exact selection on every role.
- Collect roots during fresh setup and choose placement during database creation.
- Permit later catalog edits while preventing removal of referenced roots.
- Reject unsafe roots and prevent an existing generated role from silently changing placement.

**Non-Goals:**

- No arbitrary per-role paths outside the host catalog.
- No mount creation, persistence, startup ordering, or availability monitoring.
- No data copy, relocation, import, compatibility default, or source migration.
- No change to generated assets, backup staging, locks, or repositories.

## Decisions

### Store exact paths in an ordered catalog and on each role

Replace the single root with required `Host.data_roots: tuple[Path, ...]`. Add required
`data_root: Path` to `Postgres` and `KV`, and derive `Database.data` from the role setting. Exact paths
avoid an alias layer and make placement visible beside engine settings. `Paths.databases` remains only
the initial default. List order is preserved, but multiple roots have no implicit direct-creation default.

### Validate the catalog as one unit

Every root must be a unique normalized absolute path with no symlinked or non-directory existing
component. Roots must not overlap one another, evdb configuration, generated assets, Traefik, backups,
locks, or a local Restic repository. For a non-canonical root, the immediate parent must already exist.
The parent may be operator-owned but must not be group/world-writable. Every role selection must exactly
match one catalog entry. Initialization converges every selected root to `root:root` mode `0700`.

Fresh guided setup collects roots until the operator is done and shows the ordered list in review.
Operators may later edit `config.yml` and rerun initialization. Adding or removing an unused root is
valid; removing a referenced root fails source validation.

### Select placement during creation and keep it immutable

Guided database creation asks for a root when the catalog has multiple entries and includes it in review.
Direct creation uses the sole root automatically; with multiple roots it requires `--data-root PATH`.
Creation writes the exact path under the role. Idempotent add rejects a different supplied path.

Before replacing existing role Compose, rendering compares the current primary data bind with the
desired bind. A different source fails before creating the new data directory or rewriting Compose. The
database settings editor never exposes placement.

## Risks / Trade-offs

- [A configured disk is not mounted when Docker starts] -> Mounts and startup ordering remain
  operator-managed; evdb validates directories but does not claim mount availability.
- [A source edit points at an empty directory] -> Compare generated primary data binds before mutation.
- [A catalog entry is removed while in use] -> Require every role selection to remain in the catalog.
- [Existing source lacks catalog or role placement] -> Reject it under the strict-schema policy.
- [Generated Compose is absent] -> Allow rendering because no prior managed bind exists to preserve.

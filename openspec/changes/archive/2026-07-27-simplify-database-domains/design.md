## Context

Database identity is project-first: each project may have one Postgres role and one KV role, addressed
as `<project>/postgres` and `<project>/kv`. The current public hostname formula includes the role in
the host label, producing names such as `test-dev-01.postgres-montreal-01.storage.evanovation.com`
and `test-dev-01.kv-montreal-01.storage.evanovation.com`.

The native routing topology already separates roles by entrypoint: Postgres uses host port `5432`, KV
uses host port `6379`, and KV HTTP is served by an external HTTPS proxy on port `443`. Internal Docker
names, Compose projects, generated paths, backup identities, and CLI selectors are role-qualified and
must remain that way.

There are no external users to preserve the existing role-qualified hostname contract. The later
production move can replace the old DNS names and external proxy routes directly.

## Goals / Non-Goals

**Goals:**

- Use `<project>.<host-id>.<base-domain>` as the canonical public hostname for Postgres, KV native,
  and KV HTTP.
- Keep role-qualified internal identities for selectors, Compose, containers, routes, services,
  paths, secrets, backups, locks, and state.
- Allow one project's Postgres and KV roles to share a native SNI hostname on separate Traefik
  entrypoints.
- Keep KV HTTP sidecar public domains unique among enabled sidecars because the external HTTPS proxy
  routes by hostname on one public port.
- Update docs, specs, and tests to describe the breaking contract directly.

**Non-Goals:**

- No legacy alias support for `postgres-<host-id>` or `kv-<host-id>` names.
- No production DNS, certificate, external proxy, or running container changes in this change.
- No change to CLI database selectors or internal project/role ownership boundaries.
- No change to native database ports, TLS requirements, HTTP sidecar loopback behavior, or engine
  compatibility rules.

## Decisions

### Use one project hostname for all public protocols

`Database.domain` will derive `f"{project}.{host.id}.{host.domain}"`. Postgres URLs use that hostname
with port `5432`, KV native URLs use it with port `6379`, and default KV HTTP contracts use
`https://{project}.{host.id}.{host.domain}`.

Alternative considered: keep role-qualified hostnames for explicitness. That repeats information from
the scheme and port, and keeping it would preserve a contract we no longer need.

### Keep internal identities role-qualified

The change does not collapse `<project>/postgres` and `<project>/kv` into a single runtime identity.
Compose project names remain `evdb-<project>-postgres` and `evdb-<project>-kv`; container names,
network aliases, generated files, secrets, backups, locks, Restic tags, activity records, and guided
selectors continue to include the role.

Alternative considered: simplify internal names to match the public hostname. That would increase
collision risk for projects with both roles and make operational output less precise.

### Validate native domain collisions by entrypoint

Native domain uniqueness will be checked by route identity, effectively `(domain, port)` or
`(domain, entrypoint)`, instead of hostname alone. A project's Postgres and KV roles may share a
domain because they route on different native entrypoints. Two routes competing on the same hostname
and native port remain invalid.

Alternative considered: remove domain collision validation entirely. That would hide real conflicts if
future configurable domains or route shapes are added.

### Preserve explicit KV HTTP domain override semantics

KV HTTP keeps its existing optional `http.domain` field for external proxy contracts, but its default
becomes the canonical project hostname. Enabled KV HTTP domains remain globally unique among KV
sidecars because the external HTTPS proxy uses one public port.

Alternative considered: remove `http.domain`. That would overreach the hostname simplification and
remove an existing escape hatch for non-standard external proxy routing.

## Risks / Trade-offs

- Client and DNS breakage during production migration -> The later production move must create the new
  DNS records, replace external HTTP proxy routes, regenerate Compose labels, and communicate that old
  names are intentionally unsupported.
- Same SNI hostname on multiple native entrypoints may be misread as ambiguous -> Tests will exercise
  same-hostname Postgres and KV routing through different host ports.
- Existing source files with explicit old KV HTTP domains may keep old names -> The production move
  must remove or update any explicit `http.domain` values that encode the old role-qualified contract.
- Shared public hostname couples protocol availability at DNS level -> This is accepted because the
  services are separated by port and scheme, and the goal is a simpler application-facing endpoint.

## Migration Plan

Implementation updates the code, current specs, docs, and tests locally. It does not mutate a live
host.

For the later production move, bring up the new generated config on a disposable test host first, then
replace DNS and external HTTP proxy routes with the new project hostnames. Because there are no users,
do not create long-lived aliases for old role-qualified names. Rollback for production remains the
separate production migration rollback, not part of this change.

## Open Questions

None.

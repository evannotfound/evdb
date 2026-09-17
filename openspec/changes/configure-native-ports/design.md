## Context

`host._traefik()` publishes `5432:5432` and `6379:6379`. `host._require_ports()` checks those fixed host ports, but returns early whenever the owned evdb router is running. `Database.port`, `database.connection()`, and `status._listeners()` also assume the standard ports.

The existing montreal-01 router already publishes both standard ports. Separate evdb Compose names and its `evdb` Docker network allow a second stack, but the published ports currently prevent initialization. The existing router uses entrypoints `postgresql` and `redis`; evdb uses `postgres` and `kv` and role-qualified routes. Port changes do not require changing those route identities or the database engine ports.

Redis HTTP already runs through `engines/kv.py` as a separate gateway on `127.0.0.1:<project-derived-port>`. It does not pass through evdb Traefik. Nginx Proxy Manager on this host uses host networking and can continue forwarding to those loopback ports.

## Goals / Non-Goals

**Goals:**

- Start evdb on alternate native ports beside the existing stack.
- Add the standard ports after the existing router releases them, keep both sets available during client migration, and remove the temporary ports afterward.
- Expose this through existing initialization and guided Host flows, with accurate connection details and status.
- Preserve standard behavior for configuration that does not specify port lists.

**Non-Goals:**

- Data import, replication, container adoption, credential migration, native hostname aliases, or production cutover execution.
- Per-database published ports, configurable listen addresses, additional routers, port-forwarding daemons, or firewall automation.
- Changes to Redis HTTP gateways, HTTP token handling, Nginx Proxy Manager, or ownership of ports 80/443.
- Zero-disconnection router changes or automatic rollback.

## Decisions

### 1. Keep ordered port lists on host routing

Extend `Routing` in `models.py` with immutable `postgres_ports` and `kv_ports` tuples. Serialize them as ordered YAML lists under `host.routing`:

```yaml
host:
  routing:
    # Existing routing fields remain here.
    postgres_ports: [15432, 5432]
    kv_ports: [16379, 6379]
```

Omitted fields load as `[5432]` and `[6379]`. New initialization and explicit port updates persist both resolved lists. A normal installer refresh without port flags does not rewrite source merely to insert defaults. This is an optional setting in the current schema, not an old-schema conversion.

Each list must contain at least one unique integer in 1–65535; reject booleans, duplicates, overlapping Postgres/KV lists, and ports 80/443 reserved by the existing web-proxy boundary. Preserve input order. The first element is the preferred port for connection URLs; remaining elements are additional accepted ports. A scalar port cannot express the requested overlap, while a separate preferred-port setting would duplicate the ordering decision.

`Database.port` becomes the preferred external port. Engine service definitions, health commands, PgBouncer, Redis HTTP connections, and Docker routing backend ports continue using internal 5432/6379.

### 2. Use Docker mappings to expose multiple ports on one router

Generate one `HOST:5432/tcp` mapping for each Postgres host port and one `HOST:6379/tcp` mapping for each KV host port in `host._traefik()`. Keep the existing two internal entrypoints and TLS/SNI routes. Every port for a protocol reaches the same database selected by hostname.

Render mappings in stable numeric order within each protocol. Reordering the source list to select a preferred port must not change Compose when the published set is unchanged. Adding or removing a mapping recreates the router through existing Compose convergence and drops native connections; it does not restart database or HTTP gateway containers. A second Traefik entrypoint for each external port adds no useful routing behavior.

### 3. Extend the existing initialization interface

Add repeatable `--postgres-port PORT` and `--kv-port PORT` flags to `evdb init`. Repetitions define the complete replacement list for that protocol in the given order. On a new host, omitted flags use standard defaults; on an existing host, omitted flags preserve the configured lists.

For example, after initial setup on alternate ports:

```sh
# Once the old router has released the standard ports:
sudo evdb init --postgres-port 15432 --postgres-port 5432 --kv-port 16379 --kv-port 6379

# Prefer standard ports in connection details while keeping temporary clients connected:
sudo evdb init --postgres-port 5432 --postgres-port 15432 --kv-port 6379 --kv-port 16379

# Once clients have moved back:
sudo evdb init --postgres-port 5432 --kv-port 6379
```

Direct commands retain existing immediate execution semantics and report the connection interruption before applying a changed binding set. `init --yes` without port flags retains all configured ports.

Guided initialization collects comma-separated ordered lists, defaults to standard ports, and includes them in its existing final review. The guided Host menu adds **Native ports**, showing current lists and allowing both to be edited with one final Save confirmation and a warning about native connection interruption. Blank input keeps the displayed value. Cancel or an unchanged save causes no mutation.

The Host editor reuses `host.initialize()` with the two port values, rather than introducing another host command family or another routing apply implementation. This performs the existing host convergence, including repository and timer checks, without deploying database projects. Reload the configuration after saving and invalidate guided status so subsequent Connection and Host views use the new lists.

### 4. Check newly requested ports even when evdb is running

In `host.initialize()`, reload existing source and form the candidate routing configuration under the host write lock. Validate the complete candidate and check requested ports before writing it or changing generated assets. Persist only `config.yml` when flags change routing; preserve secrets and project settings. Continue with existing host convergence.

Replace `_require_ports()`'s early return with inspection of the running `evdb-traefik` container's verified Compose ownership and actual published TCP bindings. Exempt only requested host ports already bound by that running owned router. Probe every other requested port with the existing socket check, including newly added ports when the router is already running. A stopped router owns no active ports. Conflict errors name the occupied port and leave source and runtime unchanged.

Keep existing direct-convergence failure behavior: if applying a valid saved configuration subsequently fails, report the error and leave the desired source readable for correction and retry. Do not create rollback state or claim success before the host checks complete.

### 5. Report the configured endpoints consistently

Use `Database.port` in native connection URLs and port displays. Show ordered lists and the preferred port in the Host view. `status._listeners()` takes the configured port set rather than fixed constants; retain the existing JSON listener map shape, with string keys for all configured ports and null values on assessment failure. Status must also verify that the running owned router publishes the requested bindings, so an old unrelated router listening on 5432/6379 cannot make unapplied evdb bindings appear healthy. Reuse the existing router inspection rather than introducing a separate monitoring layer.

The HTTP URL, loopback port, token, and internal Redis connection do not depend on native host ports and remain unchanged.

## Risks / Trade-offs

- [Adding or removing ports interrupts all native connections through evdb Traefik] → Warn in guided and direct updates, document client reconnection, and keep database containers running. Preferred-port-only reordering does not recreate the router.
- [Both stacks contain writable copies of the same data] → Document a per-database write freeze and final transfer before switching all native and HTTP clients; port coexistence does not synchronize data.
- [Another process claims a port after preflight] → Report the Compose failure using existing convergence behavior; do not add a reservation service or retry system.
- [Old native hostnames differ from evdb hostnames] → Show the generated evdb hostname during cutover; preserving old native hostnames is a separate change.
- [Temporary ports may be blocked outside the host] → Document that operators must permit their chosen ports in their existing firewall configuration; evdb does not change it.

## Migration Plan

No existing host must change ports. Upgrade the tool and retain standard defaults or existing explicit lists. For coexistence, initialize a new evdb stack on free alternate ports, prepare and verify each target database, then perform its data cutover and move clients to the evdb hostname and temporary port. Keep each source data directory separate while both databases run.

For HTTP clients, switch the Nginx Proxy Manager upstream to the new evdb loopback gateway after the KV data cutover. Retaining the public HTTPS hostname does not retain its HTTP token automatically; preserving or updating tokens remains an explicit migration step outside this feature. Native port updates must not affect this HTTP route.

After all native clients leave the old router, stop that router and add standard ports to evdb. Move clients back to standard ports gradually, then remove temporary ports. A port-only reversal can restore previous free bindings through the same flags; reversing a data migration after new writes requires a separate data plan and is not an automatic rollback.

README guidance should show this sequence and make the router reconnection and data-transfer boundaries explicit. No live montreal-01 operations are part of implementing this change.

## Validation

Extend existing unit tests for ordered configuration round trips and defaults, alternate/dual-port Compose rendering, owned-port versus newly occupied-port checks, CLI replacement semantics, guided save/cancel and reload, and connection/status output. Include preservation of internal engine ports and HTTP gateway output. Mock host commands and sockets; use existing focused test files, with no production connections or new test harness.

## Open Questions

None for this scope. Legacy native hostname preservation and the actual data migration procedure are intentionally separate decisions.

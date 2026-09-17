## Why

evdb currently requires host ports 5432 and 6379, so it cannot initialize alongside the existing database router on montreal-01. Configurable ports would let operators prepare a second stack, migrate applications individually, then serve both temporary and standard ports while applications move back to the standard ports.

## What Changes

- Configure an ordered, nonempty list of published host ports for Postgres and another for Redis/Dragonfly. Defaults remain 5432 and 6379.
- Publish every configured port through the same evdb router to the same protocol backend; the first port is the preferred port in connection details.
- Accept repeatable port flags during initialization and refresh, and expose the lists in guided setup and the Host menu for later edits.
- Validate requested ports, detect conflicts before applying changes, and make status and connection details reflect the configured ports.
- Document the staged cutover and the brief connection interruption when Docker recreates the router to change its port bindings.
- Keep Redis HTTP behind the existing external reverse proxy. Nginx Proxy Manager continues owning 80/443, and evdb's existing serverless-redis-http gateways remain on localhost ports.

This change does not migrate data, synchronize databases, preserve legacy native hostnames, configure Nginx Proxy Manager or firewalls, or perform the production cutover. It does not promise uninterrupted connections during router recreation.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `config`: Ordered host-level native port lists, standard defaults, validation, and preferred connection ports.
- `deploy`: Publish one or more host ports per native protocol while preserving internal ports and database services.
- `host-setup`: Initialize beside an existing router on alternate ports and refresh bindings with conflict checks that account for ports already owned by evdb.
- `operator-cli`: Guided and direct port configuration, accurate connection details, and status for all configured native listeners.

## Impact

Changes involve `models.py`, `config.py`, `host.py`, `cli.py`, `ui.py`, `status.py`, and `database.py`, their focused unit tests, and README migration guidance. Native database engines keep their internal ports; Redis HTTP service generation and Nginx Proxy Manager configuration do not change. No new dependency is needed. Existing configuration without the new fields continues to use the standard ports.

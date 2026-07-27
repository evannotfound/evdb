## 1. Domain Derivation

- [x] 1.1 Update `Database.domain` to derive `<project>.<host-id>.<base-domain>` for every role
- [x] 1.2 Update KV HTTP default-domain creation and loading to use the same project hostname
- [x] 1.3 Preserve explicit KV HTTP domain overrides while removing old role-qualified defaults
- [x] 1.4 Change native route validation to allow the same hostname on different native ports or entrypoints
- [x] 1.5 Keep enabled KV HTTP public-domain validation globally unique among sidecars

## 2. Generated Routing And URLs

- [x] 2.1 Verify generated Traefik labels use the project hostname for both Postgres and KV routers
- [x] 2.2 Verify internal Compose project names, container names, aliases, paths, secrets, backups, and locks remain role-qualified
- [x] 2.3 Update Postgres connection URL output to use the project hostname on port 5432
- [x] 2.4 Update KV native connection URL output to use the project hostname on port 6379
- [x] 2.5 Update KV HTTP connection output to use `https://<project>.<host-id>.<base-domain>` by default

## 3. Tests And Documentation

- [x] 3.1 Update config tests for shared project hostnames, HTTP defaults, and entrypoint-aware validation
- [x] 3.2 Update database info tests for Postgres, KV native, and KV HTTP URL expectations
- [x] 3.3 Update compose tests to assert same-hostname routers stay separated by entrypoint and backend
- [x] 3.4 Update Traefik integration coverage to route same-SNI Postgres and KV traffic through separate ports
- [x] 3.5 Update HTTP integration coverage where default public-domain assumptions are asserted
- [x] 3.6 Update README and docs to describe the canonical project hostname and breaking old-name removal

## 4. Validation

- [x] 4.1 Run `uv run ruff check src tests tools`
- [x] 4.2 Run `uv run ruff format --check src tests tools`
- [x] 4.3 Run `uv run pytest tests/unit tests/config`
- [x] 4.4 Run `uv run pytest tests/integration/test_traefik.py tests/integration/test_http.py` on disposable Docker infrastructure
- [x] 4.5 Run `uv run pytest --collect-only -q tests/integration`

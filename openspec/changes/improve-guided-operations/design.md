## Context

Fresh initialization is currently split between `cli._init_values()` and `host._initial()`. The CLI asks
seven required text questions once, accepts any syntactically safe DNS provider and any non-empty
repository string, then passes the complete dictionary into host convergence. Provider credentials must
already exist in a `KEY=VALUE` file. Repository availability is eventually checked by Restic, but only
after canonical source, Traefik assets, the Docker network, and Traefik itself have changed.

The interactive database UI is append-only and numbered, but prompt behavior is duplicated across
`cli.py` and `ui.py`; invalid setup fields do not retry locally; generic `pairs()` formatting exposes
nested developer-shaped values; and direct argparse options contain little field guidance. The current
source has no Rich runtime dependency despite an older archived Rich proposal.

Traefik `v3.7.8` embeds lego `v5.2.2`. Traefik exposes neither a provider-list API nor a provider
credential dry run, and provider construction is lazy until an ACME order is needed. Lego's generated
provider metadata supplies canonical codes, names, credential-variable descriptions, additional
settings, and help URLs, but does not fully encode alternative credential relationships. A real DNS-01
order is therefore the only generic proof of provider credentials, zone access, TXT mutation,
propagation, and ACME interoperability.

All native database domains have shape `<project>.<host-id>.<base-domain>`, so one certificate for
`*.<host-id>.<base-domain>` covers every managed Postgres and KV route. Current routes instead carry a
per-router certificate-resolver label and obtain exact-host certificates lazily.

Restic always drops from root to the owner of `host.backup.rclone_config`, even for a local repository.
This makes local operation depend on an unrelated rclone file and requires rclone on every host. Postgres
similarly hard-codes username `default` and database `postgres` across the model, Compose, PgBouncer,
health, backup, info, and connection URL.

The executable must remain a PyInstaller one-file release that needs no host Python. Prompts must remain
usable over ordinary SSH, copied transcripts, narrow terminals, and injected test streams. Source
replacement and database data transfer are performed independently by the operator.

## Goals / Non-Goals

**Goals:**

- Make fresh initialization explanatory, locally validated, editable, and deliberate before mutation.
- Prevent unsupported DNS providers and undocumented variables while supporting the complete provider
  catalog for the pinned Traefik and lego versions.
- Prove ACME end to end during initialization and reuse one host wildcard for all native routes.
- Make rclone repository construction selectable and local repositories genuinely independent of rclone.
- Preserve a source Postgres login identity for operator-managed data imports without changing it after
  database initialization.
- Improve human output and command help while preserving append-only and machine-output contracts.
- Keep credentials out of arguments, ordinary input, previews, status, generated Compose, and errors.

**Non-Goals:**

- No general Postgres import, restore, password rotation, role management, or username/database rename.
- No active provider test separate from the wildcard certificate obtained by initialization.
- No support for direct Restic S3, SFTP, REST, Azure, or other non-rclone remote backends.
- No arrow-key, full-screen, cursor-addressed, Rich, Questionary, InquirerPy, or prompt_toolkit UI.
- No source conversion, data import, or host cutover workflow.
- No automatic rollback or configuration snapshots.

## Decisions

### Keep prompting append-only and separate it from domain validation

Extend the existing UI boundary with small line-oriented primitives for text, numbered selection,
confirmation, and masked secret input. Each primitive accepts injected input/output functions, optional
default and help text, and a validator that returns a value or a concise error. Lists print once per
attempt; invalid input prints one error and repeats the same question. `KeyboardInterrupt` continues to
cancel through `cli.main()`, and EOF cancels without applying.

The fresh-init wizard owns a candidate value dictionary and section order, while `config.py`, `host.py`,
and `backup.py` remain authoritative for value, file, repository, and external preflight validation. The
wizard may call those validators but does not duplicate their regular expressions or filesystem rules.
After all sections, it renders a redacted review with Apply, Edit, and Cancel choices. Edit returns to a
numbered section or field without losing unrelated values. Normal database creation similarly exposes
one Advanced branch rather than asking every operator about credentials.

Questionary and InquirerPy were rejected because even their numbered modes are prompt_toolkit
applications that redraw terminal regions. They add packaging and PTY behavior without improving the
append-only interaction model that makes rclone's setup robust.

### Generate and embed provider metadata for the pinned lego version

Add a development tool that downloads the exact tagged lego source selected by the pinned Traefik image,
parses provider TOML with standard-library `tomllib`, and writes a deterministic compact package-data
catalog. The generated data records Traefik and lego versions plus canonical provider code, display name,
credential and additional variables, descriptions, and the canonical lego help URL. Canonical provider
codes come from provider metadata; recognized aliases normalize to their canonical code but are not
shown as separate choices.

Project checks verify that catalog metadata matches `DEFAULT_IMAGES["traefik"]`, the expected lego
version, and selected known provider records. The release build embeds the catalog and smoke-tests that it
can be read from the frozen executable. Runtime setup never downloads provider documentation.

The provider selector asks for a name/code search term and prints only matching numbered rows, with an
explicit path to browse further matches. After selection, it shows the provider description and official
URL. Credential collection is a numbered editor over documented variables. Secret-looking values use
masked input; paths, identifiers, regions, booleans, and ordinary names use visible input with local type
or path validation. Additional provider settings are behind an Advanced choice. The review shows only
variable names and `provided` status.

The catalog does not pretend that every listed credential is simultaneously required. Providers such as
Cloudflare, Route53, Azure, and Google expose alternative keys, files, profiles, or workload identities.
The wizard presents the exact provider help and lets the operator select the documented variables for the
chosen authentication path. Final wildcard issuance is authoritative. Direct non-interactive setup keeps
`--dns-file`; parsing rejects unknown keys for the selected provider before source changes.

### Obtain one wildcard certificate as a required initialization phase

Generate a non-secret Traefik dynamic file defining
`tls.stores.default.defaultGeneratedCert` with resolver `evdb` and main domain
`*.<host-id>.<base-domain>`. Enable the Traefik file provider and mount that file alongside persistent
mode-`0600` `acme.json`. Database router labels retain `tls=true` but remove `tls.certresolver=evdb`, so
routers use the default wildcard and cannot race independent exact-host orders.

After starting Traefik, initialization polls `acme.json` for an exact wildcard certificate entry with
non-empty certificate and key fields. Reading uses no-follow, regular-file, and mode checks; returned
status contains only the expected domain and readiness, never account or key data. A bounded certificate
timeout is long enough for normal DNS propagation and prints one append-only wait message. Timeout or
malformed storage reports a credential-redacted bounded Traefik log detail. Traefik ping health alone is
not certificate readiness.

Initialization writes reviewed canonical source before starting external convergence, consistent with
the existing preserved-progress contract. A wildcard failure leaves source, private credentials, and
Traefik assets for correction. Every rerun rewrites or reloads the dynamic TLS-store input when the
wildcard is absent, ensuring Traefik retries the asynchronous order; an existing matching certificate
passes immediately. Repository initialization and timer enablement occur only after wildcard readiness.

An explicit local-only credential check was rejected because Traefik exposes no constructor dry run and
such a check would not prove zone permissions or propagation. A separate staging test was rejected for
normal operation because it would still mutate real DNS and then repeat the production order.

### Model rclone and local repositories explicitly

Repository parsing recognizes only:

- `rclone:<remote>:<safe-relative-path>` with a non-empty configured remote and no unsafe path traversal;
- a normalized absolute local path with no symlinked existing component.

`BackupSettings.rclone_config` becomes optional. Rclone mode requires the current private external file
rules and confirms the named remote from its native configuration. Guided setup lists those remotes and
constructs the repository value from the selected remote and entered path. Local mode omits the source
field entirely.

One repository-operator resolver replaces direct calls to `rclone_owner()`. In rclone mode it returns the
rclone file owner. In local mode it uses the repository directory when present or requires its immediate
parent when absent; that directory must be safe, user-writable, owned by a known non-root account, and
agree with the repository owner when both exist. Account home and supplementary-group checks remain the
same. Requiring an existing immediate parent makes ownership unambiguous and prevents root from guessing
which user should own a new repository.

Restic's common identity, cache, password-descriptor, lock, and redaction behavior remains shared. Rclone
mode additionally requires `/usr/bin/rclone`, sets `RCLONE_CONFIG`, and passes the trusted rclone program
option. Local mode does none of those. Missing local repository initialization creates only the leaf as
the derived operator through Restic. `protected()` reads rclone credentials only when a file is present.

Before Apply, the candidate runs `restic cat config` without creating a managed lock or canonical files.
Success proves access and the supplied password; documented missing-repository status is an acceptable
`will create` result; other errors return to review. Rclone may perform its normal user-owned OAuth token
refresh during this read. After Apply, ordinary locked initialization creates a missing repository and
verifies format v1.

Supporting every native Restic remote backend was rejected because the exact replacement environment
currently supplies no backend-specific credentials. Keeping an unused rclone file for local mode was
rejected because it makes the source model and prerequisite error misleading.

### Persist immutable Postgres connection identity

Add required username and database name to `Postgres` settings. Creation defaults them to `default` and
`postgres` and writes both values explicitly. The loader rejects Postgres entries that omit either field;
the operator must provide current source rather than relying on synthesized prior-schema values. Both
values must be non-empty one-line PostgreSQL identifiers of at most 63 encoded bytes and may contain
characters that require PgBouncer or URL quoting.

`database.add()` accepts optional creation identity and compares any supplied values on idempotent rerun.
Different username, database, or password fails before mutation because Docker's Postgres initialization
variables affect only an empty data directory. The settings editor and `database configure` do not expose
these values.

The Postgres engine uses the configured identity for `POSTGRES_USER`, `POSTGRES_DB`, PgBouncer userlist,
health commands, authenticated PgBouncer checks, database enumeration, dumps, global dumps, engine info,
and connection details. Subprocess argument arrays remain safe for unusual names; PgBouncer values use
its native quoting and URL components use percent encoding. The password remains only in role secrets.

Preserving identity helps an operator perform a manual dump/restore without changing application login
details, but evdb does not import source data or guarantee preservation of an old hostname.

### Replace generic formatting with contextual renderers

Keep the root database overview and backup history as compact aligned row sets because they compare
multiple records. Provider and rclone selections may also align repeated choices. Host, database,
connection, settings, setup review, progress, success, and errors use named sections or key/value lines.
Replace uses of generic `pairs()` for nested domain values with explicit renderers that decide which
fields belong in each view and when credentials may appear.

Argparse parsers gain descriptions, metavars, option help, and command epilogs with valid examples.
Domain validation errors name the rejected field and one valid form without dumping hundreds of provider
choices. Human output remains plain under injected or non-terminal streams; JSON status remains exactly
one secret-free document.

## Risks / Trade-offs

- [The generated provider list drifts from Traefik] -> Tie catalog metadata and checks to both the pinned
  Traefik patch and lego tag; require regeneration in the same change as either version update.
- [Provider TOML cannot express every authentication alternative] -> Present official variables and help,
  validate known local types, and make real wildcard issuance the authoritative result.
- [Wildcard issuance mutates DNS and consumes ACME limits] -> Request one stable identifier set, preserve
  `acme.json`, remove per-router orders, avoid automatic retries in a tight loop, and report bounded errors.
- [Traefik remains healthy after failed issuance] -> Derive setup and status readiness from the expected
  stored wildcard certificate rather than container ping or ACME file mode.
- [A wildcard private key covers all host database names] -> Keep one root-owned mode-`0600` ACME store and
  never expose its contents; this is the same dedicated proxy trust boundary that terminates all routes.
- [Read-only repository preflight can refresh OAuth state] -> Run it as the rclone file owner against the
  canonical external file and document that normal rclone token refresh remains user-owned.
- [A local repository parent has ambiguous ownership] -> Require the immediate parent to exist and be
  safely owned and writable by one known non-root account before initialization.
- [Custom Postgres identity cannot be changed by rewriting files] -> Treat it as creation-only, compare it
  during idempotent add, omit it from settings, and reject mismatches before mutation.
- [The broader guided flow becomes verbose] -> Use progressive sections, filtered provider results,
  defaults, local retries, one review, and tables only for repeated comparable values.

## Delivery

1. Add provider catalog generation and frozen-package checks without changing a host.
2. Extend models and loaders with required Postgres identity fields and conditional rclone configuration.
3. Add prompt and renderer behavior, then repository preflight and mode-specific identity.
4. Change Traefik generation to the wildcard default store and remove per-router resolver labels.
5. Validate with unit tests, a disposable local and rclone Restic integration, custom Postgres/PgBouncer
   integration, disposable Traefik routing, and a local ACME test service rather than public DNS.
6. Build and smoke-test the one-file executable with the embedded catalog.

This change performs no host deployment, source conversion, or data transfer. Existing exact-host
certificates may remain in `acme.json`; Traefik may renew stored entries until an operator removes them,
but new routers do not request more.

## Open Questions

None. The exploration selected the full versioned provider catalog, rclone plus local repositories,
derived local ownership, complete Postgres username/database/password identity, and wildcard issuance
during initialization.

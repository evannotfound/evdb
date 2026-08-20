## 1. Versioned DNS Catalog

- [x] 1.1 Add a standard-library development generator and deterministic package-data catalog for every canonical provider in lego v5.2.2, including Traefik/lego version metadata, documented variables, descriptions, help URLs, and canonical alias normalization.
- [x] 1.2 Add catalog loading, search, provider normalization, variable validation, version-consistency checks, package configuration, and focused tests that prove unsupported providers and undocumented variables are rejected.
- [x] 1.3 Curate generated credential and additional variables to omit aliases and deprecated names, restrict Cloudflare to its current token variables, and cover the resulting validation boundary.

## 2. Repository Modes And Identity

- [x] 2.1 Extend backup configuration and validation to distinguish safe `rclone:<remote>:<path>` repositories from normalized absolute local repositories, make rclone configuration conditional, validate named remotes, and derive the correct known non-root operator for each mode with focused config tests.
- [x] 2.2 Update Restic execution, credential protection, prerequisites, and read-only candidate preflight so rclone mode uses the external file and trusted rclone executable while local mode uses neither; cover missing/existing repositories, ownership, exact environments, format checks, and disposable local/rclone integrations.

## 3. Append-Only Guided Setup

- [x] 3.1 Consolidate line-oriented text, numbered selection, confirmation, masked secret, default, validation-retry, EOF, and cancellation behavior in the existing UI boundary with transcript tests and no terminal control sequences.
- [x] 3.2 Replace fresh `_init_values` prompting with editable Host, DNS, and Backup sections that search the provider catalog, collect only documented provider variables, select rclone remotes or local storage, preflight the candidate, and show one credential-redacted Apply/Edit/Cancel review.
- [x] 3.3 Expand argparse descriptions, metavars, option help, examples, and non-interactive missing-input errors while preserving `--dns-file`, secret-free arguments, grouped commands, and installer-only existing-host `init --yes` behavior.
- [x] 3.4 Show one asterisk per character for production interactive secret input while preserving injected prompt functions and secret validation behavior.

## 4. Verified Wildcard Routing

- [x] 4.1 Generate and mount the Traefik dynamic default wildcard certificate configuration, enable the file provider, remove per-router certificate-resolver labels, and cover generated Compose, wildcard domain, and router behavior with focused tests.
- [x] 4.2 Parse private ACME storage without exposing its contents, report readiness only for the exact non-empty host wildcard, and make initialization wait, fail with bounded redacted Traefik detail, and reliably retry missing issuance before repository or timer activation.
- [x] 4.3 Add disposable Traefik/ACME coverage using local test infrastructure to prove wildcard issuance and reuse without public DNS, production credentials, production hosts, or per-database certificate orders.

## 5. Immutable Postgres Identity

- [x] 5.1 Add required validated username and database-name fields to Postgres source with `default`/`postgres` creation defaults, explicit writes, direct creation flags, and idempotent mismatch rejection while keeping the values out of mutable settings.
- [x] 5.2 Use the configured identity throughout Postgres Compose, PgBouncer files, health, backup, info, and percent-encoded connection URLs; extend unit and disposable Postgres/PgBouncer integration coverage for custom names and supplied passwords.
- [x] 5.3 Change guided Postgres creation to generate the normal managed identity without a password question and provide an Advanced username/database/masked-confirmed-password path with one redacted Create review.

## 6. Grounded Human Output

- [x] 6.1 Replace generic nested-value rendering with contextual setup, host, database details, connection, settings, progress, result, and error renderers while retaining compact aligned rows only for database, backup, provider, and remote comparisons.
- [x] 6.2 Update guided and direct output tests for narrow terminals, progressive disclosure, local validation errors, secret boundaries, stable plain capture, and unchanged single-document status JSON.

## 7. Distribution And Guidance

- [x] 7.1 Update the README's linear setup and first-database guidance for provider selection, wildcard verification, rclone and local storage, advanced Postgres identity, examples, and the continued absence of restore and remote pruning.
- [x] 7.2 Update release packaging and checks so the frozen executable embeds and reads the provider catalog alongside the two units, then verify the affected unit/config tests, disposable integrations, integration collection, and one-file smoke paths.

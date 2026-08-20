## Why

Fresh host setup currently accepts unverified free-form DNS and repository input, requires operators to
prepare DNS environment files by hand, and gives little help before a late initialization failure.
The guided CLI also lacks local retries, useful examples, review and edit steps, and a way to preserve a
Postgres login identity when the operator imports data independently.

## What Changes

- Replace the flat setup questionnaire with an append-only, numbered, SSH-safe flow that explains inputs,
  shows defaults and examples, validates each step locally, and provides a redacted review/edit/apply loop.
- Generate a searchable DNS-provider catalog from the lego version embedded in the pinned Traefik image,
  collect current provider variables without deprecated names or credential aliases, and retain a validated
  credential-file path for explicit non-interactive setup.
- Obtain and verify one host wildcard certificate during initialization so DNS provider credentials, zone
  access, propagation, and ACME issuance are proven before setup completes; database routes reuse it.
- Replace free-form guided rclone URLs with selection from the remotes in a validated native rclone file plus
  a repository path, while retaining an explicit local repository mode.
- Make local repositories independent of rclone configuration and run Restic as the safe non-root owner of
  the local repository or its existing parent.
- Add an advanced Postgres creation path for immutable username, database name, and asterisk-masked password input;
  the normal path continues to generate a managed login.
- Ground human output in task-specific summaries, numbered choices, concise progress and actionable errors;
  use aligned tables only for comparable database and backup rows and improve direct command help examples.
- Keep non-interactive commands explicit, append-only, secret-free, and included in the standalone
  executable. Data import, source conversion, and general database restore automation remain operator
  responsibilities outside evdb.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `operator-cli`: Define the guided setup, reusable prompt behavior, grounded presentation, advanced
  Postgres creation, and actionable direct-command help.
- `host-setup`: Validate initial inputs before mutation and require verified wildcard certificate issuance
  before initialization completes.
- `deploy`: Replace per-database ACME orders with one persisted host wildcard certificate used by native
  TCP routes.
- `config`: Validate versioned DNS providers, conditional rclone settings, local repository ownership, and
  configurable Postgres connection identity.
- `restic`: Support rclone and local repository modes with mode-specific non-root execution and preflight.
- `http`: Build Postgres connection details from the configured username and database name.
- `release-distribution`: Package the generated provider catalog and update the public setup examples for
  the new guided flow.

## Impact

The change affects CLI parsing and guided workflows, host initialization and status, Traefik generation,
Restic subprocess identity, configuration models and serialization, Postgres engine commands and generated
files, README guidance, package data, and focused unit and disposable integration tests. It adds generated
provider metadata tied to Traefik `v3.7.8` and lego `v5.2.2`, but no runtime prompting dependency.

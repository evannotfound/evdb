## MODIFIED Requirements

### Requirement: Complete release executable
Each architecture asset SHALL be one executable with exactly the two canonical systemd unit templates
and the DNS provider catalog matching the pinned Traefik and lego versions embedded. The executable
SHALL run guided setup, validate provider input, and install the units without host Python or a Python
package manager.

#### Scenario: Release executable is inspected
- **WHEN** the release workflow builds an architecture executable
- **THEN** it verifies the reported version, both non-empty embedded units, and matching non-empty provider catalog metadata

### Requirement: Public project entry point
The README SHALL preserve the project headline, slogan, three badges, and concise description of evdb
as Linux tooling for Postgres and Redis-compatible databases. It SHALL provide one linear public guide
that presents host requirements and official prerequisite installation links before any evdb command,
then covers DNS routing and provider credentials, rclone or local repository preparation, evdb
installation, guided initialization and its wildcard issuance, first Postgres creation, advanced
identity preservation, connection information, CLI usage, automatic backups, development commands, and
licensing. It SHALL describe Linux with systemd as the host requirement and Ubuntu as the tested path
without an Ubuntu-version or architecture matrix. It SHALL identify CLI help as the detailed command
reference and SHALL NOT depend on a separate `docs/` tree. It SHALL state that evdb does not provide
restore or remote snapshot pruning and SHALL NOT claim private networking, live restore, restore
verification, remote retention, or automatic rollback.

#### Scenario: New self-hoster follows the README
- **WHEN** a new self-hoster opens the repository README
- **THEN** they can prepare required software, DNS routing and credentials, choose rclone or local storage, initialize and verify the host, create and connect to Postgres, and find later operations through CLI help

### Requirement: Disposable host source workflow
The repository SHALL retain a workflow that syncs only explicit source, test, and locked project files
to an operator-selected disposable VPS and runs a native `uv sync --locked` environment. It SHALL copy
no ignored files or credentials and SHALL leave the installed `/usr/local/bin/evdb` unchanged.
Development commands SHALL invoke the checkout's executable explicitly rather than temporarily replacing
the installed command.

#### Scenario: Developer tests an uncommitted source revision
- **WHEN** explicit development files are synced to a selected disposable VPS
- **THEN** its architecture-native environment runs the source revision without a release

#### Scenario: Development command completes
- **WHEN** a checkout command exits
- **THEN** `/usr/local/bin/evdb` remains the installed release executable

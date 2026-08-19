## MODIFIED Requirements

### Requirement: Public project entry point
The README SHALL preserve the project headline, slogan, three badges, and concise description of evdb
as Linux tooling for Postgres and Redis-compatible databases. It SHALL provide one linear public guide
that presents host requirements and official prerequisite installation links before any evdb command,
then covers DNS and rclone preparation, evdb installation, guided initialization, first Postgres
creation, connection information, CLI usage, automatic backups, development commands, and licensing.
It SHALL describe Linux with systemd as the host requirement and Ubuntu as the tested path without an
Ubuntu-version or architecture matrix. It SHALL identify CLI help as the detailed command reference and
SHALL NOT depend on a separate `docs/` tree. It SHALL state that evdb does not provide restore or remote
snapshot pruning and SHALL NOT claim private networking, live restore, restore verification, remote
retention, or automatic rollback.

#### Scenario: New self-hoster follows the README
- **WHEN** a new self-hoster opens the repository README
- **THEN** they can prepare required software, DNS credentials, DNS routing, and remote storage before
  installing evdb, initialize the host, create and connect to a first Postgres database, and find later
  operations through the CLI usage section and command help

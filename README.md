# evdb

**Own your database infrastructure.**

[![Check](https://github.com/evannotfound/evdb/actions/workflows/check.yml/badge.svg)](https://github.com/evannotfound/evdb/actions/workflows/check.yml)
[![Release](https://img.shields.io/github/v/release/evannotfound/evdb)](https://github.com/evannotfound/evdb/releases)
[![License](https://img.shields.io/github/license/evannotfound/evdb)](LICENSE)

evdb turns a Linux server into a platform for Postgres and Redis-compatible databases.

It manages the infrastructure around them:

- Docker-based database services
- TLS routing and generated credentials
- Health checks and lifecycle operations
- Automatic Restic backups through your rclone remote

## Before you install

evdb requires a Linux server with systemd and root access. Ubuntu is the tested path. Native database
connections use ports `5432` and `6379`, which must be free on the host and reachable by your clients.

Install these prerequisites using their official instructions:

- [Docker Engine](https://docs.docker.com/engine/install/) with the
  [Docker Compose plugin](https://docs.docker.com/compose/install/linux/)
- [Restic](https://restic.readthedocs.io/en/stable/020_installation.html) 0.17 or newer at
  `/usr/bin/restic`
- [rclone](https://rclone.org/install/) at `/usr/bin/rclone`

You also need a domain managed by a DNS provider supported by
[Traefik's DNS challenge](https://doc.traefik.io/traefik/reference/install-configuration/tls/certificate-resolvers/acme/#providers)
and remote storage
supported by rclone.

## Prepare DNS and backups

Choose a host ID and base domain. For a host ID of `example-01` and a base domain of
`storage.example.com`, arrange for `*.example-01.storage.example.com` to resolve to the server from
your clients. evdb uses DNS-01 to issue certificates but does not create DNS records.

Guided initialization includes every DNS provider supported by the pinned Traefik release. It shows the
provider's documented variables and help URL, accepts credentials with masked input, and verifies them by
obtaining one certificate for `*.<host-id>.<base-domain>`. For non-interactive setup, prepare a private
file containing documented `KEY=VALUE` lines and pass it with `--dns-file`.

For remote backups, configure rclone as the normal non-root user who will own remote access:

```sh
rclone config
rclone config file
rclone lsd remote:
```

Set the reported rclone configuration file to mode `0600`; evdb uses it in place. Guided setup reads its
configured remotes and asks you to select one, then defaults the repository path to `evdb/<host-id>`.

For a local Restic repository, create only its parent as the non-root backup user. For example:

```sh
install -d -m 0700 "$HOME/restic"
```

Guided setup can then use an absent child such as `$HOME/restic/example-01`. Local mode does not require
rclone and derives the Restic process identity from that safe parent.

## Install evdb

Install the latest standalone release:

```sh
curl -fsSL https://github.com/evannotfound/evdb/releases/latest/download/install.sh | sudo sh
```

The installer verifies the downloaded executable and installs it at `/usr/local/bin/evdb`.

## Initialize the host

Start guided initialization:

```sh
sudo evdb init
```

The append-only setup flow validates each answer, searches supported DNS providers, collects documented
credentials, selects rclone or local storage, and shows a redacted review before Apply. Leave the initial
Restic password blank to generate one. Setup waits for the wildcard certificate and verifies or creates
the Restic repository before enabling automatic backups.

Initialization stores configuration under `/etc/evdb`, managed data under `/var/lib/evdb`, starts the
TLS router, initializes the Restic repository, and enables automatic daily backups.

## Create your first database

Project names must end in `-dev-N`, `-test-N`, or `-prod-N`. Create Postgres with a generated managed
login and retrieve its TLS-secured connection URL:

```sh
sudo evdb database add notes-prod-01 postgres
sudo evdb database info notes-prod-01/postgres
```

```text
postgresql://default:<password>@notes-prod-01.example-01.storage.example.com:5432/postgres?sslmode=require
```

Use the URL with any standard Postgres client. `database info` prints complete credentials and
therefore requires a terminal.

Guided creation has an Advanced option for preserving an existing username, database name, and password
when you import data yourself. Automation can provide the same creation-only identity without exposing
the password in process arguments:

```sh
sudo evdb database add imported-prod-01 postgres \
  --username app_user --database-name app_db --password-file /private/postgres-password
```

evdb does not import the source database or rotate that identity after creation; use standard Postgres
dump and restore tools for the data transfer.

## CLI usage

Run `sudo evdb` for the guided terminal interface. Direct commands execute immediately and are useful
for scripts and SSH. Use `--help` at any command level, such as
`sudo evdb database configure --help`, for all available options.

```sh
sudo evdb status
sudo evdb status --json

sudo evdb database list
sudo evdb database add cache-prod-01 kv
sudo evdb database add cache-prod-02 kv --engine redis
sudo evdb database info cache-prod-01/kv
sudo evdb database configure notes-prod-01/postgres --max-clients 200
sudo evdb database start notes-prod-01/postgres
sudo evdb database stop notes-prod-01/postgres
sudo evdb database restart notes-prod-01/postgres
sudo evdb database logs notes-prod-01/postgres --lines 200

sudo evdb backup create notes-prod-01/postgres
sudo evdb backup create --all
sudo evdb backup list notes-prod-01/postgres
```

Rerun the installer to update evdb. On a configured host it also refreshes the managed router and
backup timer without restarting healthy databases.

## Backups and recovery

The automatic timer backs up every durable database sequentially. A backup succeeds only after the
database files are checked and Restic confirms the remote snapshot.

evdb does not delete remote snapshots, so configure retention separately. Recovery is manual with
Restic and the matching database tools; evdb does not provide a restore command.

## Development

Development uses Python and `uv`; installed hosts require neither.

```sh
uv sync --locked
make check
make binary
```

## License

[MIT](LICENSE)

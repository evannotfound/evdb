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
connections default to ports `5432` and `6379`. You can select alternate or multiple ports during setup
or later through **Host → Native ports**. Selected ports must be free (or already owned by evdb) and
reachable by your clients; evdb does not configure firewalls.

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

## Run alongside an existing database stack

Start guided setup on free alternate ports while the existing router keeps `5432` and `6379`:

```sh
sudo evdb init --postgres-port 15432 --kv-port 16379
```

Non-interactive initialization also requires the other host setup arguments. On a configured host,
each repeated port flag supplies the **complete replacement list** for its protocol, in preferred
order. Omitted flags preserve that protocol's current ports; `init --yes` without port flags preserves
both lists. The first port appears in connection URLs. Guided setup and **Host → Native ports** accept
comma-separated lists instead. All ports in a protocol's list reach the same database selected by its
evdb hostname; internal database ports remain unchanged.

Prepare the evdb databases and transfer data separately. Keep separate data directories while both
stacks run. For each database, pause writers, perform and verify the final transfer, then switch all
its native and HTTP clients before resuming writes. Port coexistence does not synchronize databases.
Use the evdb hostname `<project>.<host-id>.<base-domain>` and the temporary port reported by
`database info`; this feature does not preserve legacy native hostnames.

Once all native clients have left the old router, stop it to release the standard ports. Add them to
evdb while keeping temporary ports available:

```sh
sudo evdb init --postgres-port 15432 --postgres-port 5432 --kv-port 16379 --kv-port 6379
```

Prefer standard ports in new connection details, then move existing clients back gradually:

```sh
sudo evdb init --postgres-port 5432 --postgres-port 15432 --kv-port 6379 --kv-port 16379
```

After all clients use the standard ports, remove the temporary bindings:

```sh
sudo evdb init --postgres-port 5432 --kv-port 6379
```

Adding or removing bindings recreates the Traefik container and briefly drops native connections;
clients must reconnect. Database and HTTP gateway containers keep running. Reordering an unchanged
port set only changes the preferred connection port and does not itself recreate the router.
An occupied requested port is rejected before the configuration is saved.

## Redis HTTP with an external reverse proxy

KV creation enables `hiett/serverless-redis-http` by default for both Redis and Dragonfly. Run
`sudo evdb database info PROJECT/kv` to obtain its intended HTTPS URL, localhost gateway address, and
HTTP token. Configure your external reverse proxy to forward the public hostname to the reported
`http://127.0.0.1:<port>` address. A containerized proxy must be able to reach the host's loopback
interface, as with a host-networked Nginx Proxy Manager deployment.

Nginx Proxy Manager can keep owning ports **80 and 443**, the public HTTP hostname, and its certificate.
evdb's Traefik only handles native database traffic and obtains its certificates through DNS-01.
The optional KV `http.domain` configuration records the intended public hostname; it does not configure
the reverse proxy automatically.

At a KV data cutover, update the existing proxy host's upstream to the new gateway address. Existing
HTTP clients can keep their public URL, but their token must also be handled: evdb generates a new HTTP
token by default, so preserving the old token or updating clients is a separate migration step.
Changes to native Postgres/KV port lists do not change HTTP URLs, loopback ports, or tokens.

## Create your first database

Project names must end in `-dev-N`, `-test-N`, or `-prod-N`. Guided Postgres creation asks for an official
major version (default 16) before optional advanced identity and PgBouncer settings. Create Postgres with
a generated managed login and retrieve its TLS-secured connection URL:

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
sudo evdb database configure notes-prod-01/postgres --postgres-version 18
sudo evdb database start notes-prod-01/postgres
sudo evdb database stop notes-prod-01/postgres
sudo evdb database restart notes-prod-01/postgres
sudo evdb database logs notes-prod-01/postgres --lines 200

sudo evdb backup create notes-prod-01/postgres
sudo evdb backup create --all
sudo evdb backup list notes-prod-01/postgres
```

Changing `--postgres-version` to a newer official major creates and uploads a fresh backup, logically
restores and verifies an isolated target cluster, and rolls back to the old image and data if final health
fails. Major downgrades and custom-image major migrations are rejected.

The guided database menu can permanently delete a managed role after layered typed confirmation. Deletion
removes containers, source credentials, generated files, and live data but retains local and remote
backups.

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

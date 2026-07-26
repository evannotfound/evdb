# Host setup and updates

## Prerequisites

The database host needs a compatible Python, Docker with Compose, Restic, rclone, systemd, writable
canonical filesystems, DNS-01 routing inputs, and free native ports 5432 and 6379. Setup reports
missing prerequisites but does not install or upgrade unrelated host software. Docker group access
is root-equivalent and must be granted deliberately.

Install one exact package version into a candidate version directory and run setup through the
stable executable:

```sh
VERSION=1.2.3
UV="$(command -v uv)"
sudo install -d -m 0755 \
  "/opt/evdb/versions/${VERSION}/tools" \
  "/opt/evdb/versions/${VERSION}/bin"
sudo env \
  UV_TOOL_DIR="/opt/evdb/versions/${VERSION}/tools" \
  UV_TOOL_BIN_DIR="/opt/evdb/versions/${VERSION}/bin" \
  "${UV}" tool install "evanovation-db==${VERSION}"
sudo ln -sfn "versions/${VERSION}" /opt/evdb/current
sudo ln -sfn /opt/evdb/current/bin/evdb /usr/local/bin/evdb
sudo /usr/local/bin/evdb host setup
/usr/local/bin/evdb host check
```

`UV_TOOL_DIR` stores the isolated tool environment and metadata under that version. The separate
`UV_TOOL_BIN_DIR` places its linked `evdb` executable at
`/opt/evdb/versions/<version>/bin/evdb`. The exact `==${VERSION}` requirement prevents an
unbounded package resolution. `/usr/local/bin/evdb` always resolves through `current`, so operators
and units do not depend on a version-specific command path.

For explicit non-interactive setup, provide every required value and `--yes`:

```sh
sudo evdb host setup \
  --host-id example-01 \
  --domain storage.example.com \
  --data-root /srv/databases \
  --acme-email operations@example.com \
  --dns-provider cloudflare \
  --postgres-repo rclone:remote:example-01/postgres \
  --kv-repo rclone:remote:example-01/kv \
  --dns-env-file /root/evdb-dns.env \
  --rclone-config /root/rclone.conf \
  --yes
```

Setup creates a non-login `evdb` account, canonical config, state, data and tool directories,
private host files, the dedicated Docker network and native Traefik definition, and packaged
systemd units. It is idempotent. Repeated setup does not replace database credentials or mutable
rclone OAuth state, restart healthy databases, or change enabled timers.

## Versioned tool layout

```text
/opt/evdb/versions/<version>/
/opt/evdb/current -> versions/<version>
/opt/evdb/previous -> versions/<previous-version>
/usr/local/bin/evdb -> /opt/evdb/current/bin/evdb
```

Configuration, generated Compose, secrets, machine-owned state, backups, and data remain outside
tool versions. Update requires an exact semantic version:

```sh
sudo evdb host update 1.3.0
```

The candidate reads current config, state, Compose, backup records, and packaged units before the
switch. It previews compatible file migrations, snapshots affected config and unit files,
activates atomically, refreshes units, and runs `evdb host check`. Failure restores the active tool,
files, and loaded units. Update retains one previous version and must preserve timer state.

A package update does not regenerate database Compose, change image digests, restart databases,
restore data, or perform an engine migration. A candidate unable to read the installed contracts is
rejected before activation.

## Development boundary

Repository checks use `tests/fixtures/config` and temporary `/etc`, `/var/lib`, `/opt`, data, and
Restic roots. They do not run setup, package activation, systemd changes, or production commands.
Initial production conversion and credential import are a separate production migration.
Operator Make targets do not accept alternate configuration paths; fixture configuration is loaded
only by tests that inject temporary canonical paths.

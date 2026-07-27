# Host setup and updates

## Requirements

evdb supports Ubuntu 22.04 or newer on ARM64 and x86_64. A database host needs:

- Docker with Compose
- Restic and rclone
- systemd
- DNS-01 credentials for native TLS routing
- writable configuration, state, tool, and data filesystems
- free native ports `5432` and `6379`

The standalone release includes evdb's Python runtime. Python, pip, pipx, and `uv` are not production
prerequisites. Setup reports missing host tools but does not install or upgrade them. Docker group
membership is root-equivalent and must be granted deliberately.

## Install

Install the latest public release:

```sh
curl -fsSL https://github.com/evannotfound/evdb/releases/latest/download/install.sh | sudo sh
sudo evdb host setup
```

Pin the first installation to an exact version when reproducibility matters:

```sh
curl -fsSL https://github.com/evannotfound/evdb/releases/download/v1.2.3/install.sh \
  | sudo sh -s -- 1.2.3
sudo evdb host setup
```

The anonymous installer works after the repository and its GitHub Releases are public. Before the
first public release, download and inspect the matching release assets through authenticated GitHub
access rather than piping a private URL.

The installer detects ARM64 or x86_64, downloads the matching archive and SHA-256 file, rejects an
unexpected archive layout, and confirms that `evdb --version` matches the selected release. It stages
the complete version before changing either managed link. Release assets use the stable names
`evdb_linux_arm64.tar.gz` and `evdb_linux_amd64.tar.gz`.

Rerunning the installer can replace an existing tool version only before `/etc/evdb/host.yml` exists.
Once host configuration exists, repair any invalid config and use `sudo evdb host update VERSION`.

## Guided and explicit setup

`sudo evdb host setup` opens a guided session when the initial configuration is absent. For explicit
non-interactive setup, provide every required value and `--yes`:

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

Setup creates the non-login `evdb` service account, `/etc/evdb/host.yml`, machine-owned state,
private host files, data and tool directories, the dedicated Docker network and Traefik project, and
canonical systemd units. It finishes with `evdb host check`.

Setup is idempotent. Repeated setup does not replace database credentials or mutable rclone OAuth
state, restart healthy databases, or change enabled timers.

## Version layout

Each release archive contains one standalone executable and the canonical service and timer files:

```text
bin/evdb
units/*.service
units/*.timer
```

Installation places that archive in the versioned tool layout:

```text
/opt/evdb/versions/<version>/
/opt/evdb/current -> versions/<version>
/opt/evdb/previous -> versions/<previous-version>
/usr/local/bin/evdb -> /opt/evdb/current/bin/evdb
```

Configuration, generated Compose, private host files, machine-owned state, backups, and database data
remain outside tool versions.

## Update

Updates always name an exact semantic version:

```sh
sudo evdb host update 1.3.0
```

evdb downloads the architecture-specific archive and checksum from the immutable `v1.3.0` GitHub
Release. It rejects checksum errors, links, traversal, duplicate or unexpected members, missing units,
and an executable reporting another version.

Before activation, the candidate reads current configuration, state, Compose, backup records, and
installed units without writing them. evdb previews compatible changes, snapshots affected files,
switches the active version atomically, refreshes units, preserves timer state, and runs
`evdb host check`. Failure restores the prior executable, links, files, loaded units, and timers. One
previous version is retained.

A tool update does not regenerate database Compose, change image digests, restart databases, restore
data, or perform an engine migration.

## Release trust

Semantic-version tags build native ARM64 and x86_64 executables on Ubuntu 22.04 GitHub-hosted runners.
The release workflow runs repository checks, verifies the tag against `evdb --version`, smoke-tests
both executables, packages canonical units, publishes SHA-256 files, and records GitHub artifact
attestations before creating the release.

Checksums protect installation and update from corrupted or substituted archive content within the
GitHub Release channel. Artifact attestations let operators inspect which workflow and source revision
produced an archive. The repository and release account remain the trust root.

## Development boundary

Repository development uses `uv sync --locked`, Ruff, pytest, disposable containers, temporary host
paths, and local Restic repositories. Development never runs setup, systemd changes, or production
migration commands against a live host. Operator commands always use `/etc/evdb/host.yml`; alternate
fixture paths are test inputs only.

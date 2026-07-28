# Host setup and updates

## Requirements

evdb v1 supports Ubuntu 22.04 or newer with systemd on ARM64 and x86_64. A database host needs:

- Docker with Compose
- Restic 0.17 or newer
- rclone
- systemd
- DNS-01 credentials for native TLS routing
- writable configuration, local backup, tool, and data filesystems
- free native ports `5432` and `6379`

The standalone release includes evdb's Python runtime. Python, pip, pipx, and `uv` are not production
prerequisites. Initialization reports missing host tools but does not install or upgrade them. Docker
group membership is root-equivalent and must be granted deliberately.

## Install

Install the latest public release:

```sh
curl -fsSL https://github.com/evannotfound/evdb/releases/latest/download/install.sh | sudo sh
sudo evdb init
```

Pin the first installation to an exact version when reproducibility matters:

```sh
curl -fsSL https://github.com/evannotfound/evdb/releases/download/v1.2.3/install.sh \
  | sudo sh -s -- 1.2.3
sudo evdb init
```

The anonymous installer works after the repository and its GitHub Releases are public. Before the
first public release, download and inspect the matching release assets through authenticated GitHub
access rather than piping a private URL.

The installer detects ARM64 or x86_64, downloads the matching archive and SHA-256 file, rejects an
unexpected archive layout, and confirms that `evdb --version` matches the selected release. It stages
the complete version before changing managed links. Release assets use the stable names
`evdb_linux_arm64.tar.gz` and `evdb_linux_amd64.tar.gz`.

## Initialize

`sudo evdb init` opens a guided session when canonical source is absent. It collects or accepts the
host identity, data root, routing settings, one Restic repository, DNS credentials, Restic password,
and an initial native rclone configuration.

The guided Restic password prompt is masked; leaving it blank generates a password. Direct first init
may use `--restic-password-file PATH`. The file must be a readable, non-symlinked regular file with no
group or other permissions and exactly one non-empty UTF-8 line; one trailing LF is allowed. evdb has
no inline or environment Restic password input and does not print the supplied or generated value.

Initialization creates the non-login `evdb` service account and canonical files:

```text
/etc/evdb/config.yml
/etc/evdb/secrets.yml
/etc/evdb/rclone.conf
```

Canonical `/etc/evdb` is `root:evdb` mode `01770`. `config.yml` is `root:evdb` mode `0640`;
`secrets.yml` and `rclone.conf` are `evdb:evdb` mode `0600`. The sticky, group-writable directory lets
rclone atomically persist its evdb-owned OAuth state without allowing the service account to replace
the root-owned configuration file.

It creates required directories, converges the dedicated Docker network and Traefik project,
initializes or verifies the one Restic repository, installs exactly `evdb-backup.service` and
`evdb-backup.timer`, enables the timer with `systemctl enable --now`, and finishes with `evdb status`.
Restic through rclone creates a configured missing repository path during initialization.

Initialization is rerunnable. Repeated runs preserve existing credentials, mutable rclone OAuth state,
database source, generated role files, and data, and do not restart healthy database projects. Once
`config.yml` exists, init always uses the Restic password already in `secrets.yml`;
`--restic-password-file` has no effect and its path is not read.

## Configured-host updates

Rerun the verified installer with the desired exact version:

```sh
curl -fsSL https://github.com/evannotfound/evdb/releases/download/v1.3.0/install.sh \
  | sudo sh -s -- 1.3.0
```

When `/etc/evdb/config.yml` and a managed current release already exist, the installer verifies and
selects the requested release, records the prior release as `previous`, and invokes
`evdb init --yes`. That refresh may update Traefik and the two packaged backup units from unchanged
host source. It does not rewrite project settings or credentials, regenerate database Compose, change
images or data, or restart database roles.

If post-selection initialization fails, the installer exits nonzero with the direct error. The
selected release and the prior verified release remain on disk so the operator can correct source and
rerun initialization or the installer.

## Version layout

Each release archive contains exactly one standalone executable and two systemd units:

```text
bin/evdb
units/evdb-backup.service
units/evdb-backup.timer
```

Installation places that archive in the versioned tool layout:

```text
/opt/evdb/versions/<version>/
/opt/evdb/current -> versions/<version>
/opt/evdb/previous -> versions/<previous-version>
/usr/local/bin/evdb -> /opt/evdb/current/bin/evdb
```

Configuration, generated files, credentials, local backups, and database data remain outside tool
versions.

## Release trust

Semantic-version tags build native ARM64 and x86_64 executables on Ubuntu 22.04 GitHub-hosted runners.
The release workflow runs repository checks, verifies the tag against `evdb --version`, smoke-tests
both executables, packages the two canonical units, publishes SHA-256 files, and records GitHub
artifact attestations before creating the release.

Checksums protect installation from corrupted or substituted archive content within the GitHub Release
channel. Artifact attestations let operators inspect which workflow and source revision produced an
archive. The repository and release account remain the trust root.

## Development boundary

Repository development uses `uv sync --locked`, Ruff, pytest, disposable containers, temporary host
paths, and local Restic repositories. Development never runs deployment, Restic, Docker, cron, or
systemd changes on `montreal-01`. Operator commands use `/etc/evdb/config.yml`,
`/etc/evdb/secrets.yml`, and `/etc/evdb/rclone.conf`; alternate paths are test inputs only.

### Disposable VPS development

Install `uv` once for the remote operator and create a writable checkout. This setup is deliberately
outside production installation; the remote `uv` process creates a native environment for the VPS
architecture.

```sh
ssh -t toronto-01 'sudo install -d -o "$USER" -g "$(id -gn)" /srv/evdb-dev'
uv run python tools/dev_vps.py toronto-01 /srv/evdb-dev sync
```

The helper rejects `montreal-01` locally before any subprocess and checks the remote hostname before
each action. Sync builds a NUL-delimited manifest of tracked and unignored files
under `src/`, `tests/`, `pyproject.toml`, `uv.lock`, `Makefile`, and `README.md`, passes it directly to
`rsync`, then runs `uv sync --project /srv/evdb-dev --locked`. It does not copy the repository,
ignored files, credentials, or the local virtual environment, and it preserves `.venv` and generated
`__pycache__` directories. Because `.git` is intentionally absent, sync supplies the non-release build
version `0.0.dev0` to `setuptools-scm` for the editable environment only.

Use explicit-path execution for the normal fast loop:

```sh
uv run python tools/dev_vps.py toronto-01 /srv/evdb-dev test tests/unit -q
uv run python tools/dev_vps.py toronto-01 /srv/evdb-dev check
uv run python tools/dev_vps.py toronto-01 /srv/evdb-dev evdb --version
```

`test` and `check` run without sudo. `evdb` allocates a TTY and uses remote sudo because operator
commands require host access; sudo credentials are entered remotely and never placed in command
arguments. Each command resyncs and starts a fresh process with the checkout environment first on
PATH. No persistent shell profile is changed.

For a bounded systemd test window, activation changes only the stable command symlink used by the
units. `/opt/evdb/current` remains untouched:

```sh
uv run python tools/dev_vps.py toronto-01 /srv/evdb-dev activate
# Run the approved disposable-host checks.
uv run python tools/dev_vps.py toronto-01 /srv/evdb-dev deactivate
```

Activation points `/usr/local/bin/evdb` at `/srv/evdb-dev/.venv/bin/evdb`; deactivation points it back
to `/opt/evdb/current/bin/evdb`. Do not run the release installer while development activation is
active because configured-host updates invoke the stable command. Rerun an interrupted sync directly.
Always deactivate after a failed or completed systemd test window.

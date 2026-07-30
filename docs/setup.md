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

The installer detects ARM64 or x86_64, downloads the matching executable and SHA-256 file, and confirms
that `evdb --version` matches the selected release before atomically replacing
`/usr/local/bin/evdb`. Release assets use the stable names `evdb_linux_arm64` and
`evdb_linux_amd64`.

## Initialize

`sudo evdb init` opens a guided session when canonical source is absent. It collects or accepts the
host identity, routing settings, one Restic repository, DNS credentials, Restic password, and the
absolute path of a private native rclone configuration.

The guided Restic password prompt is masked; leaving it blank generates a password. Direct first init
may use `--restic-password-file PATH`. The file must be a readable, non-symlinked regular file with no
group or other permissions and exactly one non-empty UTF-8 line; one trailing LF is allowed. evdb has
no inline or environment Restic password input and does not print the supplied or generated value.

Initialization creates the root-owned canonical files:

```text
/etc/evdb/config.yml
/etc/evdb/secrets.yml
```

Canonical `/etc/evdb` is `root:root` mode `0700`; both files are `root:root` mode `0600`. Generated
Compose, Traefik, backup, lock, and database files live below `/var/lib/evdb`. evdb validates the
configured root-owned rclone file and uses it in place without copying or changing it.

It creates required directories, converges the dedicated Docker network and Traefik project,
initializes or verifies the one Restic repository, installs exactly `evdb-backup.service` and
`evdb-backup.timer`, enables the root-run timer with `systemctl enable --now`, and finishes with `evdb status`.
Restic through rclone creates a configured missing repository path during initialization.

Initialization is rerunnable. Repeated runs preserve existing credentials, external rclone OAuth state,
database source, generated role files, and data, and do not restart healthy database projects. Once
`config.yml` exists, init always uses the Restic password already in `secrets.yml`;
`--restic-password-file` has no effect and its path is not read.

## Configured-host updates

Rerun the verified installer with the desired exact version:

```sh
curl -fsSL https://github.com/evannotfound/evdb/releases/download/v1.3.0/install.sh \
  | sudo sh -s -- 1.3.0
```

When `/etc/evdb/config.yml` and `/usr/local/bin/evdb` already exist, the installer verifies and
atomically installs the requested executable, then invokes `evdb init --yes`. That refresh may update
Traefik and the two embedded backup units from unchanged
host source. It does not rewrite project settings or credentials, regenerate database Compose, change
images or data, or restart database roles.

If post-selection initialization fails, the installer exits nonzero with the direct error and leaves
the verified selected executable installed so the operator can correct source and rerun initialization
or the installer. Automatic tool rollback is outside v1.

## Installed command

Each release is one standalone executable with the two systemd unit templates embedded. Installation
creates one regular file:

```text
/usr/local/bin/evdb
```

Configuration, generated files, credentials, local backups, and database data remain outside the
executable.

## Release trust

Semantic-version tags build native ARM64 and x86_64 executables on Ubuntu 22.04 GitHub-hosted runners.
The release workflow runs repository checks, verifies the tag against `evdb --version`, smoke-tests
both executables and their embedded canonical units, publishes SHA-256 files, and records GitHub
artifact attestations before creating the release.

Checksums protect installation from corrupted or substituted executable content within the GitHub Release
channel. Artifact attestations let operators inspect which workflow and source revision produced an
asset. The repository and release account remain the trust root.

## Development boundary

Repository development uses `uv sync --locked`, Ruff, pytest, disposable containers, temporary host
paths, and local Restic repositories. Development never runs deployment, Restic, Docker, cron, or
systemd changes on `montreal-01`. Operator commands use `/etc/evdb/config.yml`,
`/etc/evdb/secrets.yml`, the configured host rclone file, and `/var/lib/evdb`; alternate paths are test
inputs only.

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

The helper always invokes the checkout executable explicitly and never replaces
`/usr/local/bin/evdb`. Release-level systemd checks use the installed release rather than temporarily
activating development source.

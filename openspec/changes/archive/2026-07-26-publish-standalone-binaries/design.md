## Context

evdb is developed as a Python package and currently asks operators to reproduce its package-manager
layout on every database host. Initial installation creates `uv` tool and bin directories manually,
and `host update` shells out to `uv tool install`. This leaks development tooling into production,
adds Python and `uv` prerequisites, and assumes a package registry that has not been established.

The useful part of the existing design is independent of Python packaging: exact semantic versions
live under `/opt/evdb/versions`, the active and previous versions are symlinks, a candidate must read
the installed contracts before activation, systemd assets are verified and updated atomically, and a
failed health check restores the previous version. The distribution mechanism can change without
weakening that model.

The current production platform is Ubuntu 22.04 ARM64. Public self-hosters also need the common Linux
x86_64 build. The repository will be public before anonymous installation is advertised. Development
continues to use `uv`, the lock file, Ruff, and pytest.

## Goals / Non-Goals

**Goals:**

- Give Linux ARM64 and x86_64 users a short, verified installation path that needs no host Python or
  `uv`.
- Publish reproducible release inputs from semantic-version tags with checksums and GitHub build
  provenance.
- Preserve exact-version updates, candidate compatibility checks, atomic activation, one-version
  rollback, timer state, and the boundary against database deployment.
- Keep one application version consistent across source metadata, the binary, release tags, host
  state, and status.
- Present evdb as a public self-hosting tool in a concise README while retaining operational detail
  in focused documentation.

**Non-Goals:**

- Supporting macOS, Windows, non-glibc Linux, or Linux older than Ubuntu 22.04.
- Rewriting evdb in a compiled language or producing a fully static executable.
- Installing Docker, Restic, rclone, systemd, DNS credentials, or database engines.
- Changing database configuration, Compose contracts, data, backup formats, or production migration
  state.
- Publishing wheels to a Python package registry as the production distribution path.

## Decisions

### Build a PyInstaller one-file executable on each native architecture

PyInstaller will freeze the current Python application and PyYAML dependency into one executable.
The release workflow will build ARM64 on `ubuntu-22.04-arm` and x86_64 on `ubuntu-22.04`, because a
GNU/Linux frozen application must be built against the oldest supported glibc. Native builds avoid
cross-compilation and exercise the actual bootloader architecture.

Alternatives considered:

- Wheels, `uv`, pipx, PEX, and zipapps still require Python or a Python installer on the host.
- Nuitka adds a compiler toolchain without a demonstrated runtime benefit for this command-oriented
  application.
- A Go or Rust rewrite would replace proven database safety behavior solely to change packaging.

One-file startup extracts runtime components to a temporary directory. This is acceptable for the
current command and timer frequency and will be covered by a release smoke test under the service
account assumptions.

### Publish an archive containing the executable and inspectable units

Each release will contain stable assets named `evdb_linux_arm64.tar.gz` and
`evdb_linux_amd64.tar.gz`. An archive contains:

```text
bin/evdb
units/*.service
units/*.timer
```

The binary is standalone, but systemd units remain adjacent because the active updater must inspect
and compare a candidate's units before switching executables. Production unit discovery uses the
adjacent version directory; editable and wheel-based development falls back to package resources.
This avoids inventing a private command to extract assets from another executable.

### Use one source version and validate every release boundary

`evanovation_db.__version__` will be the application version. Package metadata will derive from that
attribute, `evdb --version` will print it, and the release workflow will reject a tag other than
`v<version>`. The installer and updater will run the downloaded executable and reject a mismatch
before placing or activating it.

### Keep the installer small and keep setup interactive

Every release will include `install.sh`. It will:

1. Require Linux and map `aarch64`/`arm64` and `x86_64`/`amd64` to a release asset.
2. Download the latest release by default or an exact requested semantic version.
3. Verify the archive with its release `.sha256` file.
4. Extract into a temporary directory, reject an unexpected layout, and verify `evdb --version`.
5. Atomically place `/opt/evdb/versions/<version>`, then create `current` and the stable command link.

The installer will refuse to replace an existing managed installation and direct that operator to
`evdb host update VERSION`. It will not launch guided setup while its standard input is the curl
pipeline; the README will show `sudo evdb host setup` as the second command. A pinned install uses the
same script with an explicit version argument.

### Download exact update candidates inside evdb

`evdb host update VERSION` will map the current machine architecture, construct the immutable
`v<VERSION>` GitHub Release URL, and use the Python standard library included in the executable to
download the archive and checksum. It will not invoke curl, tar, Python, `uv`, or a shell.

Archive handling will validate the checksum before reading members; allow only the expected regular
files beneath `bin/` and `units/`; reject absolute paths, traversal, links, duplicates, unknown files,
and missing files; and write a staged directory with explicit modes. The staged directory becomes the
candidate only after its version and complete layout pass. The existing compatibility, preview,
activation, unit refresh, post-check, cleanup, and rollback flow then continues unchanged.

### Publish from tags with checksums and provenance

A release workflow will run repository checks, build both architectures, smoke-test each executable,
assemble the archives, generate SHA-256 files, attest the archives with GitHub artifact attestations,
and create the matching GitHub Release. Each release namespace may reuse stable asset names, allowing
the unauthenticated `releases/latest/download/...` installer URL while exact updates use an immutable
tag URL.

The checksum detects corruption and unexpected content. It shares GitHub Releases as a trust domain;
the GitHub attestation supplies independently inspectable build provenance without adding a verifier
or credential to production hosts.

### Make the README a product entry point

The README will contain a concise identity, CI/release/license badges, core features, the two-command
installation, representative guided and direct usage, requirements, topic-document links,
development commands, and MIT licensing. Configuration schemas, the full command catalog, update
internals, recovery algorithms, and Evanovation-specific migration boundaries remain in focused docs
instead of the front page.

## Risks / Trade-offs

- [PyInstaller binaries depend on the build glibc] -> Build natively on Ubuntu 22.04 and document
  Ubuntu 22.04 or newer as the supported baseline.
- [One-file extraction can fail on a hardened no-exec temporary filesystem] -> Smoke-test the release
  artifact and document the supported host assumptions; move to a one-directory archive if a real
  supported host exhibits this restriction.
- [A malformed archive could write outside its candidate directory] -> Verify the checksum first and
  copy only explicitly validated regular members instead of calling unrestricted extraction.
- [A failed download could leave an apparent version] -> Download and validate in a private staging
  directory and clean it on errors and interrupts before creating the candidate path.
- [Latest installation is not a reproducible selector] -> The installer validates and records the
  exact embedded version, supports an explicit version, and keeps host updates exact-only.
- [Public releases expose artifacts while the repository is currently private] -> Do not advertise or
  test anonymous installation until repository visibility is changed deliberately.
- [Release checksums share the release account's trust domain] -> Publish GitHub artifact attestations
  and keep workflow permissions minimal; stronger offline signing remains a future option.

## Migration Plan

1. Add binary/version support, release archive handling, installer, tests, and documentation without
   changing production hosts.
2. Publish and verify a pre-release on native ARM64 and x86_64 runners.
3. Make the repository public, publish the first stable tag, and verify anonymous latest and pinned
   downloads.
4. Fresh hosts install through `install.sh` and run guided setup. Existing development installations
   are reinstalled once from a release archive; subsequent updates use `evdb host update VERSION`.
5. If a candidate fails before activation, remove staging and keep the current version. If activation
   or post-check fails, use the existing automatic rollback to restore the prior binary, units, state,
   links, and timer state.

## Open Questions

None. Public release access, Linux ARM64 and x86_64 support, exact internal updates, latest-by-default
initial installation, a public self-hoster README, and MIT licensing were selected before proposal.

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
INSTALLER = ROOT / "install.sh"


def _release(tmp_path, version, *, init_code=0, valid_checksum=True):
    releases = tmp_path / "releases"
    asset = "evdb_linux_amd64"
    binary = (
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = --version ]; then printf '%s\\n' 'evdb "
        f"{version}'; exit 0; fi\n"
        'if [ "${1:-}" = init ]; then : > "${DESTDIR}/init-called"; '
        f"exit {init_code}; fi\n"
        "exit 2\n"
    )
    folder = releases / f"download/v{version}"
    folder.mkdir(parents=True, exist_ok=True)
    executable = folder / asset
    executable.write_text(binary)
    executable.chmod(0o755)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    if not valid_checksum:
        digest = "0" * 64
    (folder / f"{asset}.sha256").write_text(f"{digest}  {asset}\n")
    return releases


def _run(tmp_path, releases, version, *, extra_env=None):
    destination = tmp_path / "destination"
    env = {
        **os.environ,
        "DESTDIR": str(destination),
        "EVDB_RELEASES": releases.as_uri(),
        "EVDB_UNAME_S": "Linux",
        "EVDB_UNAME_M": "x86_64",
        **(extra_env or {}),
    }
    result = subprocess.run(
        ["sh", str(INSTALLER), version],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return destination, result


def test_installer_syntax_and_first_install(tmp_path):
    subprocess.run(["sh", "-n", str(INSTALLER)], check=True)
    releases = _release(tmp_path, "1.0.0")
    destination, result = _run(tmp_path, releases, "1.0.0")

    command = destination / "usr/local/bin/evdb"
    assert result.returncode == 0, result.stderr
    assert command.is_file() and not command.is_symlink()
    assert command.stat().st_mode & 0o777 == 0o755
    assert "Next: sudo evdb init" in result.stdout
    assert not (destination / "opt/evdb").exists()


def test_configured_update_replaces_command_then_runs_init(tmp_path):
    releases = _release(tmp_path, "1.0.0")
    destination, first = _run(tmp_path, releases, "1.0.0")
    assert first.returncode == 0
    config = destination / "etc/evdb/config.yml"
    config.parent.mkdir(parents=True)
    config.write_text("configured: true\n")
    _release(tmp_path, "1.1.0")

    _destination, second = _run(tmp_path, releases, "1.1.0")

    assert second.returncode == 0, second.stderr
    command = destination / "usr/local/bin/evdb"
    assert subprocess.run(
        [command, "--version"], text=True, capture_output=True
    ).stdout.strip() == ("evdb 1.1.0")
    assert (destination / "init-called").exists()


def test_failed_configured_refresh_leaves_verified_new_command(tmp_path):
    releases = _release(tmp_path, "1.0.0")
    destination, first = _run(tmp_path, releases, "1.0.0")
    assert first.returncode == 0
    config = destination / "etc/evdb/config.yml"
    config.parent.mkdir(parents=True)
    config.write_text("configured: true\n")
    _release(tmp_path, "1.1.0", init_code=7)

    _destination, second = _run(tmp_path, releases, "1.1.0")

    assert second.returncode == 7
    command = destination / "usr/local/bin/evdb"
    assert subprocess.run(
        [command, "--version"], text=True, capture_output=True
    ).stdout.strip() == ("evdb 1.1.0")


def test_invalid_checksum_leaves_installed_command_unchanged(tmp_path):
    releases = _release(tmp_path, "1.0.0")
    destination, first = _run(tmp_path, releases, "1.0.0")
    assert first.returncode == 0
    before = (destination / "usr/local/bin/evdb").read_bytes()
    _release(tmp_path, "1.1.0", valid_checksum=False)

    _destination, second = _run(tmp_path, releases, "1.1.0")

    assert second.returncode != 0
    assert (destination / "usr/local/bin/evdb").read_bytes() == before


def test_installer_rejects_existing_symlinked_command(tmp_path):
    releases = _release(tmp_path, "1.0.0")
    destination = tmp_path / "destination"
    command = destination / "usr/local/bin/evdb"
    command.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    shutil.copy2(releases / "download/v1.0.0/evdb_linux_amd64", outside)
    command.symlink_to(outside)

    _destination, result = _run(tmp_path, releases, "1.0.0")

    assert result.returncode != 0
    assert "unsafe" in result.stderr
    assert command.is_symlink()

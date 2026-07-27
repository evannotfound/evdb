import hashlib
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from evdb import host

ROOT = Path(__file__).parents[2]
INSTALLER = ROOT / "install.sh"


def _release(tmp_path, version="1.2.3", reported=None, extra=None):
    releases = tmp_path / "releases"
    asset = "evdb_linux_amd64.tar.gz"
    binary = (f"#!/bin/sh\nprintf '%s\\n' 'evdb {reported or version}'\n").encode()
    members = [("bin/evdb", binary)]
    members.extend((f"units/{path.name}", path.read_bytes()) for path in host._units())
    if extra is not None:
        members.append(extra)

    built = tmp_path / asset
    with tarfile.open(built, "w:gz") as bundle:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name == "bin/evdb" else 0o644
            bundle.addfile(info, io.BytesIO(data))
    checksum = tmp_path / f"{asset}.sha256"
    checksum.write_text(f"{hashlib.sha256(built.read_bytes()).hexdigest()}  {asset}\n")

    for folder in (releases / "latest/download", releases / f"download/v{version}"):
        folder.mkdir(parents=True)
        shutil.copy2(built, folder / asset)
        shutil.copy2(checksum, folder / checksum.name)
    return releases, asset


def _run(tmp_path, releases, *args, system="Linux", machine="x86_64", env_extra=None):
    destination = tmp_path / "destination"
    env = {
        **os.environ,
        "DESTDIR": str(destination),
        "EVDB_RELEASES": releases.as_uri(),
        "EVDB_UNAME_S": system,
        "EVDB_UNAME_M": machine,
    }
    env.update(env_extra or {})
    result = subprocess.run(
        ["sh", str(INSTALLER), *args],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return destination, result


def test_installer_has_valid_posix_shell_syntax():
    subprocess.run(["sh", "-n", str(INSTALLER)], check=True)


@pytest.mark.parametrize("version_arg", [(), ("1.2.3",)])
def test_installer_supports_latest_and_pinned_release(tmp_path, version_arg):
    releases, _asset = _release(tmp_path)

    destination, result = _run(tmp_path, releases, *version_arg)

    assert result.returncode == 0, result.stderr
    target = destination / "opt/evdb/versions/1.2.3"
    assert (target / "bin/evdb").stat().st_mode & 0o777 == 0o755
    assert sorted(path.name for path in (target / "units").iterdir()) == sorted(
        path.name for path in host._units()
    )
    assert (destination / "opt/evdb/current").resolve() == target
    assert (destination / "usr/local/bin/evdb").resolve() == target / "bin/evdb"
    assert "Next: sudo evdb host setup" in result.stdout


def test_installer_rejects_unsupported_platform_before_writing(tmp_path):
    releases = tmp_path / "releases"

    destination, result = _run(tmp_path, releases, system="Darwin", machine="arm64")

    assert result.returncode != 0
    assert "unsupported operating system: Darwin" in result.stderr
    assert not destination.exists()


def test_installer_rejects_checksum_and_version_mismatch_without_version(tmp_path):
    releases, asset = _release(tmp_path, reported="1.2.4")
    checksum = releases / "download/v1.2.3" / f"{asset}.sha256"
    checksum.write_text("0" * 64 + f"  {asset}\n")

    destination, result = _run(tmp_path, releases, "1.2.3")

    assert result.returncode != 0
    assert "checksum does not match" in result.stderr
    assert not (destination / "opt/evdb/versions/1.2.3").exists()

    source = releases / "latest/download" / asset
    checksum = releases / "latest/download" / f"{asset}.sha256"
    checksum.write_text(f"{hashlib.sha256(source.read_bytes()).hexdigest()}  {asset}\n")
    destination, result = _run(tmp_path, releases)

    assert result.returncode == 0
    assert (destination / "opt/evdb/versions/1.2.4").is_dir()


def test_installer_rejects_pinned_version_mismatch_and_unexpected_archive_member(tmp_path):
    releases, _asset = _release(tmp_path, reported="1.2.4")

    destination, result = _run(tmp_path, releases, "1.2.3")

    assert result.returncode != 0
    assert "version does not match 1.2.3" in result.stderr
    assert not (destination / "opt/evdb/versions/1.2.3").exists()

    other = tmp_path / "unexpected"
    other.mkdir()
    releases, _asset = _release(other, extra=("README", b"unexpected"))
    destination, result = _run(other, releases, "1.2.3")

    assert result.returncode != 0
    assert "unexpected member: README" in result.stderr
    assert not (destination / "opt/evdb/versions/1.2.3").exists()


def test_installer_refuses_existing_managed_installation(tmp_path):
    releases, _asset = _release(tmp_path)
    destination, first = _run(tmp_path, releases)
    current = destination / "opt/evdb/current"
    before = os.readlink(current)

    _destination, second = _run(tmp_path, releases)

    assert first.returncode == 0
    assert second.returncode != 0
    assert "use evdb host update VERSION" in second.stderr
    assert os.readlink(current) == before


@pytest.mark.parametrize(
    "value",
    ["01.2.3", "1.2.3-01", "1.2.3-..", "1.2.3-alpha..1", "1.2.3+build..1"],
)
def test_installer_rejects_invalid_semver_before_downloading(tmp_path, value):
    releases = tmp_path / "releases"

    destination, result = _run(tmp_path, releases, value)

    assert result.returncode != 0
    assert "exact semantic version" in result.stderr
    assert not destination.exists()


def test_installer_rejects_symlinked_managed_root(tmp_path):
    releases = tmp_path / "releases"
    destination = tmp_path / "destination"
    outside = tmp_path / "outside"
    outside.mkdir()
    (destination / "opt").mkdir(parents=True)
    (destination / "opt/evdb").symlink_to(outside, target_is_directory=True)

    _destination, result = _run(tmp_path, releases)

    assert result.returncode != 0
    assert "not a safe directory" in result.stderr
    assert not list(outside.iterdir())


def test_installer_limits_expanded_release_size(tmp_path):
    releases, _asset = _release(
        tmp_path,
        extra=("units/evdb-extra.timer", b"x" * 1_000_000),
    )

    destination, result = _run(
        tmp_path,
        releases,
        env_extra={"EVDB_MAX_RELEASE_SIZE": "100000"},
    )

    assert result.returncode != 0
    assert "expands beyond the size limit" in result.stderr
    assert not (destination / "opt/evdb/current").exists()


def test_installer_limits_archive_while_downloading(tmp_path):
    releases, _asset = _release(tmp_path)

    destination, result = _run(
        tmp_path,
        releases,
        env_extra={"EVDB_MAX_RELEASE_SIZE": "100"},
    )

    assert result.returncode != 0
    assert not (destination / "opt/evdb/versions/1.2.3").exists()
    assert not list((destination / "opt/evdb/versions").glob(".install.*"))


def test_installer_lock_rejects_concurrent_install_without_removing_lock(tmp_path):
    releases = tmp_path / "releases"
    lock = tmp_path / "destination/opt/evdb/.install.lock"
    lock.mkdir(parents=True)

    destination, result = _run(tmp_path, releases)

    assert result.returncode != 0
    assert "another evdb installation is in progress" in result.stderr
    assert lock.is_dir()
    assert not (destination / "opt/evdb/current").exists()


def test_installer_rolls_back_target_and_current_when_stable_link_fails(tmp_path):
    releases, _asset = _release(tmp_path)
    commands = tmp_path / "commands"
    commands.mkdir()
    real_mv = shutil.which("mv")
    wrapper = commands / "mv"
    wrapper.write_text(
        f'#!/bin/sh\ncase "$2" in */usr/local/bin/evdb) exit 42;; esac\nexec "{real_mv}" "$@"\n'
    )
    wrapper.chmod(0o755)

    destination, result = _run(
        tmp_path,
        releases,
        env_extra={"PATH": f"{commands}:{os.environ['PATH']}"},
    )

    assert result.returncode != 0
    assert not (destination / "opt/evdb/versions/1.2.3").exists()
    assert not (destination / "opt/evdb/current").exists()
    assert not (destination / "usr/local/bin/evdb").exists()


def test_installer_signal_cleans_staging_and_does_not_activate(tmp_path):
    releases = tmp_path / "releases"
    commands = tmp_path / "commands"
    commands.mkdir()
    wrapper = commands / "curl"
    wrapper.write_text('#!/bin/sh\nkill -TERM "$PPID"\nsleep 1\nexit 1\n')
    wrapper.chmod(0o755)

    destination, result = _run(
        tmp_path,
        releases,
        env_extra={"PATH": f"{commands}:{os.environ['PATH']}"},
    )

    assert result.returncode != 0
    assert not list((destination / "opt/evdb/versions").glob(".install.*"))
    assert not (destination / "opt/evdb/current").exists()
    assert not (destination / "usr/local/bin/evdb").exists()

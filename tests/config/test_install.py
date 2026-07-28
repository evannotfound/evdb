import hashlib
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

from evdb import host

ROOT = Path(__file__).parents[2]
INSTALLER = ROOT / "install.sh"


def _release(tmp_path, version, *, init_code=0):
    releases = tmp_path / "releases"
    asset = "evdb_linux_amd64.tar.gz"
    binary = (
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = --version ]; then printf '%s\\n' 'evdb "
        f"{version}'; exit 0; fi\n"
        'if [ "${1:-}" = init ]; then : > "${DESTDIR}/init-called"; '
        f"exit {init_code}; fi\n"
        "exit 2\n"
    ).encode()
    members = [("bin/evdb", binary)]
    members.extend((f"units/{path.name}", path.read_bytes()) for path in host._units())
    built = tmp_path / f"{version}-{asset}"
    with tarfile.open(built, "w:gz") as bundle:
        for name, data in members:
            item = tarfile.TarInfo(name)
            item.size = len(data)
            item.mode = 0o755 if name == "bin/evdb" else 0o644
            bundle.addfile(item, io.BytesIO(data))
    checksum = f"{hashlib.sha256(built.read_bytes()).hexdigest()}  {asset}\n"
    folder = releases / f"download/v{version}"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built, folder / asset)
    (folder / f"{asset}.sha256").write_text(checksum)
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


def test_installer_syntax_archive_layout_and_first_install(tmp_path):
    subprocess.run(["sh", "-n", str(INSTALLER)], check=True)
    releases = _release(tmp_path, "1.0.0")
    destination, result = _run(tmp_path, releases, "1.0.0")

    assert result.returncode == 0, result.stderr
    units = destination / "opt/evdb/current/units"
    assert {path.name for path in units.iterdir()} == {"evdb-backup.service", "evdb-backup.timer"}
    assert "Next: sudo evdb init" in result.stdout
    assert not (destination / "init-called").exists()


def test_configured_update_selects_release_then_runs_init(config, tmp_path):
    releases = _release(tmp_path, "1.0.0")
    destination, first = _run(tmp_path, releases, "1.0.0")
    assert first.returncode == 0
    installed_config = destination / "etc/evdb/config.yml"
    installed_config.parent.mkdir(parents=True)
    installed_config.write_text("configured: true\n")
    _release(tmp_path, "1.1.0")

    _destination, second = _run(tmp_path, releases, "1.1.0")

    assert second.returncode == 0, second.stderr
    assert (destination / "opt/evdb/current").resolve().name == "1.1.0"
    assert (destination / "opt/evdb/previous").resolve().name == "1.0.0"
    assert (destination / "init-called").exists()


def test_failed_configured_refresh_keeps_selected_and_previous(tmp_path):
    releases = _release(tmp_path, "1.0.0")
    destination, first = _run(tmp_path, releases, "1.0.0")
    assert first.returncode == 0
    config = destination / "etc/evdb/config.yml"
    config.parent.mkdir(parents=True)
    config.write_text("configured: true\n")
    _release(tmp_path, "1.1.0")
    _destination, second = _run(tmp_path, releases, "1.1.0")
    assert second.returncode == 0
    _release(tmp_path, "1.2.0", init_code=7)

    _destination, third = _run(tmp_path, releases, "1.2.0")

    versions = destination / "opt/evdb/versions"
    assert third.returncode == 7
    assert (destination / "opt/evdb/current").resolve().name == "1.2.0"
    assert (destination / "opt/evdb/previous").resolve().name == "1.1.0"
    assert {path.name for path in versions.iterdir()} == {"1.1.0", "1.2.0"}


def test_three_version_upgrade_retains_current_and_one_previous(tmp_path):
    releases = _release(tmp_path, "1.0.0")
    destination, first = _run(tmp_path, releases, "1.0.0")
    assert first.returncode == 0
    config = destination / "etc/evdb/config.yml"
    config.parent.mkdir(parents=True)
    config.write_text("configured: true\n")
    _release(tmp_path, "1.1.0")
    _destination, second = _run(tmp_path, releases, "1.1.0")
    assert second.returncode == 0
    versions = destination / "opt/evdb/versions"
    shutil.copytree(versions / "1.0.0", versions / "0.9.0")
    _release(tmp_path, "1.2.0")

    _destination, third = _run(tmp_path, releases, "1.2.0")

    assert third.returncode == 0, third.stderr
    assert (destination / "opt/evdb/current").resolve().name == "1.2.0"
    assert (destination / "opt/evdb/previous").resolve().name == "1.1.0"
    assert {path.name for path in versions.iterdir()} == {"1.1.0", "1.2.0"}


def test_existing_inactive_exact_version_is_rejected(tmp_path):
    releases = _release(tmp_path, "1.0.0")
    destination, first = _run(tmp_path, releases, "1.0.0")
    assert first.returncode == 0
    versions = destination / "opt/evdb/versions"
    _release(tmp_path, "1.1.0")
    _destination, second = _run(tmp_path, releases, "1.1.0")
    assert second.returncode == 0
    (versions / "1.0.0/stale").write_text("replace me\n")
    _release(tmp_path, "1.0.0")

    _destination, third = _run(tmp_path, releases, "1.0.0")

    assert third.returncode != 0
    assert "already installed" in third.stderr
    assert (destination / "opt/evdb/current").resolve().name == "1.1.0"
    assert (destination / "opt/evdb/previous").resolve().name == "1.0.0"
    assert {path.name for path in versions.iterdir()} == {"1.0.0", "1.1.0"}
    assert (versions / "1.0.0/stale").is_file()


def test_unsafe_version_entry_fails_before_links_and_refresh_then_reruns(tmp_path):
    releases = _release(tmp_path, "1.0.0")
    destination, first = _run(tmp_path, releases, "1.0.0")
    assert first.returncode == 0
    config = destination / "etc/evdb/config.yml"
    config.parent.mkdir(parents=True)
    config.write_text("configured: true\n")
    _release(tmp_path, "1.1.0")
    _destination, second = _run(tmp_path, releases, "1.1.0")
    assert second.returncode == 0
    (destination / "init-called").unlink()
    versions = destination / "opt/evdb/versions"
    outside = tmp_path / "outside"
    outside.mkdir()
    unsafe = versions / "0.9.0"
    unsafe.symlink_to(outside, target_is_directory=True)
    _release(tmp_path, "1.2.0")

    _destination, failed = _run(tmp_path, releases, "1.2.0")

    assert failed.returncode != 0
    assert (destination / "opt/evdb/current").resolve().name == "1.1.0"
    assert (destination / "opt/evdb/previous").resolve().name == "1.0.0"
    assert unsafe.is_symlink() and outside.is_dir()
    assert not (destination / "init-called").exists()

    unsafe.unlink()
    _destination, recovered = _run(tmp_path, releases, "1.2.0")
    assert recovered.returncode == 0, recovered.stderr
    assert (destination / "opt/evdb/current").resolve().name == "1.2.0"
    assert (destination / "opt/evdb/previous").resolve().name == "1.1.0"
    assert {path.name for path in versions.iterdir()} == {"1.1.0", "1.2.0"}
    assert (destination / "init-called").exists()


def test_prune_failure_rolls_back_links_skips_refresh_and_reruns(tmp_path):
    releases = _release(tmp_path, "1.0.0")
    destination, first = _run(tmp_path, releases, "1.0.0")
    assert first.returncode == 0
    config = destination / "etc/evdb/config.yml"
    config.parent.mkdir(parents=True)
    config.write_text("configured: true\n")
    _release(tmp_path, "1.1.0")
    _destination, second = _run(tmp_path, releases, "1.1.0")
    assert second.returncode == 0
    (destination / "init-called").unlink()
    _release(tmp_path, "1.2.0")
    commands = tmp_path / "commands"
    commands.mkdir()
    real_rm = shutil.which("rm")
    assert real_rm is not None
    fake_rm = commands / "rm"
    fake_rm.write_text(
        f'#!/bin/sh\ncase "$*" in *"/versions/1.0.0") exit 73 ;; esac\nexec "{real_rm}" "$@"\n'
    )
    fake_rm.chmod(0o755)

    _destination, failed = _run(
        tmp_path,
        releases,
        "1.2.0",
        extra_env={"PATH": f"{commands}:{os.environ['PATH']}"},
    )

    versions = destination / "opt/evdb/versions"
    assert failed.returncode != 0
    assert (destination / "opt/evdb/current").resolve().name == "1.1.0"
    assert (destination / "opt/evdb/previous").resolve().name == "1.0.0"
    assert {path.name for path in versions.iterdir()} == {"1.0.0", "1.1.0"}
    assert not (destination / "init-called").exists()

    _destination, recovered = _run(tmp_path, releases, "1.2.0")
    assert recovered.returncode == 0, recovered.stderr
    assert (destination / "opt/evdb/current").resolve().name == "1.2.0"
    assert (destination / "opt/evdb/previous").resolve().name == "1.1.0"
    assert {path.name for path in versions.iterdir()} == {"1.1.0", "1.2.0"}
    assert (destination / "init-called").exists()

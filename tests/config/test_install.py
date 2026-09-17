import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
INSTALLER = ROOT / "install.sh"


def _release(tmp_path, version, *, init_code=0, valid_checksum=True, preview=False, arch="amd64"):
    releases = tmp_path / "releases"
    commit = "a" * 40
    asset = f"evdb_linux_{arch}" + (f"_{commit}_17_1" if preview else "")
    binary = (
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = --version ]; then printf '%s\\n' 'evdb "
        f"{version}'; exit 0; fi\n"
        'if [ "${1:-}" = init ]; then : > "${DESTDIR}/init-called"; '
        f"exit {init_code}; fi\n"
        "exit 2\n"
    )
    folder = releases / ("download/preview" if preview else f"download/v{version}")
    folder.mkdir(parents=True, exist_ok=True)
    executable = folder / asset
    executable.write_text(binary)
    executable.chmod(0o755)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    if not valid_checksum:
        digest = "0" * 64
    (folder / f"{asset}.sha256").write_text(f"{digest}  {asset}\n")
    if preview:
        (folder / "preview.txt").write_text(f"{commit} {version} 17 1\n")
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
        ["sh", str(INSTALLER), *([version] if isinstance(version, str) else version)],
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


def test_default_installer_selects_stable_even_when_preview_exists(tmp_path):
    releases = _release(tmp_path, "1.2.3")
    shutil.copytree(releases / "download/v1.2.3", releases / "latest/download")
    _release(tmp_path, "1.2.4.dev17+gabc1234", preview=True)

    destination, result = _run(tmp_path, releases, [])

    assert result.returncode == 0, result.stderr
    assert "Installed evdb 1.2.3." in result.stdout
    assert (destination / "usr/local/bin/evdb").is_file()


@pytest.mark.parametrize("version", ["1.2.4.dev17+gabc1234", "1.2.4rc1.dev2+gabc1234", "1.2.3"])
@pytest.mark.parametrize("machine,arch", [("x86_64", "amd64"), ("aarch64", "arm64")])
def test_preview_installs_exact_scm_version(tmp_path, version, machine, arch):
    releases = _release(tmp_path, version, preview=True, arch=arch)

    _destination, result = _run(
        tmp_path, releases, "--preview", extra_env={"EVDB_UNAME_M": machine}
    )

    assert result.returncode == 0, result.stderr
    assert f"Installed evdb {version}." in result.stdout


@pytest.mark.parametrize("args", [["--preview", "1.2.3"], ["1.2.3", "--preview"], ["--version"]])
def test_preview_and_exact_selection_are_exclusive(tmp_path, args):
    releases = _release(tmp_path, "1.2.3")

    destination, result = _run(tmp_path, releases, args)

    assert result.returncode != 0
    assert "usage:" in result.stderr
    assert not (destination / "usr/local/bin/evdb").exists()


@pytest.mark.parametrize(
    "invalid",
    [
        "checksum",
        "version",
        "manifest",
        "extra-line",
        "trailing-data",
        "missing-asset",
        "dirty",
        "path",
        "attempt",
    ],
)
def test_preview_failure_preserves_configured_installation(tmp_path, invalid):
    releases = _release(tmp_path, "1.2.3")
    destination, first = _run(tmp_path, releases, "1.2.3")
    assert first.returncode == 0
    command = destination / "usr/local/bin/evdb"
    before = command.read_bytes()
    config = destination / "etc/evdb/config.yml"
    config.parent.mkdir(parents=True)
    config.write_text("configured: true\n")
    _release(tmp_path, "1.2.4.dev17+gabc1234", preview=True, valid_checksum=invalid != "checksum")
    folder = releases / "download/preview"
    manifest = folder / "preview.txt"
    replacements = {
        "version": manifest.read_text().replace("dev17", "dev18"),
        "manifest": "bad\n",
        "extra-line": manifest.read_text() + "unexpected\n",
        "trailing-data": manifest.read_text() + "unexpected",
        "dirty": manifest.read_text().replace("gabc1234", "gabc1234.d20260917"),
        "path": manifest.read_text().replace("a" * 40, "../../outside"),
        "attempt": manifest.read_text().replace("17 1", "17 0"),
    }
    if invalid in replacements:
        manifest.write_text(replacements[invalid])
    if invalid == "missing-asset":
        (folder / f"evdb_linux_amd64_{'a' * 40}_17_1").unlink()

    _destination, result = _run(tmp_path, releases, "--preview")

    assert result.returncode != 0
    assert command.read_bytes() == before
    assert not (destination / "init-called").exists()
    assert not list(command.parent.glob("*.new.*"))


def test_preview_upgrade_refreshes_host_and_can_return_to_stable(tmp_path):
    releases = _release(tmp_path, "1.2.3")
    shutil.copytree(releases / "download/v1.2.3", releases / "latest/download")
    destination, first = _run(tmp_path, releases, "1.2.3")
    assert first.returncode == 0
    config = destination / "etc/evdb/config.yml"
    config.parent.mkdir(parents=True)
    config.write_text("configured: true\n")
    _release(tmp_path, "1.2.4.dev17+gabc1234", preview=True)

    _destination, preview = _run(tmp_path, releases, "--preview")
    assert preview.returncode == 0, preview.stderr
    assert (destination / "init-called").exists()
    _destination, stable = _run(tmp_path, releases, [])
    assert stable.returncode == 0, stable.stderr
    assert "Upgraded evdb to 1.2.3." in stable.stdout


def test_preview_uses_one_manifest_snapshot_during_publication(tmp_path):
    releases = _release(tmp_path, "1.2.4.dev17+gabc1234", preview=True)
    manifest = releases / "download/preview/preview.txt"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, subprocess, sys\n"
        f"subprocess.run([{shutil.which('curl')!r}, *sys.argv[1:]], check=True)\n"
        "if sys.argv[-3].endswith('/preview.txt'):\n"
        "    manifest = pathlib.Path(os.environ['PREVIEW_MANIFEST'])\n"
        "    manifest.write_text('b' * 40 + ' 1.2.4.dev18+gdef1234 18 1\\n')\n"
    )
    curl.chmod(0o755)

    _destination, result = _run(
        tmp_path,
        releases,
        "--preview",
        extra_env={"PATH": f"{fake_bin}:{os.environ['PATH']}", "PREVIEW_MANIFEST": str(manifest)},
    )

    assert result.returncode == 0, result.stderr
    assert "Installed evdb 1.2.4.dev17+gabc1234." in result.stdout
    assert manifest.read_text().startswith("b" * 40)

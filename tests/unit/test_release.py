import hashlib
import io
import shutil
import tarfile

import pytest

from evanovation_db import host
from evanovation_db.errors import HostError
from evanovation_db.run import Result


def _archive(path, members=None):
    members = members or [
        ("bin/evdb", b"binary"),
        ("units/evdb-status.service", b"[Service]\nExecStart=evdb status\n"),
    ]
    with tarfile.open(path, "w:gz") as bundle:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name == "bin/evdb" else 0o644
            bundle.addfile(info, io.BytesIO(data))
    return path


def _checksum(archive):
    path = archive.with_name(archive.name + ".sha256")
    path.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
    return path


def test_release_assets_support_linux_arm64_and_amd64_only():
    assert host._release_asset("Linux", "aarch64") == "evdb_linux_arm64.tar.gz"
    assert host._release_asset("linux", "arm64") == "evdb_linux_arm64.tar.gz"
    assert host._release_asset("Linux", "x86_64") == "evdb_linux_amd64.tar.gz"
    assert host._release_asset("linux", "amd64") == "evdb_linux_amd64.tar.gz"

    with pytest.raises(HostError, match="operating system: darwin"):
        host._release_asset("Darwin", "arm64")
    with pytest.raises(HostError, match="architecture: riscv64"):
        host._release_asset("Linux", "riscv64")


def test_release_urls_pin_exact_tag_and_encode_build_version():
    archive, checksum = host._release_urls("1.2.3+build.1", "Linux", "aarch64")

    assert archive == (
        "https://github.com/evannotfound/evanovation-db/releases/download/"
        "v1.2.3%2Bbuild.1/evdb_linux_arm64.tar.gz"
    )
    assert checksum == archive + ".sha256"
    assert "latest" not in archive


def test_checksum_requires_one_matching_sha256_record(tmp_path):
    archive = tmp_path / "evdb_linux_amd64.tar.gz"
    archive.write_bytes(b"release")
    checksum = _checksum(archive)

    host._verify_checksum(archive, checksum)

    checksum.write_text("0" * 64 + f"  {archive.name}\n")
    with pytest.raises(HostError, match="does not match"):
        host._verify_checksum(archive, checksum)

    checksum.write_text("not a checksum\n")
    with pytest.raises(HostError, match="malformed"):
        host._verify_checksum(archive, checksum)


def test_download_enforces_streaming_size_limit_and_removes_partial_file(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        status = 200

    monkeypatch.setattr(host, "MAX_RELEASE_SIZE", 3)
    monkeypatch.setattr(host, "urlopen", lambda request, timeout: Response(b"four"))
    target = tmp_path / "evdb_linux_amd64.tar.gz"

    with pytest.raises(HostError, match="size limit"):
        host._download("https://example.test/release", target, 30)

    assert not target.exists()


def test_extract_release_writes_only_executable_and_units_with_fixed_modes(tmp_path):
    archive = _archive(
        tmp_path / "release.tar.gz",
        [
            ("bin/evdb", b"binary"),
            ("units/evdb-status.service", b"status"),
            ("units/evdb-new.timer", b"timer"),
        ],
    )
    target = tmp_path / "release"

    host._extract_release(archive, target, {"evdb-status.service"})

    assert (target / "bin/evdb").read_bytes() == b"binary"
    assert (target / "bin/evdb").stat().st_mode & 0o777 == 0o755
    assert (target / "units/evdb-status.service").stat().st_mode & 0o777 == 0o644
    assert sorted(path.name for path in (target / "units").iterdir()) == [
        "evdb-new.timer",
        "evdb-status.service",
    ]


@pytest.mark.parametrize("name", ["../escape", "/bin/evdb", "README", "units/nested/x.timer"])
def test_extract_release_rejects_unsafe_and_unexpected_members(tmp_path, name):
    archive = _archive(
        tmp_path / "release.tar.gz",
        [
            ("bin/evdb", b"binary"),
            ("units/evdb-status.service", b"status"),
            (name, b"bad"),
        ],
    )
    target = tmp_path / "release"

    with pytest.raises(HostError, match="unsafe|unexpected"):
        host._extract_release(archive, target, {"evdb-status.service"})

    assert not target.exists()


def test_extract_release_rejects_links_duplicates_and_missing_units(tmp_path):
    linked = tmp_path / "linked.tar.gz"
    with tarfile.open(linked, "w:gz") as bundle:
        info = tarfile.TarInfo("bin/evdb")
        info.type = tarfile.SYMTYPE
        info.linkname = "/outside"
        bundle.addfile(info)
    with pytest.raises(HostError, match="unsafe"):
        host._extract_release(linked, tmp_path / "linked", set())

    duplicate = _archive(
        tmp_path / "duplicate.tar.gz",
        [("bin/evdb", b"one"), ("bin/evdb", b"two")],
    )
    with pytest.raises(HostError, match="unsafe"):
        host._extract_release(duplicate, tmp_path / "duplicate", set())

    incomplete = _archive(tmp_path / "incomplete.tar.gz", [("bin/evdb", b"binary")])
    with pytest.raises(HostError, match="missing canonical"):
        host._extract_release(incomplete, tmp_path / "incomplete", {"evdb-status.service"})

    wrong_mode = tmp_path / "wrong-mode.tar.gz"
    with tarfile.open(wrong_mode, "w:gz") as bundle:
        for name, data, mode in (
            ("bin/evdb", b"binary", 0o644),
            ("units/evdb-status.service", b"status", 0o644),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = mode
            bundle.addfile(info, io.BytesIO(data))
    with pytest.raises(HostError, match="invalid mode"):
        host._extract_release(wrong_mode, tmp_path / "wrong-mode", {"evdb-status.service"})


def test_install_release_downloads_verifies_versions_and_atomically_places(tmp_path, monkeypatch):
    versions = tmp_path / "versions"
    versions.mkdir()
    target = versions / "1.2.3"
    asset = host._release_asset()
    source = _archive(tmp_path / asset)
    source_checksum = _checksum(source)
    downloads = []

    def download(url, destination, timeout):
        downloads.append((url, timeout))
        shutil.copy2(source_checksum if url.endswith(".sha256") else source, destination)

    monkeypatch.setattr(host, "_download", download)
    monkeypatch.setattr(
        host,
        "run",
        lambda args, **kwargs: Result(tuple(args), 0, "evdb 1.2.3\n", ""),
    )

    host._install_release(
        target,
        "1.2.3",
        (tmp_path / "evdb-status.service",),
        timeout=90,
    )

    assert (target / "bin/evdb").is_file()
    assert (target / "units/evdb-status.service").is_file()
    assert all("/download/v1.2.3/" in url for url, _ in downloads)
    assert all(timeout == 90 for _, timeout in downloads)
    assert not list(versions.glob(".install-*"))


def test_install_release_rejects_version_mismatch_and_cleans_staging(tmp_path, monkeypatch):
    versions = tmp_path / "versions"
    versions.mkdir()
    target = versions / "1.2.3"
    asset = host._release_asset()
    source = _archive(tmp_path / asset)
    source_checksum = _checksum(source)

    monkeypatch.setattr(
        host,
        "_download",
        lambda url, destination, timeout: shutil.copy2(
            source_checksum if url.endswith(".sha256") else source, destination
        ),
    )
    monkeypatch.setattr(
        host,
        "run",
        lambda args, **kwargs: Result(tuple(args), 0, "evdb 1.2.4\n", ""),
    )

    with pytest.raises(HostError, match="version does not match"):
        host._install_release(
            target,
            "1.2.3",
            (tmp_path / "evdb-status.service",),
            timeout=90,
        )

    assert not target.exists()
    assert not list(versions.glob(".install-*"))


def test_install_release_cleans_staging_after_interruption(tmp_path, monkeypatch):
    versions = tmp_path / "versions"
    versions.mkdir()
    target = versions / "1.2.3"
    monkeypatch.setattr(
        host,
        "_download",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        host._install_release(target, "1.2.3", (), timeout=90)

    assert not target.exists()
    assert not list(versions.glob(".install-*"))

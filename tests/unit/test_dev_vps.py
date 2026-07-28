import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location("dev_vps", ROOT / "tools/dev_vps.py")
assert SPEC is not None and SPEC.loader is not None
dev_vps = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev_vps)
SYNC_FILES = (
    "src/evdb/cli.py",
    "tests/unit/test_cli.py",
    "pyproject.toml",
    "uv.lock",
    "Makefile",
    "README.md",
)


def result():
    return SimpleNamespace(stdout=b"")


def test_target_guard_rejects_production_before_subprocesses():
    assert dev_vps.target_host("ubuntu@test-dev-01") == "test-dev-01"
    assert dev_vps.target_host("toronto-01") == "toronto-01"

    for target in (
        "montreal-01",
        "ubuntu@montreal-01",
        "test-dev-01;id",
        "-oProxyCommand=id@toronto-01",
        "bad user@toronto-01",
    ):
        with pytest.raises(dev_vps.DevError, match="montreal-01"):
            dev_vps.target_host(target)


def test_sync_transfers_only_named_paths_and_uses_locked_uv():
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ["git", "ls-files"]:
            files = "\0".join(SYNC_FILES).encode() + b"\0"
            return SimpleNamespace(stdout=files)
        return result()

    dev_vps.sync("ubuntu@test-dev-01", "/srv/evdb-dev", run=run)

    rsync = next(args for args, kwargs in calls if args[0] == "rsync")
    rsync_call = next((args, kwargs) for args, kwargs in calls if args[0] == "rsync")
    assert set(rsync_call[1]["input"].rstrip(b"\0").decode().split("\0")) == set(SYNC_FILES)
    assert "--files-from=-" in rsync
    assert "--from0" in rsync
    assert not any(".git" in item for item in rsync)
    assert "--filter=P /.venv/" in rsync
    assert "/opt/evdb/current" not in " ".join(rsync)
    remote = "\n".join(" ".join(args) for args, kwargs in calls if args[0] == "ssh")
    assert 'test "$(hostname -s)" != montreal-01' in remote
    assert "test ! -L /srv/evdb-dev" in remote
    assert 'test "$resolved" = /srv/evdb-dev' in remote
    assert "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_EVDB=0.0.dev0" in remote
    assert "uv sync --project /srv/evdb-dev --locked" in remote
    assert "/opt/evdb/current" not in remote


def test_production_guard_stops_before_copy_link_or_evdb():
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return result()

    with pytest.raises(dev_vps.DevError, match="montreal-01"):
        dev_vps.execute(
            "montreal-01",
            "/srv/evdb-dev",
            "evdb",
            ["host", "setup"],
            run=run,
        )

    assert calls == []


@pytest.mark.parametrize(
    "path",
    ("/srv/evdb-dev/../other", "/srv/./evdb-dev", "/srv/evdb-dev//nested", "/tmp/dev"),
)
def test_checkout_rejects_escaping_or_ambiguous_paths(path):
    with pytest.raises(dev_vps.DevError, match="below /srv"):
        dev_vps.checkout_path(path)


def test_sync_manifest_excludes_ignored_files(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/kept.py").write_text("kept = True\n")
    (tmp_path / "src/private.env").write_text("credential\n")
    (tmp_path / ".gitignore").write_text("*.env\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", ".gitignore", "src/kept.py"], cwd=tmp_path, check=True)
    monkeypatch.setattr(dev_vps, "ROOT", tmp_path)

    manifest = dev_vps.sync_manifest().rstrip(b"\0").decode().split("\0")

    assert "src/kept.py" in manifest
    assert "src/private.env" not in manifest


def test_sync_manifest_excludes_tracked_files_that_become_ignored(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/legacy.env").write_text("not a credential\n")
    (tmp_path / ".gitignore").write_text("*.env\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-f", "src/legacy.env"], cwd=tmp_path, check=True)
    monkeypatch.setattr(dev_vps, "ROOT", tmp_path)

    manifest = dev_vps.sync_manifest().rstrip(b"\0").decode().split("\0")

    assert "src/legacy.env" not in manifest


def test_test_activation_is_scoped_to_fresh_remote_process():
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["git", "ls-files"]:
            files = "\0".join(SYNC_FILES).encode() + b"\0"
            return SimpleNamespace(stdout=files)
        return result()

    dev_vps.execute("test-dev-01", "/srv/evdb-dev", "test", ["tests/unit", "-q"], run=run)

    command = " ".join(calls[-1])
    assert "exec env EVDB_DEV=1" in command
    assert 'PATH="/srv/evdb-dev/.venv/bin:$HOME/.local/bin:$PATH"' in command
    assert "uv run --project /srv/evdb-dev --locked pytest tests/unit -q" in command
    assert "systemctl" not in command
    assert "/etc/evdb" not in command


def test_activation_is_reversible_without_touching_installed_release():
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return result()

    dev_vps.activate("toronto-01", "/srv/evdb-dev", enabled=True, run=run)
    dev_vps.activate("toronto-01", "/srv/evdb-dev", enabled=False, run=run)

    enabled = " ".join(calls[0])
    disabled = " ".join(calls[1])
    assert "ssh -tt -- toronto-01" in enabled
    assert "sudo ln -sfn /srv/evdb-dev/.venv/bin/evdb /usr/local/bin/evdb" in enabled
    assert "sudo ln -sfn /opt/evdb/current/bin/evdb /usr/local/bin/evdb" in disabled
    assert "expected=$(readlink -f /opt/evdb/current/bin/evdb)" in disabled
    assert "rm" not in enabled + disabled


def test_interrupted_rsync_cleans_partial_state_and_retry_is_identical():
    calls = []
    failed = False

    def run(args, **kwargs):
        nonlocal failed
        calls.append(args)
        if args[:2] == ["git", "ls-files"]:
            files = "\0".join(SYNC_FILES).encode() + b"\0"
            return SimpleNamespace(stdout=files)
        if args[0] == "rsync" and not failed:
            failed = True
            raise subprocess.CalledProcessError(23, args)
        return result()

    with pytest.raises(subprocess.CalledProcessError):
        dev_vps.sync("test-dev-01", "/srv/evdb-dev", run=run)

    assert ".rsync-partial" in " ".join(calls[-1])
    first_rsync = next(args for args in calls if args[0] == "rsync")
    calls.clear()

    dev_vps.sync("test-dev-01", "/srv/evdb-dev", run=run)

    second_rsync = next(args for args in calls if args[0] == "rsync")
    assert second_rsync == first_rsync

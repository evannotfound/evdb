import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location("dev_vps", ROOT / "tools/dev_vps.py")
assert SPEC and SPEC.loader
dev_vps = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev_vps)


def test_invalid_target_is_rejected_before_any_subprocess():
    calls = []
    with pytest.raises(dev_vps.DevError, match="valid explicit SSH host"):
        dev_vps.execute(
            "bad target",
            "/srv/evdb-dev",
            "evdb",
            ["init"],
            run=lambda *args, **kwargs: calls.append(args),
        )
    assert calls == []


def test_execute_returns_remote_exit_code_without_raising(monkeypatch):
    monkeypatch.setattr(dev_vps, "sync", lambda *args, **kwargs: None)
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=7)

    code = dev_vps.execute("toronto-01", "/srv/evdb-dev", "test", ["-q"], run=run)

    assert code == 7
    assert calls[0][1]["check"] is False
    assert "hostname -s" not in " ".join(calls[0][0])


def test_sync_stops_after_fatal_check_ignore(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ["git", "ls-files"]:
            return SimpleNamespace(stdout=b"src/evdb/current.py\0")
        if args[:2] == ["git", "check-ignore"]:
            return SimpleNamespace(stdout=b"", returncode=128)
        pytest.fail(f"unexpected subprocess after check-ignore: {args}")

    with pytest.raises(dev_vps.DevError, match="check-ignore.*128"):
        dev_vps.sync("toronto-01", "/srv/evdb-dev", run=run)

    assert [args[:2] for args, _ in calls] == [
        ["git", "ls-files"],
        ["git", "check-ignore"],
    ]


def test_sync_uses_guarded_manifest_fed_rsync(monkeypatch):
    calls = []
    manifest = b"src/evdb/current.py\0tests/unit/test_current.py\0"
    monkeypatch.setattr(dev_vps, "sync_manifest", lambda **kwargs: manifest)

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    dev_vps.sync("toronto-01", "/srv/evdb-dev", run=run)

    rsync, options = next((args, kwargs) for args, kwargs in calls if args[0] == "rsync")
    assert options["input"] == manifest
    assert "--files-from=-" in rsync
    assert "--from0" in rsync
    assert "--filter=P /.venv/" in rsync
    assert "--filter=P **/__pycache__/" in rsync
    assert not any(".rsync-partial" in item for item in rsync)
    preflight = " ".join(calls[0][0])
    assert "hostname -s" not in preflight
    assert "resolved=$(readlink -f /srv/evdb-dev)" in preflight

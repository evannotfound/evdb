from dataclasses import replace
from pathlib import Path

import pytest

from evanovation_db import deployment, lifecycle
from evanovation_db.config import Config, load, load_lock
from evanovation_db.errors import CommandError, LockError
from evanovation_db.files import hash as file_hash
from evanovation_db.files import write_json, write_text
from evanovation_db.lock import operation
from evanovation_db.run import Result

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests/fixtures/config/minimal"


def test_lifecycle_uses_exact_active_compose_and_preserves_files(tmp_path, monkeypatch):
    config, instance, release = _active(tmp_path, monkeypatch)
    preserved = []
    for name in ("source.yml", "data/value", "etc/secrets/value", "backups/value", "state/value"):
        path = tmp_path / name
        write_text(path, name)
        preserved.append((path, path.read_bytes()))
    before_release = {
        path.relative_to(release): path.read_bytes()
        for path in release.rglob("*")
        if path.is_file()
    }
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(lifecycle, "run", fake_run)
    monkeypatch.setattr(lifecycle, "health", lambda *args, **kwargs: None)

    for action in ("stop", "start", "restart"):
        lifecycle.execute(config, instance, action, {})

    compose = release / _compose_name(instance)
    assert all(str(compose) in args for args, _ in calls)
    assert calls[0][0][-1] == "stop"
    assert calls[1][0][-2:] == ["up", "-d"]
    assert calls[2][0][-1] == "restart"
    assert all(path.read_bytes() == value for path, value in preserved)
    assert {
        path.relative_to(release): path.read_bytes()
        for path in release.rglob("*")
        if path.is_file()
    } == before_release


def test_logs_are_bounded_and_redacted(tmp_path, monkeypatch):
    config, instance, release = _active(tmp_path, monkeypatch)
    secret = "visible-value"

    def fake_run(args, **kwargs):
        assert str(release / _compose_name(instance)) in args
        assert args[-4:] == ["logs", "--no-color", "--tail", "25"]
        output = (
            f"password={secret} token:other redis://default:{secret}@db op://Vault/Item/field\n"
        )
        return Result(tuple(args), 0, output, "")

    monkeypatch.setattr(lifecycle, "run", fake_run)

    result = lifecycle.execute(config, instance, "logs", {"lines": 25})

    assert secret not in result["logs"]
    assert "other" not in result["logs"]
    assert "op://" not in result["logs"]
    assert "<redacted>" in result["logs"]


def test_lifecycle_conflict_and_timeout_fail_closed(tmp_path, monkeypatch):
    config, instance, _ = _active(tmp_path, monkeypatch)
    host = replace(config.host, timeouts={**config.host.timeouts, "command": 0})
    config = Config(host, config.instances)

    with operation(host, instance), pytest.raises(LockError, match="lock is busy"):
        lifecycle.execute(config, instance, "stop", {})

    monkeypatch.setattr(
        lifecycle,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(CommandError("command timed out")),
    )
    with pytest.raises(CommandError, match="timed out"):
        lifecycle.execute(config, instance, "stop", {})


def _active(tmp_path, monkeypatch):
    source = load(FIXTURE)
    host = replace(source.host, lock_dir=tmp_path / "locks")
    config = Config(host, source.instances)
    instance = config.select("postgres/example-prod-01")
    bundle = deployment.build(
        config,
        load_lock(FIXTURE / "host.lock.json"),
        file_hash(FIXTURE / "host.yml"),
    )
    root = tmp_path / "opt"
    release = root / "releases/release-test"
    compose_path = release / _compose_name(instance)
    write_text(compose_path, bundle.files[_compose_name(instance)])
    manifest = {**bundle.manifest, "id": "release-test"}
    write_json(release / deployment.MANIFEST, manifest)
    (root / "current").parent.mkdir(parents=True, exist_ok=True)
    (root / "current").symlink_to(release)
    monkeypatch.setattr(deployment, "ROOT", root)
    return config, instance, release


def _compose_name(instance):
    return f"compose/{instance.group}/{instance.id}.json"

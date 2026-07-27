from dataclasses import replace

import pytest

from evdb.engines import dragonfly
from evdb.errors import BackupError, RestoreError
from evdb.run import Result


def _target(config):
    target = config.select("app-test-01/kv")
    settings = replace(
        target.settings,
        engine="dragonfly",
        image="docker.dragonflydb.io/dragonflydb/dragonfly:v1.34.1",
        memory="256mb",
        threads=1,
    )
    return replace(target, settings=settings)


def test_backup_copies_and_cleans_one_native_generation(config, tmp_path, monkeypatch):
    target = _target(config)
    saved = False
    copied = []
    removed = []
    checked = []

    monkeypatch.setattr(dragonfly.secrets, "read", lambda *args: "password")

    def text(container, password, args, **kwargs):
        nonlocal saved
        assert (container, password, args) == (
            f"evdb-{target.project}-{target.role}-primary",
            "password",
            ["SAVE", "DF", "evdb-run-01"],
        )
        saved = True
        return "OK"

    def execute(container, args, **kwargs):
        if args[0] == "find":
            output = (
                "evdb-run-01-summary.dfs\nevdb-run-01-0000.dfs\nevdb-run-01-0001.dfs\n"
                if saved
                else ""
            )
            return Result(tuple(args), 0, output, "")
        assert args[:3] == ["rm", "-f", "--"]
        removed.extend(args[3:])
        return Result(tuple(args), 0, "", "")

    def copy(source, destination, **kwargs):
        copied.append(source)
        destination.write_text(source.rsplit("/", 1)[1])

    monkeypatch.setattr(dragonfly.kv, "text", text)
    monkeypatch.setattr(dragonfly.kv, "_wait", lambda name: checked.append(("wait", name)))
    monkeypatch.setattr(
        dragonfly.kv,
        "facts",
        lambda name, password: (
            checked.append(("facts", name, password))
            or {"version": "df-v1.34.1", "databases": {"0": 1}, "keys": 1}
        ),
    )
    monkeypatch.setattr(dragonfly.docker, "exec", execute)
    monkeypatch.setattr(dragonfly.docker, "copy", copy)
    monkeypatch.setattr(
        dragonfly.docker,
        "start",
        lambda image, name, args, **kwargs: checked.append(("start", image, name, args, kwargs)),
    )
    monkeypatch.setattr(dragonfly.docker, "remove", lambda name: checked.append(("remove", name)))

    state = type(
        "State",
        (),
        {
            "roles": {
                target.identity: type(
                    "Role",
                    (),
                    {"images": {"primary": type("Image", (), {"image": "dragonfly@sha256:x"})()}},
                )()
            }
        },
    )()

    result = dragonfly.backup(config, target, tmp_path, "run-01", state)

    assert result["format"] == "dragonfly-dfs-v1"
    assert result["snapshot_base"] == "evdb-run-01"
    assert result["files"] == [
        "evdb-run-01-0000.dfs",
        "evdb-run-01-0001.dfs",
        "evdb-run-01-summary.dfs",
    ]
    assert copied == [
        f"evdb-{target.project}-{target.role}-primary:/data/evdb-run-01-0000.dfs",
        f"evdb-{target.project}-{target.role}-primary:/data/evdb-run-01-0001.dfs",
        f"evdb-{target.project}-{target.role}-primary:/data/evdb-run-01-summary.dfs",
    ]
    assert removed == [
        "/data/evdb-run-01-0000.dfs",
        "/data/evdb-run-01-0001.dfs",
        "/data/evdb-run-01-summary.dfs",
    ]
    start = next(item for item in checked if item[0] == "start")
    assert start[4]["mounts"] == [(tmp_path, "/data", True)]
    assert checked[-1][0] == "remove"


@pytest.mark.parametrize(
    "files",
    [
        ("evdb-run-01-0000.dfs",),
        ("evdb-run-01-summary.dfs",),
        ("evdb-run-01-0001.dfs", "evdb-run-01-summary.dfs"),
        (
            "evdb-run-01-0000.dfs",
            "evdb-run-01-other.dfs",
            "evdb-run-01-summary.dfs",
        ),
    ],
)
def test_native_generation_rejects_missing_or_unexpected_files(files):
    with pytest.raises(BackupError):
        dragonfly._snapshot("evdb-run-01", files)


def test_restore_loads_every_native_snapshot_file(config, tmp_path, monkeypatch):
    target = _target(config)
    base = "evdb-run-01"
    files = (f"{base}-0000.dfs", f"{base}-summary.dfs")
    for item in files:
        (tmp_path / item).write_text(item)
    state = type(
        "State",
        (),
        {
            "roles": {
                target.identity: type(
                    "Role",
                    (),
                    {"images": {"primary": type("Image", (), {"image": "dragonfly@sha256:x"})()}},
                )()
            }
        },
    )()
    captured = {}

    def restore(image, folder, name, work, args, expected, **kwargs):
        captured.update(image=image, args=args, files=kwargs["files"], expected=expected)
        return {"databases": {"0": 1}}

    monkeypatch.setattr(dragonfly.kv, "restore", restore)
    record = {
        "format": "dragonfly-dfs-v1",
        "facts": {"snapshot_base": base, "databases": {"0": 1}},
        "files": [{"name": item} for item in files],
    }

    result = dragonfly.restore(config, target, tmp_path, "verify", tmp_path / "work", state, record)

    assert result == {"databases": {"0": 1}}
    assert captured["files"] == files
    assert f"--dbfilename={base}" in captured["args"]
    assert "--nodf_snapshot_format" not in captured["args"]


def test_restore_rejects_incomplete_native_generation(config, tmp_path):
    target = _target(config)
    state = type(
        "State",
        (),
        {
            "roles": {
                target.identity: type(
                    "Role",
                    (),
                    {"images": {"primary": type("Image", (), {"image": "dragonfly@sha256:x"})()}},
                )()
            }
        },
    )()
    record = {
        "format": "dragonfly-dfs-v1",
        "facts": {"snapshot_base": "evdb-run-01"},
        "files": [{"name": "evdb-run-01-summary.dfs"}],
    }

    with pytest.raises(RestoreError, match="summary and at least one shard"):
        dragonfly.restore(config, target, tmp_path, "verify", tmp_path / "work", state, record)


def test_cleanup_failure_is_reported(monkeypatch):
    monkeypatch.setattr(
        dragonfly.docker,
        "exec",
        lambda *args, **kwargs: Result(("rm",), 1, "", "failed"),
    )

    with pytest.raises(BackupError, match="cleanup command failed"):
        dragonfly._cleanup("dragonfly", ("evdb-run-01-summary.dfs",))

from pathlib import Path

from evanovation_db import cli


def test_validate_source(capsys):
    root = Path(__file__).parents[2]

    code = cli.main(["validate", "--source", str(root / "config/montreal-01")])

    assert code == 0
    assert "25 instances" in capsys.readouterr().out


def test_grouped_backup_selects_one_instance(config, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(cli, "load", lambda path: config)

    def fake_backup(current, instance):
        seen["instance"] = instance
        return Path("/backup")

    monkeypatch.setattr(cli, "backup", fake_backup)

    code = cli.main(["--config", "ignored", "backup", "kv", "vercount-prod-01"])

    assert code == 0
    assert seen["instance"].engine == "dragonfly"
    assert capsys.readouterr().out.strip() == "/backup"


def test_restore_selects_snapshot(config, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(cli, "load", lambda path: config)

    def fake_restore(current, instance, folder, *, snapshot):
        seen["instance"] = instance
        seen["folder"] = folder
        seen["snapshot"] = snapshot
        return {"ok": True}

    monkeypatch.setattr(cli, "restore", fake_restore)

    code = cli.main(
        [
            "--config",
            "ignored",
            "restore-check",
            "postgres",
            "test-dev-01",
            "--snapshot",
            "snapshot-id",
        ]
    )

    assert code == 0
    assert seen["instance"].engine == "postgres"
    assert seen["folder"] is None
    assert seen["snapshot"] == "snapshot-id"
    assert '"ok": true' in capsys.readouterr().out


def test_status_prints_current_failure_and_exits_nonzero(config, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load", lambda path: config)
    monkeypatch.setattr(
        cli.status,
        "get",
        lambda current: (
            [
                {
                    "selector": "postgres/test-dev-01",
                    "state": "failed",
                    "errors": {"backup": {"message": "backup failed"}},
                    "details": ["backup: backup failed"],
                }
            ],
            True,
        ),
    )

    code = cli.main(["--config", "ignored", "status"])

    assert code == 1
    assert capsys.readouterr().out.strip() == "postgres/test-dev-01: failed"

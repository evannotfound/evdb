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

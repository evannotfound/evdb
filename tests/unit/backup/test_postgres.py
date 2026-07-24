from evanovation_db.backup import postgres
from evanovation_db.run import Result


def test_postgres_backup_discovers_and_checks_databases(config, tmp_path, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    calls = []

    def fake_exec(container, args, **kwargs):
        calls.append(args)
        if kwargs.get("stdout"):
            kwargs["stdout"].write(b"archive")
            return Result(tuple(args), 0, "", "")
        sql = args[-1] if "-c" in args else ""
        if sql == postgres.DATABASE_SQL:
            return Result(
                tuple(args),
                0,
                '{"name":"postgres","owner":"default"}\n{"name":"other db","owner":"reporting"}\n',
                "",
            )
        if sql == postgres.OBJECT_SQL:
            return Result(tuple(args), 0, "3\n", "")
        if sql == "SHOW server_version":
            return Result(tuple(args), 0, "16.3\n", "")
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(postgres.docker, "exec", fake_exec)
    monkeypatch.setattr(postgres, "_check_archive", lambda instance, archive: None)

    result = postgres.backup(config.host, instance, tmp_path, "run")

    assert result["databases"] == ["postgres", "other db"]
    assert "databases/other%20db.dump" in result["files"]
    assert result["objects"] == {"postgres": 3, "other db": 3}
    assert result["owners"] == {"postgres": "default", "other db": "reporting"}
    assert any(call[0] == "pg_dumpall" for call in calls)

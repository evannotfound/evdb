from dataclasses import replace
from pathlib import Path

import pytest

from evdb import backup, database
from evdb.config import load, write
from evdb.errors import CommandError, DatabaseError


def _runtime(monkeypatch):
    calls = []
    monkeypatch.setattr(database.docker, "validate_compose", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        database.docker,
        "up",
        lambda path, project, **kwargs: calls.append(("up", Path(path), project)),
    )
    monkeypatch.setattr(database, "health", lambda *args, **kwargs: None)
    return calls


def test_add_persists_source_credentials_and_generated_files_before_start(config, monkeypatch):
    calls = _runtime(monkeypatch)
    monkeypatch.setattr(database.random, "token_urlsafe", lambda size: "generated-value")

    updated = database.add(config, "queue-prod-01", "kv")

    target = updated.select("queue-prod-01/kv")
    loaded = load(updated.paths.source, paths=updated.paths)
    assert loaded.select(target.identity).engine == "dragonfly"
    assert loaded.select(target.identity).credentials.password == "generated-value"
    assert target.compose.is_file()
    assert (target.generated / "dragonfly.flags").is_file()
    assert calls == [("up", target.compose, target.compose_project)]


def test_add_persists_custom_postgres_identity_and_encodes_connection(config, monkeypatch):
    _runtime(monkeypatch)

    updated = database.add(
        config,
        "identity-prod-01",
        "postgres",
        username="app user",
        database_name="app/data",
        password="custom password",
    )

    target = updated.select("identity-prod-01/postgres")
    connection = database.connection(target)
    assert target.settings.username == "app user"
    assert target.settings.database == "app/data"
    assert connection["username"] == "app user"
    assert connection["database"] == "app/data"
    assert "app%20user" in connection["url"]
    assert "/app%2Fdata?" in connection["url"]


def test_existing_postgres_rejects_different_creation_identity(config, monkeypatch):
    monkeypatch.setattr(database, "health", lambda *args: pytest.fail("health after mismatch"))

    with pytest.raises(DatabaseError, match="initialized Postgres identity"):
        database.add(config, "app-test-01", "postgres", username="other")


def test_failed_creation_leaves_readable_source_and_generated_files(config, monkeypatch):
    monkeypatch.setattr(database.random, "token_urlsafe", lambda size: "generated-value")
    monkeypatch.setattr(database.docker, "validate_compose", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        database.docker,
        "up",
        lambda *args, **kwargs: (_ for _ in ()).throw(CommandError("compose failed")),
    )

    with pytest.raises(CommandError, match="compose failed"):
        database.add(config, "new-prod-01", "postgres")

    loaded = load(config.paths.source, paths=config.paths)
    target = loaded.select("new-prod-01/postgres")
    assert target.compose.is_file()
    assert (target.generated / "postgres-password").is_file()


def test_start_rerenders_current_source_before_compose(config, monkeypatch):
    target = config.select("app-test-01/kv")
    calls = _runtime(monkeypatch)

    database.start(config, target)

    assert target.compose.is_file()
    assert calls[0][0] == "up"
    assert target.image in target.compose.read_text()


def test_configure_writes_and_invokes_compose_once(config, monkeypatch):
    calls = _runtime(monkeypatch)
    target = config.select("app-test-01/kv")

    updated = database.configure(
        config,
        target,
        {"http_connections": 40, "mode": "cache"},
    )

    changed = updated.select(target.identity)
    assert changed.settings.http.connections == 40
    assert changed.settings.mode == "cache"
    assert len(calls) == 1


def test_logs_are_bounded_and_pass_exact_credentials_for_redaction(config, monkeypatch):
    target = config.select("app-test-01/kv")
    target.compose.parent.mkdir(parents=True)
    target.compose.write_text("services: {}\n")
    seen = {}

    def logs(path, project, lines, **kwargs):
        seen.update(lines=lines, secrets=kwargs["secrets"])
        return "repository visible; local-kv-password\n"

    monkeypatch.setattr(database.docker, "logs", logs)

    assert "repository visible" in database.logs(config, target, lines=25)
    assert seen["lines"] == 25
    assert "local-kv-password" in seen["secrets"]


def test_kv_info_reports_public_and_loopback_http_endpoints(config, monkeypatch):
    target = config.select("app-test-01/kv")
    monkeypatch.setattr(
        database,
        "observe",
        lambda *args: {"running": False, "healthy": False, "health": "stopped"},
    )
    monkeypatch.setattr(backup, "history", lambda *args: [])

    value = database.info(config, target)
    connection = value["connection"]

    assert connection["http_url"] == f"https://{target.domain}"
    assert connection["http_loopback"] == f"http://127.0.0.1:{target.http_port}"
    assert value["backup"]["state"] == "missing"


def test_add_reloads_under_write_lock_and_preserves_concurrent_source(config, monkeypatch):
    calls = _runtime(monkeypatch)
    monkeypatch.setattr(database.random, "token_urlsafe", lambda size: "generated")
    concurrent = replace(
        config,
        host=replace(
            config.host,
            backup=replace(config.host.backup, max_age_hours=48),
        ),
    )
    write(concurrent, secrets=False)

    updated = database.add(config, "worker-prod-01", "kv")

    assert updated.host.backup.max_age_hours == 48
    assert updated.select("worker-prod-01/kv")
    assert len(calls) == 1


def test_configure_reloads_under_write_lock_and_preserves_concurrent_source(config, monkeypatch):
    calls = _runtime(monkeypatch)
    stale = config.select("app-test-01/kv")
    concurrent = replace(
        config,
        host=replace(
            config.host,
            backup=replace(config.host.backup, max_age_hours=48),
        ),
    )
    write(concurrent, secrets=False)

    updated = database.configure(config, stale, {"http_connections": 35})

    assert updated.host.backup.max_age_hours == 48
    assert updated.select(stale.identity).settings.http.connections == 35
    assert len(calls) == 1


def test_restart_rerenders_then_converges_with_compose_up(config, monkeypatch):
    target = config.select("app-test-01/kv")
    calls = _runtime(monkeypatch)
    monkeypatch.setattr(
        database,
        "run",
        lambda *args, **kwargs: pytest.fail("compose restart must not be used"),
        raising=False,
    )

    database.restart(config, target)

    assert calls == [("up", target.compose, target.compose_project)]


@pytest.mark.parametrize("action", [database.start, database.restart])
def test_lifecycle_reloads_and_reselects_current_source_under_locks(config, monkeypatch, action):
    stale = config.select("app-test-01/kv")
    project = config.projects[0]
    updated = replace(
        config,
        projects=(replace(project, kv=replace(project.kv, image="redis:7.4.2")),),
    )
    write(updated, secrets=False)
    seen = []
    monkeypatch.setattr(
        database,
        "render",
        lambda current, target: seen.append(("render", target.image)),
    )
    monkeypatch.setattr(
        database.docker,
        "up",
        lambda path, project, **kwargs: seen.append(("up", kwargs["secrets"])),
    )
    monkeypatch.setattr(
        database,
        "health",
        lambda current, target: seen.append(("health", target.image)),
    )

    action(config, stale)

    assert seen[0] == ("render", "redis:7.4.2")
    assert seen[-1] == ("health", "redis:7.4.2")


def test_existing_role_add_checks_health_and_omitted_engine_is_idempotent(config, monkeypatch):
    calls = []
    monkeypatch.setattr(database, "health", lambda current, target: calls.append(target.engine))

    updated = database.add(config, "app-test-01", "kv")

    assert updated.select("app-test-01/kv").engine == "redis"
    assert calls == ["redis"]


def test_existing_role_add_rejects_conflicting_explicit_engine(config, monkeypatch):
    monkeypatch.setattr(
        database, "health", lambda *args: pytest.fail("health called after conflict")
    )
    with pytest.raises(DatabaseError, match="already uses redis"):
        database.add(config, "app-test-01", "kv", engine="dragonfly")


def test_existing_or_unchanged_role_does_not_succeed_when_unhealthy(config, monkeypatch):
    def unhealthy(*args):
        raise DatabaseError("database is unhealthy")

    monkeypatch.setattr(database, "health", unhealthy)

    with pytest.raises(DatabaseError, match="unhealthy"):
        database.add(config, "app-test-01", "kv")

    target = config.select("app-test-01/kv")
    with pytest.raises(DatabaseError, match="unhealthy"):
        database.configure(config, target, {})


def test_canonical_database_mutation_preserves_source_owner(config, monkeypatch):
    uid = config.paths.source.stat().st_uid
    gid = config.paths.source.stat().st_gid
    write(config)
    calls = _runtime(monkeypatch)
    monkeypatch.setattr(database, "render", lambda *args: None)
    target = config.select("app-test-01/kv")

    database.configure(
        config,
        target,
        {"http_connections": target.settings.http.connections + 1},
    )

    details = config.paths.source.stat()
    assert (details.st_uid, details.st_gid, details.st_mode & 0o777) == (uid, gid, 0o600)
    assert len(calls) == 1


@pytest.mark.parametrize("component", ["project", "role", "data"])
def test_render_rejects_symlinked_data_components(config, tmp_path, monkeypatch, component):
    target = config.select("app-test-01/kv")
    root = config.paths.databases
    root.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / f"outside-{component}"
    if component == "project":
        (outside / target.role / "data").mkdir(parents=True)
        (root / target.project).symlink_to(outside, target_is_directory=True)
    elif component == "role":
        (root / target.project).mkdir()
        (outside / "data").mkdir(parents=True)
        (root / target.project / target.role).symlink_to(outside, target_is_directory=True)
    else:
        (root / target.project / target.role).mkdir(parents=True)
        outside.mkdir()
        target.data.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(
        database.docker,
        "validate_compose",
        lambda *args, **kwargs: pytest.fail("unsafe data path reached Compose validation"),
    )

    with pytest.raises(DatabaseError, match="data path is unsafe"):
        database.render(config, target)


def test_render_preserves_existing_mode_restricted_data_directory(config, monkeypatch):
    target = config.select("app-test-01/kv")
    target.data.mkdir(parents=True)
    target.data.chmod(0)
    before = target.data.lstat()
    monkeypatch.setattr(database.docker, "validate_compose", lambda *args, **kwargs: None)

    try:
        database.render(config, target)
        after = target.data.lstat()
    finally:
        target.data.chmod(0o700)

    assert (after.st_uid, after.st_gid, after.st_mode & 0o777) == (
        before.st_uid,
        before.st_gid,
        0,
    )


def test_render_uses_custom_host_data_root(config, tmp_path, monkeypatch):
    parent = tmp_path / "database-volume"
    parent.mkdir()
    selected = replace(config, host=replace(config.host, data_root=parent / "evdb"))
    target = selected.select("app-test-01/kv")
    monkeypatch.setattr(database.docker, "validate_compose", lambda *args, **kwargs: None)

    data = database.render(selected, target)

    assert target.data.is_dir()
    assert data["services"][target.service("primary")]["volumes"][0] == f"{target.data}:/data"


def test_render_rejects_changed_data_root_before_mutation(config, tmp_path, monkeypatch):
    target = config.select("app-test-01/kv")
    monkeypatch.setattr(database.docker, "validate_compose", lambda *args, **kwargs: None)
    database.render(config, target)
    before = target.compose.read_text()
    parent = tmp_path / "database-volume"
    parent.mkdir()
    selected = replace(config, host=replace(config.host, data_root=parent / "evdb"))
    changed = selected.select(target.identity)

    with pytest.raises(DatabaseError, match="data root cannot change"):
        database.render(selected, changed)

    assert not changed.data.exists()
    assert target.compose.read_text() == before


def test_info_reports_latest_validated_backup_without_credentials(config, monkeypatch):
    target = config.select("app-test-01/kv")
    monkeypatch.setattr(
        database,
        "observe",
        lambda *args: {"running": False, "healthy": False, "health": "stopped"},
    )
    monkeypatch.setattr(
        backup,
        "history",
        lambda *args: [
            {
                "backup": "latest-backup",
                "time": "2026-07-28T12:00:00+00:00",
                "snapshot": "latest-snapshot",
                "source": "local+remote",
            },
            {
                "backup": "older-backup",
                "time": "2026-07-27T12:00:00+00:00",
                "snapshot": "older-snapshot",
                "source": "remote",
            },
        ],
    )

    summary = database.info(config, target)["backup"]

    assert summary == {
        "state": "available",
        "availability": "local+remote",
        "time": "2026-07-28T12:00:00+00:00",
        "backup": "latest-backup",
        "snapshot": "latest-snapshot",
    }
    assert target.credentials.password not in str(summary)
    assert target.credentials.http_token not in str(summary)


def test_info_reports_disabled_and_redacted_backup_errors(config, monkeypatch):
    target = config.select("app-test-01/kv")
    monkeypatch.setattr(
        database,
        "observe",
        lambda *args: {"running": False, "healthy": False, "health": "stopped"},
    )
    cache = replace(target, settings=replace(target.settings, mode="cache"))
    assert database.info(config, cache)["backup"]["state"] == "disabled"

    secret = target.credentials.password
    monkeypatch.setattr(
        backup,
        "history",
        lambda *args: (_ for _ in ()).throw(DatabaseError(f"history failed {secret}")),
    )

    summary = database.info(config, target)["backup"]

    assert summary["state"] == "error"
    assert summary["error"] == "history failed <redacted>"
    assert secret not in str(summary)

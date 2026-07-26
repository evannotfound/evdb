import json
from dataclasses import replace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from evanovation_db import backup, compose, database, secrets
from evanovation_db.config import dump, load_state, replace_role, resolve_state, write_state
from evanovation_db.errors import CommandError, DatabaseError
from evanovation_db.run import Result

DIGEST = "sha256:" + "a" * 64


def _prepare(config, monkeypatch):
    monkeypatch.setattr(compose, "validate", lambda *args, **kwargs: None)
    state = resolve_state(config, resolver=lambda source: DIGEST)
    roles = {name: replace(role, installed=True) for name, role in state.roles.items()}
    state = replace(state, roles=roles)
    config.paths.source.parent.mkdir(parents=True, exist_ok=True)
    config.paths.source.write_text(dump(config))
    write_state(config, state)
    for target in config.databases:
        secrets.ensure(config, target, generate=lambda: "private-value")
        compose.write(target.compose, compose.database(config, target, state))
    return state


def test_add_defaults_kv_to_dragonfly_and_commits_once(config, monkeypatch):
    monkeypatch.setattr(compose, "validate", lambda *args, **kwargs: None)
    calls = []
    monkeypatch.setattr(
        database,
        "run",
        lambda args, **kwargs: calls.append(args) or Result(tuple(args), 0, "", ""),
    )

    change = database.prepare_add(
        config,
        "queue-prod-01",
        "kv",
        resolver=lambda source: DIGEST,
        generate=lambda: "private-value",
    )
    result = database.commit(change, check_health=lambda *args, **kwargs: None)

    added = change.after.select("queue-prod-01/kv")
    assert added.engine == "dragonfly"
    assert (added.settings.memory, added.settings.threads) == ("256mb", 1)
    assert result["status"] == "healthy"
    assert len([args for args in calls if "up" in args and "-d" in args]) == 1
    assert all("--remove-orphans" in args for args in calls if "up" in args)
    assert load_state(change.after).roles[added.identity].installed
    assert "engine: dragonfly" in change.after.paths.source.read_text()


def test_matching_add_is_idempotent(config, monkeypatch):
    state = _prepare(config, monkeypatch)

    change = database.prepare_add(
        config,
        "app-test-01",
        "kv",
        state=state,
        resolver=lambda source: DIGEST,
    )

    assert change.noop
    assert database.commit(change) == {
        "database": "app-test-01/kv",
        "changed": [],
        "status": "unchanged",
    }


def test_configure_previews_and_uses_one_restart_after_safety_backup(config, monkeypatch):
    state = _prepare(config, monkeypatch)
    calls = []
    monkeypatch.setattr(
        database,
        "run",
        lambda args, **kwargs: calls.append(args) or Result(tuple(args), 0, "", ""),
    )
    backups = []

    def backup_create(*args, **kwargs):
        backups.append((args, kwargs))
        current = load_state(config)
        role = current.roles["app-test-01/postgres"]
        write_state(
            config,
            replace(
                current,
                roles={
                    **current.roles,
                    "app-test-01/postgres": replace(
                        role,
                        operations={"backup": {"backup": "safety", "ok": True}},
                    ),
                },
            ),
        )
        return {"snapshot": "snapshot-1"}

    change = database.prepare_configure(
        config,
        "app-test-01/postgres",
        {"image": "postgres:16.1", "max_clients": 30},
        state=state,
        resolver=lambda source: DIGEST,
    )

    assert "image" in change.preview()
    assert "max_clients" in change.preview()
    assert change.safety_backup
    result = database.commit(
        change,
        backup_create=backup_create,
        check_health=lambda *args, **kwargs: None,
    )

    assert result["safety_snapshot"] == "snapshot-1"
    assert backups[0][1] == {"purpose": "safety", "lock_held": True}
    assert len([args for args in calls if "up" in args and "-d" in args]) == 1
    assert all("--remove-orphans" in args for args in calls if "up" in args)
    operations = load_state(change.after).roles[change.database.identity].operations
    assert operations["backup"]["backup"] == "safety"
    assert change.after.paths.source.stat().st_mode & 0o777 == 0o640
    assert change.after.paths.previous.stat().st_mode & 0o777 == 0o640
    assert change.database.compose.stat().st_mode & 0o777 == 0o640
    assert change.after.paths.machine_state.stat().st_mode & 0o777 == 0o600


def test_service_contracts_limit_safety_backup_to_primary_changes(config, monkeypatch):
    target = config.select("app-test-01/kv")
    durable = replace_role(config, target, replace(target.settings, mode="durable"))
    state = _prepare(durable, monkeypatch)
    target = durable.select(target.identity)
    before = compose.database(durable, target, state)
    primary_name = "evdb-app-test-01-kv-primary"

    sidecar = database.prepare_configure(
        durable,
        target.identity,
        {"http_connections": target.settings.http.connections + 1},
        state=state,
        resolver=lambda source: DIGEST,
    )
    primary = database.prepare_configure(
        durable,
        target.identity,
        {"mode": "cache"},
        state=state,
        resolver=lambda source: DIGEST,
    )

    assert not sidecar.safety_backup
    assert before["services"][primary_name] == sidecar.compose_data["services"][primary_name]
    assert primary.safety_backup
    assert before["services"][primary_name] != primary.compose_data["services"][primary_name]
    sidecar.cancel()
    primary.cancel()


def test_http_disable_and_reenable_preserves_port_and_reads_or_creates_token(config, monkeypatch):
    state = _prepare(config, monkeypatch)
    target = config.select("app-test-01/kv")
    port = state.roles[target.identity].http_port
    calls = []
    monkeypatch.setattr(
        database,
        "run",
        lambda args, **kwargs: calls.append(args) or Result(tuple(args), 0, "", ""),
    )

    disabled = database.prepare_configure(
        config,
        target.identity,
        {"http": False},
        state=state,
        resolver=lambda source: DIGEST,
    )
    database.commit(disabled, check_health=lambda *args, **kwargs: None)
    disabled_config = disabled.after
    disabled_state = load_state(disabled_config)
    token_path = secrets.path(disabled_config, disabled.database, "http-token")

    assert disabled_state.roles[target.identity].http_port == port
    assert token_path.read_text().strip() == "private-value"

    existing = database.prepare_configure(
        disabled_config,
        target.identity,
        {"http": True},
        state=disabled_state,
        resolver=lambda source: DIGEST,
        generate=lambda: "unused-token",
    )
    existing_token = next(item for item in existing.secret_files if item.path.name == "http-token")
    assert existing.after_state.roles[target.identity].http_port == port
    assert existing_token.content == "private-value\n"
    existing.cancel()

    token_path.unlink()
    generated = database.prepare_configure(
        disabled_config,
        target.identity,
        {"http": True},
        state=disabled_state,
        resolver=lambda source: DIGEST,
        generate=lambda: "new-http-token",
    )
    generated_token = next(
        item for item in generated.secret_files if item.path.name == "http-token"
    )
    assert generated.after_state.roles[target.identity].http_port == port
    assert generated_token.content == "new-http-token\n"
    assert not token_path.exists()

    database.commit(generated, check_health=lambda *args, **kwargs: None)
    assert token_path.read_text().strip() == "new-http-token"
    assert all("--remove-orphans" in args for args in calls if "up" in args)


def test_major_and_engine_specific_changes_fail_before_compose(config, monkeypatch):
    state = _prepare(config, monkeypatch)

    with pytest.raises(DatabaseError, match="major changes"):
        database.prepare_configure(
            config,
            "app-test-01/kv",
            {"image": "redis:8"},
            state=state,
            resolver=lambda source: DIGEST,
        )
    with pytest.raises(DatabaseError, match="only valid for dragonfly"):
        database.prepare_configure(
            config,
            "app-test-01/kv",
            {"threads": 4},
            state=state,
            resolver=lambda source: DIGEST,
        )


def test_failed_candidate_restores_exact_files_and_prior_health(config, monkeypatch):
    state = _prepare(config, monkeypatch)
    source = config.paths.source.read_bytes()
    target = config.select("app-test-01/kv")
    prior_compose = target.compose.read_bytes()
    calls = []
    monkeypatch.setattr(
        database,
        "run",
        lambda args, **kwargs: calls.append(args) or Result(tuple(args), 0, "", ""),
    )
    checks = 0

    def health(*args, **kwargs):
        nonlocal checks
        checks += 1
        if checks == 1:
            raise DatabaseError("candidate unhealthy")

    change = database.prepare_configure(
        config,
        target.identity,
        {"http_connections": 33},
        state=state,
        resolver=lambda source: DIGEST,
    )

    with pytest.raises(DatabaseError, match="prior service recovered"):
        database.commit(change, check_health=health)

    assert config.paths.source.read_bytes() == source
    assert target.compose.read_bytes() == prior_compose
    assert checks == 2
    assert not change.transaction.exists()
    assert len([args for args in calls if "up" in args and "-d" in args]) == 2
    assert all("--remove-orphans" in args for args in calls if "up" in args)


def test_failed_recovery_preserves_diagnostics(config, monkeypatch):
    state = _prepare(config, monkeypatch)
    monkeypatch.setattr(
        database,
        "run",
        lambda args, **kwargs: Result(tuple(args), 0, "", ""),
    )
    change = database.prepare_configure(
        config,
        "app-test-01/kv",
        {"http_connections": 34},
        state=state,
        resolver=lambda source: DIGEST,
    )

    with pytest.raises(DatabaseError, match="recovery failed"):
        database.commit(
            change,
            check_health=lambda *args, **kwargs: (_ for _ in ()).throw(DatabaseError("unhealthy")),
        )

    assert change.transaction.is_dir()
    assert (change.transaction / "transaction.json").is_file()


def test_preview_fingerprint_blocks_changed_source(config, monkeypatch):
    state = _prepare(config, monkeypatch)
    change = database.prepare_configure(
        config,
        "app-test-01/kv",
        {"http_connections": 35},
        state=state,
        resolver=lambda source: DIGEST,
    )
    config.paths.source.write_text(config.paths.source.read_text() + "\n")

    with pytest.raises(DatabaseError, match="changed after preview"):
        database.commit(change, check_health=lambda *args, **kwargs: None)


def test_orphaned_installed_role_blocks_mutation_without_deleting_it(config, monkeypatch):
    state = _prepare(config, monkeypatch)
    orphan = replace(state.roles["app-test-01/kv"], installed=True)
    state = replace(state, roles={**state.roles, "old-prod-01/kv": orphan})

    with pytest.raises(DatabaseError, match="removal is unsupported"):
        database.prepare_configure(
            config,
            "app-test-01/kv",
            {"http_connections": 40},
            state=state,
            resolver=lambda source: DIGEST,
        )

    assert "old-prod-01/kv" in state.roles


def test_lifecycle_uses_only_the_selected_compose_project(config, monkeypatch):
    state = _prepare(config, monkeypatch)
    target = config.select("app-test-01/kv")
    calls = []
    monkeypatch.setattr(
        database,
        "run",
        lambda args, **kwargs: calls.append(args) or Result(tuple(args), 0, "bounded logs\n", ""),
    )
    monkeypatch.setattr(database, "health", lambda *args, **kwargs: None)

    database.start(config, target, state=state)
    database.stop(config, target, state=state)
    database.restart(config, target, state=state)
    text = database.logs(config, target, lines=20)

    assert text == "bounded logs\n"
    assert all(target.compose_project in args for args in calls)
    assert not any("app-test-01-postgres" in args for args in calls)
    assert calls[-1][-4:] == ["logs", "--no-color", "--tail", "20"]


def test_add_failure_stops_candidate_before_cleanup_and_removes_empty_assets(config, monkeypatch):
    monkeypatch.setattr(compose, "validate", lambda *args, **kwargs: None)
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if "up" in args and "-d" in args:
            raise CommandError("start failed")
        if args[-1] == "stop":
            assert change.database.compose.is_file()
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(database, "run", fake_run)
    change = database.prepare_add(
        config,
        "new-prod-01",
        "postgres",
        resolver=lambda source: DIGEST,
        generate=lambda: "private-value",
    )

    with pytest.raises(DatabaseError, match="candidate services stopped"):
        database.commit(change, check_health=lambda *args, **kwargs: None)

    assert any(args[-1:] == ["stop"] for args in calls)
    assert not change.database.compose.exists()
    assert not change.database.data.exists()


def test_add_failure_preserves_nonempty_transaction_data(config, monkeypatch):
    monkeypatch.setattr(compose, "validate", lambda *args, **kwargs: None)
    change = database.prepare_add(
        config,
        "new-prod-01",
        "postgres",
        resolver=lambda source: DIGEST,
        generate=lambda: "private-value",
    )

    def fake_run(args, **kwargs):
        if "up" in args and "-d" in args:
            (change.database.data / "created-by-engine").write_text("diagnostic")
            raise CommandError("start failed")
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(database, "run", fake_run)

    with pytest.raises(DatabaseError, match="candidate services stopped"):
        database.commit(change, check_health=lambda *args, **kwargs: None)

    assert (change.database.data / "created-by-engine").read_text() == "diagnostic"


def test_add_stop_failure_preserves_candidate_definition_diagnostics_and_redacted_log(
    config, monkeypatch, capsys
):
    monkeypatch.setattr(compose, "validate", lambda *args, **kwargs: None)
    change = database.prepare_add(
        config,
        "new-prod-01",
        "postgres",
        resolver=lambda source: DIGEST,
        generate=lambda: "private-value",
    )

    def fake_run(args, **kwargs):
        if "up" in args and "-d" in args:
            raise CommandError("private-value start failed")
        if args[-1] == "stop":
            assert change.database.compose.is_file()
            raise CommandError("stop failed")
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(database, "run", fake_run)

    with pytest.raises(DatabaseError, match="recovery failed"):
        database.commit(change, check_health=lambda *args, **kwargs: None)

    assert change.database.compose.is_file()
    assert change.database.data.is_dir()
    assert change.transaction.is_dir()
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    failed = next(
        event
        for event in events
        if event["event"] == "database_operation" and event["result"] == "failed"
    )
    assert (failed["project"], failed["role"], failed["engine"]) == (
        change.database.project,
        change.database.role,
        change.database.engine,
    )
    assert "private-value" not in json.dumps(failed)


def test_info_includes_settings_live_version_health_and_postgres_url(config, monkeypatch):
    state = _prepare(config, monkeypatch)
    target = config.select("app-test-01/postgres")
    password = "p@ss:/ word"
    secret = config.paths.role_secrets(target.project, target.role) / "password"
    secret.write_text(password + "\n")
    secret.chmod(0o600)
    expected = compose.expected_services(compose.database(config, target, state), target)

    def docker_state(name, **kwargs):
        item = next(value for value in expected.values() if value["container"] == name)
        return {
            "running": True,
            "healthy": None,
            "image": item["image"],
            "labels": {compose.CONTRACT_LABEL: item["contract"]},
        }

    monkeypatch.setattr(database.docker, "state", docker_state)
    monkeypatch.setattr(database.postgres, "health", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        database,
        "run",
        lambda args, **kwargs: Result(tuple(args), 0, "postgres (PostgreSQL) 16.4\n", ""),
    )
    monkeypatch.setattr(backup, "history", lambda *args: [])

    value = database.info(config, target)
    parsed = urlsplit(value["url"])

    assert value["settings"]["pgbouncer"] is False
    assert value["source_image"] == "postgres:16"
    assert value["engine_version"] == "postgres (PostgreSQL) 16.4"
    assert value["health"] == "healthy"
    assert unquote(parsed.username) == target.settings.user
    assert unquote(parsed.password) == password
    assert parse_qs(parsed.query) == {"sslmode": ["require"]}


def test_info_includes_concrete_kv_http_credentials(config, monkeypatch):
    state = _prepare(config, monkeypatch)
    target = config.select("app-test-01/kv")
    expected = compose.expected_services(compose.database(config, target, state), target)

    def docker_state(name, **kwargs):
        item = next(value for value in expected.values() if value["container"] == name)
        return {
            "running": True,
            "healthy": True,
            "image": item["image"],
            "labels": {compose.CONTRACT_LABEL: item["contract"]},
        }

    monkeypatch.setattr(database.docker, "state", docker_state)
    monkeypatch.setattr(database.redis, "health", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        database,
        "run",
        lambda args, **kwargs: Result(tuple(args), 0, "Redis server v=7.2.5\n", ""),
    )
    monkeypatch.setattr(backup, "history", lambda *args: [])

    value = database.info(config, target)

    assert value["engine"] == "redis"
    assert value["settings"]["mode"] == "cache"
    assert value["engine_version"] == "Redis server v=7.2.5"
    assert value["url"].startswith("rediss://default:")
    assert unquote(urlsplit(value["url"]).password) == "private-value"
    assert value["http"] == {
        "enabled": True,
        "url": "https://app-test-01.kv-test-01.storage.example.com",
        "token": "private-value",
    }


def test_database_logs_apply_generic_redaction(config, monkeypatch):
    _prepare(config, monkeypatch)
    target = config.select("app-test-01/kv")
    monkeypatch.setattr(
        database,
        "run",
        lambda args, **kwargs: Result(
            tuple(args),
            0,
            "postgresql://user:hunter2@db/app token=abc ref=op://vault/item/password\n",
            "",
        ),
    )

    text = database.logs(config, target)

    assert "hunter2" not in text
    assert "abc" not in text
    assert "op://" not in text
    assert text.count("<redacted>") == 3

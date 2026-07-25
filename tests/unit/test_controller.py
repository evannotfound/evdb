from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from evanovation_db import controller
from evanovation_db.config import Config, ConfigError, load
from evanovation_db.secrets import Credentials

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/config"


class Client:
    def __init__(self, credentials=None, error=None):
        self.value = credentials
        self.error = error
        self.calls = []

    def credentials(self, instance):
        self.calls.append(instance.selector)
        if self.error:
            raise self.error
        return self.value


def test_help_lists_public_workflows_and_requires_explicit_config(monkeypatch, capsys):
    monkeypatch.delenv("EVANOVATION_DB_CONFIG", raising=False)

    with pytest.raises(SystemExit) as caught:
        controller.parser().parse_args(["--help"])

    assert caught.value.code == 0
    output = capsys.readouterr().out
    for command in (
        "validate",
        "plan",
        "apply",
        "create",
        "show",
        "status",
        "backup",
        "backups",
        "backup-check",
        "restore",
        "promote",
        "releases",
        "rollback",
    ):
        assert command in output

    with pytest.raises(SystemExit) as caught:
        controller.parser().parse_args(["validate"])
    assert caught.value.code == 2
    assert "--config" in capsys.readouterr().err


def test_validate_reads_source_and_generated_lock_without_remote_calls(monkeypatch, capsys):
    monkeypatch.setattr(
        controller.remote,
        "call",
        lambda *args, **kwargs: pytest.fail("validate must not use SSH"),
    )

    code = controller.main(["--config", str(FIXTURES / "minimal"), "validate"])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "valid: test-01 (3 databases)\n"
    assert captured.err == ""


def test_show_postgres_prints_complete_running_details(monkeypatch, capsys):
    config = load(FIXTURES / "minimal")
    instance = config.select("example-prod-01")
    client = Client(Credentials("local-password"))
    calls = []
    monkeypatch.setattr(controller, "load", lambda path: config)
    monkeypatch.setattr(controller, "_op", lambda host: client)

    def call(current, operation, selector):
        calls.append((current, operation, selector))
        return _facts(instance, running=True)

    monkeypatch.setattr(controller.remote, "call", call)

    code = controller.main(["--config", "ignored", "show", "example-prod-01"])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    assert "Database: postgres/example-prod-01" in captured.out
    assert "State: running" in captured.out
    assert "Engine version: PostgreSQL 16.9" in captured.out
    assert f"Image: {instance.image}" in captured.out
    assert "Active release: release-123" in captured.out
    assert f"Data path: {instance.data}" in captured.out
    assert f"Container: {instance.container}" in captured.out
    assert "Latest backup: 2026-07-25T10:00:00+00:00 (snapshot snapshot-123)" in captured.out
    assert "1Password item: example-prod-01-postgres" in captured.out
    assert "Username: default" in captured.out
    assert "Database name: postgres" in captured.out
    assert (
        f"Connection URL: postgresql://default:local-password@{instance.domain}:5432/"
        "postgres?sslmode=require" in captured.out
    )
    assert calls == [(config, "show", "postgres/example-prod-01")]
    assert client.calls == ["postgres/example-prod-01"]


def test_show_http_enabled_kv_always_prints_url_endpoint_and_token(monkeypatch, capsys):
    config = load(FIXTURES / "minimal")
    instance = config.select("cache-dev-01")
    client = Client(Credentials("redis-password", "http-token-value"))
    monkeypatch.setattr(controller, "load", lambda path: config)
    monkeypatch.setattr(controller, "_op", lambda host: client)
    monkeypatch.setattr(
        controller.remote, "call", lambda current, operation, selector: _facts(instance)
    )

    code = controller.main(["--config", "ignored", "show", "cache-dev-01"])

    captured = capsys.readouterr()
    assert code == 0
    assert (
        f"Connection URL: rediss://default:redis-password@{instance.domain}:6379/0" in captured.out
    )
    assert f"HTTP endpoint: https://{instance.http['domain']}" in captured.out
    assert "HTTP token: http-token-value" in captured.out


def test_show_stopped_http_disabled_database_keeps_connection_details(monkeypatch, capsys):
    config = load(FIXTURES / "overrides")
    instance = config.select("transient-prod-01")
    client = Client(Credentials("stopped-password"))
    monkeypatch.setattr(controller, "load", lambda path: config)
    monkeypatch.setattr(controller, "_op", lambda host: client)
    monkeypatch.setattr(
        controller.remote,
        "call",
        lambda current, operation, selector: _facts(instance, running=False),
    )

    code = controller.main(["--config", "ignored", "show", "transient-prod-01"])

    captured = capsys.readouterr()
    assert code == 0
    assert "State: exited" in captured.out
    assert "Running: no" in captured.out
    assert f"rediss://default:stopped-password@{instance.domain}:6379/0" in captured.out
    assert "HTTP: disabled" in captured.out
    assert "HTTP token:" not in captured.out


def test_show_ambiguous_selector_reveals_nothing_and_makes_no_external_call(monkeypatch, capsys):
    source = load(FIXTURES / "minimal")
    postgres = source.select("example-prod-01")
    redis = replace(source.select("cache-dev-01"), id=postgres.id)
    config = Config(source.host, (postgres, redis))
    monkeypatch.setattr(controller, "load", lambda path: config)
    monkeypatch.setattr(
        controller.remote,
        "call",
        lambda *args: pytest.fail("SSH must not run for an ambiguous selector"),
    )
    monkeypatch.setattr(
        controller,
        "_op",
        lambda host: pytest.fail("1Password must not run for an ambiguous selector"),
    )

    code = controller.main(["--config", "ignored", "show", "example-prod-01"])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "ambiguous" in captured.err
    assert "postgres/example-prod-01" in captured.err
    assert "redis/example-prod-01" in captured.err
    assert "Connection URL" not in captured.err


def test_show_missing_credentials_emits_no_partial_details(monkeypatch, capsys):
    config = load(FIXTURES / "minimal")
    instance = config.select("cache-dev-01")
    client = Client(error=ConfigError("1Password item is missing required field http-token"))
    monkeypatch.setattr(controller, "load", lambda path: config)
    monkeypatch.setattr(controller, "_op", lambda host: client)
    monkeypatch.setattr(
        controller.remote, "call", lambda current, operation, selector: _facts(instance)
    )

    code = controller.main(["--config", "ignored", "show", "cache-dev-01"])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "required field http-token" in captured.err
    assert "Connection URL" not in captured.err
    assert instance.domain not in captured.err


def test_show_rejects_unexpected_remote_fields_before_reading_credentials(monkeypatch, capsys):
    config = load(FIXTURES / "minimal")
    instance = config.select("cache-dev-01")
    client = Client(Credentials("must-not-be-read", "must-not-be-read"))
    facts = {**_facts(instance), "password": "remote-value"}
    monkeypatch.setattr(controller, "load", lambda path: config)
    monkeypatch.setattr(controller, "_op", lambda host: client)
    monkeypatch.setattr(controller.remote, "call", lambda *args: facts)

    code = controller.main(["--config", "ignored", "show", "cache-dev-01"])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert client.calls == []
    assert "remote-value" not in captured.err


def test_connection_urls_round_trip_reserved_components():
    config = load(FIXTURES / "minimal")
    postgres = config.select("example-prod-01")
    settings = {**postgres.settings, "user": "user:name/@?#%", "database": "db/name ?#%"}
    postgres = replace(postgres, settings=settings)
    password = "p@ss:/?#% word"

    parsed = urlsplit(controller.postgres_url(postgres, password))

    assert parsed.scheme == "postgresql"
    assert unquote(parsed.username) == settings["user"]
    assert unquote(parsed.password) == password
    assert parsed.hostname == postgres.domain
    assert parsed.port == 5432
    assert unquote(parsed.path.removeprefix("/")) == settings["database"]
    assert parsed.query == "sslmode=require"

    redis = config.select("cache-dev-01")
    parsed = urlsplit(controller.kv_url(redis, password))
    assert parsed.scheme == "rediss"
    assert unquote(parsed.username) == "default"
    assert unquote(parsed.password) == password
    assert parsed.hostname == redis.domain
    assert parsed.port == 6379
    assert parsed.path == "/0"


def _facts(instance, *, running=True):
    return {
        "selector": instance.selector,
        "state": "running" if running else "exited",
        "running": running,
        "health": "healthy" if running else "exited",
        "engine_version": "PostgreSQL 16.9" if running else None,
        "image": instance.image,
        "image_id": "sha256:image-id",
        "service_hash": "service-contract",
        "active_release": "release-123",
        "backup": {
            "finished": "2026-07-25T10:00:00+00:00",
            "uploaded": "2026-07-25T10:01:00+00:00",
            "snapshot": "snapshot-123",
            "upload_ok": True,
        },
    }

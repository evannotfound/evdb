import json
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest

from evdb import cli, database, docker, host, status, ui
from evdb.errors import BackupError, CommandError, DatabaseError
from evdb.run import Result


def _value(*, error=None):
    now = datetime.now(UTC).isoformat()
    return {
        "version": 2,
        "healthy": error is None,
        "host": {
            "id": "test-01",
            "tool_version": "1.2.3",
            "healthy": error is None,
            "source": {"config": "/etc/evdb/config.yml", "valid": True},
            "infrastructure": {
                "network": True,
                "traefik": True,
                "listeners": {"5432": True, "6379": True},
                "acme": True,
                "image": "traefik:v3.7.8",
                "healthy": True,
            },
            "storage": {"path": "/var/lib/evdb", "free_gb": 50.0, "ok": True},
            "repository": {"url": "rclone:remote:evdb/test-01", "ready": True},
            "timer": {
                "unit": "evdb-backup.timer",
                "loaded": True,
                "enabled": True,
                "active": True,
                "ok": True,
            },
        },
        "databases": {
            "app-test-01/kv": {
                "project": "app-test-01",
                "role": "kv",
                "engine": "redis",
                "running": False,
                "health": "unknown" if error else "stopped",
                "healthy": False,
                "image": "redis:7.2.5",
                "latest_backup": {
                    "state": "current",
                    "time": now,
                    "backup": "backup-1",
                    "snapshot": "snapshot-1",
                    "local": True,
                    "remote": True,
                },
                "error": error,
            }
        },
        "errors": (
            [{"code": "assessment_failed", "scope": "app-test-01/kv", "message": error}]
            if error
            else []
        ),
    }


@pytest.mark.parametrize(
    "removed",
    [
        "restore",
        "host",
        "plan",
        "apply",
        "retention",
        "prune",
        "repository-check",
    ],
)
def test_removed_commands_are_not_public(removed):
    with pytest.raises(SystemExit):
        cli.parser().parse_args([removed])


def test_retained_parser_has_top_level_init_status_database_and_backup():
    choices = next(action.choices for action in cli.parser()._actions if action.choices)
    assert set(choices) == {"init", "status", "database", "backup"}
    args = cli.parser().parse_args(["backup", "create", "--all"])
    assert args.all and args.database is None
    assert not hasattr(args, "yes")
    init = cli.parser().parse_args(["init", "--restic-password-file", "/private/password"])
    assert init.restic_password_file == "/private/password"
    assert cli.parser().parse_args(["init", "--data-root", "/mnt/evdb"]).data_root == ("/mnt/evdb")
    add = cli.parser().parse_args(["database", "add", "app-test-01", "kv"])
    assert add.engine is None


def test_command_help_contains_actionable_examples_and_creation_identity():
    root = cli.parser().format_help()
    init = next(action.choices["init"] for action in cli.parser()._actions if action.choices)
    add = next(action.choices["database"] for action in cli.parser()._actions if action.choices)
    add = add._subparsers._group_actions[0].choices["add"]

    assert "sudo evdb init" in root
    assert "rclone:REMOTE:PATH or absolute local path" in init.format_help()
    assert "--username" in add.format_help() and "--database-name" in add.format_help()


def test_explicit_lifecycle_executes_without_confirmation(config, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "load", lambda path: config)
    monkeypatch.setattr(cli, "_canonical", lambda path: True)
    monkeypatch.setattr(cli, "_host_access_allowed", lambda: True)
    monkeypatch.setattr(
        cli.database,
        "stop",
        lambda current, target: calls.append(target.identity),
    )

    code = cli.main(
        ["database", "stop", "app-test-01/kv"],
        input_fn=lambda prompt: pytest.fail(f"unexpected prompt: {prompt}"),
        output=lambda value: None,
    )

    assert code == 0
    assert calls == ["app-test-01/kv"]


def test_cli_catches_expected_errors_but_unexpected_faults_keep_traceback(config, monkeypatch):
    monkeypatch.setattr(cli, "load", lambda path: config)
    monkeypatch.setattr(
        cli.status, "collect", lambda *args: (_ for _ in ()).throw(RuntimeError("bug"))
    )

    with pytest.raises(RuntimeError, match="bug"):
        cli.main(["--config", str(config.paths.source), "status"], output=lambda value: None)


def test_status_json_is_credential_free_and_repository_visible(config, monkeypatch):
    monkeypatch.setattr(status, "free_gb", lambda path: 100.0)
    monkeypatch.setattr(status.backup, "repository_ready", lambda current: True)
    monkeypatch.setattr(status.backup, "history", lambda *args: [])
    monkeypatch.setattr(
        status,
        "run",
        lambda args, **kwargs: Result(tuple(args), 0, "", ""),
    )
    monkeypatch.setattr(
        status.docker,
        "state",
        lambda *args, **kwargs: {"running": False, "healthy": False, "image": None},
    )
    monkeypatch.setattr(
        __import__("evdb.database", fromlist=["observe"]),
        "observe",
        lambda current, target: {"running": False, "healthy": False, "health": "stopped"},
    )
    value = status.collect(config)
    text = status.dumps(value)

    json.loads(text)
    assert config.host.backup.repository in text
    assert "local-postgres-password" not in text
    assert "local-http-token" not in text
    assert "machine_state" not in text
    assert "transaction" not in text
    assert "restore" not in text
    assert set(value["host"]["infrastructure"]["listeners"]) == {"5432", "6379"}


@pytest.mark.parametrize("width", [60, 100, 160])
def test_compact_overview_preserves_identity_and_progressive_details(width):
    value = {
        "host": {"id": "test-01", "healthy": True},
        "databases": {
            "long-application-name-prod-01/postgres": {
                "engine": "postgres",
                "health": "healthy",
                "latest_backup": {"state": "current"},
                "image": "postgres@sha256:" + "a" * 64,
            }
        },
    }

    text = ui.overview(value, width=width)

    assert "Database" in text and "Engine" in text and "Status" in text and "Backup" in text
    assert "postgres" in text
    assert "sha256" not in text
    assert "..." not in text
    assert "long-" in text or "postgres" in text
    assert max(map(len, text.splitlines())) <= width


@pytest.mark.parametrize("width", [60, 100, 160])
def test_status_render_respects_terminal_width(width):
    text = status.render(_value(), width=width)

    assert max(map(len, text.splitlines())) <= width
    assert "app-test-01/kv" in text
    assert "sha256" not in text
    assert "\x1b" not in text


def test_backup_all_returns_nonzero_after_reporting_every_failure(config, monkeypatch):
    monkeypatch.setattr(cli, "load", lambda path: config)
    monkeypatch.setattr(cli, "_canonical", lambda path: True)
    monkeypatch.setattr(cli, "_host_access_allowed", lambda: True)
    monkeypatch.setattr(
        cli.backup,
        "create_all",
        lambda current: {
            "app-test-01/postgres": {"ok": False, "error": "failed"},
            "app-test-01/kv": {"ok": True, "backup": "b1", "snapshot": "s1"},
        },
    )
    output = []

    code = cli.main(["backup", "create", "--all"], output=output.append)

    assert code == 1
    assert len(output) == 2
    assert "postgres" in output[0] and "kv" in output[1]


def test_status_uses_newest_confirmed_remote_snapshot(config, monkeypatch):
    target = config.select("app-test-01/kv")
    now = datetime.now(UTC)
    monkeypatch.setattr(
        database,
        "observe",
        lambda *args: {"running": True, "healthy": True, "health": "healthy"},
    )
    monkeypatch.setattr(
        status.backup,
        "history",
        lambda *args: [
            {
                "time": (now - timedelta(hours=30)).isoformat(),
                "remote": True,
                "snapshot": "old",
                "backup": "old-backup",
                "local": False,
            },
            {
                "time": now.isoformat(),
                "remote": True,
                "snapshot": "new",
                "backup": "new-backup",
                "local": True,
            },
            {
                "time": (now + timedelta(hours=1)).isoformat(),
                "remote": True,
                "snapshot": None,
                "backup": "unconfirmed",
                "local": True,
            },
        ],
    )

    value, errors = status._database(config, target)

    assert value["latest_backup"]["snapshot"] == "new"
    assert value["latest_backup"]["state"] == "current"
    assert not any(item["code"] == "backup_stale" for item in errors)


def test_status_errors_redact_nested_rclone_tokens_and_strip_controls(config, monkeypatch):
    target = config.select("app-test-01/kv")
    secret = "nested OAuth token"
    config.host.backup.rclone_config.write_text(
        '[remote]\ntype = local\ntoken = {"access_token":"nested OAuth token"}\n'
    )
    monkeypatch.setattr(
        database,
        "observe",
        lambda *args: {"running": True, "healthy": True, "health": "healthy"},
    )
    monkeypatch.setattr(
        status.backup,
        "history",
        lambda *args: (_ for _ in ()).throw(
            BackupError(f"\x1b[31mremote failed {secret} {quote(secret, safe='')}\x1b[0m")
        ),
    )

    value, _errors = status._database(config, target)
    text = json.dumps(value)

    assert secret not in text and quote(secret, safe="") not in text
    assert "remote failed" in text
    assert "\x1b" not in text


def test_guided_root_has_headings_blank_rhythm_and_no_duplicate_database_options(
    monkeypatch, config
):
    output = []
    monkeypatch.setattr(status, "collect", lambda current: _value())

    result = ui.run(config, input_fn=lambda prompt: "0", output=output.append)

    assert result == 0
    assert output[0] == "" and output[1] == "Databases"
    assert output.count("") >= 3
    assert sum("app-test-01/kv" in item for item in output) == 1
    assert "Add database" in "\n".join(output)
    assert "\x1b" not in "\n".join(output)


def test_guided_database_details_errors_and_credentials_stay_in_context(config, monkeypatch):
    target = config.select("app-test-01/kv")
    password = target.credentials.password
    token = target.credentials.http_token
    info = {
        "database": target.identity,
        "engine": target.engine,
        "status": "stopped",
        "image": target.image,
        "sidecar_images": {"http": target.settings.http.image},
        "data": str(target.data),
        "compose": str(target.compose),
        "settings": {"mode": "durable"},
        "engine_info": {},
        "backup": {
            "state": "available",
            "availability": "local+remote",
            "time": "2026-07-28T12:00:00+00:00",
            "backup": "history-backup",
            "snapshot": "history-snapshot",
        },
        "connection": {"password": password, "http_token": token},
    }
    monkeypatch.setattr(
        database,
        "observe",
        lambda *args: {"running": False, "healthy": False, "health": "stopped"},
    )
    monkeypatch.setattr(database, "info", lambda *args: info)
    monkeypatch.setattr(
        status,
        "collect",
        lambda *args: _value(error="bounded local assessment error"),
    )
    choices = iter(["1", "2", "0"])
    output = []

    ui._database(config, target.identity, lambda prompt: next(choices), output.append)

    details = output[output.index("Details") + 1]
    connection = output[output.index("Connection") + 1]
    assert target.image in details
    assert "bounded local assessment error" in details
    assert "history-backup" in details and "history-snapshot" in details
    assert "backup-1" not in details and "snapshot-1" not in details
    assert password not in details and token not in details
    assert password in connection and token in connection


def test_guided_connection_does_not_collect_database_info(config, monkeypatch):
    target = config.select("app-test-01/kv")
    monkeypatch.setattr(
        database,
        "observe",
        lambda *args: {"running": False, "healthy": False, "health": "stopped"},
    )
    monkeypatch.setattr(
        database,
        "info",
        lambda *args: pytest.fail("connection should not collect database information"),
    )
    choices = iter(["2", "0"])
    output = []

    ui._database(config, target.identity, lambda prompt: next(choices), output.append)

    connection = output[output.index("Connection") + 1]
    assert target.credentials.password in connection
    assert target.credentials.http_token in connection


def test_direct_terminal_info_includes_backup_summary(config, monkeypatch):
    target = config.select("app-test-01/kv")
    value = {
        "database": target.identity,
        "engine": target.engine,
        "status": "healthy",
        "backup": {
            "state": "available",
            "availability": "remote",
            "time": "2026-07-28T12:00:00+00:00",
            "backup": "direct-backup",
            "snapshot": "direct-snapshot",
        },
        "connection": {"password": target.credentials.password},
    }
    monkeypatch.setattr(database, "info", lambda *args: value)
    args = cli.parser().parse_args(["database", "info", target.identity])
    output = []

    code = cli._database(config, args, output.append, terminal=True)

    assert code == 0
    assert "Backup:" in output[0]
    assert "direct-backup" in output[0] and "direct-snapshot" in output[0]


def test_observe_failure_stays_in_selected_database_with_unknown_state_and_actions(
    config, monkeypatch
):
    monkeypatch.setattr(
        database,
        "observe",
        lambda *args: (_ for _ in ()).throw(DatabaseError("\x1b[31mobserve failed\x1b[0m\0")),
    )
    output = []

    result = ui._database(config, "app-test-01/kv", lambda prompt: "0", output.append)
    text = "\n".join(output)

    assert result is config
    assert "Status: unknown" in text
    assert "Error: observe failed" in text
    assert "1. Details" in text and "4. Start" in text and "0. Back" in text
    assert "\x1b" not in text and "\0" not in text


def test_settings_invalid_field_retries_locally(config):
    target = config.select("app-test-01/kv")
    answers = iter(["", "", "", "", "bad", "30", "y"])
    output = []

    values = ui._settings(target, lambda prompt: next(answers), output.append)

    assert values == {"http_connections": 30}
    assert "Invalid http_connections" in output
    assert "integer value required" in output


def test_host_screen_explicitly_shows_network_traefik_acme_and_runtime_fields():
    output = []

    ui._host(_value(), output.append)
    text = "\n".join(output)

    for expected in (
        "Source:",
        "Storage:",
        "Listeners:",
        "Network: healthy",
        "Traefik: healthy",
        "Acme: ready",
        "Repository:",
        "Timer:",
        "Errors: none",
    ):
        assert expected in text


def test_guided_init_uses_masked_restic_prompt_and_blank_generates(tmp_path):
    prompts = []
    secrets = iter(["dns-token", ""])
    answers = iter(
        [
            "new-test-01",
            "storage.example.com",
            "",
            "ops@example.com",
            "cloudflare",
            "",
            "6",
            "0",
            "n",
            "2",
            str(tmp_path / "repository"),
            "",
        ]
    )

    values = cli._init_values(
        {},
        lambda prompt: next(answers),
        lambda prompt: prompts.append(prompt) or next(secrets),
    )

    assert values["restic_password"] == ""
    assert values["data_root"] == "/var/lib/evdb/databases"
    assert values["dns"] == {"CLOUDFLARE_DNS_API_TOKEN": "dns-token"}
    assert prompts == ["CLOUDFLARE_DNS_API_TOKEN: ", "Initial Restic password: "]
    generated = host._restic_password(values)
    assert generated and generated != values["restic_password"]


def test_guided_init_does_not_prompt_over_supplied_restic_password_file(tmp_path):
    path = tmp_path / "password"
    values = cli._init_values(
        {
            "host_id": "new-test-01",
            "domain": "storage.example.com",
            "data_root": str(tmp_path / "data-root"),
            "acme_email": "ops@example.com",
            "dns_provider": "cloudflare",
            "repository": str(tmp_path / "repository"),
            "dns_file": str(tmp_path / "dns.env"),
            "restic_password_file": str(path),
        },
        lambda prompt: (
            "" if prompt.startswith("Next") else pytest.fail(f"unexpected text prompt: {prompt}")
        ),
        lambda prompt: pytest.fail(f"unexpected password prompt: {prompt}"),
    )

    assert values["restic_password_file"] == str(path)
    assert "restic_password" not in values


def test_append_only_text_prompt_retries_locally_without_control_sequences():
    answers = iter(["bad domain", "storage.example.com"])
    output = []

    value = ui.ask_text(
        lambda prompt: next(answers),
        output.append,
        "Base domain",
        validate=__import__("evdb.config", fromlist=["validate_domain"]).validate_domain,
    )

    assert value == "storage.example.com"
    assert any("example: storage.example.com" in line for line in output)
    assert "\x1b" not in "\n".join(output)


def test_guided_postgres_advanced_identity_is_redacted(config, monkeypatch):
    answers = iter(["custom-prod-01", "1", "y", "app_user", "app_db", "y"])
    passwords = iter(["private password", "private password"])
    seen = {}
    output = []

    def add(current, project, role, **values):
        seen.update(project=project, role=role, **values)
        return current

    monkeypatch.setattr(database, "add", add)

    ui._add(config, lambda prompt: next(answers), output.append, lambda prompt: next(passwords))

    assert seen["username"] == "app_user"
    assert seen["database_name"] == "app_db"
    assert seen["password"] == "private password"
    assert "private password" not in "\n".join(output)
    assert "Password: provided" in "\n".join(output)


@pytest.mark.parametrize(
    ("result", "raises"),
    [
        (Result(("docker",), 1, "", "Error: No such object: absent"), False),
        (Result(("docker",), 1, "", "permission denied"), True),
        (Result(("docker",), 0, "not-json", ""), True),
    ],
)
def test_docker_state_only_suppresses_confirmed_missing_objects(monkeypatch, result, raises):
    monkeypatch.setattr(docker, "run", lambda *args, **kwargs: result)

    if raises:
        with pytest.raises(CommandError):
            docker.state("selected")
    else:
        assert docker.state("selected")["running"] is False


def test_status_json_survives_unavailable_host_and_database_probes(config, monkeypatch):
    def failed(*args, **kwargs):
        raise CommandError("probe unavailable")

    monkeypatch.setattr(status, "free_gb", failed)
    monkeypatch.setattr(status, "run", failed)
    monkeypatch.setattr(status.docker, "state", failed)
    monkeypatch.setattr(status.backup, "repository_ready", failed)
    monkeypatch.setattr(status.backup, "history", failed)
    monkeypatch.setattr(database, "observe", failed)

    value = status.collect(config)
    text = status.dumps(value)
    parsed = json.loads(text)

    assert not parsed["healthy"]
    assert parsed["host"]["storage"]["free_gb"] is None
    assert parsed["host"]["infrastructure"]["network"] is None
    assert parsed["host"]["infrastructure"]["traefik"] is None
    assert set(parsed["host"]["infrastructure"]["listeners"].values()) == {None}
    assert parsed["host"]["timer"]["loaded"] is None
    assert parsed["host"]["repository"]["ready"] is None
    assert all(item["health"] == "unknown" for item in parsed["databases"].values())
    assert all(item["running"] is None for item in parsed["databases"].values())
    codes = {item["code"] for item in parsed["errors"]}
    assert {
        "disk_assessment_failed",
        "network_assessment_failed",
        "traefik_assessment_failed",
        "listener_assessment_failed",
        "timer_assessment_failed",
        "repository_assessment_failed",
        "assessment_failed",
    }.issubset(codes)
    assert max(len(item["message"]) for item in parsed["errors"]) <= 500

    output = []
    monkeypatch.setattr(cli, "load", lambda path: config)
    code = cli.main(
        ["--config", str(config.paths.source), "status", "--json"],
        output=output.append,
    )

    assert code == 1
    assert json.loads(output[0])["host"]["infrastructure"]["network"] is None


def test_status_does_not_hide_unexpected_probe_faults(config, monkeypatch):
    monkeypatch.setattr(
        status,
        "free_gb",
        lambda *args: (_ for _ in ()).throw(RuntimeError("bug")),
    )

    with pytest.raises(RuntimeError, match="bug"):
        status.collect(config)


def test_cli_does_not_claim_healthy_for_existing_unhealthy_role(config, monkeypatch):
    monkeypatch.setattr(cli, "load", lambda path: config)
    monkeypatch.setattr(cli, "_canonical", lambda path: True)
    monkeypatch.setattr(cli, "_host_access_allowed", lambda: True)
    monkeypatch.setattr(
        cli.database,
        "add",
        lambda *args, **kwargs: (_ for _ in ()).throw(DatabaseError("database is unhealthy")),
    )
    output = []
    errors = []

    code = cli.main(
        ["database", "add", "app-test-01", "kv"],
        output=output.append,
        error=errors.append,
    )

    assert code == 1
    assert output == []
    assert errors == ["evdb: database is unhealthy"]

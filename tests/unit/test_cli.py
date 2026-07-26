import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from evanovation_db import cli
from evanovation_db.config import replace_role
from evanovation_db.run import Result


@pytest.mark.parametrize(
    ("argv", "command", "subcommand"),
    [
        (["status"], "status", None),
        (["database", "list"], "database", "list"),
        (["database", "add", "app-prod-01", "postgres"], "database", "add"),
        (["database", "info", "app-prod-01/postgres"], "database", "info"),
        (["database", "configure", "app-prod-01/kv"], "database", "configure"),
        (["database", "start", "app-prod-01/kv"], "database", "start"),
        (["database", "stop", "app-prod-01/kv"], "database", "stop"),
        (["database", "restart", "app-prod-01/kv"], "database", "restart"),
        (["database", "logs", "app-prod-01/kv"], "database", "logs"),
        (["backup", "create", "app-prod-01/postgres"], "backup", "create"),
        (["backup", "list", "app-prod-01/postgres"], "backup", "list"),
        (["backup", "test", "app-prod-01/postgres"], "backup", "test"),
        (["backup", "retention", "app-prod-01/postgres"], "backup", "retention"),
        (["backup", "prune", "postgres"], "backup", "prune"),
        (["backup", "repository-check", "kv"], "backup", "repository-check"),
        (["restore", "app-prod-01/postgres", "latest"], "restore", None),
        (["host", "check"], "host", "check"),
        (["host", "setup"], "host", "setup"),
        (["host", "update", "1.2.3"], "host", "update"),
    ],
)
def test_grouped_parser_contract(argv, command, subcommand):
    args = cli.parser().parse_args(argv)

    assert args.command == command
    if command == "database":
        assert args.database_command == subcommand
    elif command == "backup":
        assert args.backup_command == subcommand
    elif command == "host":
        assert args.host_command == subcommand


@pytest.mark.parametrize("removed", ["plan", "apply", "releases", "rollback", "promote", "remote"])
def test_removed_commands_are_not_parsed(removed):
    with pytest.raises(SystemExit):
        cli.parser().parse_args([removed])


def test_non_tty_missing_argument_fails_immediately(config, monkeypatch):
    monkeypatch.setattr(cli, "load", lambda path: config)
    monkeypatch.setattr(cli, "_tty", lambda: False)
    errors = []

    code = cli.main(["database", "info"], output=lambda value: None, error=errors.append)

    assert code == 1
    assert "database is required" in errors[0]
    assert "app-prod-01/postgres" in errors[0]


def test_status_json_prints_one_document_and_uses_health_exit(config, monkeypatch):
    monkeypatch.setattr(cli, "load", lambda path: config)
    value = {"version": 1, "healthy": True, "host": {}, "databases": {}, "errors": []}
    monkeypatch.setattr(cli.status, "collect", lambda *args: value)
    output = []

    code = cli.main(["status", "--json"], output=output.append)

    assert code == 0
    assert json.loads(output[0]) == value
    assert len(output) == 1


def test_database_info_refuses_non_terminal_output(config, monkeypatch):
    monkeypatch.setattr(cli, "load", lambda path: config)
    monkeypatch.setattr(cli.sys, "stdout", SimpleNamespace(isatty=lambda: False))
    errors = []

    code = cli.main(
        ["database", "info", "app-test-01/kv"],
        output=lambda value: None,
        error=errors.append,
    )

    assert code == 1
    assert "prints credentials" in errors[0]


def test_database_info_rejects_injected_output_even_when_stdout_is_terminal(config, monkeypatch):
    monkeypatch.setattr(cli, "load", lambda path: config)
    monkeypatch.setattr(cli.sys, "stdout", SimpleNamespace(isatty=lambda: True))
    errors = []

    code = cli.main(
        ["database", "info", "app-test-01/kv"],
        output=lambda value: None,
        error=errors.append,
    )

    assert code == 1
    assert "prints credentials" in errors[0]


def test_non_tty_initial_setup_names_missing_option_and_example(paths, monkeypatch):
    monkeypatch.setattr(cli, "_canonical", lambda source: True)
    monkeypatch.setattr(cli, "_tty", lambda: False)
    errors = []

    code = cli.main(
        ["--config", str(paths.source), "host", "setup", "--yes"],
        output=lambda value: None,
        error=errors.append,
    )

    assert code == 1
    assert "host-id is required" in errors[0]
    assert "evdb host setup --host-id host-01" in errors[0]


def test_yes_confirms_but_does_not_invent_missing_input(config, monkeypatch):
    monkeypatch.setattr(cli, "load", lambda path: config)
    monkeypatch.setattr(cli, "_tty", lambda: False)
    errors = []

    code = cli.main(["database", "add", "--yes"], output=lambda value: None, error=errors.append)

    assert code == 1
    assert "project is required" in errors[0]


def test_parser_contract_includes_grouped_public_options():
    args = cli.parser().parse_args(
        [
            "--config",
            "/tmp/host.yml",
            "database",
            "configure",
            "app-prod-01/kv",
            "--image",
            "redis:7.2.5",
            "--no-pgbouncer",
            "--pgbouncer-image",
            "pool:1",
            "--max-clients",
            "101",
            "--pool-size",
            "21",
            "--reserve-size",
            "6",
            "--mode",
            "cache",
            "--no-http",
            "--http-image",
            "http:1",
            "--http-connections",
            "30",
            "--memory",
            "1gb",
            "--threads",
            "4",
            "--reset",
            "mode",
            "--yes",
        ]
    )

    assert vars(args) == {
        "config": "/tmp/host.yml",
        "command": "database",
        "database_command": "configure",
        "database": "app-prod-01/kv",
        "image": "redis:7.2.5",
        "pgbouncer": False,
        "pgbouncer_image": "pool:1",
        "max_clients": 101,
        "pool_size": 21,
        "reserve_size": 6,
        "mode": "cache",
        "http": False,
        "http_image": "http:1",
        "http_connections": 30,
        "memory": "1gb",
        "threads": 4,
        "reset": ["mode"],
        "yes": True,
    }

    setup = cli.parser().parse_args(
        [
            "host",
            "setup",
            "--host-id",
            "host-01",
            "--domain",
            "example.com",
            "--data-root",
            "/srv/db",
            "--acme-email",
            "ops@example.com",
            "--dns-provider",
            "test",
            "--postgres-repo",
            "repo:pg",
            "--kv-repo",
            "repo:kv",
            "--dns-env-file",
            "/tmp/dns.env",
            "--rclone-config",
            "/tmp/rclone.conf",
            "--yes",
        ]
    )
    assert setup.host_id == "host-01"
    assert setup.rclone_config == "/tmp/rclone.conf"
    assert setup.yes

    testing = cli.parser().parse_args(["backup", "test", "--due", "--yes"])
    assert testing.due and testing.yes and testing.database is None
    check = cli.parser().parse_args(
        ["backup", "repository-check", "--all", "--part", "4", "--rotate"]
    )
    assert check.all and check.part == 4 and check.rotate


def test_non_tty_restore_requires_explicit_backup_with_example(config, monkeypatch):
    monkeypatch.setattr(cli, "load", lambda path: config)
    monkeypatch.setattr(cli, "_tty", lambda: False)
    errors = []

    code = cli.main(
        ["restore", "app-test-01/postgres", "--yes"],
        output=lambda value: None,
        error=errors.append,
    )

    assert code == 1
    assert "backup is required" in errors[0]
    assert "latest" in errors[0]


def test_restore_forwards_exact_backup_and_yes_to_domain(config, monkeypatch):
    monkeypatch.setattr(cli, "load", lambda path: config)
    monkeypatch.setattr(cli, "_tty", lambda: False)
    calls = []

    def restore(config, target, selected, **kwargs):
        calls.append((target.identity, selected, kwargs["yes"]))
        return {"status": "healthy"}

    monkeypatch.setattr(cli.restore, "restore", restore)

    code = cli.main(
        ["restore", "app-test-01/postgres", "snapshot-1", "--yes"],
        output=lambda value: None,
    )

    assert code == 0
    assert calls == [("app-test-01/postgres", "snapshot-1", True)]


def test_guided_and_explicit_settings_use_one_identical_domain_request(config, monkeypatch):
    monkeypatch.setattr(cli, "load", lambda path, **kwargs: config)
    monkeypatch.setattr(cli, "_tty", lambda: True)
    calls = []

    class Change:
        noop = False

        def preview(self):
            return "preview"

        def cancel(self):
            calls.append(("cancel",))

    def prepare(config, target, values, *, reset):
        calls.append(("prepare", target, values, reset))
        return Change()

    monkeypatch.setattr(cli.database, "prepare_configure", prepare)
    monkeypatch.setattr(
        cli.database,
        "commit",
        lambda change: calls.append(("commit",)) or {"status": "healthy"},
    )

    cli.main(
        [
            "database",
            "configure",
            "app-test-01/kv",
            "--http-connections",
            "33",
            "--reset",
            "mode",
            "--yes",
        ],
        output=lambda value: None,
    )
    explicit = list(calls)
    calls.clear()

    actions = cli._actions(config, lambda prompt: "yes", lambda value: None)
    actions.configure("app-test-01/kv", {"http_connections": 33}, ("mode",))
    guided = list(calls)

    assert (
        explicit
        == guided
        == [
            ("prepare", "app-test-01/kv", {"http_connections": 33}, ("mode",)),
            ("commit",),
        ]
    )


def test_yes_skips_lifecycle_prompt_but_decline_does_not_call_domain(config, monkeypatch):
    monkeypatch.setattr(cli, "load", lambda path: config)
    calls = []
    monkeypatch.setattr(
        cli.database,
        "stop",
        lambda config, target: calls.append(target.identity),
    )
    monkeypatch.setattr(cli, "_tty", lambda: False)

    assert (
        cli.main(
            ["database", "stop", "app-test-01/kv", "--yes"],
            input_fn=lambda prompt: pytest.fail("--yes prompted"),
            output=lambda value: None,
        )
        == 0
    )
    assert calls == ["app-test-01/kv"]

    monkeypatch.setattr(cli, "_tty", lambda: True)
    cli.main(
        ["database", "stop", "app-test-01/kv"],
        input_fn=lambda prompt: "no",
        output=lambda value: None,
    )
    assert calls == ["app-test-01/kv"]


def test_configure_preview_includes_old_and_new_values(config):
    before = config.select("app-test-01/kv")
    after = replace(
        before,
        settings=replace(
            before.settings,
            http=replace(before.settings.http, connections=33),
        ),
    )
    change = SimpleNamespace(
        prior=before,
        database=after,
        changed=("http_connections",),
        preview=lambda: "Host: test-01",
    )

    text = cli._configure_preview(change)

    assert "Host: test-01" in text
    assert "http_connections: 20 -> 33" in text


def test_alternate_config_rejects_mutation_before_load_or_domain(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load", lambda *args, **kwargs: pytest.fail("loaded config"))
    monkeypatch.setattr(
        cli.database,
        "prepare_configure",
        lambda *args, **kwargs: pytest.fail("called mutation domain"),
    )
    errors = []

    code = cli.main(
        [
            "--config",
            str(tmp_path / "host.yml"),
            "database",
            "configure",
            "app-test-01/kv",
            "--mode",
            "durable",
            "--yes",
        ],
        output=lambda value: None,
        error=errors.append,
    )

    assert code == 1
    assert "alternate --config is read-only" in errors[0]
    assert "/etc/evdb/host.yml" in errors[0]


def test_alternate_config_retains_source_only_database_list(config, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load", lambda path: config)
    output = []

    code = cli.main(
        ["--config", str(tmp_path / "host.yml"), "database", "list"],
        output=output.append,
    )

    assert code == 0
    assert output == [
        "app-test-01/postgres\tpostgres",
        "app-test-01/kv\tredis",
    ]


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["database", "add", "new-prod-01", "postgres"],
        ["database", "configure", "app-prod-01/kv"],
        ["database", "start", "app-prod-01/kv"],
        ["database", "stop", "app-prod-01/kv"],
        ["database", "restart", "app-prod-01/kv"],
        ["backup", "create", "app-prod-01/postgres"],
        ["backup", "test", "app-prod-01/postgres"],
        ["backup", "retention", "--all"],
        ["backup", "prune", "--all"],
        ["backup", "repository-check", "--all"],
        ["restore", "app-prod-01/postgres", "latest"],
        ["host", "setup"],
        ["host", "update", "1.2.3"],
    ],
)
def test_mutating_command_classification(argv):
    assert cli._mutating(cli.parser().parse_args(argv))


def test_retention_dry_run_displays_preview_before_approval(config, monkeypatch):
    output = []
    approvals = []
    args = cli.parser().parse_args(["backup", "retention", "app-test-01/postgres", "--dry-run"])
    monkeypatch.setattr(
        cli.restic,
        "forget",
        lambda *args, **kwargs: Result(("restic",), 0, "remove old snapshot token=private\n", ""),
    )
    monkeypatch.setattr(
        cli.restic,
        "approve_retention",
        lambda current, target: approvals.append((target.identity, len(output))),
    )

    assert cli._backup(config, args, input, output.append) == 0

    assert "remove old snapshot" in output[0]
    assert "private" not in output[0]
    assert approvals == [("app-test-01/postgres", 1)]


def test_interactive_actions_reload_config_and_state_between_mutations(config, monkeypatch):
    persisted = [config]
    observed = []
    state_loads = []

    monkeypatch.setattr(cli, "load", lambda path, **kwargs: persisted[0])
    monkeypatch.setattr(
        cli,
        "load_state",
        lambda current: state_loads.append(current) or SimpleNamespace(),
    )

    def configure(current, target, values, reset, yes, input_fn, output):
        database = current.select(target)
        observed.append((database.settings.http.connections, database.settings.mode, values))
        settings = database.settings
        if "http_connections" in values:
            settings = replace(
                settings,
                http=replace(settings.http, connections=values["http_connections"]),
            )
        if "mode" in values:
            settings = replace(settings, mode=values["mode"])
        persisted[0] = replace_role(current, database, settings)
        return "configured"

    monkeypatch.setattr(cli, "_configure", configure)
    actions = cli._actions(config, lambda prompt: "yes", lambda value: None)

    first = actions.configure("app-test-01/kv", {"http_connections": 33}, ())
    second = actions.configure("app-test-01/kv", {"mode": "durable"}, ())

    assert observed == [
        (20, "cache", {"http_connections": 33}),
        (33, "cache", {"mode": "durable"}),
    ]
    assert first[1].select("app-test-01/kv").settings.http.connections == 33
    assert second[1].select("app-test-01/kv").settings.mode == "durable"
    assert len(state_loads) == 4

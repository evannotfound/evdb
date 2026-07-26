from dataclasses import replace
from pathlib import Path

from evanovation_db import interactive
from evanovation_db.config import DEFAULT_IMAGES, HTTP, KV, Paths, load, with_role


def _actions(calls):
    history = [
        {
            "time": "2026-07-25T10:00:00+00:00",
            "backup": "backup-1",
            "snapshot": "snapshot-1",
            "source": "local+remote",
        },
        {
            "time": "2026-07-24T10:00:00+00:00",
            "backup": "backup-2",
            "snapshot": "snapshot-2",
            "source": "remote",
        },
    ]
    return interactive.Actions(
        status=lambda: "host healthy",
        health=lambda: {
            "app-test-01/postgres": "healthy",
            "app-test-01/kv": "stopped",
        },
        add=lambda project, role, engine: calls.append(("add", project, role, engine)) or "added",
        info=lambda target: calls.append(("info", target)) or "info",
        configure=lambda target, values, reset: (
            calls.append(("configure", target, values, reset)) or "configured"
        ),
        start=lambda target: "started",
        stop=lambda target: "stopped",
        restart=lambda target: "restarted",
        logs=lambda target: "logs",
        backup=lambda target: "backup",
        backup_list=lambda target: "list",
        backup_history=lambda target: history,
        backup_test=lambda target, selected: (
            calls.append(("backup_test", target, selected)) or "tested"
        ),
        restore=lambda target, selected: calls.append(("restore", target, selected)) or "restored",
        host_check=lambda: "checked",
        host_setup=lambda: calls.append(("host_setup",)) or "setup",
        host_update=lambda version: calls.append(("host_update", version)) or "updated",
    )


def _input(values):
    iterator = iter(values)

    def read(prompt):
        value = next(iterator)
        if isinstance(value, BaseException):
            raise value
        return value

    return read


def test_invalid_choice_reprompts_and_eof_exits(config):
    output = []

    code = interactive.run(
        config,
        _actions([]),
        input_fn=_input(["invalid", EOFError()]),
        output=output.append,
    )

    assert code == 0
    assert "Invalid choice" in output


def test_settings_reprompt_invalid_value_and_save_reset(tmp_path):
    root = Path(__file__).parents[2]
    config = load(
        root / "tests/fixtures/config/kv",
        paths=Paths(tmp_path / "etc", tmp_path / "state", tmp_path / "opt"),
    )
    target = config.select("app-dev-01/kv")
    output = []
    values, reset = interactive.settings(
        target,
        input_fn=_input(["", "reset", "invalid", "false", "", "", "bad", "512mb", "2", "yes"]),
        output=output.append,
    )

    assert values == {"http": False, "memory": "512mb", "threads": 2}
    assert reset == ("mode",)
    assert output.count("Invalid value") == 2


def test_database_navigation_can_repeat_without_recursion(config):
    calls = []
    output = []

    code = interactive.run(
        config,
        _actions(calls),
        input_fn=_input(["1", "1", "1", "0", "1", "2", "1", "0", "0"]),
        output=output.append,
    )

    assert code == 0
    assert calls == [
        ("info", "app-test-01/postgres"),
        ("info", "app-test-01/kv"),
    ]


def test_dragonfly_settings_are_context_aware_and_save_once(tmp_path):
    root = Path(__file__).parents[2]
    config = load(
        root / "tests/fixtures/config/kv",
        paths=Paths(tmp_path / "etc", tmp_path / "state", tmp_path / "opt"),
    )
    target = config.select("app-dev-01/kv")
    answers = ["", "", "", "", "", "512mb", "4", "yes"]

    values, reset = interactive.settings(
        target,
        input_fn=_input(answers),
        output=lambda value: None,
    )

    assert values == {"memory": "512mb", "threads": 4}
    assert reset == ()


def test_settings_reset_discard_and_eof_are_non_mutating(config):
    target = config.select("app-test-01/kv")
    values, reset = interactive.settings(
        target,
        input_fn=_input(["", "reset", "", "", "", "no"]),
        output=lambda value: None,
    )
    assert values == {} and reset == ()

    values, reset = interactive.settings(
        target,
        input_fn=_input([EOFError()]),
        output=lambda value: None,
    )
    assert values == {} and reset == ()


def test_database_list_shows_identity_engine_health_and_setting_labels(config):
    output = []

    interactive.run(
        config,
        _actions([]),
        input_fn=_input(["1", "0", "0"]),
        output=output.append,
    )

    assert "1. app-test-01/postgres (postgres; healthy)" in output
    assert "2. app-test-01/kv (redis; stopped)" in output

    prompts = []
    interactive.settings(
        config.select("app-test-01/kv"),
        input_fn=lambda prompt: prompts.append(prompt) or "",
        output=lambda value: None,
    )
    assert any("image [redis:7.2.5 (default)]" in prompt for prompt in prompts)
    assert any("mode [cache (custom)]" in prompt for prompt in prompts)


def test_guided_add_setup_and_update_call_actions(config):
    calls = []

    interactive.run(
        config,
        _actions(calls),
        input_fn=_input(
            [
                "1",
                "3",
                "queue-prod-01",
                "2",
                "2",
                "4",
                "2",
                "4",
                "3",
                "1.2.3",
                "0",
            ]
        ),
        output=lambda value: None,
    )

    assert calls == [
        ("add", "queue-prod-01", "kv", "redis"),
        ("host_setup",),
        ("host_update", "1.2.3"),
    ]


def test_guided_backup_test_and_restore_select_exact_displayed_backup(config):
    calls = []

    interactive.run(
        config,
        _actions(calls),
        input_fn=_input(["2", "1", "3", "2", "3", "1", "1", "0"]),
        output=lambda value: None,
    )

    assert calls == [
        ("backup_test", "app-test-01/postgres", "snapshot-2"),
        ("restore", "app-test-01/postgres", "snapshot-1"),
    ]


def test_guided_initial_setup_collects_required_values():
    values = interactive.setup(
        input_fn=_input(
            [
                "host-01",
                "storage.example.com",
                "/srv/databases",
                "ops@example.com",
                "testdns",
                "repo:postgres",
                "repo:kv",
                "/tmp/dns.env",
                "",
            ]
        ),
        output=lambda value: None,
    )

    assert values == {
        "host_id": "host-01",
        "domain": "storage.example.com",
        "data_root": "/srv/databases",
        "acme_email": "ops@example.com",
        "dns_provider": "testdns",
        "postgres_repo": "repo:postgres",
        "kv_repo": "repo:kv",
        "dns_env_file": "/tmp/dns.env",
    }


def test_added_database_is_selectable_in_same_menu_session(config):
    calls = []
    updated = with_role(
        config,
        "queue-prod-01",
        "kv",
        KV("dragonfly", DEFAULT_IMAGES["dragonfly"], http=HTTP()),
    )
    actions = replace(
        _actions(calls),
        health=lambda: {
            "app-test-01/postgres": "healthy",
            "app-test-01/kv": "healthy",
            "queue-prod-01/kv": "healthy",
        },
        add=lambda project, role, engine: ("added", updated),
    )

    interactive.run(
        config,
        actions,
        input_fn=_input(["1", "3", "queue-prod-01", "2", "1", "1", "3", "1", "0", "0"]),
        output=lambda value: None,
    )

    assert ("info", "queue-prod-01/kv") in calls

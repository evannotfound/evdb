from dataclasses import replace

import pytest

from evdb import cli, database, host, status, ui
from evdb.config import dump, load
from evdb.errors import Error


def _value(config):
    value = status.pending(config)
    value["host"].update(
        source={"config": str(config.paths.source), "valid": True},
        storage={"path": str(config.paths.state), "available": False},
        infrastructure={
            "network": True,
            "traefik": True,
            "acme": True,
            "listeners": {
                str(port): True
                for port in (*config.host.routing.postgres_ports, *config.host.routing.kv_ports)
            },
        },
        timer={"unit": "evdb-backup.timer", "loaded": True, "enabled": True, "active": True},
    )
    return value


def _save_ports(config, values):
    routing = replace(config.host.routing, **{key: tuple(value) for key, value in values.items()})
    updated = replace(config, host=replace(config.host, routing=routing))
    config.paths.source.write_text(dump(updated))
    return updated


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ([], {}),
        (["--yes"], {}),
        (
            ["--postgres-port", "15432", "--postgres-port", "5432"],
            {"postgres_ports": [15432, 5432]},
        ),
        (["--kv-port", "16379", "--kv-port", "6379"], {"kv_ports": [16379, 6379]}),
        (
            ["--postgres-port", "15432", "--kv-port", "16379"],
            {"postgres_ports": [15432], "kv_ports": [16379]},
        ),
    ],
)
def test_init_passes_replacement_lists_and_omits_unspecified_ports(
    config, monkeypatch, flags, expected
):
    seen = []
    monkeypatch.setattr(cli, "CONFIG_DIR", config.paths.config)
    monkeypatch.setattr(cli, "_host_access_allowed", lambda: True)
    monkeypatch.setattr(cli, "_tty", lambda: True)

    def initialize(source, values, *, output):
        seen.append((source, values))
        return {"host": {"id": config.host.id, "healthy": True}}

    monkeypatch.setattr(host, "initialize", initialize)
    result = cli.main(
        ["--config", str(config.paths.source), "init", *flags],
        input_fn=lambda prompt: pytest.fail(f"unexpected prompt: {prompt}"),
        output=lambda text: None,
    )

    assert result == 0
    assert seen == [(config.paths.source, expected)]


@pytest.mark.parametrize("flag", ["--postgres-port", "--kv-port"])
def test_init_port_flags_require_integers(flag):
    with pytest.raises(SystemExit) as exc:
        cli.parser().parse_args(["init", flag, "5432,15432"])
    assert exc.value.code == 2


@pytest.mark.parametrize(
    ("answers", "postgres_ports", "kv_ports"),
    [
        (["", "", "1"], [5432], [6379]),
        (["15432, 5432", "16379, 6379", "1"], [15432, 5432], [16379, 6379]),
        (
            ["15432", "16379", "2", "8", "5432, 15432", "6379, 16379", "1"],
            [5432, 15432],
            [6379, 16379],
        ),
    ],
)
def test_guided_init_reviews_and_edits_native_ports(
    config, monkeypatch, answers, postgres_ports, kv_ports
):
    monkeypatch.setattr(cli, "Paths", lambda **kwargs: config.paths)
    values = {
        "host_id": config.host.id,
        "domain": config.host.domain,
        "data_roots": [str(root) for root in config.host.data_roots],
        "acme_email": config.host.routing.acme_email,
        "dns_provider": config.host.routing.dns_provider,
        "dns": dict(config.secrets.dns),
        "repository": config.host.backup.repository,
        "rclone_config": str(config.host.backup.rclone_config),
        "restic_password": "",
    }
    responses = iter(answers)
    output = []

    result = cli._init_values(
        values,
        lambda prompt: next(responses),
        output=output.append,
        source=config.paths.source,
    )

    assert result["postgres_ports"] == postgres_ports
    assert result["kv_ports"] == kv_ports
    text = "\n".join(output)
    assert f"Postgres Ports: {', '.join(map(str, postgres_ports))}" in text
    assert f"Kv Ports: {', '.join(map(str, kv_ports))}" in text
    assert f"(preferred: {postgres_ports[0]})" in text


@pytest.mark.parametrize("invalid", ["abc", "5432,", "0", "65536", "80", "443", "5432,5432"])
def test_native_port_input_retries_invalid_lists(invalid):
    responses = iter([invalid, "15432, 5432"])
    output = []

    result = ui.ask_ports(lambda prompt: next(responses), output.append, "Postgres ports", (5432,))

    assert result == (15432, 5432)
    assert len(output) == 2  # Help followed by the validation error.


@pytest.mark.parametrize(
    "answers",
    [
        ["2", "15432,5432", "16379,6379", "n"],
        ["2", "", "", "y"],
        ["2", "5432", "6379", "y"],
        ["2"],
        ["2", "15432"],
    ],
)
def test_native_port_cancel_and_unchanged_save_do_not_mutate(config, monkeypatch, answers):
    before = config.paths.source.read_bytes()
    responses = iter(answers)

    def input_fn(prompt):
        try:
            return next(responses)
        except StopIteration as exc:
            raise EOFError from exc

    monkeypatch.setattr(host, "initialize", lambda *a, **kw: pytest.fail("unexpected apply"))
    session = ui._StatusSession(config)
    monkeypatch.setattr(session, "invalidate", lambda *a: pytest.fail("unexpected invalidation"))
    try:
        result = ui._host(config, _value(config), input_fn, lambda text: None, session=session)
    finally:
        session.close()

    assert result is config
    assert config.paths.source.read_bytes() == before


def test_native_port_editor_validates_overlap_before_apply(config, monkeypatch):
    before = config.paths.source.read_bytes()
    responses = iter(["2", "15432", "15432"])
    monkeypatch.setattr(host, "initialize", lambda *a, **kw: pytest.fail("unexpected apply"))

    with pytest.raises(Error, match="overlap|both|shared"):
        ui._host(config, _value(config), lambda prompt: next(responses), lambda text: None)

    assert config.paths.source.read_bytes() == before


def test_native_port_save_uses_initialize_and_invalidates_reloaded_session(config, monkeypatch):
    calls = []
    output = []
    responses = iter(["2", "15432,5432", "16379,6379", "y"])
    session = ui._StatusSession(config)

    def initialize(source, values, *, paths, output):
        assert "Changing bindings will briefly drop native connections." in messages
        assert session.snapshot()[0] == 1
        assert session.value["pending"] is True
        calls.append((source, values, paths))
        return _value(_save_ports(config, values))

    messages = output
    monkeypatch.setattr(host, "initialize", initialize)
    try:
        updated = ui._host(
            config, _value(config), lambda prompt: next(responses), output.append, session=session
        )
        assert session.config == updated
        assert session.snapshot()[0] == 2
        assert session.value["pending"] is True
    finally:
        session.close()

    assert calls == [
        (
            config.paths.source,
            {"postgres_ports": [15432, 5432], "kv_ports": [16379, 6379]},
            config.paths,
        )
    ]
    assert updated == load(config.paths.source, paths=config.paths)
    assert updated.host.routing.postgres_ports == (15432, 5432)
    assert updated.host.routing.kv_ports == (16379, 6379)
    assert "Native ports saved" in output


def test_guided_root_reloads_host_and_connection_after_save(config, monkeypatch):
    output = []
    host_choice = str(len(config.databases) + 2)
    responses = iter(
        [
            host_choice,
            "2",
            "15432,5432",
            "16379,6379",
            "y",
            host_choice,
            "0",
            "1",
            "2",
            "0",
            "0",
        ]
    )
    collected = []

    def collect(current, *args, **kwargs):
        collected.append(current)
        return _value(current)

    monkeypatch.setattr(status, "collect", collect)
    monkeypatch.setattr(
        host,
        "initialize",
        lambda source, values, **kwargs: _value(_save_ports(config, values)),
    )
    monkeypatch.setattr(
        database, "observe", lambda *a, **kw: {"health": "healthy", "running": True}
    )

    assert ui.run(config, input_fn=lambda prompt: next(responses), output=output.append) == 0

    assert collected[0] is config
    assert all(item.host.routing.postgres_ports == (15432, 5432) for item in collected[1:])
    text = "\n".join(output)
    assert "1. Restart traffic" in text and "2. Native ports" in text
    assert "Postgres Ports: 15432, 5432 (preferred: 15432)" in text
    assert "Kv Ports: 16379, 6379 (preferred: 16379)" in text
    assert ":15432/" in text

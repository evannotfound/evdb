import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from evanovation_db import secrets
from evanovation_db.config import Config, load
from evanovation_db.errors import ConfigError
from evanovation_db.run import Result

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests/fixtures/config/minimal"


class FakeOp:
    def __init__(self, items=()):
        self.items = {item["id"]: item for item in items}
        self.calls = []
        self.writes = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if args[1:3] == ["whoami", "--format"]:
            return Result(tuple(args), 0, json.dumps({"type": "USER"}), "")
        if args[1:3] == ["item", "list"]:
            data = [{"id": item["id"], "title": item["title"]} for item in self.items.values()]
            return Result(tuple(args), 0, json.dumps(data), "")
        if args[1:3] == ["item", "get"]:
            return Result(tuple(args), 0, json.dumps(self.items[args[3]]), "")
        if args[1:3] == ["item", "create"]:
            item = json.loads(kwargs["input"])
            item["id"] = f"item-{len(self.items) + 1}"
            self.items[item["id"]] = item
            self.writes.append(("create", item))
            return Result(tuple(args), 0, json.dumps(item), "")
        if args[1:3] == ["item", "edit"]:
            item = json.loads(kwargs["input"])
            item["id"] = args[3]
            self.items[item["id"]] = item
            self.writes.append(("edit", item))
            return Result(tuple(args), 0, json.dumps(item), "")
        if args[1] == "read":
            _, title, field = args[2].removeprefix("op://").split("/", 2)
            matches = [item for item in self.items.values() if item["title"] == title]
            fields = (
                [
                    item["value"]
                    for item in matches[0].get("fields", [])
                    if item.get("label") == field and item.get("value")
                ]
                if len(matches) == 1
                else []
            )
            if len(fields) == 1:
                return Result(tuple(args), 0, fields[0] + "\n", "")
            return Result(tuple(args), 1, "", "not found")
        raise AssertionError(args)


def test_item_conventions_match_host_and_database_names():
    config = load(FIXTURE)

    assert secrets.system_item(config.host) == "evanovation-db"
    assert secrets.Op.for_host(config.host).vault == "Test"
    assert secrets.item_name(config.select("example-prod-01")) == "example-prod-01-postgres"
    assert secrets.item_name(config.select("cache-dev-01")) == "cache-dev-01-kv"


def test_connect_only_write_fails_before_op_call(monkeypatch):
    config = load(FIXTURE)
    fake = FakeOp()
    monkeypatch.setattr(secrets, "run", fake)
    client = secrets.Op(
        "Test",
        env={"OP_CONNECT_HOST": "https://connect", "OP_CONNECT_TOKEN": "not-a-real-token"},
    )

    with pytest.raises(ConfigError, match="cannot write items"):
        client.ensure(config.select("cache-dev-01"))

    assert fake.calls == []


def test_missing_item_is_created_once_with_concealed_stdin_fields(monkeypatch):
    config = load(FIXTURE)
    fake = FakeOp()
    generated = iter(["password:with/punctuation", "http-token-value"])
    monkeypatch.setattr(secrets, "run", fake)
    client = secrets.Op("Test", env={}, generate=lambda: next(generated))
    instance = config.select("cache-dev-01")

    client.ensure(instance)
    client.ensure(instance)

    assert [kind for kind, _ in fake.writes] == ["create"]
    created = fake.writes[0][1]
    assert created["title"] == "cache-dev-01-kv"
    assert {(field["label"], field["type"]) for field in created["fields"]} == {
        ("password", "CONCEALED"),
        ("http-token", "CONCEALED"),
    }
    write_args, write_options = next(call for call in fake.calls if call[0][2] == "create")
    assert write_args == [
        "op",
        "item",
        "create",
        "-",
        "--vault",
        "Test",
        "--format",
        "json",
    ]
    assert "password:with/punctuation" not in " ".join(write_args)
    assert "http-token-value" not in " ".join(write_args)
    assert set(write_options["secrets"]) == {
        "password:with/punctuation",
        "http-token-value",
    }


def test_existing_password_is_not_rotated_when_http_token_is_added(monkeypatch):
    config = load(FIXTURE)
    old_password = "existing-password"
    fake = FakeOp(
        [
            {
                "id": "item-1",
                "title": "cache-dev-01-kv",
                "category": "PASSWORD",
                "fields": [
                    {
                        "id": "password",
                        "label": "password",
                        "type": "CONCEALED",
                        "value": old_password,
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr(secrets, "run", fake)
    client = secrets.Op("Test", env={}, generate=lambda: "new-http-token")

    client.ensure(config.select("cache-dev-01"))

    assert [kind for kind, _ in fake.writes] == ["edit"]
    fields = {field["label"]: field["value"] for field in fake.writes[0][1]["fields"]}
    assert fields == {"password": old_password, "http-token": "new-http-token"}
    edit_args, edit_options = next(call for call in fake.calls if call[0][2] == "edit")
    assert old_password not in " ".join(edit_args)
    assert "new-http-token" not in " ".join(edit_args)
    assert set(edit_options["secrets"]) == {old_password, "new-http-token"}


def test_empty_concealed_field_is_filled_without_duplicate(monkeypatch):
    config = load(FIXTURE)
    fake = FakeOp(
        [
            {
                "id": "item-1",
                "title": "example-prod-01-postgres",
                "category": "PASSWORD",
                "fields": [
                    {
                        "id": "password",
                        "label": "password",
                        "type": "CONCEALED",
                        "value": "",
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr(secrets, "run", fake)

    secrets.Op("Test", env={}, generate=lambda: "filled-password").ensure(
        config.select("example-prod-01")
    )

    fields = fake.writes[0][1]["fields"]
    assert [(field["label"], field["value"]) for field in fields] == [
        ("password", "filled-password")
    ]


def test_plain_text_credential_field_is_rejected_before_write(monkeypatch):
    config = load(FIXTURE)
    fake = FakeOp(
        [
            {
                "id": "item-1",
                "title": "example-prod-01-postgres",
                "category": "PASSWORD",
                "fields": [
                    {"id": "password", "label": "password", "type": "STRING", "value": "bad"}
                ],
            }
        ]
    )
    monkeypatch.setattr(secrets, "run", fake)

    with pytest.raises(ConfigError, match="must be concealed"):
        secrets.Op("Test", env={}).ensure(config.select("example-prod-01"))

    assert fake.writes == []


def test_credentials_use_connect_compatible_op_read_without_item_listing(monkeypatch):
    config = load(FIXTURE)
    fake = FakeOp(
        [
            {
                "id": "item-1",
                "title": "cache-dev-01-kv",
                "fields": [
                    {"label": "password", "type": "CONCEALED", "value": "local-password"},
                    {"label": "http-token", "type": "CONCEALED", "value": "local-token"},
                ],
            }
        ]
    )
    monkeypatch.setattr(secrets, "run", fake)

    result = secrets.Op("Test", env={}).credentials(config.select("cache-dev-01"))

    assert result.password == "local-password"
    assert result.http_token == "local-token"
    assert repr(result) == "Credentials(<redacted>)"
    assert [args[:2] for args, _ in fake.calls] == [["op", "read"], ["op", "read"]]


def test_missing_credential_and_op_failure_do_not_reveal_values(monkeypatch):
    config = load(FIXTURE)
    fake = FakeOp(
        [
            {
                "id": "item-1",
                "title": "cache-dev-01-kv",
                "fields": [{"label": "password", "type": "CONCEALED", "value": "do-not-reveal"}],
            }
        ]
    )
    monkeypatch.setattr(secrets, "run", fake)

    with pytest.raises(ConfigError) as caught:
        secrets.Op("Test", env={}).credentials(config.select("cache-dev-01"))

    assert "http-token" in str(caught.value)
    assert "do-not-reveal" not in str(caught.value)

    def failed(args, **kwargs):
        if args[1] == "whoami":
            return Result(tuple(args), 0, "{}", "")
        return Result(tuple(args), 1, "do-not-reveal", "do-not-reveal")

    monkeypatch.setattr(secrets, "run", failed)
    with pytest.raises(ConfigError) as caught:
        secrets.Op("Test", env={}).credentials(config.select("cache-dev-01"))
    assert "do-not-reveal" not in str(caught.value)


def test_deployment_files_use_protected_paths_and_transfer_stdin_only(tmp_path):
    source = load(FIXTURE)
    host = replace(source.host, config_dir=tmp_path / "etc", state_dir=tmp_path / "state")
    config = Config(host, source.instances)

    class Client:
        def fields(self, title, names):
            assert title == "evanovation-db"
            assert names == secrets.SYSTEM_FIELDS
            return {"restic-password": "restic-secret", "rclone-config": "rclone-secret"}

        def credentials(self, instance):
            token = f"token-{instance.id}" if instance.http and instance.http["enabled"] else None
            return secrets.Credentials(f"password-{instance.id}", token)

    files = secrets.deployment_files(config, Client())
    values = [item.content for item in files]
    assert files[0].path == tmp_path / "etc/secrets/restic_password"
    assert files[1].path == tmp_path / "state/rclone/rclone.conf"
    assert all("<redacted>" in repr(item) for item in files)

    result = secrets.transfer(
        [sys.executable, "-c", "import sys; print(sys.stdin.read())"],
        files,
        timeout=10,
    )

    assert all(value not in " ".join(result.args) for value in values)
    assert all(value not in result.out for value in values)
    assert "<redacted>" in result.out
    assert not (tmp_path / "state").exists()

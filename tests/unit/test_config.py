import json
from dataclasses import replace
from pathlib import Path

import pytest

from evanovation_db.config import (
    DEFAULT_HTTP_END,
    DEFAULT_HTTP_START,
    DEFAULT_RETENTION,
    DEFAULT_TIMEOUTS,
    ConfigError,
    ImageState,
    MachineState,
    RoleState,
    as_dict,
    dump,
    load,
    load_state,
    replace_role,
    resolve_state,
    state_dict,
    with_role,
    write_config,
    write_state,
)
from evanovation_db.images import image_major, locked_image, validate_source

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/config"
DIGEST = "sha256:" + "a" * 64


def test_project_first_fixtures_cover_each_role_shape():
    postgres = load(FIXTURES / "postgres")
    kv = load(FIXTURES / "kv")
    combined = load(FIXTURES / "combined")

    assert [item.identity for item in postgres.databases] == ["app-prod-01/postgres"]
    assert [item.identity for item in kv.databases] == ["app-dev-01/kv"]
    assert [item.identity for item in combined.databases] == [
        "app-test-01/postgres",
        "app-test-01/kv",
    ]
    assert combined.select("app-test-01/postgres").compose_project == "evdb-app-test-01-postgres"
    assert combined.select("app-test-01/kv").compose_project == "evdb-app-test-01-kv"


def test_defaults_are_typed_and_concise():
    config = load(FIXTURES / "kv")
    database = config.select("app-dev-01/kv")

    assert config.host.backup.retention == DEFAULT_RETENTION
    assert config.host.timeouts == DEFAULT_TIMEOUTS
    assert (config.host.http_port_start, config.host.http_port_end) == (
        DEFAULT_HTTP_START,
        DEFAULT_HTTP_END,
    )
    assert database.engine == "dragonfly"
    assert database.settings.mode == "durable"
    assert database.settings.memory == "256mb"
    assert database.settings.threads == 1
    assert database.settings.http.enabled
    assert database.settings.http.connections == 20


def test_select_requires_role_when_project_has_both():
    config = load(FIXTURES / "combined")

    with pytest.raises(ConfigError) as caught:
        config.select("app-test-01")

    message = str(caught.value)
    assert "app-test-01/postgres" in message
    assert "app-test-01/kv" in message


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("old-schema.yml", "old source schema"),
        ("bad-suffix.yml", "must end in"),
        ("engine-setting.yml", "require dragonfly"),
        ("secret.yml", "must not contain secrets"),
        ("unsafe-path.yml", "safe absolute path"),
        ("major.yml", "positive postgres major"),
        ("duplicate-role.yml", "duplicate YAML key"),
    ],
)
def test_invalid_fixtures_fail_before_side_effects(name, message):
    with pytest.raises(ConfigError, match=message):
        load(FIXTURES / "invalid" / name)


@pytest.mark.parametrize("name", ["postgres.yml", "kv.yml", "host.lock.json"])
def test_old_side_files_are_rejected(tmp_path, name):
    (tmp_path / "host.yml").write_text((FIXTURES / "postgres/host.yml").read_text())
    (tmp_path / name).write_text("{}\n")

    with pytest.raises(ConfigError, match="old source layout"):
        load(tmp_path)


@pytest.mark.parametrize(
    "image",
    ["postgres", "registry.example.com:5000/postgres", "postgres:latest", " postgres:16"],
)
def test_images_reject_unversioned_latest_and_invalid_sources(image):
    with pytest.raises(ConfigError):
        validate_source(image)


def test_images_accept_version_tags_and_digests():
    digest_image = f"example/http@{DIGEST}"

    assert validate_source("registry.example.com:5000/postgres:16.9")
    assert validate_source(digest_image)
    assert locked_image("postgres:16", DIGEST) == f"postgres:16@{DIGEST}"
    assert image_major("dragonfly:v1.34.1") == 1


def test_paths_are_project_first_and_fully_injectable(config, paths):
    database = config.select("app-test-01/postgres")

    assert config.paths.source == paths.config / "host.yml"
    assert database.compose == paths.config / "projects/app-test-01/postgres/compose.yaml"
    assert config.paths.role_secrets(database.project, database.role) == (
        paths.config / "secrets/app-test-01/postgres"
    )
    assert database.data == config.host.data_root / "app-test-01/postgres/data"
    assert config.paths.role_backups(database.project, database.role) == (
        paths.state / "backups/app-test-01/postgres"
    )


def test_round_trip_is_deterministic(tmp_path):
    original = load(FIXTURES / "combined")
    source = tmp_path / "host.yml"
    source.write_text(dump(original))

    loaded = load(source)

    assert as_dict(loaded) == as_dict(original)
    assert dump(loaded) == dump(original)


def test_role_add_defaults_are_persisted_explicitly():
    config = load(FIXTURES / "postgres")
    from evanovation_db.config import HTTP, KV

    updated = with_role(
        config,
        "queue-prod-01",
        "kv",
        KV("dragonfly", "docker.dragonflydb.io/dragonflydb/dragonfly:v1.34.1", http=HTTP()),
    )
    data = as_dict(updated)["projects"]["queue-prod-01"]["kv"]

    assert data["engine"] == "dragonfly"
    assert data["image"].endswith(":v1.34.1")
    assert data["http"]["enabled"] is True
    assert data["http"]["image"].startswith("hiett/serverless-redis-http@sha256:")


def test_replace_role_changes_only_selected_database():
    config = load(FIXTURES / "combined")
    database = config.select("app-test-01/kv")
    changed = replace(database.settings, mode="durable")

    updated = replace_role(config, database, changed)

    assert updated.select(database.identity).settings.mode == "durable"
    assert (
        updated.select("app-test-01/postgres").settings
        == config.select("app-test-01/postgres").settings
    )


def test_machine_state_round_trip_and_stable_port(config):
    role = RoleState(
        "redis",
        {"primary": ImageState("redis:7.2.5", DIGEST, 7)},
        http_port=14001,
        compose_hash="abc",
        installed=True,
    )
    state = MachineState(1, config.host.id, {}, {"app-test-01/kv": role}, "1.0.0")

    write_state(config, state)
    loaded = load_state(config)

    assert loaded == state
    assert loaded.roles["app-test-01/kv"].http_port == 14001
    assert json.loads(config.paths.machine_state.read_text()) == state_dict(state)
    assert config.paths.machine_state.stat().st_mode & 0o777 == 0o600


def test_state_resolution_preserves_ports_images_and_orphans(config):
    calls = []

    def resolver(source):
        calls.append(source)
        return DIGEST

    initial = resolve_state(config, resolver=resolver)
    reordered = replace(config, projects=tuple(reversed(config.projects)))
    stable = resolve_state(reordered, initial, resolver=resolver)

    assert stable == initial
    assert stable.roles["app-test-01/kv"].http_port == config.host.http_port_start
    assert calls

    orphan = RoleState("redis", {}, installed=True)
    current = replace(initial, roles={**initial.roles, "old-prod-01/kv": orphan})
    preserved = resolve_state(config, current, resolver=resolver)
    assert preserved.roles["old-prod-01/kv"] is orphan


def test_state_resolution_never_reallocates_a_surviving_port(config):
    initial = resolve_state(config, resolver=lambda source: DIGEST)
    narrower = replace(
        config,
        host=replace(config.host, http_port_start=config.host.http_port_start + 1),
    )

    with pytest.raises(ConfigError, match="instead of reallocating"):
        resolve_state(narrower, initial, resolver=lambda source: DIGEST)


def test_disabled_http_port_is_preserved_and_reserved_for_reenable(config):
    initial = resolve_state(config, resolver=lambda source: DIGEST)
    target = config.select("app-test-01/kv")
    disabled_settings = replace(
        target.settings,
        http=replace(target.settings.http, enabled=False),
    )
    disabled = replace_role(config, target, disabled_settings)
    disabled_state = resolve_state(disabled, initial, resolver=lambda source: DIGEST)
    added = with_role(
        disabled,
        "other-test-01",
        "kv",
        replace(target.settings, mode="durable"),
    )
    final = resolve_state(added, disabled_state, resolver=lambda source: DIGEST)

    assert disabled_state.roles[target.identity].http_port == config.host.http_port_start
    assert final.roles[target.identity].http_port == config.host.http_port_start
    assert final.roles["other-test-01/kv"].http_port == config.host.http_port_start + 1


def test_state_rejects_secrets(config):
    path = config.paths.machine_state
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "host": config.host.id,
                "images": {},
                "roles": {},
                "password": "do-not-print",
            }
        )
    )

    with pytest.raises(ConfigError) as caught:
        load_state(config)
    assert "do-not-print" not in str(caught.value)


def test_atomic_config_write_keeps_one_previous_and_activity(config):
    config.paths.source.parent.mkdir(parents=True)
    config.paths.source.write_text("old\n")
    database = config.select("app-test-01/kv")

    write_config(config, command="database configure", database=database, changed=("mode",))

    assert config.paths.previous.read_text() == "old\n"
    assert config.paths.previous.stat().st_mode & 0o777 == 0o640
    assert config.paths.source.stat().st_mode & 0o777 == 0o640
    assert load(config.paths.source, paths=config.paths) == config
    activity = json.loads(config.paths.activity.read_text())
    assert activity["project"] == "app-test-01"
    assert activity["role"] == "kv"
    assert activity["changed"] == ["mode"]
    assert "password" not in config.paths.activity.read_text().lower()


def test_http_port_range_rejects_invalid_order(tmp_path):
    text = (
        (FIXTURES / "kv/host.yml")
        .read_text()
        .replace("projects:", "  http_ports:\n    start: 14001\n    end: 14000\nprojects:")
    )
    path = tmp_path / "host.yml"
    path.write_text(text)

    with pytest.raises(ConfigError, match="ordered unprivileged range"):
        load(path)


def test_config_source_contains_no_runtime_or_secret_fields():
    data = as_dict(load(FIXTURES / "combined"))
    text = json.dumps(data).lower()

    assert "digest" not in text
    assert "http_port" in text  # only the allocation range is human-owned
    assert not any(word in text for word in ("password", "token", "op://", "current", "target"))

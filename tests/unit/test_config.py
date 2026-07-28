import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from evdb import lock as lock_module
from evdb.config import (
    DEFAULT_HTTP_END,
    DEFAULT_HTTP_START,
    DEFAULT_RETENTION,
    DEFAULT_TIMEOUTS,
    STATE_VERSION,
    ConfigError,
    ImageState,
    MachineState,
    Project,
    RoleState,
    append_activity,
    as_dict,
    dump,
    load,
    load_state,
    replace_role,
    require_valid,
    resolve_state,
    state_dict,
    with_role,
    write_state,
)
from evdb.images import image_major, locked_image, validate_source

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/config"
DIGEST = "sha256:" + "a" * 64


def test_project_first_fixtures_cover_each_role_shape():
    postgres = load(FIXTURES / "postgres/host.yml")
    kv = load(FIXTURES / "kv/host.yml")
    combined = load(FIXTURES / "combined/host.yml")

    assert [item.identity for item in postgres.databases] == ["app-prod-01/postgres"]
    assert [item.identity for item in kv.databases] == ["app-dev-01/kv"]
    assert [item.identity for item in combined.databases] == [
        "app-test-01/postgres",
        "app-test-01/kv",
    ]
    combined_postgres = combined.select("app-test-01/postgres")
    combined_kv = combined.select("app-test-01/kv")
    assert combined_postgres.compose_project == "evdb-app-test-01-postgres"
    assert combined_kv.compose_project == "evdb-app-test-01-kv"
    assert combined_postgres.domain == "app-test-01.test-01.storage.example.com"
    assert combined_kv.domain == combined_postgres.domain
    assert combined_kv.settings.http.domain == combined_kv.domain


def test_defaults_are_typed_and_concise():
    config = load(FIXTURES / "kv/host.yml")
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
    assert database.settings.http.domain == "app-dev-01.test-01.storage.example.com"


def test_select_requires_role_when_project_has_both():
    config = load(FIXTURES / "combined/host.yml")

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
def test_invalid_fixtures_fail_before_side_effects(tmp_path, name, message):
    path = tmp_path / "host.yml"
    path.write_text((FIXTURES / "invalid" / name).read_text())

    with pytest.raises(ConfigError, match=message):
        load(path)


@pytest.mark.parametrize("name", ["postgres.yml", "kv.yml", "host.lock.json"])
def test_old_side_files_are_rejected(tmp_path, name):
    (tmp_path / "host.yml").write_text((FIXTURES / "postgres/host.yml").read_text())
    (tmp_path / name).write_text("{}\n")

    with pytest.raises(ConfigError, match="old source layout"):
        load(tmp_path / "host.yml")


def test_source_load_requires_exact_host_yml(tmp_path):
    path = tmp_path / "host.yaml"
    path.write_text((FIXTURES / "postgres/host.yml").read_text())

    with pytest.raises(ConfigError, match="host.yml"):
        load(path)


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


def test_config_rejects_repository_credentials_and_overlong_host_label(config):
    credentialed = replace(
        config,
        host=replace(
            config.host,
            backup=replace(
                config.host.backup,
                repos={**config.host.backup.repos, "kv": "rest:https://user@repo.example/db"},
            ),
        ),
    )
    with pytest.raises(ConfigError, match="must not contain credentials"):
        require_valid(credentialed)

    with pytest.raises(ConfigError, match="at most 60"):
        require_valid(replace(config, host=replace(config.host, id="a" * 61)))


def test_constructed_config_uses_complete_validation(config):
    with pytest.raises(ConfigError, match="valid email"):
        require_valid(
            replace(
                config,
                host=replace(config.host, routing=replace(config.host.routing, acme_email="bad")),
            )
        )

    with pytest.raises(ConfigError, match="non-empty postgres and kv"):
        require_valid(
            replace(
                config,
                host=replace(
                    config.host,
                    backup=replace(config.host.backup, repos={"postgres": "", "kv": "/tmp/kv"}),
                ),
            )
        )

    with pytest.raises(ConfigError, match="retention.daily"):
        require_valid(
            replace(
                config,
                host=replace(
                    config.host,
                    backup=replace(
                        config.host.backup,
                        retention={**DEFAULT_RETENTION, "daily": 0},
                    ),
                ),
            )
        )

    with pytest.raises(ConfigError, match="postgres must be Postgres"):
        require_valid(replace(config, projects=(Project("other-prod-01", postgres="bad"),)))


def test_http_domains_must_be_unique_between_kv_roles(config):
    target = config.select("app-test-01/kv")

    with pytest.raises(ConfigError, match="HTTP domain collides"):
        with_role(config, "other-test-01", "kv", target.settings)


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
    original = load(FIXTURES / "combined/host.yml")
    source = tmp_path / "host.yml"
    source.write_text(dump(original))

    loaded = load(source)

    assert as_dict(loaded) == as_dict(original)
    assert dump(loaded) == dump(original)


def test_role_add_defaults_are_persisted_explicitly():
    config = load(FIXTURES / "postgres/host.yml")
    from evdb.config import HTTP, KV

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
    assert data["http"]["domain"] == "queue-prod-01.test-01.storage.example.com"


def test_explicit_http_domain_override_is_preserved():
    config = load(FIXTURES / "postgres/host.yml")
    from evdb.config import HTTP, KV

    updated = with_role(
        config,
        "queue-prod-01",
        "kv",
        KV("redis", "redis:7.2.5", http=HTTP(domain="queue.storage.example.com")),
    )

    assert updated.select("queue-prod-01/kv").settings.http.domain == "queue.storage.example.com"


def test_replace_role_changes_only_selected_database():
    config = load(FIXTURES / "combined/host.yml")
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
    state = MachineState(STATE_VERSION, config.host.id, {}, {"app-test-01/kv": role}, "1.0.0")

    write_state(config, state)
    loaded = load_state(config)

    assert loaded == state
    assert loaded.roles["app-test-01/kv"].http_port == 14001
    assert json.loads(config.paths.machine_state.read_text()) == state_dict(state)
    assert json.loads(config.paths.machine_state.read_text())["version"] == 1
    assert (
        json.loads(config.paths.machine_state.read_text())["roles"]["app-test-01/kv"]["images"][
            "primary"
        ]["major"]
        == 7
    )
    assert config.paths.machine_state.stat().st_mode & 0o777 == 0o600


def test_activity_history_is_bounded(config):
    path = config.paths.activity
    path.parent.mkdir(parents=True)
    path.write_text("{}\n" * 1001)

    append_activity(config, command="test", result="success")

    lines = path.read_text().splitlines()
    assert len(lines) == 1000
    assert json.loads(lines[-1])["command"] == "test"
    assert path.stat().st_mode & 0o777 == 0o600
    assert (config.paths.locks / "activity.lock").is_file()


@pytest.mark.parametrize("failure", [OSError("disk unavailable"), KeyboardInterrupt()])
def test_activity_write_failure_is_logged_without_failing_completed_operation(
    config, monkeypatch, capsys, failure
):
    @contextmanager
    def unavailable(*args, **kwargs):
        raise failure
        yield

    monkeypatch.setattr(lock_module, "lock", unavailable)

    append_activity(config, command="database configure", result="success")

    event = json.loads(capsys.readouterr().err)
    assert event["event"] == "activity_operation"
    assert event["result"] == "failed"
    assert event["command"] == "database configure"


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


def test_state_resolution_rejects_duplicate_ports_and_skips_occupied_ports(config):
    initial = resolve_state(
        config,
        resolver=lambda source: DIGEST,
        port_available=lambda port: port != config.host.http_port_start,
    )
    target = config.select("app-test-01/kv")
    assert initial.roles[target.identity].http_port == config.host.http_port_start + 1

    duplicate = replace(
        initial,
        roles={
            **initial.roles,
            "other-test-01/kv": replace(initial.roles[target.identity], installed=False),
        },
    )
    with pytest.raises(ConfigError, match="assigned to both"):
        resolve_state(config, duplicate, resolver=lambda source: DIGEST)


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


def test_machine_state_requires_current_writer_fields(config):
    path = config.paths.machine_state
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": STATE_VERSION,
                "host": config.host.id,
                "images": {},
                "roles": {},
            }
        )
    )

    with pytest.raises(ConfigError, match="tool_version is required"):
        load_state(config)


def test_machine_state_requires_released_v1_image_shape(config):
    path = config.paths.machine_state
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": STATE_VERSION,
                "host": config.host.id,
                "tool_version": "1.0.0",
                "images": {"traefik": {"source": "traefik:v3.7.8", "digest": DIGEST}},
                "roles": {},
            }
        )
    )

    with pytest.raises(ConfigError, match="machine state.images.traefik.major is required"):
        load_state(config)


def test_machine_state_rejects_future_versions(config):
    path = config.paths.machine_state
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": STATE_VERSION + 1,
                "host": config.host.id,
                "tool_version": "1.0.0",
                "images": {},
                "roles": {},
            }
        )
    )

    with pytest.raises(ConfigError, match="unsupported machine state version"):
        load_state(config)


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
    data = as_dict(load(FIXTURES / "combined/host.yml"))
    text = json.dumps(data).lower()

    assert "digest" not in text
    assert "http_port" in text  # only the allocation range is human-owned
    assert not any(word in text for word in ("password", "token", "op://", "current", "target"))


def test_secret_words_in_project_ids_are_allowed(config, tmp_path):
    settings = config.select("app-test-01/postgres").settings
    updated = with_role(config, "token-prod-01", "postgres", settings)
    path = tmp_path / "host.yml"
    path.write_text(dump(updated))

    assert load(path).select("token-prod-01/postgres").project == "token-prod-01"

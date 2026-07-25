import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from evanovation_db import config as config_module
from evanovation_db.config import (
    BACKUP_DIR,
    CONFIG_DIR,
    DEFAULT_HTTP_END,
    DEFAULT_HTTP_START,
    DEFAULT_RETENTION,
    DEFAULT_TIMEOUTS,
    LOCK_DIR,
    STATE_DIR,
    ConfigError,
    DatabaseSource,
    ImageLock,
    load,
    load_lock,
    load_source,
    normalize,
    render,
    resolve_image_digest,
    resolve_lock,
    write_lock,
)
from evanovation_db.run import Result

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/config"
DIGEST = "sha256:" + "a" * 64


def test_minimal_source_applies_all_defaults():
    config = load(FIXTURES / "minimal")

    assert config.host.config_dir == CONFIG_DIR
    assert config.host.state_dir == STATE_DIR
    assert config.host.backup_dir == BACKUP_DIR
    assert config.host.lock_dir == LOCK_DIR
    assert config.host.retention == DEFAULT_RETENTION
    assert config.host.min_free_gb == 5
    assert config.host.backup_max_age_hours == 26
    assert config.host.restore_max_age_days == 30
    assert config.host.timeouts == DEFAULT_TIMEOUTS
    assert (config.host.http_port_start, config.host.http_port_end) == (
        DEFAULT_HTTP_START,
        DEFAULT_HTTP_END,
    )
    assert all("@sha256:" in image for image in config.host.images.values())

    postgres = config.select("postgres/example-prod-01")
    assert postgres.env == "prod"
    assert postgres.port == 5432
    assert postgres.container == "example-prod-01-postgres-1"
    assert postgres.project == "example-prod-01"
    assert postgres.data == Path("/srv/databases/postgres/example-prod-01/data")
    assert postgres.domain == "example-prod-01.postgres-test-01.storage.example.com"
    assert postgres.durable and postgres.backup == {"enabled": True}
    assert postgres.settings == {
        "user": "default",
        "database": "postgres",
        "pgbouncer": True,
        "max_clients": 100,
        "pool_size": 20,
        "reserve_size": 5,
    }
    assert postgres.secrets == {"password": "op://Test/example-prod-01-postgres/password"}

    redis = config.select("redis/cache-dev-01")
    assert redis.env == "dev"
    assert redis.port == 6379
    assert redis.settings == {}
    assert redis.http == {
        "enabled": True,
        "port": 13379,
        "domain": "cache-dev-01.kv-test-01.storage.example.com",
        "image": config.host.images["http"],
        "max_connections": 20,
        "token": "op://Test/cache-dev-01-kv/http-token",
    }

    dragonfly = config.select("dragonfly/queue-test-01")
    assert dragonfly.env == "test"
    assert dragonfly.settings == {"threads": 1, "maxmemory": "256mb", "cache": False}
    assert dragonfly.durable
    assert dragonfly.http["port"] == 13380


def test_supported_overrides_change_only_requested_behavior():
    config = load(FIXTURES / "overrides")

    assert config.host.retention == {**DEFAULT_RETENTION, "daily": 3}
    assert config.host.min_free_gb == 10
    assert config.host.backup_max_age_hours == 30
    assert config.host.restore_max_age_days == 14
    assert (config.host.http_port_start, config.host.http_port_end) == (14000, 14009)

    postgres = config.select("pooled-prod-01")
    assert postgres.settings == {
        "user": "default",
        "database": "postgres",
        "pgbouncer": False,
        "max_clients": 500,
        "pool_size": 40,
        "reserve_size": 8,
    }
    assert postgres.resources == {"database": "unlimited"}

    dragonfly = config.select("transient-prod-01")
    assert dragonfly.settings == {"threads": 8, "maxmemory": "2gb", "cache": True}
    assert not dragonfly.durable
    assert dragonfly.backup == {"enabled": False}
    assert dragonfly.http["enabled"] is False
    assert dragonfly.http["port"] is None
    assert "token" not in dragonfly.http
    assert dragonfly.resources == {"database": "unlimited"}


def test_same_name_across_types_requires_a_typed_selector():
    source = load_source(FIXTURES / "minimal")
    redis = replace(source.databases[1], name="example-prod-01")
    source = replace(source, databases=(source.databases[0], redis, source.databases[2]))
    lock = resolve_lock(source, load_lock(FIXTURES / "minimal/host.lock.json"), resolver=_unused)
    config = normalize(source, lock)

    assert config.select("postgres/example-prod-01").engine == "postgres"
    assert config.select("redis/example-prod-01").engine == "redis"
    with pytest.raises(ConfigError) as caught:
        config.select("example-prod-01")
    assert "ambiguous" in str(caught.value)
    assert "postgres/example-prod-01" in str(caught.value)
    assert "redis/example-prod-01" in str(caught.value)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda data: data["databases"][0].update(type="mysql"), "unsupported type"),
        (lambda data: data["databases"][0].update(name="Bad Name"), "safe lowercase"),
        (lambda data: data["databases"][0].update(pooler="yes"), "must be a boolean"),
        (lambda data: data["databases"][2].update(memory="lots"), "positive kb"),
        (lambda data: data["databases"][0].update(current={}), "migration-only field current"),
        (lambda data: data["databases"][0].update(target={}), "migration-only field target"),
        (lambda data: data["databases"][1].update(memory="1gb"), "only valid for dragonfly"),
        (lambda data: data["databases"][0].update(http=True), "HTTP is only valid for KV"),
    ],
)
def test_invalid_source_fields_fail(tmp_path, change, message):
    data = _source_data("minimal")
    change(data)
    path = _write_source(tmp_path, data)

    with pytest.raises(ConfigError, match=message):
        load_source(path)


@pytest.mark.parametrize(
    "image",
    [
        "postgres",
        "registry.example.com:5000/postgres",
        "postgres:latest",
        "postgres:latest@sha256:" + "a" * 64,
    ],
)
def test_source_images_reject_implicit_and_explicit_latest(tmp_path, image):
    data = _source_data("minimal")
    data["host"]["images"]["postgres"] = image

    with pytest.raises(ConfigError, match="latest|explicit non-latest"):
        load_source(_write_source(tmp_path, data))


def test_source_images_accept_explicit_tag_and_immutable_digest(tmp_path):
    data = _source_data("minimal")
    data["host"]["images"]["postgres"] = "registry.example.com:5000/postgres:16.9"
    data["host"]["images"]["http"] = "example/http@sha256:" + "b" * 64

    source = load_source(_write_source(tmp_path, data))

    assert source.images["postgres"] == "registry.example.com:5000/postgres:16.9"
    assert source.images["http"] == "example/http@sha256:" + "b" * 64


@pytest.mark.parametrize(
    ("engine", "image"),
    [
        ("postgres", "postgres:stable"),
        ("postgres", "registry16.example.com/postgres:stable"),
        ("postgres", "postgres@sha256:" + "a" * 64),
        ("redis", "redis:0"),
        ("dragonfly", "dragonfly:version-1"),
    ],
)
def test_source_engine_images_require_positive_tag_major(tmp_path, engine, image):
    data = _source_data("minimal")
    data["host"]["images"][engine] = image

    with pytest.raises(ConfigError, match="positive engine major"):
        load_source(_write_source(tmp_path, data))


def test_duplicate_typed_identity_fails(tmp_path):
    data = _source_data("minimal")
    data["databases"].append(deepcopy(data["databases"][0]))

    with pytest.raises(ConfigError, match="duplicate typed identity"):
        load_source(_write_source(tmp_path, data))


def test_distinct_kv_types_with_same_name_fail_on_derived_collision(tmp_path):
    data = _source_data("minimal")
    data["databases"][2]["name"] = data["databases"][1]["name"]

    with pytest.raises(ConfigError, match="derived container collides"):
        load_source(_write_source(tmp_path, data))


def test_secret_value_fails_without_printing_it(tmp_path):
    data = _source_data("minimal")
    data["databases"][0]["password"] = "do-not-print"

    with pytest.raises(ConfigError) as caught:
        load_source(_write_source(tmp_path, data))
    assert "password" in str(caught.value)
    assert "do-not-print" not in str(caught.value)


def test_old_separate_source_files_are_rejected(tmp_path):
    _write_source(tmp_path, _source_data("minimal"))
    (tmp_path / "postgres.yml").write_text("instances: []\n")

    with pytest.raises(ConfigError, match="old source layout is not supported"):
        load_source(tmp_path)


def test_host_lock_rejects_duplicate_ports_and_secret_data(tmp_path):
    data = json.loads((FIXTURES / "minimal/host.lock.json").read_text())
    data["http_ports"]["redis/other-prod-01"] = 13379
    path = tmp_path / "host.lock.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match="duplicate HTTP port"):
        load_lock(path)

    data = json.loads((FIXTURES / "minimal/host.lock.json").read_text())
    data["token"] = "do-not-print"
    path.write_text(json.dumps(data))
    with pytest.raises(ConfigError) as caught:
        load_lock(path)
    assert "secret data" in str(caught.value)
    assert "do-not-print" not in str(caught.value)


def test_write_lock_is_atomic_and_loadable(tmp_path):
    lock = load_lock(FIXTURES / "minimal/host.lock.json")
    target = tmp_path / "host.lock.json"
    target.write_text("old\n")

    write_lock(target, lock)

    assert load_lock(target) == lock
    assert target.stat().st_mode & 0o777 == 0o644
    assert not list(tmp_path.glob(".host.lock.json.*"))


def test_verbose_manifest_resolution_selects_linux_amd64(monkeypatch):
    calls = []
    output = [
        {
            "Digest": "sha256:" + "1" * 64,
            "Platform": {"os": "linux", "architecture": "arm64"},
        },
        {
            "Digest": DIGEST,
            "Platform": {"os": "linux", "architecture": "amd64"},
        },
    ]

    def fake_run(args, *, timeout):
        calls.append((args, timeout))
        return Result(tuple(args), 0, json.dumps(output), "")

    monkeypatch.setattr(config_module, "run", fake_run)

    assert resolve_image_digest("postgres:16") == DIGEST
    assert calls == [(["docker", "manifest", "inspect", "--verbose", "postgres:16"], 120)]


def test_lock_refresh_preserves_unchanged_images_and_resolves_only_changes():
    source = load_source(FIXTURES / "minimal")
    current = load_lock(FIXTURES / "minimal/host.lock.json", source)
    images = {**source.images, "postgres": "postgres:17"}
    source = replace(source, images=images)
    calls = []

    def resolver(image, **platform):
        calls.append((image, platform))
        return DIGEST

    updated = resolve_lock(source, current, resolver=resolver)

    assert calls == [("postgres:17", {"os_name": "linux", "architecture": "amd64"})]
    assert updated.images["postgres"] == ImageLock("postgres:17", DIGEST)
    for name in current.images.keys() - {"postgres"}:
        assert updated.images[name] is current.images[name]


def test_lock_reuses_source_digest_without_resolution_or_double_digest():
    source = load_source(FIXTURES / "minimal")
    current = load_lock(FIXTURES / "minimal/host.lock.json", source)
    digest = "sha256:" + "b" * 64
    source = replace(source, images={**source.images, "postgres": f"postgres:16@{digest}"})

    updated = resolve_lock(source, current, resolver=_unused)
    normalized = normalize(source, updated)

    assert updated.images["postgres"] == ImageLock(f"postgres:16@{digest}", digest)
    assert normalized.host.images["postgres"] == f"postgres:16@{digest}"
    assert normalized.host.images["postgres"].count("@") == 1


def test_http_ports_stay_stable_across_reorder_add_and_remove():
    source = load_source(FIXTURES / "minimal")
    current = load_lock(FIXTURES / "minimal/host.lock.json", source)
    reordered = replace(source, databases=tuple(reversed(source.databases)))
    stable = resolve_lock(reordered, current, resolver=_unused)
    assert stable.http_ports == current.http_ports

    added = replace(
        source,
        databases=(
            *source.databases,
            DatabaseSource(name="new-prod-01", type="redis"),
        ),
    )
    with_new = resolve_lock(added, current, resolver=_unused)
    assert with_new.http_ports["redis/cache-dev-01"] == 13379
    assert with_new.http_ports["dragonfly/queue-test-01"] == 13380
    assert with_new.http_ports["redis/new-prod-01"] == 13381

    removed = replace(source, databases=(source.databases[0], source.databases[2]))
    without_redis = resolve_lock(removed, current, resolver=_unused)
    assert without_redis.http_ports == {"dragonfly/queue-test-01": 13380}


def test_lock_refresh_never_reallocates_a_surviving_port():
    source = load_source(FIXTURES / "minimal")
    current = load_lock(FIXTURES / "minimal/host.lock.json", source)
    narrower = replace(source, http_port_start=13380)

    with pytest.raises(ConfigError, match="instead of reallocating"):
        resolve_lock(narrower, current, resolver=_unused)


def test_normalization_rejects_source_lock_drift():
    source = load_source(FIXTURES / "minimal")
    lock = load_lock(FIXTURES / "minimal/host.lock.json", source)
    changed = replace(source, images={**source.images, "redis": "redis:7.4"})
    with pytest.raises(ConfigError, match="image redis source changed"):
        normalize(changed, lock)

    missing = replace(lock, http_ports={"redis/cache-dev-01": 13379})
    with pytest.raises(ConfigError, match="missing HTTP ports"):
        normalize(source, missing)


def test_normalization_is_deterministic():
    source = load_source(FIXTURES / "minimal")
    lock = load_lock(FIXTURES / "minimal/host.lock.json", source)

    assert normalize(source, lock) == normalize(source, lock)


def test_runtime_render_uses_only_protected_secret_paths(tmp_path):
    rendered = render(FIXTURES / "minimal", tmp_path)
    text = "\n".join(path.read_text() for path in tmp_path.rglob("*.json"))

    assert "op://" not in text
    assert "do-not-print" not in text
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in tmp_path.rglob("*.json"))
    runtime = load(tmp_path)
    assert runtime.host.runtime
    assert {item.selector for item in runtime.instances} == {
        "postgres/example-prod-01",
        "redis/cache-dev-01",
        "dragonfly/queue-test-01",
    }
    assert (
        runtime.select("example-prod-01").secrets["password"]
        == (Path("/etc/evanovation-db/secrets/postgres-example-prod-01.password")).as_posix()
    )
    assert rendered.select("cache-dev-01").http["token"].startswith("op://")
    assert runtime.select("cache-dev-01").http["token"].endswith("kv-cache-dev-01-http.token")


def test_montreal_normalization_matches_golden_contract():
    root = ROOT / "config/montreal-01"
    source = load_source(root)
    config = load(root)
    golden = json.loads((FIXTURES / "montreal-contract.json").read_text())

    assert source.images == golden["source_images"]
    assert _host_contract(config) == golden["host"]
    assert _instance_contract(config) == _expand_instances(golden)


def _source_data(name: str) -> dict:
    return yaml.safe_load((FIXTURES / name / "host.yml").read_text())


def _write_source(root: Path, data: dict) -> Path:
    path = root / "host.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def _unused(*args, **kwargs):
    raise AssertionError(f"resolver should not be called: {args!r} {kwargs!r}")


def _host_contract(config) -> dict:
    host = config.host
    return {
        "id": host.id,
        "ssh": host.ssh,
        "domain": host.domain,
        "data_root": str(host.data_root),
        "config_dir": str(host.config_dir),
        "state_dir": str(host.state_dir),
        "backup_dir": str(host.backup_dir),
        "lock_dir": str(host.lock_dir),
        "repos": host.repos,
        "retention": host.retention,
        "images": host.images,
        "secrets": host.secrets,
        "min_free_gb": host.min_free_gb,
    }


def _instance_contract(config) -> list[dict]:
    return [
        {
            "name": item.id,
            "env": item.env,
            "engine": item.engine,
            "image": item.image,
            "port": item.port,
            "container": item.container,
            "project": item.project,
            "data": str(item.data),
            "domain": item.domain,
            "durable": item.durable,
            "backup": item.backup,
            "settings": item.settings,
            "password": item.secrets["password"],
            "http": item.http,
        }
        for item in config.instances
    ]


def _expand_instances(golden: dict) -> list[dict]:
    result = []
    for item in golden["databases"]:
        name = item["name"]
        engine = item["engine"]
        group = "postgres" if engine == "postgres" else "kv"
        settings = {**golden["settings"][engine], **item.get("settings", {})}
        http = None
        if group == "kv":
            http = {
                "enabled": True,
                "port": item["http_port"],
                "domain": golden["patterns"]["domain"].format(name=name, group=group),
                "image": golden["host"]["images"]["http"],
                "max_connections": 20,
                "token": golden["patterns"]["token"].format(name=name),
            }
        result.append(
            {
                "name": name,
                "env": name.rsplit("-", 2)[1],
                "engine": engine,
                "image": golden["host"]["images"][engine],
                "port": 5432 if engine == "postgres" else 6379,
                "container": golden["patterns"]["container"][group].format(name=name),
                "project": golden["patterns"]["project"].format(name=name),
                "data": golden["patterns"]["data"].format(name=name, group=group),
                "domain": golden["patterns"]["domain"].format(name=name, group=group),
                "durable": True,
                "backup": {"enabled": True},
                "settings": settings,
                "password": golden["patterns"]["password"].format(name=name, group=group),
                "http": http,
            }
        )
    return result

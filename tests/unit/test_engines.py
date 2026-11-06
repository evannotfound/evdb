import json
from dataclasses import replace

import pytest
import yaml

from evdb.config import defaults
from evdb.engines import dragonfly, get, kv, postgres, redis, valkey
from evdb.errors import ConfigError
from evdb.run import Result


def test_direct_engine_lookup_has_only_concrete_modules():
    assert get("postgres") is postgres
    assert get("redis") is redis
    assert get("dragonfly") is dragonfly
    assert get("valkey") is valkey
    with pytest.raises(ConfigError):
        get("kv")


def test_postgres_owns_private_files_services_and_pgbouncer_access(config):
    target = config.select("app-test-01/postgres")
    target.generated.mkdir(parents=True)
    files = postgres.files(target)
    services = postgres.services(target)
    text = yaml.safe_dump(services)

    assert files["postgres-password"] == "local-postgres-password\n"
    assert '"default" "local-postgres-password"' in files["pgbouncer-users"]
    assert "local-postgres-password" not in text
    pool = services[target.service("pgbouncer")]
    assert "user" not in pool
    assert pool["group_add"] == [str(target.generated.stat().st_gid)]
    assert pool["cap_drop"] == ["ALL"]
    assert "pgbouncer.ini" in " ".join(pool["volumes"])
    assert services[target.service("primary")]["image"] == "postgres:16"
    assert "tls.certresolver" not in text


def test_postgres_custom_identity_reaches_primary_and_pgbouncer(config):
    target = config.select("app-test-01/postgres")
    target = replace(
        target,
        settings=replace(target.settings, username="app user", database="app/data"),
    )

    files = postgres.files(target)
    services = postgres.services(target)

    assert '"app user" "local-postgres-password"' in files["pgbouncer-users"]
    primary = services[target.service("primary")]
    assert primary["environment"]["POSTGRES_USER"] == "app user"
    assert primary["environment"]["POSTGRES_DB"] == "app/data"
    pool = services[target.service("pgbouncer")]
    assert "app user" in pool["healthcheck"]["test"]
    assert "app/data" in pool["healthcheck"]["test"]


@pytest.mark.parametrize("enabled", [True, False])
def test_postgres_direct_and_transaction_pool_routes(config, enabled):
    target = config.select("app-test-01/postgres")
    target = replace(
        target,
        settings=replace(
            target.settings, pgbouncer=replace(target.settings.pgbouncer, enabled=enabled)
        ),
    )
    services = postgres.services(target)
    key = f"postgres-{target.project}"
    primary = services[target.service("primary")]
    labels = primary["labels"]
    assert labels[f"traefik.tcp.routers.{key}.rule"] == f"HostSNI(`{target.domain}`)"
    assert labels[f"traefik.tcp.routers.{key}.tls"] == "true"
    assert labels[f"traefik.tcp.routers.{key}.entrypoints"] == "postgres"
    assert labels[f"traefik.tcp.services.{key}.loadbalancer.server.port"] == "5432"
    assert all("ports" not in service for service in services.values())
    files = postgres.files(target)
    if enabled:
        assert target.pooled_domain == "app-test-01-pooled.test-01.storage.example.com"
        labels = services[target.service("pgbouncer")]["labels"]
        key += "-pooled"
        assert labels[f"traefik.tcp.routers.{key}.rule"] == f"HostSNI(`{target.pooled_domain}`)"
        assert labels[f"traefik.tcp.routers.{key}.tls"] == "true"
        assert labels[f"traefik.tcp.routers.{key}.entrypoints"] == "postgres"
        assert labels[f"traefik.tcp.services.{key}.loadbalancer.server.port"] == "5432"
        assert "pool_mode = transaction\n" in files["pgbouncer.ini"]
    else:
        assert set(services) == {target.service("primary")}
        assert set(files) == {"postgres-password"}
        assert "-pooled" not in yaml.safe_dump(services)


@pytest.mark.parametrize("engine", ["redis", "dragonfly", "valkey"])
def test_concrete_kv_engine_owns_native_files_http_and_routes(config, engine):
    target = config.select("app-test-01/kv")
    settings = defaults("kv", target.settings.data_root, engine)
    target = replace(target, settings=settings)
    module = get(engine)
    module.validate(settings)
    files = module.files(target)
    services = module.services(target)
    compose = json.dumps(services)

    native = "dragonfly.flags" if engine == "dragonfly" else f"{engine}.conf"
    assert native in files
    assert "http.env" in files
    assert "local-kv-password" not in compose
    assert "local-http-token" not in compose
    assert target.service("http") in services
    http = services[target.service("http")]
    assert http["ports"] == [f"127.0.0.1:{target.http_port}:80"]
    assert "labels" not in http


def test_redis_rejects_dragonfly_only_settings(config):
    settings = replace(config.select("app-test-01/kv").settings, threads=2)
    with pytest.raises(ConfigError, match="Dragonfly"):
        redis.validate(settings)


def test_dragonfly_options_are_optional_and_change_the_service_command(config):
    target = config.select("app-test-01/kv")
    target = replace(target, settings=defaults("kv", target.settings.data_root, "dragonfly"))
    original = dragonfly.services(target)[target.service("primary")]["command"]
    assert not any("cluster_mode" in arg or "snapshot_cron" in arg for arg in original)
    settings = replace(
        target.settings,
        cluster_mode="emulated",
        lock_on_hashtags=True,
        snapshot_cron="*/5 * * * *",
    )
    dragonfly.validate(settings)
    target = replace(
        target,
        settings=settings,
        host=replace(target.host, routing=replace(target.host.routing, kv_ports=(16379,))),
    )
    command = dragonfly.services(target)[target.service("primary")]["command"]
    assert "--cluster_mode=emulated" in command
    assert "--lock_on_hashtags=true" in command
    assert "--snapshot_cron=*/5 * * * *" in command
    assert f"--cluster_announce_ip={target.domain}" in command
    assert "--announce_port=16379" in command
    assert target.credentials.password not in " ".join(command)


@pytest.mark.parametrize(
    "values",
    [
        {"cluster_mode": "yes"},
        {"cluster_mode": []},
        {"lock_on_hashtags": True},
        {"lock_on_hashtags": "true"},
        {"snapshot_cron": ""},
        {"snapshot_cron": "* * *"},
        {"snapshot_cron": "60 * * * *"},
        {"snapshot_cron": "*/0 * * * *"},
        {"snapshot_cron": "0 0 0 * *"},
        {"snapshot_cron": "0 0 * * 5-1"},
        {"snapshot_cron": "* * * * *\n"},
    ],
)
def test_dragonfly_rejects_invalid_cluster_and_snapshot_settings(config, values):
    settings = defaults("kv", config.host.data_roots[0], "dragonfly")
    with pytest.raises(ConfigError, match="cluster_mode|lock_on_hashtags|snapshot_cron"):
        dragonfly.validate(replace(settings, **values))


@pytest.mark.parametrize("schedule", ["off", "*/5 * * * *", "0,30 6-18/2 * 1,6 1-5"])
def test_dragonfly_accepts_numeric_snapshot_schedules(config, schedule):
    settings = defaults("kv", config.host.data_roots[0], "dragonfly")
    dragonfly.validate(replace(settings, snapshot_cron=schedule))


@pytest.mark.parametrize("engine", ["redis", "valkey"])
def test_other_engines_reject_dragonfly_options(config, engine):
    settings = defaults("kv", config.host.data_roots[0], engine)
    with pytest.raises(ConfigError, match="Dragonfly"):
        get(engine).validate(replace(settings, snapshot_cron="*/5 * * * *"))


def test_postgres_info_reports_logical_data_totals(config, monkeypatch):
    target = config.select("app-test-01/postgres")

    def psql(container, database, sql, **kwargs):
        if sql == postgres.DATA_SQL:
            return '{"logical_bytes":3145728,"databases":2}'
        return "16.4"

    monkeypatch.setattr(postgres, "_psql", psql)

    value = postgres.info(target)

    assert value["version"] == "16.4"
    assert value["data"] == {
        "available": True,
        "logical_bytes": 3145728,
        "databases": 2,
    }


def test_redis_compatible_usage_reports_dataset_memory_and_keys(monkeypatch):
    values = {
        "memory": {"used_memory_dataset": "2048", "used_memory": "4096"},
        "keyspace": {"db0": "keys=4,expires=1", "db2": "keys=3,expires=0"},
    }
    monkeypatch.setattr(kv, "info", lambda container, password, section, **kw: values[section])

    assert kv.usage("primary", "password", engine="redis") == {
        "available": True,
        "dataset_bytes": 2048,
        "keys": 7,
    }


@pytest.mark.parametrize("engine", ["redis", "dragonfly", "valkey"])
def test_native_cli_authentication_and_health(monkeypatch, engine):
    calls = []
    replies = iter([(0, "PONG\n"), (0, "NOAUTH Authentication required."), (1, "PONG")])

    def execute(container, args, **kwargs):
        calls.append((container, args, kwargs))
        code, out = next(replies)
        return Result(tuple(args), code, out, "")

    monkeypatch.setattr(kv.docker, "exec", execute)
    assert kv.health("primary", "private password", engine=engine, timeout=3)
    assert not kv.health("primary", "", engine=engine)
    assert not kv.health("primary", "wrong password", engine=engine)
    container, args, options = calls[0]
    cli = "valkey" if engine == "valkey" else "redis"
    assert container == "primary"
    assert args == [f"{cli}-cli", "--raw", "PING"]
    assert options["env"] == {f"{cli.upper()}CLI_AUTH": "private password"}
    assert options["secrets"] == ("private password",)
    assert options["timeout"] == 3 and options["check"] is False
    assert calls[1][2]["env"] is None


@pytest.mark.parametrize("mode", ["durable", "cache"])
def test_valkey_persistence_and_memory_are_rendered_without_exposing_secrets(config, mode):
    target = config.select("app-test-01/kv")
    settings = replace(defaults("kv", target.settings.data_root, "valkey"), mode=mode)
    target = replace(target, settings=settings)
    valkey.validate(settings)
    native = valkey.files(target)["valkey.conf"]
    assert 'save ""\n' in native
    assert f"appendonly {'yes' if mode == 'durable' else 'no'}\n" in native
    assert "appendfsync everysec\n" in native
    assert f"maxmemory-policy {'noeviction' if mode == 'durable' else 'allkeys-lru'}\n" in native
    service = valkey.services(target)[target.service("primary")]
    assert service["stop_grace_period"] == "60s"
    assert service["command"] == [
        "/usr/local/bin/valkey-server",
        "/run/secrets/valkey.conf",
        "--maxmemory",
        "256mb",
    ]
    changed = replace(target, settings=replace(settings, memory="512mb"))
    assert valkey.services(changed)[target.service("primary")]["command"][-1] == "512mb"
    assert target.credentials.password in native
    assert target.credentials.password not in json.dumps(service)


@pytest.mark.parametrize("values", [{"memory": "0mb"}, {"memory": None}, {"threads": 1}])
def test_valkey_rejects_invalid_memory_and_dragonfly_threads(config, values):
    settings = defaults("kv", config.host.data_roots[0], "valkey")
    with pytest.raises(ConfigError, match="memory|thread"):
        valkey.validate(replace(settings, **values))


def test_valkey_facts_and_info_report_native_version(config, monkeypatch):
    def execute(container, args, **kwargs):
        values = {
            "server": "redis_version:7.2.4\r\nvalkey_version:9.1.3\r\n",
            "keyspace": "# Keyspace\r\ndb0:keys=2,expires=0\r\n",
            "memory": "used_memory_dataset:2048\r\n",
        }
        assert args[0] == "valkey-cli"
        return Result(tuple(args), 0, values[args[-1]], "")

    monkeypatch.setattr(kv.docker, "exec", execute)
    assert kv.facts("primary", "password", engine="valkey") == {
        "version": "9.1.3",
        "databases": {"0": 2},
        "keys": 2,
    }
    target = config.select("app-test-01/kv")
    target = replace(target, settings=defaults("kv", target.settings.data_root, "valkey"))
    assert valkey.info(target)["version"] == "9.1.3"

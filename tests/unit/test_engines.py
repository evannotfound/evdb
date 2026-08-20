import json
from dataclasses import replace

import pytest
import yaml

from evdb.engines import dragonfly, get, postgres, redis
from evdb.errors import ConfigError


def test_direct_engine_lookup_has_only_concrete_modules():
    assert get("postgres") is postgres
    assert get("redis") is redis
    assert get("dragonfly") is dragonfly
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


@pytest.mark.parametrize("engine", ["redis", "dragonfly"])
def test_concrete_kv_engine_owns_native_files_http_and_routes(config, engine):
    target = config.select("app-test-01/kv")
    if engine == "dragonfly":
        settings = replace(
            target.settings,
            engine=engine,
            image="dragonflydb/dragonfly:v1.34.1",
            memory="256mb",
            threads=1,
        )
    else:
        settings = target.settings
    target = replace(target, settings=settings)
    module = get(engine)
    module.validate(settings)
    files = module.files(target)
    services = module.services(target)
    compose = json.dumps(services)

    native = "redis.conf" if engine == "redis" else "dragonfly.flags"
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

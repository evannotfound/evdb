import json
from dataclasses import replace

import yaml

from evdb import compose
from evdb.config import replace_role, resolve_state
from evdb.run import Result

DIGEST = "sha256:" + "a" * 64


def _state(config):
    return resolve_state(config, resolver=lambda source: DIGEST)


def test_roles_render_as_separate_compose_projects(config):
    state = _state(config)
    postgres = config.select("app-test-01/postgres")
    kv = config.select("app-test-01/kv")

    pg_data = compose.database(config, postgres, state)
    kv_data = compose.database(config, kv, state)

    assert postgres.domain == "app-test-01.test-01.storage.example.com"
    assert kv.domain == postgres.domain
    assert pg_data["name"] == "evdb-app-test-01-postgres"
    assert kv_data["name"] == "evdb-app-test-01-kv"
    assert set(pg_data["services"]).isdisjoint(kv_data["services"])
    assert set(pg_data["services"]) == {
        "evdb-app-test-01-postgres-primary",
    }
    assert set(kv_data["services"]) == {
        "evdb-app-test-01-kv-primary",
        "evdb-app-test-01-kv-http",
    }
    assert all("@sha256:" in item["image"] for item in pg_data["services"].values())
    assert all("@sha256:" in item["image"] for item in kv_data["services"].values())


def test_postgres_routes_to_pgbouncer_and_mounts_private_files(config):
    postgres = config.select("app-test-01/postgres")
    settings = replace(
        postgres.settings,
        pgbouncer=replace(postgres.settings.pgbouncer, enabled=True),
    )
    target = replace(postgres, settings=settings)
    state = _state(config)
    role = state.roles[target.identity]
    role = replace(
        role,
        images={
            **role.images,
            "pgbouncer": role.images.get("pgbouncer")
            or resolve_state(
                replace(
                    config,
                    projects=(replace(config.projects[0], postgres=settings),),
                ),
                state,
                resolver=lambda source: DIGEST,
            )
            .roles[target.identity]
            .images["pgbouncer"],
        },
    )
    state = replace(state, roles={**state.roles, target.identity: role})

    data = compose.database(config, target, state)
    primary = data["services"]["evdb-app-test-01-postgres-primary"]
    pooler = data["services"]["evdb-app-test-01-postgres-pgbouncer"]

    assert primary["environment"]["POSTGRES_PASSWORD_FILE"] == "/run/secrets/postgres-password"
    assert any("/password:/run/secrets/postgres-password:ro" in item for item in primary["volumes"])
    assert any(key.startswith("traefik.tcp.routers.") for key in pooler["labels"])
    assert not any(key.startswith("traefik.tcp.routers.") for key in primary["labels"])
    assert "auth_file = /run/secrets/pgbouncer-users" in compose.pool_config(target)


def test_kv_is_private_native_and_http_is_loopback_only(config):
    target = config.select("app-test-01/kv")
    state = _state(config)
    data = compose.database(config, target, state)
    primary = data["services"]["evdb-app-test-01-kv-primary"]
    http = data["services"]["evdb-app-test-01-kv-http"]

    assert "ports" not in primary
    assert primary["command"] == ["/usr/local/bin/redis-server", "/run/secrets/redis.conf"]
    assert primary["cap_drop"] == ["ALL"]
    assert primary["cap_add"] == ["DAC_OVERRIDE"]
    assert primary["security_opt"] == ["no-new-privileges:true"]
    assert http["ports"] == [f"127.0.0.1:{state.roles[target.identity].http_port}:80"]
    assert http["environment"] == {"SRH_MODE": "env", "SRH_MAX_CONNECTIONS": "20"}
    assert http["env_file"] == [
        str(config.paths.role_secrets(target.project, target.role) / "http.env")
    ]
    assert all(
        alias.startswith("evdb-app-test-01-kv-")
        for service in data["services"].values()
        for alias in service["networks"][compose.NETWORK]["aliases"]
    )


def test_compose_is_deterministic_secret_free_and_contract_labeled(config):
    target = config.select("app-test-01/kv")
    state = _state(config)

    first = compose.database(config, target, state)
    second = compose.database(config, target, state)
    text = json.dumps(first, sort_keys=True)

    assert first == second
    assert "do-not-print" not in text
    assert "password" not in text.lower()
    labels = [item["labels"][compose.CONTRACT_LABEL] for item in first["services"].values()]
    assert len(set(labels)) == len(labels)
    assert all(len(label) == 64 for label in labels)


def test_http_sidecar_change_does_not_change_primary_contract(config):
    target = config.select("app-test-01/kv")
    state = _state(config)
    before = compose.database(config, target, state)
    settings = replace(
        target.settings,
        http=replace(target.settings.http, connections=target.settings.http.connections + 1),
    )
    changed = replace_role(config, target, settings)
    changed_target = changed.select(target.identity)
    changed_state = resolve_state(changed, state, resolver=lambda source: DIGEST)
    after = compose.database(changed, changed_target, changed_state)
    primary = "evdb-app-test-01-kv-primary"
    http = "evdb-app-test-01-kv-http"

    assert before["services"][primary] == after["services"][primary]
    assert (
        before["services"][http]["labels"][compose.CONTRACT_LABEL]
        != after["services"][http]["labels"][compose.CONTRACT_LABEL]
    )


def test_native_routes_are_unique_and_use_sni_certificates(config):
    state = _state(config)
    routes = []
    rules = []
    for target in config.databases:
        data = compose.database(config, target, state)
        labels = {
            key: value
            for service in data["services"].values()
            for key, value in service.get("labels", {}).items()
            if key.startswith("traefik.tcp.routers.")
        }
        router = f"{target.role}-{target.project}"
        entrypoint = "postgres" if target.role == "postgres" else "kv"
        rule = f"HostSNI(`{target.domain}`)"
        assert labels[f"traefik.tcp.routers.{router}.entrypoints"] == entrypoint
        assert labels[f"traefik.tcp.routers.{router}.rule"] == rule
        assert any(
            key.endswith(".tls.certresolver") and value == "evdb" for key, value in labels.items()
        )
        rules.append(rule)
        routes.extend(labels)
    assert rules == ["HostSNI(`app-test-01.test-01.storage.example.com`)" for _ in rules]
    assert len(routes) == len(set(routes))


def test_dedicated_traefik_uses_dns01_and_only_native_ports(config):
    data = compose.traefik(config, _state(config))
    service = data["services"]["traefik"]
    command = service["command"]

    assert data["name"] == "evdb-traefik"
    assert service["ports"] == ["5432:5432/tcp", "6379:6379/tcp"]
    assert not any(":80" in value or ":443" in value for value in service["ports"])
    assert "--certificatesresolvers.evdb.acme.dnschallenge=true" in command
    assert any(value.endswith(".dnschallenge.provider=testdns") for value in command)
    assert service["env_file"] == [str(config.paths.traefik / "dns.env")]
    assert service["healthcheck"]["test"] == ["CMD", "traefik", "healthcheck", "--ping"]


def test_write_produces_readable_yaml(config, tmp_path):
    value = compose.database(config, config.select("app-test-01/kv"), _state(config))
    path = tmp_path / "compose.yaml"

    compose.write(path, value)

    assert yaml.safe_load(path.read_text()) == value
    assert path.stat().st_mode & 0o777 == 0o640


def test_validation_uses_exact_compose_project(monkeypatch, tmp_path):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(compose, "run", fake_run)
    path = tmp_path / "compose.yaml"

    compose.validate(path, "evdb-app-prod-01-kv", timeout=42, secrets=("private",))

    assert calls == [
        (
            [
                "docker",
                "compose",
                "-f",
                str(path),
                "--project-name",
                "evdb-app-prod-01-kv",
                "config",
                "--quiet",
                "--no-env-resolution",
            ],
            {"timeout": 42, "secrets": ("private",)},
        )
    ]


def test_existing_unowned_network_is_rejected(monkeypatch):
    monkeypatch.setattr(
        compose,
        "run",
        lambda *args, **kwargs: Result(tuple(args[0]), 0, '[{"Labels": {}}]', ""),
    )

    from evdb.errors import ConfigError

    try:
        compose.ensure_network()
    except ConfigError as exc:
        assert "not owned by evdb" in str(exc)
    else:
        raise AssertionError("unsafe network was accepted")

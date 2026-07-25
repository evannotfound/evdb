import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from evanovation_db import deployment
from evanovation_db.config import Config, load, load_lock, render_runtime
from evanovation_db.errors import DeploymentError
from evanovation_db.files import hash as file_hash
from evanovation_db.files import write_json, write_text
from evanovation_db.run import Result
from evanovation_db.secrets import Credentials, deployment_files

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests/fixtures/config/minimal"
SOURCE_HASH = file_hash(FIXTURE / "host.yml")


class Client:
    def fields(self, title, names):
        return {"restic-password": "restic-value", "rclone-config": "rclone-value"}

    def credentials(self, instance):
        token = "token-value" if instance.http and instance.http["enabled"] else None
        return Credentials(f"password-{instance.id}", token)


def test_bundle_is_deterministic_pinned_and_secret_free(tmp_path):
    config = _config(tmp_path)
    host_lock = load_lock(FIXTURE / "host.lock.json")

    first = deployment.build(config, host_lock, SOURCE_HASH)
    second = deployment.build(config, host_lock, SOURCE_HASH)

    assert first.manifest == second.manifest
    assert set(first.manifest["databases"]) == {item.selector for item in config.instances}
    assert all("@sha256:" in item["image"] for item in first.manifest["databases"].values())
    staged_units = {
        name.removeprefix("systemd/") for name in first.files if name.startswith("systemd/")
    }
    assert staged_units == set(deployment.UNIT_NAMES)
    assert all(item["services"] for item in first.manifest["databases"].values())
    text = json.dumps({"runtime": first.runtime, "files": first.files, "lock": host_lock.as_dict()})
    assert "op://" not in text
    assert "password-example" not in text


def test_stage_is_immutable_idempotent_and_keeps_secrets_outside_release(tmp_path, monkeypatch):
    config = _config(tmp_path)
    bundle = deployment.build(config, load_lock(FIXTURE / "host.lock.json"), SOURCE_HASH)
    secret_files = deployment_files(config, Client())
    payload = deployment.request(bundle, (), secret_files, None, _plan())
    monkeypatch.setattr(deployment, "ROOT", tmp_path / "opt")

    first = deployment.stage(payload)
    second = deployment.stage(payload)

    assert first == second
    assert (first / "runtime/host.json").is_file()
    assert (first / "host.lock.json").is_file()
    assert (first / "src/evanovation_db/deployment.py").is_file()
    assert (first / "ansible/variables.json").is_file()
    assert (first / deployment.MANIFEST).is_file()
    assert first.stat().st_mode & 0o222 == 0
    assert all(path.stat().st_mode & 0o222 == 0 for path in first.rglob("*") if path.is_file())
    release_text = "\n".join(path.read_text() for path in first.rglob("*") if path.is_file())
    assert "password-example-prod-01" not in release_text
    assert not (config.host.config_dir / "secrets").exists()

    manifest = first / deployment.MANIFEST
    manifest.chmod(0o644)
    with pytest.raises(DeploymentError, match="asset is writable"):
        deployment.stage(payload)
    manifest.chmod(0o444)


def test_partial_secret_install_failure_restores_exact_files(tmp_path, monkeypatch):
    config = _config(tmp_path)
    bundle = deployment.build(config, load_lock(FIXTURE / "host.lock.json"), SOURCE_HASH)
    secrets = deployment_files(config, Client())
    affected = (bundle.manifest["infrastructure"]["selector"], *bundle.manifest["databases"])
    payload = deployment.request(bundle, affected, secrets, None, _plan(*affected))
    monkeypatch.setattr(deployment, "ROOT", tmp_path / "opt")
    monkeypatch.setattr(deployment, "UNIT_DIR", tmp_path / "systemd")
    first = secrets[0].path
    second = secrets[1].path
    write_text(first, "prior-system-secret\n", mode=0o640)

    def partial(values):
        write_text(Path(values[0]["path"]), values[0]["content"], mode=0o600)
        write_text(Path(values[1]["path"]), values[1]["content"], mode=0o600)
        raise OSError("partial secret install failed")

    monkeypatch.setattr(deployment, "_install_secrets", partial)

    with pytest.raises(DeploymentError, match="validation failed"):
        deployment.apply(config, payload)

    assert first.read_bytes() == b"prior-system-secret\n"
    assert first.stat().st_mode & 0o777 == 0o640
    assert not second.exists()
    assert "prior-system-secret" not in json.dumps(deployment.history(config))


def test_compose_validation_failure_restores_all_secret_targets(tmp_path, monkeypatch):
    config = _config(tmp_path)
    bundle = deployment.build(config, load_lock(FIXTURE / "host.lock.json"), SOURCE_HASH)
    secrets = deployment_files(config, Client())
    affected = (bundle.manifest["infrastructure"]["selector"], *bundle.manifest["databases"])
    payload = deployment.request(bundle, affected, secrets, None, _plan(*affected))
    monkeypatch.setattr(deployment, "ROOT", tmp_path / "opt")
    monkeypatch.setattr(deployment, "UNIT_DIR", tmp_path / "systemd")
    existing = secrets[0].path
    write_text(existing, "prior-restic\n", mode=0o640)
    monkeypatch.setattr(
        deployment,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(DeploymentError("invalid Compose")),
    )

    with pytest.raises(DeploymentError, match="validation failed"):
        deployment.apply(config, payload)

    assert existing.read_bytes() == b"prior-restic\n"
    assert existing.stat().st_mode & 0o777 == 0o640
    assert all(not item.path.exists() for item in secrets[1:])


def test_changed_deployed_database_secret_is_rejected_before_install(tmp_path, monkeypatch):
    config = _config(tmp_path)
    bundle = deployment.build(config, load_lock(FIXTURE / "host.lock.json"), SOURCE_HASH)
    root = tmp_path / "opt"
    monkeypatch.setattr(deployment, "ROOT", root)
    monkeypatch.setattr(deployment, "UNIT_DIR", tmp_path / "systemd")
    prior = root / "releases/release-prior"
    render_runtime(config, prior / "runtime")
    write_json(prior / deployment.MANIFEST, {**bundle.manifest, "id": prior.name})
    (root / "current").symlink_to(prior)
    secrets = deployment_files(config, Client())
    password = next(
        item.path for item in secrets if item.path.name == "postgres-example-prod-01.password"
    )
    write_text(password, "existing-password\n", mode=0o600)
    payload = deployment.request(bundle, (), secrets, prior.name, _plan())

    def fake_run(args, **kwargs):
        if args[:3] == ["docker", "network", "inspect"]:
            return Result(tuple(args), 0, "[]", "")
        if args[:2] == ["docker", "inspect"]:
            return _inspect_result(args, {**bundle.manifest, "id": prior.name})
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(deployment, "run", fake_run)

    with pytest.raises(DeploymentError, match="credential rotation requires a separate workflow"):
        deployment.apply(config, payload)

    assert password.read_bytes() == b"existing-password\n"
    assert not (config.host.config_dir / "secrets/kv-cache-dev-01.password").exists()


def test_apply_starts_only_affected_project_and_health_gates_activation(tmp_path, monkeypatch):
    config = _config(tmp_path)
    bundle = deployment.build(config, load_lock(FIXTURE / "host.lock.json"), SOURCE_HASH)
    secret_files = deployment_files(config, Client())
    selected = "postgres/example-prod-01"
    root = tmp_path / "opt"
    monkeypatch.setattr(deployment, "ROOT", root)
    monkeypatch.setattr(deployment, "UNIT_DIR", tmp_path / "systemd")
    prior = deepcopy(bundle.manifest)
    prior["id"] = "release-prior"
    prior["databases"][selected]["service_hash"] = "0" * 64
    prior["databases"][selected]["labels"] = {deployment.CONTRACT_LABEL: "0" * 64}
    prior_release = root / "releases/release-prior"
    render_runtime(config, prior_release / "runtime")
    write_json(prior_release / deployment.MANIFEST, prior)
    (root / "current").symlink_to(prior_release)
    payload = deployment.request(
        bundle,
        (selected,),
        secret_files,
        "release-prior",
        _plan(selected, kind="update"),
    )
    rclone = config.host.state_dir / "rclone/rclone.conf"
    write_text(rclone, "mutable-rclone-state\n")
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ["docker", "compose"] and "config" in args:
            staged = root / "releases" / bundle.manifest["id"]
            assert (staged / "runtime/host.json").is_file()
            secret = config.host.config_dir / "secrets/postgres-example-prod-01.password"
            assert secret.is_file()
            assert secret.stat().st_mode & 0o777 == 0o600
        if args[:3] == ["docker", "network", "inspect"]:
            return Result(tuple(args), 0, "[]", "")
        if args[:2] == ["docker", "inspect"]:
            return _inspect_result(args, prior)
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(deployment, "run", fake_run)
    checked = []

    def healthy(current, instance, *, contract_hash=None, services=None):
        checked.append((instance.selector, contract_hash))
        assert services == bundle.manifest["databases"][selected]["services"]
        assert (root / "current").resolve() == prior_release

    monkeypatch.setattr(deployment, "health", healthy)

    result = deployment.apply(config, payload)

    assert result == {"release": bundle.manifest["id"], "affected": [selected]}
    assert (root / "current").resolve() == root / "releases" / bundle.manifest["id"]
    up = [args for args, _ in calls if args[-2:] == ["up", "-d"]]
    assert len(up) == 1
    assert config.select(selected).project in up[0]
    assert all(
        other.project not in up[0] for other in config.instances if other.selector != selected
    )
    assert (config.host.config_dir / "host.json").is_file()
    assert (config.host.config_dir / "secrets/postgres-example-prod-01.password").is_file()
    assert rclone.read_text() == "mutable-rclone-state\n"
    assert checked == [(selected, bundle.manifest["databases"][selected]["service_hash"])]


def test_failed_initial_apply_stops_new_projects_and_keeps_staged_release(tmp_path, monkeypatch):
    config = _config(tmp_path)
    bundle = deployment.build(config, load_lock(FIXTURE / "host.lock.json"), SOURCE_HASH)
    affected = (
        bundle.manifest["infrastructure"]["selector"],
        *sorted(bundle.manifest["databases"]),
    )
    payload = deployment.request(
        bundle,
        affected,
        deployment_files(config, Client()),
        None,
        _plan(*affected),
    )
    root = tmp_path / "opt"
    monkeypatch.setattr(deployment, "ROOT", root)
    monkeypatch.setattr(deployment, "UNIT_DIR", tmp_path / "systemd")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(deployment, "run", fake_run)
    monkeypatch.setattr(
        deployment,
        "_health",
        lambda *args: (_ for _ in ()).throw(DeploymentError("unhealthy")),
    )

    with pytest.raises(DeploymentError, match="prior services were restored"):
        deployment.apply(config, payload)

    assert (root / "releases" / bundle.manifest["id"]).is_dir()
    assert not (root / "current").exists()
    assert not (config.host.config_dir / "host.json").exists()
    assert not (config.host.config_dir / "secrets/postgres-example-prod-01.password").exists()
    audit = json.loads(
        (config.host.state_dir / f"releases/{bundle.manifest['id']}.json").read_text()
    )
    assert audit["outcome"] == "failed"
    assert audit["events"][-1]["recovered"] is True
    stopped = [args for args in calls if args[-1:] == ["stop"]]
    assert len(stopped) == len(affected)
    assert not [args for args in calls if "down" in args or "rm" in args]


def test_secret_restore_failure_records_high_severity_without_leaking_values(tmp_path, monkeypatch):
    config = _config(tmp_path)
    bundle = deployment.build(config, load_lock(FIXTURE / "host.lock.json"), SOURCE_HASH)
    affected = (
        bundle.manifest["infrastructure"]["selector"],
        *sorted(bundle.manifest["databases"]),
    )
    secrets = deployment_files(config, Client())
    payload = deployment.request(bundle, affected, secrets, None, _plan(*affected))
    root = tmp_path / "opt"
    monkeypatch.setattr(deployment, "ROOT", root)
    monkeypatch.setattr(deployment, "UNIT_DIR", tmp_path / "systemd")
    restic = secrets[0].path
    write_text(restic, "prior-secret-value\n", mode=0o640)
    monkeypatch.setattr(deployment, "run", lambda args, **kwargs: Result(tuple(args), 0, "", ""))
    monkeypatch.setattr(
        deployment,
        "_health",
        lambda *args: (_ for _ in ()).throw(DeploymentError("candidate unhealthy")),
    )
    original = deployment.write_bytes

    def fail_restore(path, content, *, mode):
        if Path(path) == restic and content == b"prior-secret-value\n":
            raise OSError("restore denied")
        original(path, content, mode=mode)

    monkeypatch.setattr(deployment, "write_bytes", fail_restore)

    with pytest.raises(DeploymentError) as caught:
        deployment.apply(config, payload)

    audit = json.loads(
        (config.host.state_dir / f"releases/{bundle.manifest['id']}.json").read_text()
    )
    text = str(caught.value) + json.dumps(audit)
    assert audit["outcome"] == "recovery_failed"
    assert audit["severity"] == "high"
    assert "prior-secret-value" not in text
    assert "restic-value" not in text


def test_fresh_apply_creates_network_then_health_checks_traefik_before_databases(
    tmp_path, monkeypatch
):
    source = _config(tmp_path)
    instance = source.select("redis/cache-dev-01")
    config = Config(source.host, (instance,))
    bundle = deployment.build(config, load_lock(FIXTURE / "host.lock.json"), SOURCE_HASH)
    host = bundle.manifest["infrastructure"]["selector"]
    affected = (host, instance.selector)
    payload = deployment.request(
        bundle,
        affected,
        deployment_files(config, Client()),
        None,
        _plan(*affected),
    )
    root = tmp_path / "opt"
    monkeypatch.setattr(deployment, "ROOT", root)
    monkeypatch.setattr(deployment, "UNIT_DIR", tmp_path / "systemd")
    order = []

    def fake_run(args, **kwargs):
        if args[:3] == ["docker", "network", "inspect"]:
            order.append("network-inspect")
            return Result(tuple(args), 1, "", "network traefik-net not found")
        if args[:3] == ["docker", "network", "create"]:
            order.append("network-create")
        elif args[:2] == ["docker", "compose"] and args[-2:] == ["up", "-d"]:
            order.append("traefik-up" if deployment.TRAEFIK_PROJECT in args else "database-up")
        return Result(tuple(args), 0, "", "")

    def healthy(current, manifest, selector):
        order.append("traefik-health" if selector == host else "database-health")

    monkeypatch.setattr(deployment, "run", fake_run)
    monkeypatch.setattr(deployment, "_health", healthy)

    deployment.apply(config, payload)

    assert order.index("network-inspect") < order.index("network-create")
    assert order.index("network-create") < order.index("traefik-up")
    assert order.index("traefik-up") < order.index("traefik-health")
    assert order.index("traefik-health") < order.index("database-up")
    assert order.index("database-up") < order.index("database-health")


def test_traefik_health_failure_restores_prior_release_infrastructure(tmp_path, monkeypatch):
    config = _config(tmp_path)
    bundle = deployment.build(config, load_lock(FIXTURE / "host.lock.json"), SOURCE_HASH)
    host = bundle.manifest["infrastructure"]["selector"]
    root = tmp_path / "opt"
    monkeypatch.setattr(deployment, "ROOT", root)
    monkeypatch.setattr(deployment, "UNIT_DIR", tmp_path / "systemd")
    prior_manifest = deepcopy(bundle.manifest)
    prior_manifest["id"] = "release-prior"
    prior_traefik = prior_manifest["infrastructure"]["traefik"]
    prior_traefik["service_hash"] = "a" * 64
    prior_traefik["labels"] = {deployment.CONTRACT_LABEL: "a" * 64}
    prior = root / "releases/release-prior"
    render_runtime(config, prior / "runtime")
    for name, text in bundle.files.items():
        write_text(prior / name, text)
    write_json(prior / deployment.MANIFEST, prior_manifest)
    (root / "current").symlink_to(prior)
    payload = deployment.request(
        bundle,
        (host,),
        deployment_files(config, Client()),
        prior.name,
        _plan(host, kind="update"),
    )
    calls = []
    checks = 0

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["docker", "network", "inspect"]:
            return Result(tuple(args), 0, "[]", "")
        if args[:2] == ["docker", "inspect"]:
            return _inspect_result(args, prior_manifest)
        return Result(tuple(args), 0, "", "")

    def healthy(current, manifest, selector):
        nonlocal checks
        checks += 1
        if checks == 1:
            raise DeploymentError("candidate Traefik is unhealthy")

    monkeypatch.setattr(deployment, "run", fake_run)
    monkeypatch.setattr(deployment, "_health", healthy)

    with pytest.raises(DeploymentError, match="prior services were restored"):
        deployment.apply(config, payload)

    up = [args for args in calls if args[-2:] == ["up", "-d"]]
    assert str(root / "releases" / bundle.manifest["id"]) in " ".join(up[0])
    assert str(prior) in " ".join(up[1])
    assert (root / "current").resolve() == prior
    assert checks == 2


@pytest.mark.parametrize(
    ("selector", "failure"),
    [
        ("postgres/example-prod-01", "absent"),
        ("redis/cache-dev-01", "unhealthy"),
        ("postgres/example-prod-01", "contract"),
    ],
)
def test_sidecar_failure_blocks_database_health(tmp_path, monkeypatch, selector, failure):
    source = _config(tmp_path)
    instance = source.select(selector)
    config = Config(source.host, (instance,))
    bundle = deployment.build(config, load_lock(FIXTURE / "host.lock.json"), SOURCE_HASH)
    database = bundle.manifest["databases"][selector]
    sidecar = next(item for item in database["services"].values() if item["health"] == "docker")

    def state(name, **kwargs):
        expected = next(item for item in database["services"].values() if item["container"] == name)
        value = {
            "running": True,
            "healthy": True if expected["health"] == "docker" else None,
            "image": expected["image"],
            "service_hash": database["service_hash"],
        }
        if name == sidecar["container"]:
            if failure == "absent":
                value["running"] = False
            elif failure == "unhealthy":
                value["healthy"] = False
            else:
                value["service_hash"] = "wrong"
        return value

    monkeypatch.setattr(deployment, "_container_state", state)
    monkeypatch.setattr(deployment, "engine_healthy", lambda *args, **kwargs: True)
    monkeypatch.setattr(deployment.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(deployment.time, "monotonic", _clock())

    with pytest.raises(DeploymentError, match="did not become healthy"):
        deployment.health(
            config,
            instance,
            contract_hash=database["service_hash"],
            services=database["services"],
        )


def test_all_expected_services_and_engine_health_pass(tmp_path, monkeypatch):
    source = _config(tmp_path)
    instance = source.select("redis/cache-dev-01")
    config = Config(source.host, (instance,))
    bundle = deployment.build(config, load_lock(FIXTURE / "host.lock.json"), SOURCE_HASH)
    database = bundle.manifest["databases"][instance.selector]
    inspected = []

    def state(name, **kwargs):
        inspected.append(name)
        expected = next(item for item in database["services"].values() if item["container"] == name)
        return {
            "running": True,
            "healthy": True if expected["health"] == "docker" else None,
            "image": expected["image"],
            "service_hash": database["service_hash"],
        }

    monkeypatch.setattr(deployment, "_container_state", state)
    monkeypatch.setattr(deployment, "engine_healthy", lambda *args, **kwargs: True)

    deployment.health(
        config,
        instance,
        contract_hash=database["service_hash"],
        services=database["services"],
    )

    assert set(inspected) == {item["container"] for item in database["services"].values()}


def test_traefik_without_docker_health_status_is_not_healthy(monkeypatch):
    monkeypatch.setattr(
        deployment,
        "run",
        lambda args, **kwargs: Result(
            tuple(args),
            0,
            json.dumps([{"State": {"Running": True}, "Config": {"Image": "traefik:v3"}}]),
            "",
        ),
    )

    state = deployment._container_state("traefik", timeout=1, health=True)

    assert state["running"] is True
    assert state["healthy"] is False


def test_release_build_defensively_rejects_zero_engine_major(tmp_path):
    config = _config(tmp_path)
    postgres = config.select("postgres/example-prod-01")
    broken = replace(postgres, image="postgres:0@sha256:" + "a" * 64)
    config = Config(
        config.host,
        tuple(broken if item is postgres else item for item in config.instances),
    )

    with pytest.raises(DeploymentError, match="positive major"):
        deployment.build(config, load_lock(FIXTURE / "host.lock.json"), SOURCE_HASH)


def _config(tmp_path):
    source = load(FIXTURE)
    host = replace(
        source.host,
        config_dir=tmp_path / "etc",
        state_dir=tmp_path / "state",
        backup_dir=tmp_path / "backups",
        lock_dir=tmp_path / "locks",
    )
    return Config(host, source.instances)


def _plan(*selectors, kind="create"):
    return {
        "actions": [
            {"kind": kind, "selector": selector, "reason": "test release change"}
            for selector in selectors
        ],
        "blocked": [],
    }


def _inspect_result(args, manifest, *, health=True):
    container = args[2]
    if container == deployment.TRAEFIK_CONTAINER:
        expected = manifest["infrastructure"]["traefik"]
        state = {"Running": True}
        if health:
            state["Health"] = {"Status": "healthy"}
        data = [
            {
                "State": state,
                "Config": {"Image": expected["image"], "Labels": expected["labels"]},
            }
        ]
        return Result(tuple(args), 0, json.dumps(data), "")
    for database in manifest["databases"].values():
        for expected in database["services"].values():
            if expected["container"] != container:
                continue
            state = {"Running": True}
            if expected["health"] == "docker" and health:
                state["Health"] = {"Status": "healthy"}
            data = [
                {
                    "State": state,
                    "Config": {
                        "Image": expected["image"],
                        "Labels": {deployment.CONTRACT_LABEL: database["service_hash"]},
                    },
                }
            ]
            return Result(tuple(args), 0, json.dumps(data), "")
    return Result(tuple(args), 1, "", "not found")


def _clock():
    values = iter((0, 121))
    return lambda: next(values, 121)

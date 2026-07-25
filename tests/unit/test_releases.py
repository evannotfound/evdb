import io
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from evanovation_db import controller, deployment, remote
from evanovation_db.config import Config, load, load_lock, render_runtime, runtime_data, write_lock
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


def test_noop_service_apply_records_secret_free_audit_and_activates_after_staging(
    tmp_path, monkeypatch
):
    config = _config(tmp_path)
    bundle = _bundle(config)
    root = _root(tmp_path, monkeypatch)
    prior = _write_release(config, bundle, root, "release-prior")
    _point(root, prior)
    payload = deployment.request(
        bundle,
        (),
        deployment_files(config, Client()),
        prior.name,
        {
            "actions": [
                {
                    "kind": "pending",
                    "selector": f"host/{config.host.id}",
                    "reason": "runtime metadata changed",
                }
            ],
            "blocked": [],
        },
    )
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["docker", "network", "inspect"]:
            return Result(tuple(args), 0, "[]", "")
        if args[:2] == ["docker", "inspect"]:
            return _inspect_result(args, bundle.manifest)
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(deployment, "run", fake_run)

    result = deployment.apply(config, payload)

    assert result["release"] == bundle.manifest["id"]
    assert not [args for args in calls if args[-2:] == ["up", "-d"]]
    assert (root / "current").resolve().name == bundle.manifest["id"]
    audit = _audit(config, bundle.manifest["id"])
    assert audit["source_hash"] == SOURCE_HASH
    assert audit["lock_hash"] == bundle.manifest["lock_hash"]
    assert audit["controller_version"] == audit["runtime_version"] == "0.1.0"
    assert audit["predecessor"] == prior.name
    assert audit["engine_majors"]["postgres/example-prod-01"] == {
        "engine": "postgres",
        "major": 16,
    }
    assert audit["status"] == audit["outcome"] == "active"
    assert audit["created_at"] <= audit["finished_at"] <= audit["activated_at"]
    assert "op://" not in json.dumps(audit)
    assert prior.is_dir()
    assert (root / "releases" / bundle.manifest["id"]).is_dir()
    assert all(
        (deployment.UNIT_DIR / name).read_text() == (deployment.SYSTEMD_SOURCE / name).read_text()
        for name in deployment.UNIT_NAMES
    )
    assert [args for args in calls if args[:1] == ["systemctl"]] == [["systemctl", "daemon-reload"]]


def test_apply_checks_all_affected_services_before_switching_pointer(tmp_path, monkeypatch):
    config = _config(tmp_path)
    bundle = _bundle(config)
    root = _root(tmp_path, monkeypatch)
    prior_manifest = deepcopy(bundle.manifest)
    for index, item in enumerate(prior_manifest["databases"].values(), start=1):
        item["service_hash"] = str(index) * 64
    prior = _write_release(
        config,
        bundle,
        root,
        "release-prior",
        manifest=prior_manifest,
    )
    _point(root, prior)
    affected = tuple(sorted(bundle.manifest["databases"]))
    payload = deployment.request(
        bundle,
        affected,
        deployment_files(config, Client()),
        prior.name,
        _plan(*affected, kind="update"),
    )
    monkeypatch.setattr(deployment, "run", _healthy_run(config))
    checked = []

    def healthy(current, instance, **kwargs):
        assert (root / "current").resolve() == prior
        checked.append(instance.selector)

    monkeypatch.setattr(deployment, "health", healthy)

    deployment.apply(config, payload)

    assert checked == list(affected)
    assert (root / "current").resolve().name == bundle.manifest["id"]


def test_failed_apply_recovers_prior_compose_and_locked_image(tmp_path, monkeypatch):
    config = _config(tmp_path)
    bundle = _bundle(config)
    root = _root(tmp_path, monkeypatch)
    selector = "postgres/example-prod-01"
    old_instance = replace(
        config.select(selector),
        image=_other_digest(config.select(selector).image, "a"),
    )
    prior_config = Config(
        config.host,
        tuple(old_instance if item.selector == selector else item for item in config.instances),
    )
    prior_bundle = _bundle(prior_config)
    prior_manifest = deepcopy(prior_bundle.manifest)
    prior = _write_release(
        prior_config,
        prior_bundle,
        root,
        "release-prior",
        manifest=prior_manifest,
    )
    _point(root, prior)
    payload = deployment.request(
        bundle,
        (selector,),
        deployment_files(config, Client()),
        prior.name,
        _plan(selector, kind="update"),
    )
    restic = config.host.config_dir / "secrets/restic_password"
    write_text(restic, "prior-restic\n", mode=0o640)
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[-2:] == ["up", "-d"] and str(prior) in args:
            assert restic.read_bytes() == b"prior-restic\n"
            assert restic.stat().st_mode & 0o777 == 0o640
        if args[:3] == ["docker", "network", "inspect"]:
            return Result(tuple(args), 0, "[]", "")
        if args[:2] == ["docker", "inspect"]:
            return _inspect_result(args, prior_manifest)
        return Result(tuple(args), 0, "", "")

    health_calls = 0

    def health(current, instance, **kwargs):
        nonlocal health_calls
        health_calls += 1
        if health_calls == 1:
            raise DeploymentError("candidate is unhealthy")

    monkeypatch.setattr(deployment, "run", fake_run)
    monkeypatch.setattr(deployment, "health", health)

    observed = deployment.state(config)
    assert deployment._affected(
        bundle.manifest["databases"],
        observed["manifest"]["databases"],
        observed["live"],
        bundle.manifest["infrastructure"],
        observed["manifest"]["infrastructure"],
        observed["infrastructure"],
    ) == {selector}

    with pytest.raises(DeploymentError, match="prior services were restored"):
        deployment.apply(config, payload)

    candidate = root / "releases" / bundle.manifest["id"]
    up = [args for args in calls if args[-2:] == ["up", "-d"]]
    assert str(candidate) in " ".join(up[0])
    assert str(prior) in " ".join(up[1])
    assert (
        prior_manifest["databases"][selector]["image"]
        in (prior / prior_manifest["databases"][selector]["compose"]).read_text()
    )
    assert (root / "current").resolve() == prior
    assert candidate.is_dir() and prior.is_dir()
    assert restic.read_bytes() == b"prior-restic\n"
    audit = _audit(config, candidate.name)
    assert audit["outcome"] == "failed"
    assert audit["events"][-1]["recovered"] is True


def test_failed_apply_and_recovery_records_high_severity_without_success(tmp_path, monkeypatch):
    config = _config(tmp_path)
    bundle = _bundle(config)
    root = _root(tmp_path, monkeypatch)
    selector = "postgres/example-prod-01"
    prior_manifest = deepcopy(bundle.manifest)
    prior_manifest["databases"][selector]["service_hash"] = "2" * 64
    prior = _write_release(
        config,
        bundle,
        root,
        "release-prior",
        manifest=prior_manifest,
    )
    _point(root, prior)
    payload = deployment.request(
        bundle,
        (selector,),
        deployment_files(config, Client()),
        prior.name,
        _plan(selector, kind="update"),
    )
    monkeypatch.setattr(deployment, "run", _healthy_run(config))
    monkeypatch.setattr(
        deployment,
        "health",
        lambda *args, **kwargs: (_ for _ in ()).throw(DeploymentError("unhealthy")),
    )

    with pytest.raises(DeploymentError, match="recovery also failed"):
        deployment.apply(config, payload)

    candidate = root / "releases" / bundle.manifest["id"]
    audit = _audit(config, candidate.name)
    assert audit["status"] == audit["outcome"] == "recovery_failed"
    assert audit["severity"] == "high"
    assert audit["successful"] is False
    assert audit["recovery_steps"]
    assert all(
        "delete" not in step.lower() or "do not" in step.lower() for step in audit["recovery_steps"]
    )
    assert (root / "current").resolve() == prior
    assert candidate.is_dir() and prior.is_dir()


def test_failed_reactivation_retains_previous_successful_history(tmp_path, monkeypatch):
    config = _config(tmp_path)
    bundle = _bundle(config)
    root = _root(tmp_path, monkeypatch)
    release = _write_release(config, bundle, root, "release-prior")
    manifest = _manifest(release)
    _mark_active(config, manifest, None)
    deployment._record_stage(
        config,
        {
            "manifest": manifest,
            "expected": "release-current",
            "plan": {"actions": [], "blocked": []},
        },
    )

    deployment._finish_apply(config, release.name, "failed", recovered=True)

    audit = _audit(config, release.name)
    assert audit["outcome"] == "failed"
    assert audit["successful"] is True
    assert audit["activated_at"] is not None


def test_activation_failure_restores_pointer_runtime_units_secrets_and_timer_state(
    tmp_path, monkeypatch
):
    config = _config(tmp_path)
    bundle = _bundle(config)
    root = _root(tmp_path, monkeypatch)
    prior = _write_release(config, bundle, root, "release-prior")
    _point(root, prior)
    deployment._activate_runtime(config, prior / "runtime")
    runtime_host = config.host.config_dir / "host.json"
    write_text(runtime_host, '{"legacy": true}\n', mode=0o640)
    old_units = {}
    for index, name in enumerate(deployment.UNIT_NAMES):
        path = deployment.UNIT_DIR / name
        write_text(path, f"old-unit-{index}\n", mode=0o600)
        old_units[path] = (path.read_bytes(), path.stat().st_mode & 0o777)
    restic = config.host.config_dir / "secrets/restic_password"
    write_text(restic, "old-restic\n", mode=0o640)
    payload = deployment.request(
        bundle,
        (),
        deployment_files(config, Client()),
        prior.name,
        {
            "actions": [
                {
                    "kind": "pending",
                    "selector": f"host/{config.host.id}",
                    "reason": "runtime and units changed",
                }
            ],
            "blocked": [],
        },
    )
    calls = []
    reloads = 0
    timer_state = {"enabled": True, "running": True}

    def fake_run(args, **kwargs):
        nonlocal reloads
        calls.append(args)
        if args[:3] == ["docker", "network", "inspect"]:
            return Result(tuple(args), 0, "[]", "")
        if args[:2] == ["docker", "inspect"]:
            return _inspect_result(args, bundle.manifest)
        if args == ["systemctl", "daemon-reload"]:
            reloads += 1
            if reloads == 1:
                raise DeploymentError("daemon reload failed")
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(deployment, "run", fake_run)

    with pytest.raises(DeploymentError, match="prior services were restored"):
        deployment.apply(config, payload)

    assert (root / "current").resolve() == prior
    assert runtime_host.read_bytes() == b'{"legacy": true}\n'
    assert runtime_host.stat().st_mode & 0o777 == 0o640
    assert {
        path: (path.read_bytes(), path.stat().st_mode & 0o777) for path in old_units
    } == old_units
    assert restic.read_bytes() == b"old-restic\n"
    assert restic.stat().st_mode & 0o777 == 0o640
    assert timer_state == {"enabled": True, "running": True}
    assert [args for args in calls if args[:1] == ["systemctl"]] == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "daemon-reload"],
    ]
    assert not any("enable" in args or "start" in args for args in calls)


def test_old_runtime_protocol_upgrade_fails_closed_then_activates_fresh_release(
    tmp_path, monkeypatch
):
    config = _config(tmp_path)
    bundle = _bundle(config)
    root = _root(tmp_path, monkeypatch)
    selector = "postgres/example-prod-01"
    prior_manifest = deepcopy(bundle.manifest)
    prior_manifest["runtime_version"] = "legacy-protocol"
    prior_manifest["databases"][selector]["service_hash"] = "0" * 64
    prior = _write_release(
        config,
        bundle,
        root,
        "release-legacy",
        manifest=prior_manifest,
    )
    legacy_runtime = b'{"host": {"legacy_schema": true}}\n'
    (prior / "runtime/host.json").write_bytes(legacy_runtime)
    _point(root, prior)
    active_runtime = config.host.config_dir / "host.json"
    write_text(active_runtime, legacy_runtime.decode(), mode=0o640)
    old_units = {}
    for name in deployment.UNIT_NAMES:
        path = deployment.UNIT_DIR / name
        write_text(path, f"legacy-{name}\n", mode=0o600)
        old_units[path] = path.read_bytes()

    bootstrap_runtime = tmp_path / "host-runtime/runtime"
    render_runtime(config, bootstrap_runtime)
    assert runtime_data(load(bootstrap_runtime)) == runtime_data(config)
    secrets = deployment_files(config, Client())
    payload = deployment.request(
        bundle,
        (selector,),
        secrets,
        prior.name,
        _plan(selector, kind="update"),
    )
    request = json.dumps(
        {
            "version": remote.VERSION,
            "operation": "apply",
            "selector": config.host.id,
            "payload": payload,
        }
    )
    calls = []
    health_calls = 0

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["docker", "network", "inspect"]:
            return Result(tuple(args), 0, "[]", "")
        if args[:2] == ["docker", "inspect"]:
            return _inspect_result(args, prior_manifest)
        return Result(tuple(args), 0, "", "")

    def health(*args, **kwargs):
        nonlocal health_calls
        health_calls += 1
        if health_calls == 1:
            raise DeploymentError("candidate health failed")

    monkeypatch.setattr(deployment, "run", fake_run)
    monkeypatch.setattr(deployment, "_health", health)

    failed_output = io.StringIO()
    assert (
        remote.serve(
            bootstrap_runtime,
            input_stream=io.StringIO(request),
            output_stream=failed_output,
        )
        == 1
    )
    assert json.loads(failed_output.getvalue())["error"]["code"] == "operation_failed"
    assert (root / "current").resolve() == prior
    assert (prior / "runtime/host.json").read_bytes() == legacy_runtime
    assert active_runtime.read_bytes() == legacy_runtime
    assert {path: path.read_bytes() for path in old_units} == old_units
    assert not [args for args in calls if args[:1] == ["systemctl"]]

    success_output = io.StringIO()
    assert (
        remote.serve(
            bootstrap_runtime,
            input_stream=io.StringIO(request),
            output_stream=success_output,
        )
        == 0
    )
    response = json.loads(success_output.getvalue())
    assert response["result"]["release"] == bundle.manifest["id"]
    assert (root / "current").resolve().name == bundle.manifest["id"]
    assert runtime_data(load(root / "current/runtime")) == runtime_data(config)
    assert runtime_data(load(config.host.config_dir)) == runtime_data(config)
    assert all(
        (deployment.UNIT_DIR / name).read_text() == (deployment.SYSTEMD_SOURCE / name).read_text()
        for name in deployment.UNIT_NAMES
    )
    assert [args for args in calls if args[:1] == ["systemctl"]] == [["systemctl", "daemon-reload"]]


def test_default_rollback_restores_release_and_never_changes_data_paths(tmp_path, monkeypatch):
    config = _config(tmp_path, local_data=True)
    bundle = _bundle(config)
    root = _root(tmp_path, monkeypatch)
    selector = "postgres/example-prod-01"
    prior_manifest = deepcopy(bundle.manifest)
    prior_manifest["databases"][selector]["service_hash"] = "3" * 64
    prior = _write_release(
        config,
        bundle,
        root,
        "release-prior",
        manifest=prior_manifest,
    )
    current = _write_release(config, bundle, root, "release-current")
    _mark_active(config, _manifest(prior), None)
    _mark_active(config, _manifest(current), prior.name)
    _point(root, current)
    markers = {}
    for instance in config.instances:
        marker = instance.data / "marker"
        write_text(marker, f"unchanged:{instance.selector}\n")
        markers[marker] = (marker.stat().st_ino, marker.read_bytes())
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(deployment, "run", fake_run)
    monkeypatch.setattr(deployment, "health", lambda *args, **kwargs: None)

    plan = deployment.rollback_plan(config, None)
    result = deployment.rollback(
        config,
        {"release": plan["release"], "expected": plan["active"]},
    )

    assert plan["release"] == prior.name
    assert plan["changes"] == [
        {
            "action": "update",
            "selector": selector,
            "from_image": bundle.manifest["databases"][selector]["image"],
            "to_image": prior_manifest["databases"][selector]["image"],
            "from_major": 16,
            "to_major": 16,
        }
    ]
    assert result == {"release": prior.name, "previous": current.name, "affected": [selector]}
    assert (root / "current").resolve() == prior
    assert any(str(prior) in " ".join(args) and args[-2:] == ["up", "-d"] for args in calls)
    assert {marker: (marker.stat().st_ino, marker.read_bytes()) for marker in markers} == markers
    assert all(path.exists() for path in markers)
    rows = {row["id"]: row for row in deployment.history(config)["releases"]}
    assert rows[prior.name]["active"] is True
    assert rows[prior.name]["status"] == "active"
    assert rows[current.name]["status"] == "rolled_back"


def test_rollback_health_failure_recovers_current_release_without_activation(tmp_path, monkeypatch):
    config = _config(tmp_path)
    bundle = _bundle(config)
    root = _root(tmp_path, monkeypatch)
    selector = "postgres/example-prod-01"
    prior_manifest = deepcopy(bundle.manifest)
    prior_manifest["databases"][selector]["service_hash"] = "8" * 64
    prior = _write_release(
        config,
        bundle,
        root,
        "release-prior",
        manifest=prior_manifest,
    )
    current = _write_release(config, bundle, root, "release-current")
    _mark_active(config, _manifest(prior), None)
    _mark_active(config, _manifest(current), prior.name)
    _point(root, current)
    calls = []
    health_calls = 0

    def fake_run(args, **kwargs):
        calls.append(args)
        return Result(tuple(args), 0, "", "")

    def health(current_config, instance, **kwargs):
        nonlocal health_calls
        health_calls += 1
        if health_calls == 1:
            raise DeploymentError("rollback target is unhealthy")

    monkeypatch.setattr(deployment, "run", fake_run)
    monkeypatch.setattr(deployment, "health", health)

    with pytest.raises(DeploymentError, match="current services were restored"):
        deployment.rollback(
            config,
            {"release": prior.name, "expected": current.name},
        )

    up = [args for args in calls if args[-2:] == ["up", "-d"]]
    assert str(prior) in " ".join(up[0])
    assert str(current) in " ".join(up[1])
    assert (root / "current").resolve() == current
    audit = _audit(config, prior.name)
    assert audit["status"] == "superseded"
    assert audit["outcome"] == "failed"
    assert audit["events"][-1]["recovered"] is True


def test_postgres_major_rollback_is_blocked_before_any_subprocess(tmp_path, monkeypatch):
    config = _config(tmp_path, local_data=True)
    bundle = _bundle(config)
    root = _root(tmp_path, monkeypatch)
    selector = "postgres/example-prod-01"
    postgres_data = config.select(selector).data
    write_text(postgres_data / "PG_VERSION", "17\n")
    prior_manifest = deepcopy(bundle.manifest)
    prior_manifest["databases"][selector]["major"] = 16
    current_manifest = deepcopy(bundle.manifest)
    prior = _write_release(
        config,
        bundle,
        root,
        "release-prior",
        manifest=prior_manifest,
    )
    current = _write_release(
        config,
        bundle,
        root,
        "release-current",
        manifest=current_manifest,
    )
    _mark_active(config, _manifest(prior), None)
    _mark_active(config, _manifest(current), prior.name)
    _point(root, current)
    monkeypatch.setattr(
        deployment,
        "run",
        lambda *args, **kwargs: pytest.fail("compatibility must run before stopping services"),
    )

    with pytest.raises(DeploymentError, match="blocked before stopping.*restore workflow"):
        deployment.rollback_plan(config, prior.name)

    assert (root / "current").resolve() == current


def test_engine_type_rollback_is_blocked_before_any_subprocess(tmp_path, monkeypatch):
    current_config = _config(tmp_path)
    redis = current_config.select("redis/cache-dev-01")
    dragonfly = replace(
        redis,
        engine="dragonfly",
        image=current_config.host.images["dragonfly"],
        settings={"threads": 1, "maxmemory": "256mb", "cache": False},
    )
    target_config = Config(
        current_config.host,
        tuple(dragonfly if item is redis else item for item in current_config.instances),
    )
    current_bundle = _bundle(current_config)
    target_bundle = _bundle(target_config)
    root = _root(tmp_path, monkeypatch)
    prior = _write_release(target_config, target_bundle, root, "release-prior")
    current = _write_release(current_config, current_bundle, root, "release-current")
    _mark_active(current_config, _manifest(prior), None)
    _mark_active(current_config, _manifest(current), prior.name)
    _point(root, current)
    monkeypatch.setattr(
        deployment,
        "run",
        lambda *args, **kwargs: pytest.fail("compatibility must run before stopping services"),
    )

    with pytest.raises(DeploymentError, match="engine type change.*restore workflow"):
        deployment.rollback_plan(current_config, prior.name)


def test_public_release_history_and_confirmed_rollback_use_narrow_protocol(
    tmp_path, monkeypatch, capsys
):
    config = _config(tmp_path)
    plan = {
        "version": deployment.ROLLBACK_VERSION,
        "host": config.host.id,
        "active": "release-current",
        "release": "release-prior",
        "activate": True,
        "changes": [
            {
                "action": "update",
                "selector": "postgres/example-prod-01",
                "from_image": "postgres:17@sha256:" + "1" * 64,
                "to_image": "postgres:17@sha256:" + "2" * 64,
                "from_major": 17,
                "to_major": 17,
            }
        ],
    }
    history = {
        "version": deployment.HISTORY_VERSION,
        "active": "release-current",
        "releases": [_history_row("release-current")],
    }
    calls = []

    def call(current, operation, selector, payload=None, **kwargs):
        calls.append((operation, selector, payload, kwargs))
        if operation == "releases":
            return history
        if operation == "rollback_plan":
            assert payload == {"release": None}
            return plan
        return {
            "release": plan["release"],
            "previous": plan["active"],
            "affected": [plan["changes"][0]["selector"]],
        }

    monkeypatch.setattr(controller, "load", lambda path: config)
    monkeypatch.setattr(controller.remote, "call", call)

    assert controller.main(["--config", "ignored", "releases"]) == 0
    assert controller.main(["--config", "ignored", "rollback", "--yes"]) == 0

    output = capsys.readouterr().out
    assert "release-current" in output
    assert "release-prior" in output
    assert [item[0] for item in calls] == ["releases", "rollback_plan", "rollback"]
    assert calls[-1][2] == {"release": "release-prior", "expected": "release-current"}


def _config(tmp_path, *, local_data=False):
    source = load(FIXTURE)
    data_root = tmp_path / "data" if local_data else source.host.data_root
    host = replace(
        source.host,
        data_root=data_root,
        config_dir=tmp_path / "etc",
        state_dir=tmp_path / "state",
        backup_dir=tmp_path / "backups",
        lock_dir=tmp_path / "locks",
    )
    instances = source.instances
    if local_data:
        instances = tuple(
            replace(item, data=data_root / item.group / item.id / "data") for item in instances
        )
    return Config(host, instances)


def _bundle(config):
    return deployment.build(config, load_lock(FIXTURE / "host.lock.json"), SOURCE_HASH)


def _root(tmp_path, monkeypatch):
    root = tmp_path / "opt"
    monkeypatch.setattr(deployment, "ROOT", root)
    monkeypatch.setattr(deployment, "UNIT_DIR", tmp_path / "systemd")
    return root


def _write_release(config, bundle, root, release_id, *, manifest=None):
    release = root / "releases" / release_id
    render_runtime(config, release / "runtime")
    write_lock(release / "host.lock.json", bundle.lock)
    for name, text in bundle.files.items():
        write_text(release / name, text, mode=0o640)
    data = deepcopy(manifest or bundle.manifest)
    data["id"] = release_id
    for item in data["databases"].values():
        item["labels"] = {deployment.CONTRACT_LABEL: item["service_hash"]}
    traefik = data["infrastructure"]["traefik"]
    traefik["labels"] = {deployment.CONTRACT_LABEL: traefik["service_hash"]}
    write_json(release / deployment.MANIFEST, data, mode=0o644)
    return release


def _point(root, release):
    current = root / "current"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.unlink(missing_ok=True)
    current.symlink_to(release)


def _mark_active(config, manifest, predecessor):
    deployment._record_stage(
        config,
        {
            "manifest": manifest,
            "expected": predecessor,
            "plan": {"actions": [], "blocked": []},
        },
    )
    deployment._record_activation(
        config,
        manifest["id"],
        predecessor,
        operation_name="apply",
    )


def _manifest(release):
    return json.loads((release / deployment.MANIFEST).read_text())


def _audit(config, release_id):
    return json.loads((config.host.state_dir / f"releases/{release_id}.json").read_text())


def _plan(*selectors, kind):
    return {
        "actions": [
            {"kind": kind, "selector": selector, "reason": "test release change"}
            for selector in selectors
        ],
        "blocked": [],
    }


def _healthy_run(config):
    bundle = _bundle(config)

    def fake_run(args, **kwargs):
        if args[:3] == ["docker", "network", "inspect"]:
            return Result(tuple(args), 0, "[]", "")
        if args[:2] == ["docker", "inspect"]:
            return _inspect_result(args, bundle.manifest)
        return Result(tuple(args), 0, "", "")

    return fake_run


def _inspect_result(args, manifest):
    container = args[2]
    if container == deployment.TRAEFIK_CONTAINER:
        expected = manifest["infrastructure"]["traefik"]
        data = [
            {
                "State": {"Running": True, "Health": {"Status": "healthy"}},
                "Config": {"Image": expected["image"], "Labels": expected["labels"]},
            }
        ]
        return Result(tuple(args), 0, json.dumps(data), "")
    for database in manifest["databases"].values():
        for expected in database["services"].values():
            if expected["container"] != container:
                continue
            state = {"Running": True}
            if expected["health"] == "docker":
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


def _other_digest(image, character):
    return image.split("@", 1)[0] + "@sha256:" + character * 64


def _history_row(release_id):
    return {
        "id": release_id,
        "active": True,
        "status": "active",
        "outcome": "active",
        "severity": "info",
        "successful": True,
        "created_at": "2026-07-25T10:00:00+00:00",
        "updated_at": "2026-07-25T10:01:00+00:00",
        "finished_at": "2026-07-25T10:01:00+00:00",
        "activated_at": "2026-07-25T10:01:00+00:00",
        "predecessor": None,
        "source_hash": "1" * 64,
        "lock_hash": "2" * 64,
        "controller_version": "0.1.0",
        "runtime_version": "0.1.0",
        "engine_majors": {},
        "plan": {"actions": [], "blocked": []},
        "recovery_steps": [],
    }

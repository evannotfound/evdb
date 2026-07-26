import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from evanovation_db import compose, host, status
from evanovation_db.config import (
    Paths,
    dump,
    load_state,
    resolve_state,
    write_state,
)
from evanovation_db.errors import HostError
from evanovation_db.run import Result

DIGEST = "sha256:" + "a" * 64


def _values(tmp_path):
    dns = tmp_path / "dns.env"
    dns.write_text("TESTDNS_TOKEN=private\n")
    rclone = tmp_path / "rclone.conf"
    rclone.write_text("[test]\ntype = local\n")
    return {
        "host_id": "test-01",
        "domain": "storage.example.com",
        "data_root": str(tmp_path / "data"),
        "acme_email": "ops@example.com",
        "dns_provider": "testdns",
        "postgres_repo": str(tmp_path / "postgres-repo"),
        "kv_repo": str(tmp_path / "kv-repo"),
        "dns_env_file": str(dns),
        "rclone_config": str(rclone),
    }


def _setup_mocks(monkeypatch):
    calls = []
    timer_state = {}
    monkeypatch.setattr(host, "prerequisites", lambda: [])
    monkeypatch.setattr(host, "_ports_available", lambda: True)
    monkeypatch.setattr(compose, "validate", lambda *args, **kwargs: None)
    monkeypatch.setattr(compose, "ensure_network", lambda *args, **kwargs: None)

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[0] == "systemd-escape":
            return Result(tuple(args), 0, _escaped_timer(args[-1]) + "\n", "")
        if args[0] == "systemctl":
            return _systemctl(args, timer_state)
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(host, "run", fake_run)
    monkeypatch.setattr(
        host,
        "check",
        lambda config: {"host": {"infrastructure": {"healthy": True}}},
    )
    return calls, timer_state


def test_setup_rerun_preserves_configured_state_and_mutable_files(config, tmp_path, monkeypatch):
    calls, timer_state = _setup_mocks(monkeypatch)
    units = tmp_path / "systemd"
    values = _values(tmp_path)
    paths = config.paths
    paths.source.parent.mkdir(parents=True)
    paths.source.write_text(dump(config))
    initial = resolve_state(config, resolver=lambda source: DIGEST)
    database = next(item for item in config.databases if item.durable)
    role = replace(
        initial.roles[database.identity],
        compose_hash="installed-hash",
        installed=True,
        operations={"backup": {"time": "2026-07-25T00:00:00Z"}},
    )
    preserved = replace(
        initial,
        roles={**initial.roles, database.identity: role},
        tool_version="1.0.0",
    )
    write_state(config, preserved)

    result = host.setup(
        paths.source,
        values,
        yes=True,
        paths=paths,
        unit_dir=units,
        resolver=lambda source: pytest.fail(f"unexpected image resolution: {source}"),
    )
    rclone = paths.rclone / "rclone.conf"
    rclone.write_text("refreshed oauth state\n")
    timer_state["evdb-status.timer"] = (False, False)
    before_timers = dict(timer_state)

    second = host.setup(
        paths.source,
        values,
        yes=True,
        paths=paths,
        unit_dir=units,
        resolver=lambda source: pytest.fail(f"unexpected image resolution: {source}"),
    )

    assert result == second == "Host setup complete"
    assert load_state(config) == preserved
    assert paths.source.is_file()
    assert paths.machine_state.stat().st_mode & 0o777 == 0o600
    assert (paths.secrets / "restic-password").stat().st_mode & 0o777 == 0o600
    assert (paths.traefik / "dns.env").stat().st_mode & 0o777 == 0o600
    assert (paths.traefik / "acme/acme.json").stat().st_mode & 0o777 == 0o600
    assert paths.tool.stat().st_mode & 0o777 == 0o755
    assert (paths.tool / "versions").stat().st_mode & 0o777 == 0o755
    assert rclone.read_text() == "refreshed oauth state\n"
    enables = [args for args in calls if args[:3] == ["systemctl", "enable", "--now"]]
    assert len(enables) == 1
    escaped = _escaped_timer(database.identity)
    assert escaped == "evdb-backup@app\\x2dtest\\x2d01-postgres.timer"
    assert ["systemd-escape", "--template=evdb-backup@.timer", database.identity] in calls
    assert escaped in enables[0]
    assert timer_state == before_timers
    marker = units / f"{escaped}.d" / host.TIMER_MARKER
    assert database.identity in marker.read_text()
    expected_paths = (
        "[Service]\nReadWritePaths=\n"
        f"ReadWritePaths=/var/lib/evdb {json.dumps(str(config.host.data_root))}\n"
    )
    assert (units / "evdb-backup@.service.d" / host.DATA_DROPIN).read_text() == expected_paths
    assert (units / "evdb-backup-test.service.d" / host.DATA_DROPIN).read_text() == expected_paths


def test_setup_validates_private_traefik_before_canonical_install(config, tmp_path, monkeypatch):
    paths = config.paths
    paths.source.parent.mkdir(parents=True)
    paths.source.write_text(dump(config))
    write_state(config, resolve_state(config, resolver=lambda source: DIGEST))
    paths.traefik.mkdir(parents=True)
    canonical = paths.traefik / "compose.yaml"
    canonical.write_text("prior compose\n")
    canonical.chmod(0o600)
    prior = (canonical.read_bytes(), canonical.stat().st_mode & 0o777)
    _setup_mocks(monkeypatch)

    def reject_candidate(path, project, **kwargs):
        assert Path(path) != canonical
        assert Path(path).parent.stat().st_mode & 0o777 == 0o700
        raise HostError("candidate invalid")

    monkeypatch.setattr(compose, "validate", reject_candidate)

    with pytest.raises(HostError, match="candidate invalid"):
        host.setup(
            paths.source,
            _values(tmp_path),
            yes=True,
            paths=paths,
            unit_dir=tmp_path / "systemd",
            resolver=lambda source: DIGEST,
        )

    assert (canonical.read_bytes(), canonical.stat().st_mode & 0o777) == prior


def test_setup_failure_restores_prior_files_metadata_and_timer_state(config, tmp_path, monkeypatch):
    paths = config.paths
    paths.source.parent.mkdir(parents=True)
    paths.source.write_text(dump(config))
    write_state(config, resolve_state(config, resolver=lambda source: DIGEST))
    paths.traefik.mkdir(parents=True)
    canonical = paths.traefik / "compose.yaml"
    canonical.write_text("prior compose\n")
    canonical.chmod(0o600)
    prior_compose = (canonical.read_bytes(), canonical.stat().st_mode & 0o777)
    prior_state = paths.machine_state.read_bytes()
    calls, timer_state = _setup_mocks(monkeypatch)
    timer_state["evdb-status.timer"] = (False, True)
    before_timers = {name: timer_state.get(name, (False, False)) for name in host.DEFAULT_TIMERS}
    monkeypatch.setattr(
        host,
        "check",
        lambda config: {"host": {"infrastructure": {"healthy": False}}},
    )
    units = tmp_path / "systemd"

    with pytest.raises(HostError, match="prior files restored"):
        host.setup(
            paths.source,
            _values(tmp_path),
            yes=True,
            paths=paths,
            unit_dir=units,
            resolver=lambda source: DIGEST,
        )

    assert (canonical.read_bytes(), canonical.stat().st_mode & 0o777) == prior_compose
    assert paths.machine_state.read_bytes() == prior_state
    assert {name: timer_state[name] for name in host.DEFAULT_TIMERS} == before_timers
    assert not any(units.glob("*.service"))
    assert not any(units.glob("*.timer"))
    assert not (units / "evdb-backup@.service.d" / host.DATA_DROPIN).exists()
    assert any(args[:2] == ["systemctl", "disable"] for args in calls)


def test_setup_checks_prerequisites_ports_and_writable_roots_before_mutation(
    paths, tmp_path, monkeypatch
):
    monkeypatch.setattr(host, "prerequisites", lambda: ["restic"])

    with pytest.raises(HostError, match="restic"):
        host.setup(paths.source, _values(tmp_path), yes=True, paths=paths)
    assert not paths.config.exists()

    monkeypatch.setattr(host, "prerequisites", lambda: [])
    monkeypatch.setattr(host, "_writable", lambda path: path != paths.state)
    with pytest.raises(HostError, match="state"):
        host.setup(paths.source, _values(tmp_path), yes=True, paths=paths)
    assert not paths.config.exists()

    monkeypatch.setattr(host, "_writable", lambda path: True)
    monkeypatch.setattr(host, "_ports_available", lambda: False)
    with pytest.raises(HostError, match="occupied"):
        host.setup(paths.source, _values(tmp_path), yes=True, paths=paths)
    assert not paths.config.exists()


def test_prerequisites_report_incompatible_python_without_installing(monkeypatch):
    calls = []
    monkeypatch.setattr(host.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(host.sys, "version_info", (3, 9))
    monkeypatch.setattr(
        host,
        "run",
        lambda args, **kwargs: calls.append(args) or Result(tuple(args), 0, "", ""),
    )

    assert host.prerequisites() == ["Python 3.10 or newer"]
    assert calls == [["docker", "compose", "version"]]


def test_setup_refuses_production_migration(paths, tmp_path):
    values = {**_values(tmp_path), "host_id": "montreal-01"}

    with pytest.raises(HostError, match="separate change"):
        host.setup(paths.source, values, yes=True, paths=paths)


def test_canonical_account_and_ownership_commands(config, monkeypatch):
    canonical = replace(config, paths=Paths())
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        code = 2 if args[:2] in (["getent", "group"], ["getent", "passwd"]) else 0
        return Result(tuple(args), code, "", "")

    monkeypatch.setattr(host, "run", fake_run)

    host._account(canonical.paths)
    host._ownership(canonical)

    assert ["groupadd", "--system", "evdb"] in calls
    useradd = next(args for args in calls if args[0] == "useradd")
    assert useradd == [
        "useradd",
        "--system",
        "--gid",
        "evdb",
        "--home-dir",
        "/var/lib/evdb",
        "--shell",
        "/usr/sbin/nologin",
        "evdb",
    ]
    assert ["usermod", "--append", "--groups", "docker", "evdb"] in calls
    assert ["chown", "root:evdb", "/etc/evdb", "/etc/evdb/host.yml"] in calls
    service_chown = next(args for args in calls if args[:3] == ["chown", "-R", "evdb:evdb"])
    assert service_chown[3:] == [
        "/etc/evdb/projects",
        "/etc/evdb/traefik",
        "/etc/evdb/secrets",
        "/var/lib/evdb",
    ]
    assert ["chown", "evdb:evdb", str(config.host.data_root)] in calls
    assert "/etc/evdb/host.yml" not in service_chown
    assert ["chmod", "0750", "/etc/evdb"] in calls
    assert ["chmod", "0640", "/etc/evdb/host.yml"] in calls
    assert ["chmod", "0755", "/opt/evdb", "/opt/evdb/versions"] in calls


def _prepare_update(config, units):
    config.paths.source.parent.mkdir(parents=True, exist_ok=True)
    config.paths.source.write_text(dump(config))
    state = resolve_state(config, resolver=lambda source: DIGEST)
    write_state(config, replace(state, tool_version="1.0.0"))
    config.paths.traefik.mkdir(parents=True, exist_ok=True)
    (config.paths.traefik / "compose.yaml").write_text("name: evdb-traefik\n")
    host._install_units(units)
    old = config.paths.tool / "versions/1.0.0"
    (old / "bin").mkdir(parents=True)
    (old / "bin/evdb").write_text("old")
    (old / "bin/evdb").chmod(0o755)
    (config.paths.tool / "current").symlink_to(old)
    return old


def _install_candidate(config, version="1.1.0", marker="# candidate package", extra_timer=None):
    candidate = config.paths.tool / f"versions/{version}"
    (candidate / "bin").mkdir(parents=True, exist_ok=True)
    (candidate / "bin/evdb").write_text("candidate")
    (candidate / "bin/evdb").chmod(0o755)
    packaged = candidate / "tools/evanovation-db/lib/python3.10/site-packages/evanovation_db/units"
    packaged.mkdir(parents=True)
    for source in host._units():
        text = source.read_text()
        if source.name == "evdb-status.service":
            text += f"\n{marker}\n"
        (packaged / source.name).write_text(text)
    if extra_timer is not None:
        (packaged / extra_timer).write_text(
            "[Unit]\nDescription=New global timer\n\n"
            "[Timer]\nOnCalendar=daily\nPersistent=true\n\n"
            "[Install]\nWantedBy=timers.target\n"
        )
    return candidate


def _status(config, *, healthy=True, version=status.VERSION):
    return json.dumps(
        {
            "version": version,
            "healthy": healthy,
            "host": {"id": config.host.id},
            "databases": {},
            "errors": [],
        }
    )


def _escaped_timer(identity):
    escaped = []
    valid = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_."
    for value in identity:
        if value == "/":
            escaped.append("-")
        elif value == "-" or value == "\\" or value not in valid:
            escaped.append(f"\\x{ord(value):02x}")
        else:
            escaped.append(value)
    return f"evdb-backup@{''.join(escaped)}.timer"


def _systemctl(args, timer_state):
    if args[1] == "is-enabled":
        enabled = timer_state.get(args[2], (False, False))[0]
        output = "enabled\n" if enabled else "disabled\n"
        return Result(tuple(args), 0 if enabled else 1, output, "")
    if args[1] == "is-active":
        active = timer_state.get(args[2], (False, False))[1]
        return Result(tuple(args), 0 if active else 3, "active\n" if active else "inactive\n", "")
    if args[1] in {"enable", "disable"}:
        names = args[3:] if args[2] == "--now" else args[2:]
        for name in names:
            current = timer_state.get(name, (False, False))
            timer_state[name] = (args[1] == "enable", True if args[2] == "--now" else current[1])
    if args[1] in {"start", "stop"}:
        name = args[2]
        timer_state[name] = (timer_state.get(name, (False, False))[0], args[1] == "start")
    return Result(tuple(args), 0, "", "")


def test_exact_update_uses_candidate_units_preserves_timers_and_cleans_versions(
    config, tmp_path, monkeypatch
):
    units = tmp_path / "systemd"
    old = _prepare_update(config, units)
    stale = config.paths.tool / "versions/0.9.0"
    (stale / "bin").mkdir(parents=True)
    (stale / "bin/evdb").write_text("stale")
    (stale / "bin/evdb").chmod(0o755)
    calls = []
    timer_state = {
        name: (index % 2 == 0, index % 3 == 0)
        for index, name in enumerate(
            sorted(path.name for path in host._units() if path.suffix == ".timer")
        )
    }
    before_timers = dict(timer_state)

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[:3] == ["uv", "tool", "install"]:
            _install_candidate(config)
        if args and args[0].endswith("/bin/evdb"):
            return Result(tuple(args), 0, _status(config), "")
        if args and args[0] == "systemctl":
            return _systemctl(args, timer_state)
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(host, "run", fake_run)

    result = host.update(config, "1.1.0", yes=True, unit_dir=units)

    candidate = config.paths.tool / "versions/1.1.0"
    assert result == "Updated evdb to 1.1.0"
    assert (config.paths.tool / "current").resolve() == candidate
    assert (config.paths.tool / "previous").resolve() == old
    assert load_state(config).tool_version == "1.1.0"
    assert "# candidate package" in (units / "evdb-status.service").read_text()
    assert timer_state == before_timers
    assert not stale.exists()
    assert sorted(path.name for path in (config.paths.tool / "versions").iterdir()) == [
        "1.0.0",
        "1.1.0",
    ]
    uv = next(item for item in calls if item[0][:3] == ["uv", "tool", "install"])
    assert uv[0][-1] == "evanovation-db==1.1.0"
    assert uv[1]["env"]["UV_TOOL_BIN_DIR"] == str(candidate / "bin")
    assert not any(args[:2] == ["docker", "compose"] for args, _ in calls)
    assert not any(
        args[:2]
        in (
            ["systemctl", "enable"],
            ["systemctl", "disable"],
            ["systemctl", "start"],
            ["systemctl", "stop"],
        )
        for args, _ in calls
    )


def test_update_enables_only_new_global_timer(config, tmp_path, monkeypatch):
    units = tmp_path / "systemd"
    _prepare_update(config, units)
    added = "evdb-new-maintenance.timer"
    timer_state = {
        name: (index % 2 == 0, index % 3 == 0) for index, name in enumerate(host.DEFAULT_TIMERS)
    }
    before = dict(timer_state)
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["uv", "tool", "install"]:
            _install_candidate(config, extra_timer=added)
        if args and args[0].endswith("/bin/evdb"):
            return Result(tuple(args), 0, _status(config), "")
        if args and args[0] == "systemctl":
            return _systemctl(args, timer_state)
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(host, "run", fake_run)

    host.update(config, "1.1.0", yes=True, unit_dir=units)

    assert timer_state[added] == (True, True)
    assert {name: timer_state[name] for name in before} == before
    enables = [args for args in calls if args[:3] == ["systemctl", "enable", "--now"]]
    assert enables == [["systemctl", "enable", "--now", added]]


def test_update_preserves_existing_backup_timer_state(config, tmp_path, monkeypatch):
    units = tmp_path / "systemd"
    _prepare_update(config, units)
    database = next(item for item in config.databases if item.durable)
    state = load_state(config)
    write_state(
        config,
        replace(
            state,
            roles={
                **state.roles,
                database.identity: replace(state.roles[database.identity], installed=True),
            },
        ),
    )
    database.compose.parent.mkdir(parents=True)
    database.compose.write_text("name: installed-database\n")
    timer = _escaped_timer(database.identity)
    marker = units / f"{timer}.d" / host.TIMER_MARKER
    marker.parent.mkdir(parents=True)
    marker.write_text(f"[Unit]\nDescription=Daily backup for {database.identity}\n")
    timer_state = {name: (True, True) for name in host.DEFAULT_TIMERS}
    timer_state[timer] = (False, True)
    before = dict(timer_state)
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[0] == "systemd-escape":
            return Result(tuple(args), 0, timer + "\n", "")
        if args[:3] == ["uv", "tool", "install"]:
            _install_candidate(config)
        if args and args[0].endswith("/bin/evdb"):
            return Result(tuple(args), 0, _status(config), "")
        if args and args[0] == "systemctl":
            return _systemctl(args, timer_state)
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(host, "run", fake_run)

    host.update(config, "1.1.0", yes=True, unit_dir=units)

    assert timer_state == before
    assert not any(args[:3] == ["systemctl", "enable", "--now"] for args in calls)


def test_update_rejects_invalid_candidate_version_directory_before_activation(
    config, tmp_path, monkeypatch
):
    units = tmp_path / "systemd"
    old = _prepare_update(config, units)
    checks = []

    def fake_run(args, **kwargs):
        if args[:3] == ["uv", "tool", "install"]:
            candidate = _install_candidate(config)
            (candidate / "bin/evdb").chmod(0o644)
        if args and args[0].endswith("/bin/evdb"):
            checks.append(args)
        return Result(tuple(args), 0, _status(config), "")

    monkeypatch.setattr(host, "run", fake_run)

    with pytest.raises(HostError, match="no executable evdb command"):
        host.update(config, "1.1.0", yes=True, unit_dir=units)

    assert not checks
    assert (config.paths.tool / "current").resolve() == old
    assert not (config.paths.tool / "versions/1.1.0").exists()


def test_update_reports_deferred_inactive_version_cleanup(config, tmp_path, monkeypatch):
    units = tmp_path / "systemd"
    old = _prepare_update(config, units)
    stale = config.paths.tool / "versions/0.9.0"
    (stale / "bin").mkdir(parents=True)
    (stale / "bin/evdb").write_text("stale")
    (stale / "bin/evdb").chmod(0o755)
    real_rmtree = shutil.rmtree

    def fake_run(args, **kwargs):
        if args[:3] == ["uv", "tool", "install"]:
            _install_candidate(config)
        if args and args[0].endswith("/bin/evdb"):
            return Result(tuple(args), 0, _status(config), "")
        if args and args[0] == "systemctl":
            return _systemctl(args, {})
        return Result(tuple(args), 0, "", "")

    def fail_cleanup(path, *args, **kwargs):
        if Path(path).name.startswith(".cleanup-"):
            raise OSError("busy")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(host, "run", fake_run)
    monkeypatch.setattr(host.shutil, "rmtree", fail_cleanup)

    result = host.update(config, "1.1.0", yes=True, unit_dir=units)

    assert "cleanup pending" in result
    assert (config.paths.tool / "current").resolve().name == "1.1.0"
    assert (config.paths.tool / "previous").resolve() == old
    assert not stale.exists()
    assert any(path.name.startswith(".cleanup-") for path in stale.parent.iterdir())


def test_exact_version_bootstrap_builds_managed_layout(config, tmp_path, monkeypatch):
    tool = tmp_path / "opt/evdb"
    target = tool / "versions/1.2.3"
    stable = tmp_path / "usr/local/bin/evdb"
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        (target / "bin").mkdir(parents=True, exist_ok=True)
        (target / "bin/evdb").write_text("candidate")
        (target / "bin/evdb").chmod(0o755)
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(host, "run", fake_run)
    monkeypatch.setattr(host, "_stable_path", lambda current: stable)

    host._bootstrap_version(target, "1.2.3", config)
    host._link(tool / "current", target)
    host._stable_command(tool)

    assert calls[0][0] == ["uv", "tool", "install", "evanovation-db==1.2.3"]
    assert calls[0][1]["env"] == {
        "UV_TOOL_DIR": str(target / "tools"),
        "UV_TOOL_BIN_DIR": str(target / "bin"),
    }
    assert (tool / "current").resolve() == target
    assert stable.resolve() == target / "bin/evdb"


@pytest.mark.parametrize(
    ("code", "version", "message"),
    (
        (2, status.VERSION, "exit code 2"),
        (0, status.VERSION + 1, "structurally incompatible"),
    ),
)
def test_update_rejects_nonzero_or_structurally_incompatible_candidate(
    config, tmp_path, monkeypatch, code, version, message
):
    units = tmp_path / "systemd"
    old = _prepare_update(config, units)
    old_unit = (units / "evdb-status.service").read_bytes()

    def fake_run(args, **kwargs):
        if args[:3] == ["uv", "tool", "install"]:
            _install_candidate(config)
        if args and args[0].endswith("/bin/evdb"):
            return Result(tuple(args), code, _status(config, version=version), "")
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(host, "run", fake_run)

    with pytest.raises(HostError, match=message):
        host.update(config, "1.1.0", yes=True, unit_dir=units)

    assert (config.paths.tool / "current").resolve() == old
    assert not (config.paths.tool / "previous").exists()
    assert not (config.paths.tool / "versions/1.1.0").exists()
    assert (units / "evdb-status.service").read_bytes() == old_unit
    assert load_state(config).tool_version == "1.0.0"


def test_update_accepts_unhealthy_structural_preflight_then_requires_healthy_postcheck(
    config, tmp_path, monkeypatch
):
    units = tmp_path / "systemd"
    old = _prepare_update(config, units)
    checks = 0

    def fake_run(args, **kwargs):
        nonlocal checks
        if args[:3] == ["uv", "tool", "install"]:
            _install_candidate(config)
        if args and args[0].endswith("/bin/evdb"):
            checks += 1
            if checks == 1:
                return Result(tuple(args), 1, _status(config, healthy=False), "")
            return Result(tuple(args), 0, _status(config), "")
        if args and args[0] == "systemctl":
            return _systemctl(args, {})
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(host, "run", fake_run)

    assert host.update(config, "1.1.0", yes=True, unit_dir=units) == "Updated evdb to 1.1.0"
    assert checks == 2
    assert (config.paths.tool / "previous").resolve() == old


def test_update_rolls_back_when_post_activation_status_is_unhealthy(config, tmp_path, monkeypatch):
    units = tmp_path / "systemd"
    old = _prepare_update(config, units)
    checks = 0

    def fake_run(args, **kwargs):
        nonlocal checks
        if args[:3] == ["uv", "tool", "install"]:
            _install_candidate(config)
        if args and args[0].endswith("/bin/evdb"):
            checks += 1
            result = _status(config, healthy=checks == 1)
            return Result(tuple(args), 0 if checks == 1 else 1, result, "")
        if args and args[0] == "systemctl":
            return _systemctl(args, {})
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(host, "run", fake_run)

    with pytest.raises(HostError, match="prior version restored"):
        host.update(config, "1.1.0", yes=True, unit_dir=units)

    assert (config.paths.tool / "current").resolve() == old
    assert not (config.paths.tool / "versions/1.1.0").exists()


def test_update_rejects_and_restores_candidate_compatibility_writes(config, tmp_path, monkeypatch):
    units = tmp_path / "systemd"
    old = _prepare_update(config, units)
    old_state = config.paths.machine_state.read_bytes()

    def fake_run(args, **kwargs):
        if args[:3] == ["uv", "tool", "install"]:
            _install_candidate(config)
        if args and args[0].endswith("/bin/evdb"):
            config.paths.machine_state.write_text("{}\n")
            return Result(tuple(args), 0, _status(config), "")
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(host, "run", fake_run)

    with pytest.raises(HostError, match="modified managed host files"):
        host.update(config, "1.1.0", yes=True, unit_dir=units)

    assert (config.paths.tool / "current").resolve() == old
    assert config.paths.machine_state.read_bytes() == old_state
    assert not (config.paths.tool / "versions/1.1.0").exists()


def test_failed_update_restores_links_state_units_and_timer_state(config, tmp_path, monkeypatch):
    units = tmp_path / "systemd"
    old = _prepare_update(config, units)
    prior = config.paths.tool / "versions/0.9.0"
    (prior / "bin").mkdir(parents=True)
    (prior / "bin/evdb").write_text("prior")
    (prior / "bin/evdb").chmod(0o755)
    (config.paths.tool / "previous").symlink_to(prior)
    old_state = config.paths.machine_state.read_bytes()
    old_units = {path.name: (units / path.name).read_bytes() for path in host._units()}
    timer_state = {path.name: (True, False) for path in host._units() if path.suffix == ".timer"}
    timer_state["evdb-prune.timer"] = (False, True)
    before_timers = dict(timer_state)
    checks = 0

    def fake_run(args, **kwargs):
        nonlocal checks
        if args[:3] == ["uv", "tool", "install"]:
            _install_candidate(config)
        if args and args[0].endswith("/bin/evdb"):
            checks += 1
            if checks == 2:
                return Result(tuple(args), 2, "", "failed")
            return Result(tuple(args), 0, _status(config), "")
        if args and args[0] == "systemctl":
            return _systemctl(args, timer_state)
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(host, "run", fake_run)

    with pytest.raises(HostError, match="prior version restored"):
        host.update(config, "1.1.0", yes=True, unit_dir=units)

    assert (config.paths.tool / "current").resolve() == old
    assert (config.paths.tool / "previous").resolve() == prior
    assert config.paths.machine_state.read_bytes() == old_state
    assert {path.name: (units / path.name).read_bytes() for path in host._units()} == old_units
    assert timer_state == before_timers
    assert not (config.paths.tool / "versions/1.1.0").exists()


def test_update_validates_backup_records_before_install(config, tmp_path, monkeypatch):
    units = tmp_path / "systemd"
    _prepare_update(config, units)
    record = config.paths.backups / "app-prod-01/postgres/backup-1/backup.json"
    record.parent.mkdir(parents=True)
    record.write_text("not json")
    calls = []
    monkeypatch.setattr(
        host,
        "run",
        lambda args, **kwargs: calls.append(args) or Result(tuple(args), 0, "", ""),
    )

    with pytest.raises(HostError, match="invalid backup record"):
        host.update(config, "1.1.0", yes=True, unit_dir=units)

    assert not any(args[:3] == ["uv", "tool", "install"] for args in calls)
    assert record.read_text() == "not json"


def test_update_rejects_non_exact_versions_before_install(config):
    for value in ("latest", "1", "1.2", ">=1.2.3", "1.2.x", "01.2.3"):
        with pytest.raises(HostError, match="exact semantic version"):
            host.update(config, value, yes=True)

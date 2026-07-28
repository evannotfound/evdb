import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from evdb import host
from evdb.models import Paths
from evdb.run import Result


def test_init_rerun_preserves_rclone_and_enables_only_backup_timer(config, tmp_path, monkeypatch):
    calls = []
    order = []
    before = b"[remote]\ntype = local\ntoken = refreshed oauth state\n"
    config.paths.rclone.write_bytes(before)
    host._install_units(tmp_path / "systemd")
    monkeypatch.setattr(host, "prerequisites", lambda: [])
    monkeypatch.setattr(host, "_require_ports", lambda current: None)
    monkeypatch.setattr(host, "_account", lambda paths: None)
    monkeypatch.setattr(host, "_source_ownership", lambda current: order.append("source ownership"))
    monkeypatch.setattr(
        host, "_generated_ownership", lambda current: order.append("generated ownership")
    )
    monkeypatch.setattr(host, "_traefik", lambda current: order.append("traefik"))
    monkeypatch.setattr(host.docker, "ensure_network", lambda **kwargs: order.append("network"))
    monkeypatch.setattr(host.backup, "initialize", lambda current: order.append("repository"))
    monkeypatch.setattr(
        __import__("evdb.status", fromlist=["collect"]),
        "collect",
        lambda current: {
            "healthy": True,
            "host": {"id": current.host.id, "healthy": True},
        },
    )

    def run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["systemctl", "enable", "--now"]:
            order.append("timer")
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(host, "run", run)

    value = host.initialize(
        config.paths.source,
        {"rclone_config": str(tmp_path / "unused")},
        paths=config.paths,
        unit_dir=tmp_path / "systemd",
    )

    assert value["host"]["id"] == config.host.id
    assert config.paths.rclone.read_bytes() == before
    assert order == [
        "source ownership",
        "network",
        "traefik",
        "repository",
        "generated ownership",
        "timer",
    ]
    assert ["systemctl", "daemon-reload"] in calls
    assert ["systemctl", "enable", "--now", "evdb-backup.timer"] in calls
    assert not any("evdb-status" in " ".join(call) for call in calls)


def test_init_refuses_production_before_subprocesses(config, monkeypatch):
    config.paths.source.write_text(
        config.paths.source.read_text().replace("test-01", "montreal-01", 1)
    )
    calls = []
    monkeypatch.setattr(host, "run", lambda *args, **kwargs: calls.append(args))

    from evdb.errors import HostError

    try:
        host.initialize(config.paths.source, paths=config.paths)
    except HostError as exc:
        assert "separate change" in str(exc)
    else:
        raise AssertionError("production host was accepted")
    assert calls == []


def test_prerequisites_require_restic_017_without_python_or_uv(monkeypatch):
    monkeypatch.setattr(host.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []
    monkeypatch.setattr(
        host,
        "run",
        lambda args, **kwargs: calls.append(args) or Result(tuple(args), 0, "", ""),
    )
    monkeypatch.setattr(host.backup, "require_version", lambda: None)

    assert host.prerequisites() == []
    assert calls == [["docker", "compose", "version"]]
    assert "python" not in host.TOOLS
    assert "uv" not in host.TOOLS


def test_first_explicit_init_does_not_require_generic_confirmation(paths, tmp_path, monkeypatch):
    dns = tmp_path / "dns.env"
    dns.write_text("TESTDNS_TOKEN=private\n")
    rclone = tmp_path / "seed.conf"
    rclone.write_text("[local]\ntype = local\n")
    values = {
        "host_id": "new-test-01",
        "domain": "storage.example.com",
        "data_root": str(tmp_path / "data"),
        "acme_email": "ops@example.com",
        "dns_provider": "testdns",
        "repository": str(tmp_path / "repository"),
        "dns_file": str(dns),
        "rclone_config": str(rclone),
    }
    monkeypatch.setattr(host, "prerequisites", lambda: [])
    monkeypatch.setattr(host, "_require_ports", lambda current: None)
    monkeypatch.setattr(host, "_account", lambda current: None)
    monkeypatch.setattr(host, "_source_ownership", lambda current: None)
    monkeypatch.setattr(host, "_generated_ownership", lambda current: None)
    monkeypatch.setattr(host, "_traefik", lambda current: None)
    monkeypatch.setattr(host.docker, "ensure_network", lambda **kwargs: None)
    monkeypatch.setattr(host.backup, "initialize", lambda current: None)
    monkeypatch.setattr(host, "_install_units", lambda target: None)
    monkeypatch.setattr(host, "run", lambda args, **kwargs: Result(tuple(args), 0, "", ""))
    monkeypatch.setattr(
        __import__("evdb.status", fromlist=["collect"]),
        "collect",
        lambda current: {"healthy": True, "host": {"id": current.host.id, "healthy": True}},
    )

    value = host.initialize(
        paths.source,
        values,
        paths=paths,
        unit_dir=tmp_path / "systemd",
    )

    assert value["host"]["id"] == "new-test-01"
    assert paths.source.is_file()
    assert paths.secrets.stat().st_mode & 0o777 == 0o600


def test_database_traefik_publishes_only_native_ports(config, monkeypatch):
    monkeypatch.setattr(host.docker, "validate_compose", lambda *args, **kwargs: None)
    monkeypatch.setattr(host.docker, "up", lambda *args, **kwargs: None)

    host._traefik(config)

    service = yaml.safe_load((config.paths.traefik / "compose.yaml").read_text())["services"][
        "traefik"
    ]
    assert service["ports"] == ["5432:5432/tcp", "6379:6379/tcp"]
    assert not any("entrypoints.https" in item for item in service["command"])


def test_mid_init_failure_preserves_source_and_rerun_converges(paths, tmp_path, monkeypatch):
    dns = tmp_path / "dns.env"
    dns.write_text("TESTDNS_TOKEN=private\n")
    rclone = tmp_path / "seed.conf"
    rclone.write_text("[local]\ntype = local\n")
    values = {
        "host_id": "retry-test-01",
        "domain": "storage.example.com",
        "data_root": str(tmp_path / "data"),
        "acme_email": "ops@example.com",
        "dns_provider": "testdns",
        "repository": str(tmp_path / "repository"),
        "dns_file": str(dns),
        "rclone_config": str(rclone),
    }
    order = []
    attempts = iter([RuntimeError("injected network failure"), None])
    monkeypatch.setattr(host, "prerequisites", lambda: [])
    monkeypatch.setattr(host, "_require_ports", lambda current: None)
    monkeypatch.setattr(host, "_account", lambda current: None)
    monkeypatch.setattr(host, "_source_ownership", lambda current: order.append("source"))
    monkeypatch.setattr(host, "_generated_ownership", lambda current: order.append("generated"))

    def network(**kwargs):
        order.append("network")
        failure = next(attempts)
        if failure:
            raise failure

    monkeypatch.setattr(host.docker, "ensure_network", network)
    monkeypatch.setattr(host, "_traefik", lambda current: order.append("traefik"))
    monkeypatch.setattr(host.backup, "initialize", lambda current: order.append("repository"))
    monkeypatch.setattr(host, "_install_units", lambda target: None)
    monkeypatch.setattr(host, "run", lambda args, **kwargs: Result(tuple(args), 0, "", ""))
    monkeypatch.setattr(
        __import__("evdb.status", fromlist=["collect"]),
        "collect",
        lambda current: {"healthy": True, "host": {"id": current.host.id, "healthy": True}},
    )

    with pytest.raises(RuntimeError, match="injected"):
        host.initialize(paths.source, values, paths=paths, unit_dir=tmp_path / "systemd")

    assert paths.source.is_file() and paths.secrets.is_file() and paths.rclone.is_file()
    assert order == ["source", "network"]

    result = host.initialize(
        paths.source,
        {"restic_password_file": str(tmp_path / "must-not-be-read")},
        paths=paths,
        unit_dir=tmp_path / "systemd",
    )

    assert result["healthy"]
    assert order == ["source", "network", "source", "network", "traefik", "repository", "generated"]


def test_unit_convergence_rejects_symlink_and_nonregular_destinations(tmp_path):
    target = tmp_path / "systemd"
    target.mkdir()
    destination = target / host.BACKUP_SERVICE
    destination.symlink_to(tmp_path / "elsewhere")

    with pytest.raises(host.HostError, match="unsafe"):
        host._install_units(target)

    destination.unlink()
    destination.mkdir()
    with pytest.raises(host.HostError, match="unsafe"):
        host._install_units(target)

    destination.rmdir()
    target.rmdir()
    target.symlink_to(tmp_path)
    with pytest.raises(host.HostError, match="directory is unsafe"):
        host._install_units(target)


def test_unit_convergence_enforces_mode_owner_and_reports_metadata_change(tmp_path, monkeypatch):
    target = tmp_path / "systemd"
    host._install_units(target)
    service = target / host.BACKUP_SERVICE
    service.chmod(0o600)
    ownership = []
    monkeypatch.setattr(host, "UNIT_DIR", target)
    monkeypatch.setattr(host.os, "chown", lambda path, uid, gid: ownership.append((path, uid, gid)))
    real_stat = host.Path.stat

    def stat_with_foreign_owner(path, *args, **kwargs):
        value = real_stat(path, *args, **kwargs)
        if path.parent == target:
            return os.stat_result(
                (value.st_mode, value.st_ino, value.st_dev, 1, 1, 1, value.st_size, 0, 0, 0)
            )
        return value

    monkeypatch.setattr(host.Path, "stat", stat_with_foreign_owner)

    host._install_units(target)
    assert service.stat().st_mode & 0o777 == 0o644
    assert {Path(path).name for path, uid, gid in ownership if (uid, gid) == (0, 0)} == {
        host.BACKUP_SERVICE,
        host.BACKUP_TIMER,
    }


def test_initial_restic_password_file_is_private_and_existing_config_ignores_replacement(
    paths, tmp_path
):
    password = tmp_path / "restic-password"
    password.write_text("existing repository password\n")
    password.chmod(0o600)
    dns = tmp_path / "dns.env"
    dns.write_text("TESTDNS_TOKEN=private\n")
    values = {
        "host_id": "password-test-01",
        "domain": "storage.example.com",
        "data_root": str(tmp_path / "data"),
        "acme_email": "ops@example.com",
        "dns_provider": "testdns",
        "repository": str(tmp_path / "repository"),
        "dns_file": str(dns),
        "rclone_config": str(tmp_path / "rclone.conf"),
        "restic_password_file": str(password),
    }

    config = host._initial(values, paths)

    assert config.secrets.restic_password == "existing repository password"
    password.chmod(0o644)
    with pytest.raises(host.HostError, match="private regular"):
        host._initial(values, paths)


def test_initial_restic_password_rejects_unsafe_file_shapes(tmp_path):
    candidates = []
    empty = tmp_path / "empty"
    empty.write_text("")
    empty.chmod(0o600)
    candidates.append(empty)
    multiline = tmp_path / "multiline"
    multiline.write_text("first\nsecond\n")
    multiline.chmod(0o600)
    candidates.append(multiline)
    nul = tmp_path / "nul"
    nul.write_bytes(b"before\0after\n")
    nul.chmod(0o600)
    candidates.append(nul)
    directory = tmp_path / "directory"
    directory.mkdir()
    candidates.append(directory)
    symlink = tmp_path / "symlink"
    symlink.symlink_to(empty)
    candidates.append(symlink)

    for path in candidates:
        with pytest.raises(host.HostError, match="password"):
            host._restic_password({"restic_password_file": str(path)})


def test_generated_ownership_never_recursively_chowns_database_data(config, monkeypatch):
    descendant = config.host.data_root / "app-test-01/postgres/data/PG_VERSION"
    descendant.parent.mkdir(parents=True)
    descendant.write_text("16\n")
    before = descendant.stat()
    calls = []
    monkeypatch.setattr(host, "CONFIG_DIR", config.paths.config)
    monkeypatch.setattr(
        host,
        "run",
        lambda args, **kwargs: calls.append(args) or Result(tuple(args), 0, "", ""),
    )

    host._generated_ownership(config)

    recursive = next(args for args in calls if "-R" in args)
    assert str(config.host.data_root) not in recursive
    assert ["chown", "evdb:evdb", str(config.host.data_root)] in calls
    after = descendant.stat()
    assert descendant.read_text() == "16\n"
    assert (after.st_uid, after.st_gid, after.st_mode) == (
        before.st_uid,
        before.st_gid,
        before.st_mode,
    )


def test_managed_directory_symlink_is_rejected_without_following(config, tmp_path):
    real = tmp_path / "real-state"
    real.mkdir()
    linked = tmp_path / "state-link"
    linked.symlink_to(real, target_is_directory=True)
    unsafe = replace(
        config,
        paths=Paths(config=config.paths.config, state=linked),
    )

    with pytest.raises(host.HostError, match="unsafe"):
        host._directories(unsafe)


def test_canonical_source_directory_converges_to_sticky_group_writable(config, monkeypatch):
    calls = []
    monkeypatch.setattr(host, "CONFIG_DIR", config.paths.config)
    monkeypatch.setattr(
        host,
        "run",
        lambda args, **kwargs: calls.append(args) or Result(tuple(args), 0, "", ""),
    )

    host._directories(config)
    host._source_ownership(config)

    assert config.paths.config.stat().st_mode & 0o7777 == 0o1770
    assert calls.count(["chmod", "01770", str(config.paths.config)]) == 1


def test_ports_probe_uses_sockets_only_for_confirmed_absent_container(config, monkeypatch):
    calls = []
    binds = []

    class Socket:
        def bind(self, address):
            binds.append(address)

        def close(self):
            pass

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return Result(tuple(args), 1, "", "Error: No such object: evdb-traefik")

    monkeypatch.setattr(host, "run", run)
    monkeypatch.setattr(host.socket, "socket", Socket)

    host._require_ports(config)
    assert binds == [("0.0.0.0", 5432), ("0.0.0.0", 6379)]
    assert config.secrets.restic_password in calls[0][1]["secrets"]


def test_ports_probe_preserves_redacted_docker_denial_without_socket_probe(config, monkeypatch):
    secret = config.secrets.restic_password
    monkeypatch.setattr(
        host,
        "run",
        lambda args, **kwargs: Result(tuple(args), 1, "", f"daemon denied {secret}"),
    )
    monkeypatch.setattr(
        host.socket,
        "socket",
        lambda: pytest.fail("socket probe must not run after Docker denial"),
    )

    with pytest.raises(host.HostError) as caught:
        host._require_ports(config)

    assert str(caught.value) == "daemon denied <redacted>"


def test_ports_probe_rejects_malformed_docker_json_without_socket_probe(config, monkeypatch):
    secret = config.secrets.restic_password
    monkeypatch.setattr(
        host,
        "run",
        lambda args, **kwargs: Result(tuple(args), 0, f"not-json {secret}", ""),
    )
    monkeypatch.setattr(
        host.socket,
        "socket",
        lambda: pytest.fail("socket probe must not run after malformed Docker output"),
    )

    with pytest.raises(host.HostError) as caught:
        host._require_ports(config)

    assert str(caught.value) == "malformed Docker inspection: not-json <redacted>"


def test_ports_probe_names_exact_occupied_native_port(config, monkeypatch):
    binds = []

    class Socket:
        def bind(self, address):
            binds.append(address)
            if address[1] == 6379:
                raise OSError("address in use")

        def close(self):
            pass

    monkeypatch.setattr(
        host,
        "run",
        lambda args, **kwargs: Result(tuple(args), 1, "", "No such container: evdb-traefik"),
    )
    monkeypatch.setattr(host.socket, "socket", Socket)

    with pytest.raises(host.HostError, match="port 6379 is occupied"):
        host._require_ports(config)

    assert binds == [("0.0.0.0", 5432), ("0.0.0.0", 6379)]


def test_ports_probe_stopped_owned_traefik_and_accepts_free_ports(config, monkeypatch):
    binds = []

    class Socket:
        def bind(self, address):
            binds.append(address)

        def close(self):
            pass

    inspected = json.dumps(
        [
            {
                "Config": {"Labels": {"com.docker.compose.project": "evdb-traefik"}},
                "State": {"Running": False},
            }
        ]
    )
    monkeypatch.setattr(
        host,
        "run",
        lambda args, **kwargs: Result(tuple(args), 0, inspected, ""),
    )
    monkeypatch.setattr(host.socket, "socket", Socket)

    host._require_ports(config)

    assert binds == [("0.0.0.0", 5432), ("0.0.0.0", 6379)]


def test_ports_accept_running_owned_traefik_without_socket_probe(config, monkeypatch):
    inspected = json.dumps(
        [
            {
                "Config": {"Labels": {"com.docker.compose.project": "evdb-traefik"}},
                "State": {"Running": True},
            }
        ]
    )
    monkeypatch.setattr(
        host,
        "run",
        lambda args, **kwargs: Result(tuple(args), 0, inspected, ""),
    )
    monkeypatch.setattr(
        host.socket,
        "socket",
        lambda: pytest.fail("running owned Traefik must not probe ports"),
    )

    host._require_ports(config)


def test_ports_probe_stopped_owned_traefik_rejects_exact_occupied_port(config, monkeypatch):
    class Socket:
        def bind(self, address):
            if address[1] == 5432:
                raise OSError("address in use")

        def close(self):
            pass

    inspected = json.dumps(
        [
            {
                "Config": {"Labels": {"com.docker.compose.project": "evdb-traefik"}},
                "State": {"Running": False},
            }
        ]
    )
    monkeypatch.setattr(
        host,
        "run",
        lambda args, **kwargs: Result(tuple(args), 0, inspected, ""),
    )
    monkeypatch.setattr(host.socket, "socket", Socket)

    with pytest.raises(host.HostError, match="port 5432 is occupied by another service"):
        host._require_ports(config)


def test_existing_service_account_is_validated_before_docker_membership(paths, monkeypatch):
    calls = []
    monkeypatch.setattr(host, "CONFIG_DIR", paths.config)

    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["getent", "group"]:
            return Result(tuple(args), 0, "evdb:x:997:\n", "")
        if args[:2] == ["getent", "passwd"]:
            return Result(
                tuple(args),
                0,
                "evdb:x:1001:997::/var/lib/evdb:/usr/sbin/nologin\n",
                "",
            )
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(host, "run", run)

    assert host._account(paths) == (1001, 997)
    assert calls[-1] == ["usermod", "--append", "--groups", "docker", "evdb"]


@pytest.mark.parametrize(
    "entry",
    [
        "evdb:x:998:996::/var/lib/evdb:/usr/sbin/nologin\n",
        "evdb:x:998:997::/home/evdb:/usr/sbin/nologin\n",
        "evdb:x:998:997::/var/lib/evdb:/bin/bash\n",
        "evdb:x:not-a-number:997::/var/lib/evdb:/usr/sbin/nologin\n",
        "evdb:x:998:997:malformed\n",
    ],
)
def test_invalid_existing_service_user_never_gets_docker_membership(paths, monkeypatch, entry):
    calls = []
    monkeypatch.setattr(host, "CONFIG_DIR", paths.config)

    def run(args, **kwargs):
        calls.append(args)
        value = "evdb:x:997:\n" if args[:2] == ["getent", "group"] else entry
        return Result(tuple(args), 0, value, "")

    monkeypatch.setattr(host, "run", run)

    with pytest.raises(host.HostError, match="evdb user"):
        host._account(paths)

    assert not any(args and args[0] == "usermod" for args in calls)


@pytest.mark.parametrize("entry", ["evdb:x:0:\n", "evdb:x:not-a-number:\n", "bad\n"])
def test_invalid_existing_service_group_never_gets_docker_membership(paths, monkeypatch, entry):
    calls = []
    monkeypatch.setattr(host, "CONFIG_DIR", paths.config)

    def run(args, **kwargs):
        calls.append(args)
        return Result(tuple(args), 0, entry, "")

    monkeypatch.setattr(host, "run", run)

    with pytest.raises(host.HostError, match="evdb group"):
        host._account(paths)

    assert not any(args and args[0] in {"useradd", "usermod"} for args in calls)


def test_missing_service_account_is_recreated_validated_then_joined_to_docker(paths, monkeypatch):
    calls = []
    created = {"group": False, "passwd": False}
    monkeypatch.setattr(host, "CONFIG_DIR", paths.config)

    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["getent", "group"]:
            return Result(
                tuple(args),
                0 if created["group"] else 2,
                "evdb:x:997:\n" if created["group"] else "",
                "",
            )
        if args[:2] == ["getent", "passwd"]:
            return Result(
                tuple(args),
                0 if created["passwd"] else 2,
                ("evdb:x:998:997::/var/lib/evdb:/usr/sbin/nologin\n" if created["passwd"] else ""),
                "",
            )
        if args[0] == "groupadd":
            created["group"] = True
        if args[0] == "useradd":
            created["passwd"] = True
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(host, "run", run)

    assert host._account(paths) == (998, 997)
    actions = [args[0] for args in calls]
    assert actions.index("groupadd") < actions.index("useradd") < actions.index("usermod")


def test_existing_canonical_bootstrap_orders_guard_account_ownership_before_load(
    config, monkeypatch
):
    order = []
    monkeypatch.setattr(host, "_guard_preload", lambda *args: order.append("guard"))
    monkeypatch.setattr(
        host,
        "_require_safe_canonical_source",
        lambda *args: order.append("paths"),
    )
    monkeypatch.setattr(
        host,
        "_account",
        lambda *args, **kwargs: order.append("account") or (998, 997),
    )
    monkeypatch.setattr(
        host,
        "_converge_canonical_source",
        lambda *args: order.append("ownership"),
    )

    host._bootstrap_existing(config.paths.source, config.paths)

    assert order == ["guard", "paths", "account", "ownership"]


def test_existing_canonical_initialize_bootstraps_before_strict_load(config, monkeypatch):
    order = []
    monkeypatch.setattr(host, "CONFIG_DIR", config.paths.config)
    monkeypatch.setattr(
        host,
        "_bootstrap_existing",
        lambda *args: order.append("bootstrap"),
    )

    def load(*args, **kwargs):
        order.append("load")
        raise RuntimeError("stop after ordering assertion")

    monkeypatch.setattr(host, "load", load)

    with pytest.raises(RuntimeError, match="ordering assertion"):
        host.initialize(config.paths.source, paths=config.paths)

    assert order == ["bootstrap", "load"]


def test_existing_canonical_init_guards_actual_montreal_host_before_subprocesses(
    config, monkeypatch
):
    calls = []
    monkeypatch.setattr(host, "CONFIG_DIR", config.paths.config)
    monkeypatch.setattr(host.socket, "gethostname", lambda: "montreal-01.example.com")
    monkeypatch.setattr(host, "run", lambda *args, **kwargs: calls.append(args))

    with pytest.raises(host.HostError, match="separate change"):
        host.initialize(config.paths.source, paths=config.paths)

    assert calls == []


@pytest.mark.parametrize(
    "text",
    [
        "BAD-KEY=value\n",
        "EMPTY=\n",
        "NUL=before\0after\n",
        "MULTI=first\nsecond\n",
    ],
)
def test_first_init_dns_file_uses_secret_value_validation(tmp_path, text):
    source = tmp_path / "dns.env"
    source.write_text(text)

    with pytest.raises(host.HostError, match="DNS credential file"):
        host._env_file(source)


def test_first_init_dns_file_rejects_duplicate_keys(tmp_path):
    source = tmp_path / "dns.env"
    source.write_text("TOKEN=first\nTOKEN=second\n")

    with pytest.raises(host.HostError, match="duplicate key: TOKEN"):
        host._env_file(source)

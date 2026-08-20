import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from evdb import files as files_module
from evdb import host
from evdb.models import Paths
from evdb.run import Result


def test_init_rerun_preserves_rclone_and_enables_only_backup_timer(config, tmp_path, monkeypatch):
    calls = []
    order = []
    before = b"[remote]\ntype = local\ntoken = refreshed oauth state\n"
    config.host.backup.rclone_config.write_bytes(before)
    host._install_units(tmp_path / "systemd")
    monkeypatch.setattr(host, "prerequisites", lambda current=None: [])
    monkeypatch.setattr(host, "_require_ports", lambda current: None)
    monkeypatch.setattr(host, "_source_ownership", lambda current: order.append("source ownership"))
    monkeypatch.setattr(host, "_traefik", lambda current: order.append("traefik"))
    monkeypatch.setattr(host, "_wait_certificate", lambda current: order.append("certificate"))
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
    assert config.host.backup.rclone_config.read_bytes() == before
    assert order == [
        "source ownership",
        "network",
        "traefik",
        "certificate",
        "repository",
        "timer",
    ]
    assert ["systemctl", "daemon-reload"] in calls
    assert ["systemctl", "enable", "--now", "evdb-backup.timer"] in calls
    assert not any("evdb-status" in " ".join(call) for call in calls)


def test_prerequisites_require_restic_017_without_python_or_uv(tmp_path, monkeypatch):
    restic = tmp_path / "restic"
    rclone = tmp_path / "rclone"
    for path in (restic, rclone):
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
    monkeypatch.setattr(host.backup, "RESTIC", restic)
    monkeypatch.setattr(host.backup, "RCLONE", rclone)
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
    dns.write_text("CF_DNS_API_TOKEN=private\n")
    values = {
        "host_id": "new-test-01",
        "domain": "storage.example.com",
        "acme_email": "ops@example.com",
        "dns_provider": "cloudflare",
        "repository": str(tmp_path / "repository"),
        "dns_file": str(dns),
    }
    monkeypatch.setattr(host, "prerequisites", lambda current=None: [])
    monkeypatch.setattr(host, "_require_ports", lambda current: None)
    monkeypatch.setattr(host, "_source_ownership", lambda current: None)
    monkeypatch.setattr(host, "_traefik", lambda current: None)
    monkeypatch.setattr(host, "_wait_certificate", lambda current: None)
    monkeypatch.setattr(host.docker, "ensure_network", lambda **kwargs: None)
    monkeypatch.setattr(host.backup, "initialize", lambda current: None)
    monkeypatch.setattr(host.backup, "preflight", lambda current: "missing")
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
    assert service["environment"] == {"LEGO_DISABLE_CNAME_SUPPORT": "true"}
    assert not any("entrypoints.https" in item for item in service["command"])
    assert "--providers.file.filename=/config/tls.yml" in service["command"]
    dynamic = yaml.safe_load((config.paths.traefik / "tls.yml").read_text())
    assert dynamic["tls"]["options"]["default"]["alpnProtocols"] == [
        "postgresql",
        "h2",
        "http/1.1",
        "acme-tls/1",
    ]
    generated = dynamic["tls"]["stores"]["default"]["defaultGeneratedCert"]
    assert generated == {
        "resolver": "evdb",
        "domain": {"main": "*.test-01.storage.example.com"},
    }


def test_acme_readiness_requires_exact_nonempty_wildcard(config):
    acme = config.paths.traefik / "acme/acme.json"
    acme.parent.mkdir(parents=True)
    acme.write_text(
        json.dumps(
            {
                "evdb": {
                    "Certificates": [
                        {
                            "domain": {"main": "*.other.example.com"},
                            "certificate": "certificate",
                            "key": "key",
                        }
                    ]
                }
            }
        )
    )
    acme.chmod(0o600)

    assert not host.certificate_ready(config)

    acme.write_text(
        json.dumps(
            {
                "evdb": {
                    "Certificates": [
                        {
                            "domain": {"main": host.wildcard(config)},
                            "certificate": "certificate",
                            "key": "key",
                        }
                    ]
                }
            }
        )
    )
    assert host.certificate_ready(config)


def test_mid_init_failure_preserves_source_and_rerun_converges(paths, tmp_path, monkeypatch):
    dns = tmp_path / "dns.env"
    dns.write_text("CF_DNS_API_TOKEN=private\n")
    values = {
        "host_id": "retry-test-01",
        "domain": "storage.example.com",
        "acme_email": "ops@example.com",
        "dns_provider": "cloudflare",
        "repository": str(tmp_path / "repository"),
        "dns_file": str(dns),
    }
    order = []
    attempts = iter([RuntimeError("injected network failure"), None])
    monkeypatch.setattr(host, "prerequisites", lambda current=None: [])
    monkeypatch.setattr(host, "_require_ports", lambda current: None)
    monkeypatch.setattr(host, "_source_ownership", lambda current: order.append("source"))

    def network(**kwargs):
        order.append("network")
        failure = next(attempts)
        if failure:
            raise failure

    monkeypatch.setattr(host.docker, "ensure_network", network)
    monkeypatch.setattr(host, "_traefik", lambda current: order.append("traefik"))
    monkeypatch.setattr(host, "_wait_certificate", lambda current: order.append("certificate"))
    monkeypatch.setattr(host.backup, "initialize", lambda current: order.append("repository"))
    monkeypatch.setattr(host.backup, "preflight", lambda current: "missing")
    monkeypatch.setattr(host, "_install_units", lambda target: None)
    monkeypatch.setattr(host, "run", lambda args, **kwargs: Result(tuple(args), 0, "", ""))
    monkeypatch.setattr(
        __import__("evdb.status", fromlist=["collect"]),
        "collect",
        lambda current: {"healthy": True, "host": {"id": current.host.id, "healthy": True}},
    )

    with pytest.raises(RuntimeError, match="injected"):
        host.initialize(paths.source, values, paths=paths, unit_dir=tmp_path / "systemd")

    assert paths.source.is_file() and paths.secrets.is_file()
    assert order == ["source", "network"]

    result = host.initialize(
        paths.source,
        {"restic_password_file": str(tmp_path / "must-not-be-read")},
        paths=paths,
        unit_dir=tmp_path / "systemd",
    )

    assert result["healthy"]
    assert order == [
        "source",
        "network",
        "source",
        "network",
        "traefik",
        "certificate",
        "repository",
    ]


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
    dns.write_text("CF_DNS_API_TOKEN=private\n")
    values = {
        "host_id": "password-test-01",
        "domain": "storage.example.com",
        "acme_email": "ops@example.com",
        "dns_provider": "cloudflare",
        "repository": str(tmp_path / "repository"),
        "dns_file": str(dns),
        "restic_password_file": str(password),
    }
    config = host._initial(values, paths)

    assert config.secrets.restic_password == "existing repository password"
    assert config.host.data_roots == (paths.databases,)
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


def test_host_directories_expose_only_backup_traversal(config):
    host._directories(config)

    assert config.paths.state.stat().st_mode & 0o777 == 0o711
    assert config.paths.backups.stat().st_mode & 0o777 == 0o711
    for path in (
        config.paths.projects,
        config.paths.traefik,
        *config.host.data_roots,
        config.paths.locks,
    ):
        assert path.stat().st_mode & 0o777 == 0o700


def test_host_directories_prepare_custom_data_root(config, tmp_path):
    parent = tmp_path / "database-volume"
    parent.mkdir()
    root = parent / "evdb"
    selected = replace(
        config,
        host=replace(config.host, data_roots=(config.paths.databases, root)),
    )

    host._directories(selected)

    assert root.is_dir()
    assert root.stat().st_mode & 0o777 == 0o700


def test_canonical_host_directories_take_ownership_of_data_roots(config, monkeypatch):
    calls = []
    monkeypatch.setattr(host, "CONFIG_DIR", config.paths.config)
    monkeypatch.setattr(
        host,
        "managed_dir",
        lambda path, mode, owner=None: calls.append((path, mode, owner)),
    )

    host._directories(config)

    assert (config.host.data_roots[0], 0o700, (0, 0)) in calls


def test_existing_host_rejects_requested_data_root_change(config, tmp_path):
    parent = tmp_path / "database-volume"
    parent.mkdir()

    with pytest.raises(host.HostError, match="cannot be changed"):
        host.initialize(
            config.paths.source,
            {"data_roots": [str(parent / "evdb")]},
            paths=config.paths,
        )


def test_canonical_source_directory_converges_to_private_root_layout(config, monkeypatch):
    calls = []
    monkeypatch.setattr(host, "CONFIG_DIR", config.paths.config)
    real_fchown = files_module.os.fchown
    monkeypatch.setattr(
        files_module.os,
        "fchown",
        lambda descriptor, uid, gid: real_fchown(descriptor, os.getuid(), os.getgid()),
    )
    monkeypatch.setattr(
        host,
        "run",
        lambda args, **kwargs: calls.append(args) or Result(tuple(args), 0, "", ""),
    )

    host._directories(config)
    host._source_ownership(config)

    assert config.paths.config.stat().st_mode & 0o7777 == 0o700
    assert [
        "chown",
        "root:root",
        str(config.paths.config),
        str(config.paths.source),
        str(config.paths.secrets),
    ] in calls
    assert ["chmod", "0700", str(config.paths.config)] in calls


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


def test_existing_canonical_bootstrap_orders_guard_and_ownership_before_load(config, monkeypatch):
    order = []
    monkeypatch.setattr(host, "_guard_preload", lambda *args: order.append("guard"))
    monkeypatch.setattr(
        host,
        "_require_safe_canonical_source",
        lambda *args: order.append("paths"),
    )
    monkeypatch.setattr(
        host,
        "_converge_canonical_source",
        lambda *args: order.append("ownership"),
    )

    host._bootstrap_existing(config.paths.source, config.paths)

    assert order == ["guard", "paths", "ownership"]


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

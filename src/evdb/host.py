from __future__ import annotations

import json
import os
import secrets as random
import shutil
import socket
import stat
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any

from . import backup, docker
from .config import DEFAULT_IMAGES, dns_values, load, protected, reject_legacy, seed_rclone, write
from .errors import CommandError, ConfigError, Error, HostError
from .files import managed_dir, private_line, write_text
from .lock import operation
from .models import CONFIG_DIR, STATE_DIR, BackupSettings, Config, Host, Paths, Routing, Secrets
from .run import redact, run

TOOLS = ("docker", "restic", "rclone", "systemctl")
BACKUP_SERVICE = "evdb-backup.service"
BACKUP_TIMER = "evdb-backup.timer"
UNIT_DIR = Path("/etc/systemd/system")


def initialize(
    source: str | Path,
    values: dict[str, Any] | None = None,
    *,
    paths: Paths | None = None,
    unit_dir: str | Path = "/etc/systemd/system",
) -> dict[str, Any]:
    source_path = Path(source)
    managed = paths or Paths(config=source_path.parent)
    reject_legacy(managed)
    existing = source_path.exists() or source_path.is_symlink()
    if managed.config == CONFIG_DIR:
        if existing:
            _bootstrap_existing(source_path, managed)
        else:
            _guard_preload(source_path, managed)
    config = load(source_path, paths=managed) if existing else _initial(values or {}, managed)
    _guard(config)
    missing = prerequisites()
    if missing:
        raise HostError("missing prerequisites: " + ", ".join(sorted(set(missing))))
    _writable(config, Path(unit_dir))
    _require_ports(config)
    with operation(config, write=True, timeout=config.host.timeouts["backup"]):
        _account(config.paths)
        _directories(config)
        if not existing:
            write(config)
        else:
            _assert_private(config.paths.secrets)
        seed_rclone(config, (values or {}).get("rclone_config"))
        _source_ownership(config)
        _dns(config)
        docker.ensure_network(timeout=config.host.timeouts["command"])
        _traefik(config)
        backup.initialize(config)
        _install_units(Path(unit_dir))
        _generated_ownership(config)
        run(["systemctl", "daemon-reload"], timeout=60)
        run(["systemctl", "enable", "--now", BACKUP_TIMER], timeout=120)
    return _wait_status(config)


def prerequisites() -> list[str]:
    missing = [name for name in TOOLS if shutil.which(name) is None]
    if "docker" not in missing:
        result = run(["docker", "compose", "version"], timeout=30, check=False)
        if result.code:
            missing.append("docker compose")
    if "restic" not in missing:
        try:
            backup.require_version()
        except Error:
            missing.append("restic 0.17+")
    return missing


def _initial(values: dict[str, Any], paths: Paths) -> Config:
    required = (
        "host_id",
        "domain",
        "data_root",
        "acme_email",
        "dns_provider",
        "repository",
        "dns_file",
        "rclone_config",
    )
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise HostError("initialization requires: " + ", ".join(missing))
    dns = _env_file(Path(values["dns_file"]))
    config = Config(
        Host(
            values["host_id"],
            values["domain"],
            Path(values["data_root"]),
            BackupSettings(values["repository"], 5, 26),
            Routing(values["acme_email"], values["dns_provider"], DEFAULT_IMAGES["traefik"]),
        ),
        (),
        Secrets(_restic_password(values), tuple(sorted(dns.items()))),
        paths,
    )
    from .config import require_valid

    require_valid(config)
    return config


def _directories(config: Config) -> None:
    try:
        config_mode = 0o1770 if config.paths.config == CONFIG_DIR else 0o750
        for path, mode in (
            (config.paths.config, config_mode),
            (config.paths.projects, 0o700),
            (config.paths.traefik, 0o700),
            (config.paths.traefik / "acme", 0o700),
            (config.paths.state, 0o700),
            (config.paths.backups, 0o700),
            (config.paths.locks, 0o700),
            (config.host.data_root, 0o700),
        ):
            managed_dir(path, mode)
    except OSError as exc:
        raise HostError(str(exc)) from exc


def _dns(config: Config) -> None:
    text = "".join(f"{key}={json.dumps(value)}\n" for key, value in config.secrets.dns)
    write_text(config.paths.traefik / "dns.env", text, mode=0o600)
    acme = config.paths.traefik / "acme/acme.json"
    if not acme.exists():
        write_text(acme, "{}\n", mode=0o600)


def _traefik(config: Config) -> None:
    provider = config.host.routing.dns_provider
    data = {
        "name": docker.TRAEFIK_PROJECT,
        "services": {
            "traefik": {
                "image": config.host.routing.traefik_image,
                "container_name": docker.TRAEFIK_CONTAINER,
                "restart": "unless-stopped",
                "command": [
                    "--providers.docker=true",
                    "--providers.docker.exposedbydefault=false",
                    f"--providers.docker.network={docker.NETWORK}",
                    "--ping=true",
                    "--entrypoints.postgres.address=:5432",
                    "--entrypoints.kv.address=:6379",
                    f"--certificatesresolvers.evdb.acme.email={config.host.routing.acme_email}",
                    "--certificatesresolvers.evdb.acme.storage=/acme/acme.json",
                    "--certificatesresolvers.evdb.acme.dnschallenge=true",
                    f"--certificatesresolvers.evdb.acme.dnschallenge.provider={provider}",
                ],
                "env_file": [str(config.paths.traefik / "dns.env")],
                "ports": ["5432:5432/tcp", "6379:6379/tcp"],
                "healthcheck": docker.healthcheck(["CMD", "traefik", "healthcheck", "--ping"]),
                "volumes": [
                    "/var/run/docker.sock:/var/run/docker.sock:ro",
                    f"{config.paths.traefik / 'acme'}:/acme",
                ],
                "networks": [docker.NETWORK],
            }
        },
        "networks": {docker.NETWORK: {"external": True, "name": docker.NETWORK}},
    }
    path = config.paths.traefik / "compose.yaml"
    docker.write_compose(path, data)
    credentials = protected(config)
    docker.validate_compose(
        path,
        docker.TRAEFIK_PROJECT,
        timeout=config.host.timeouts["command"],
        secrets=credentials,
    )
    docker.up(
        path,
        docker.TRAEFIK_PROJECT,
        timeout=config.host.timeouts["command"],
        secrets=credentials,
    )


def _wait_status(config: Config) -> dict[str, Any]:
    import time

    from . import status

    deadline = time.monotonic() + config.host.timeouts["health"]
    value = status.collect(config)
    while not value["host"]["healthy"] and time.monotonic() < deadline:
        time.sleep(1)
        value = status.collect(config)
    return value


def _account(paths: Paths, *, docker_group: bool = True) -> tuple[int, int] | None:
    if paths.config != CONFIG_DIR:
        return None
    group = _getent("group")
    if group is None:
        run(["groupadd", "--system", "evdb"], timeout=60)
        group = _getent("group")
    if group is None or len(group) != 4 or group[0] != "evdb":
        raise HostError("evdb group entry is missing or malformed")
    gid = _system_id(group[2], "evdb group")
    user = _getent("passwd")
    if user is None:
        run(
            [
                "useradd",
                "--system",
                "--gid",
                "evdb",
                "--home-dir",
                str(STATE_DIR),
                "--shell",
                "/usr/sbin/nologin",
                "evdb",
            ],
            timeout=60,
        )
        user = _getent("passwd")
    if user is None or len(user) != 7 or user[0] != "evdb":
        raise HostError("evdb user entry is missing or malformed")
    uid = _system_id(user[2], "evdb user")
    primary_gid = _system_id(user[3], "evdb user primary group")
    if primary_gid != gid:
        raise HostError("evdb user primary group does not match the evdb group")
    if user[5] != str(STATE_DIR):
        raise HostError(f"evdb user home must be {STATE_DIR}")
    if user[6] not in {"/usr/sbin/nologin", "/sbin/nologin", "/bin/false"}:
        raise HostError("evdb user must use a non-login shell")
    if docker_group:
        run(["usermod", "--append", "--groups", "docker", "evdb"], timeout=60)
    return uid, gid


def _getent(database: str) -> list[str] | None:
    result = run(["getent", database, "evdb"], timeout=30, check=False)
    if result.code == 2 and not result.out.strip():
        return None
    if result.code != 0:
        detail = result.err.strip() or result.out.strip() or "no output"
        raise HostError(f"getent {database} evdb failed: {detail}")
    lines = result.out.splitlines()
    if len(lines) != 1 or "\0" in result.out:
        raise HostError(f"evdb {database} entry is malformed")
    return lines[0].split(":")


def _system_id(value: str, name: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise HostError(f"{name} ID is invalid")
    number = int(value)
    if number < 1:
        raise HostError(f"{name} ID is invalid")
    return number


def _bootstrap_existing(source: Path, paths: Paths) -> None:
    _guard_preload(source, paths)
    _require_safe_canonical_source(source, paths)
    account = _account(paths, docker_group=False)
    if account is None:
        raise HostError("canonical initialization requires the evdb account")
    _converge_canonical_source(paths, *account)


def _guard_preload(source: Path, paths: Paths) -> None:
    hostname = socket.gethostname().split(".", 1)[0]
    if hostname == "montreal-01" or "montreal-01" in source.parts:
        raise HostError("production migration for montreal-01 is a separate change")
    if source != CONFIG_DIR / "config.yml" or source != paths.source:
        raise HostError("canonical initialization requires /etc/evdb/config.yml")
    if os.geteuid() != 0:
        raise HostError("host initialization requires root; run sudo evdb init")


def _require_safe_canonical_source(source: Path, paths: Paths) -> None:
    try:
        directory = paths.config.lstat()
        if (
            not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != 0
            or directory.st_mode & 0o007
            or (directory.st_mode & 0o020 and not directory.st_mode & stat.S_ISVTX)
        ):
            raise HostError(f"canonical configuration directory is unsafe: {paths.config}")
        for path, root_owned in (
            (source, True),
            (paths.secrets, False),
            (paths.rclone, False),
        ):
            if path == paths.rclone and not path.exists() and not path.is_symlink():
                continue
            details = path.lstat()
            if (
                not stat.S_ISREG(details.st_mode)
                or (root_owned and details.st_uid != 0)
                or details.st_mode & (0o022 if root_owned else 0o077)
            ):
                raise HostError(f"canonical source file is unsafe: {path}")
    except OSError as exc:
        raise HostError(
            f"canonical source is missing or unsafe: {exc.filename or paths.config}"
        ) from exc


def _converge_canonical_source(paths: Paths, uid: int, gid: int) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    directory = os.open(paths.config, flags | os.O_DIRECTORY)
    try:
        details = os.fstat(directory)
        if not stat.S_ISDIR(details.st_mode) or details.st_uid != 0:
            raise HostError(f"canonical configuration directory is unsafe: {paths.config}")
        os.fchown(directory, 0, gid)
        os.fchmod(directory, 0o1770)
        for name, owner, mode in (
            ("config.yml", 0, 0o640),
            ("secrets.yml", uid, 0o600),
            ("rclone.conf", uid, 0o600),
        ):
            try:
                descriptor = os.open(name, flags, dir_fd=directory)
            except FileNotFoundError:
                if name == "rclone.conf":
                    continue
                raise
            try:
                current = os.fstat(descriptor)
                private = mode == 0o600
                if (
                    not stat.S_ISREG(current.st_mode)
                    or (not private and current.st_uid != 0)
                    or current.st_mode & (0o077 if private else 0o022)
                ):
                    raise HostError(f"canonical source file is unsafe: {paths.config / name}")
                os.fchown(descriptor, owner, gid)
                os.fchmod(descriptor, mode)
            finally:
                os.close(descriptor)
    except OSError as exc:
        raise HostError(f"canonical source cannot be converged: {exc}") from exc
    finally:
        os.close(directory)


def _source_ownership(config: Config) -> None:
    if config.paths.config != CONFIG_DIR:
        return
    run(["chown", "root:evdb", str(config.paths.config), str(config.paths.source)], timeout=60)
    run(["chmod", "01770", str(config.paths.config)], timeout=60)
    run(["chmod", "0640", str(config.paths.source)], timeout=60)
    run(
        [
            "chown",
            "evdb:evdb",
            str(config.paths.secrets),
            str(config.paths.rclone),
        ],
        timeout=60,
    )
    run(["chmod", "0600", str(config.paths.secrets), str(config.paths.rclone)], timeout=60)


def _generated_ownership(config: Config) -> None:
    if config.paths.config != CONFIG_DIR:
        return
    run(
        [
            "chown",
            "-R",
            "evdb:evdb",
            str(config.paths.projects),
            str(config.paths.traefik),
            str(config.paths.state),
        ],
        timeout=120,
    )
    run(["chown", "evdb:evdb", str(config.host.data_root)], timeout=60)


def _install_units(target: Path) -> None:
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir():
            raise HostError(f"systemd unit directory is unsafe: {target}")
    else:
        target.mkdir(parents=True, mode=0o755)
    sources = _units()
    if {source.name for source in sources} != {BACKUP_SERVICE, BACKUP_TIMER}:
        raise HostError("release must contain exactly the backup service and timer")
    for source in sources:
        details = source.lstat()
        if source.is_symlink() or not stat.S_ISREG(details.st_mode) or details.st_size == 0:
            raise HostError(f"release unit is missing or unsafe: {source}")
        destination = target / source.name
        text = source.read_text()
        if destination.exists() or destination.is_symlink():
            current = destination.lstat()
            if destination.is_symlink() or not stat.S_ISREG(current.st_mode):
                raise HostError(f"installed unit is unsafe: {destination}")
        else:
            current = None
        if current is None or destination.read_text() != text:
            write_text(destination, text, mode=0o644)
        if destination.stat().st_mode & 0o777 != 0o644:
            destination.chmod(0o644)
        if target == UNIT_DIR:
            details = destination.stat()
            if details.st_uid != 0 or details.st_gid != 0:
                os.chown(destination, 0, 0)


def _units() -> tuple[Path, ...]:
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent.parent / "units"
    else:
        root = Path(str(files("evdb").joinpath("units")))
    return tuple(sorted((*root.glob("*.service"), *root.glob("*.timer"))))


def _require_ports(config: Config) -> None:
    credentials = protected(config)
    try:
        result = run(
            ["docker", "inspect", docker.TRAEFIK_CONTAINER],
            timeout=10,
            check=False,
            secrets=credentials,
        )
    except CommandError as exc:
        raise HostError(redact(str(exc), credentials)) from exc
    detail = redact(result.err.strip() or result.out.strip() or "no output", credentials)
    if result.code == 0:
        try:
            items = json.loads(result.out)
            if not isinstance(items, list) or not items or not isinstance(items[0], dict):
                raise TypeError
            item = items[0]
            settings = item.get("Config")
            state = item.get("State")
            if not isinstance(settings, dict) or not isinstance(state, dict):
                raise TypeError
            labels = settings.get("Labels") or {}
            if not isinstance(labels, dict):
                raise TypeError
            running = state.get("Running")
            if not isinstance(running, bool):
                raise TypeError
            if labels.get("com.docker.compose.project") != docker.TRAEFIK_PROJECT:
                raise HostError(
                    f"existing {docker.TRAEFIK_CONTAINER} is not an evdb Traefik container"
                )
            if running:
                return
        except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
            raise HostError(f"malformed Docker inspection: {detail}") from exc
    else:
        lowered = detail.lower()
        if "no such object:" not in lowered and "no such container:" not in lowered:
            raise HostError(detail)
    sockets = []
    try:
        for port in (5432, 6379):
            current = socket.socket()
            sockets.append(current)
            current.bind(("0.0.0.0", port))
    except OSError as exc:
        raise HostError(f"port {port} is occupied by another service") from exc
    finally:
        for current in sockets:
            current.close()


def _env_file(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise HostError(f"DNS credential file is missing or unsafe: {path}")
    values = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise HostError("DNS credential file must contain KEY=VALUE lines")
        key, value = line.split("=", 1)
        if key in values:
            raise HostError(f"DNS credential file contains duplicate key: {key}")
        values[key] = value
    try:
        return dns_values(values, "DNS credential file")
    except ConfigError as exc:
        raise HostError(str(exc)) from exc


def _writable(config: Config, unit_dir: Path) -> None:
    roots = (config.paths.config, config.paths.state, config.host.data_root, unit_dir)
    for path in roots:
        current = path
        while True:
            try:
                details = current.lstat()
            except FileNotFoundError:
                if current == current.parent:
                    raise HostError(f"canonical root is missing: {path}") from None
                current = current.parent
                continue
            if not stat.S_ISDIR(details.st_mode):
                raise HostError(f"canonical root is symlinked or unsafe: {current}")
            break
        if not os.access(current, os.W_OK | os.X_OK):
            raise HostError(f"canonical root is not writable: {path}")


def _assert_private(path: Path) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o777 != 0o600:
        raise HostError(f"managed file must be a private regular file: {path}")


def _restic_password(values: dict[str, Any]) -> str:
    supplied = values.get("restic_password")
    password_path = values.get("restic_password_file")
    if supplied is not None and password_path is not None:
        raise HostError("provide only one initial Restic password source")
    if password_path is not None:
        try:
            return private_line(password_path)
        except ValueError as exc:
            raise HostError(str(exc)) from exc
    if supplied is None or supplied == "":
        return random.token_urlsafe(48)
    if not isinstance(supplied, str) or any(character in supplied for character in "\0\r\n"):
        raise HostError("Restic password must be one non-empty line")
    return supplied


def _guard(config: Config) -> None:
    hostname = socket.gethostname().split(".", 1)[0]
    if (
        hostname == "montreal-01"
        or config.host.id == "montreal-01"
        or "montreal-01" in config.paths.config.parts
    ):
        raise HostError("production migration for montreal-01 is a separate change")
    if config.paths.config == CONFIG_DIR and os.geteuid() != 0:
        raise HostError("host initialization requires root; run sudo evdb init")

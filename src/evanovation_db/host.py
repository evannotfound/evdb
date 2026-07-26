from __future__ import annotations

import json
import os
import re
import secrets as random
import shutil
import socket
import sys
import tempfile
from dataclasses import dataclass, replace
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from importlib.resources import files
from pathlib import Path
from typing import Any

from . import compose, status
from .config import (
    BackupSettings,
    Config,
    Host,
    Paths,
    Routing,
    dump,
    load,
    load_state,
    resolve_state,
    write_state,
)
from .errors import HostError
from .files import write_bytes, write_text
from .lock import operation
from .run import run

VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
)
TOOLS = ("docker", "restic", "rclone", "systemctl", "systemd-escape", "uv")
MIN_PYTHON = (3, 10)
DEFAULT_TIMERS = (
    "evdb-status.timer",
    "evdb-backup-test.timer",
    "evdb-retention.timer",
    "evdb-prune.timer",
    "evdb-repository-check.timer",
)
DATA_DROPIN = "evdb-data-root.conf"
TIMER_MARKER = "evdb-managed.conf"


@dataclass(frozen=True)
class _FileState:
    data: bytes
    mode: int
    uid: int
    gid: int


def check(config: Config) -> dict[str, Any]:
    return status.collect(config)


def prerequisites() -> list[str]:
    missing = [name for name in TOOLS if shutil.which(name) is None]
    if shutil.which("python3") is None:
        missing.append("python3")
    elif sys.version_info < MIN_PYTHON:
        missing.append(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer")
    if "docker" not in missing:
        result = run(["docker", "compose", "version"], timeout=30, check=False)
        if result.code != 0:
            missing.append("docker compose")
    return missing


def setup(
    source: str | Path,
    values: dict[str, Any],
    *,
    yes: bool = False,
    confirm=None,
    paths: Paths | None = None,
    unit_dir: str | Path = "/etc/systemd/system",
    resolver=None,
) -> str:
    source_path = Path(source)
    managed = paths or Paths(config=source_path.parent)
    existing = source_path.is_file()
    config = load(source_path, paths=managed) if existing else _initial(values, managed)
    _guard(config, source_path)
    missing = prerequisites()
    if missing:
        raise HostError("missing prerequisites: " + ", ".join(sorted(set(missing))))
    _validate_writable(config, Path(unit_dir))
    current_state = load_state(config)
    if not _ports_available():
        raise HostError(
            "ports 5432 or 6379 are occupied; perform an explicit native routing migration"
        )

    dns_source = values.get("dns_env_file")
    dns_target = managed.traefik / "dns.env"
    if not dns_target.is_file() and not dns_source:
        raise HostError("dns_env_file is required for initial DNS-01 setup")
    dns_data = (
        _required_input(Path(dns_source), "DNS credential") if not dns_target.is_file() else None
    )
    rclone_source = values.get("rclone_config")
    rclone_target = managed.rclone / "rclone.conf"
    rclone_data = (
        _required_input(Path(rclone_source), "rclone configuration")
        if not rclone_target.is_file() and rclone_source
        else None
    )
    running_version, bootstrap_version = _setup_version(managed.tool)

    preview = (
        f"Host: {config.host.id}\n"
        f"Configuration: {managed.source}\n"
        "Create evdb directories, native routing, and systemd units"
    )
    if not yes and (confirm is None or not confirm(preview)):
        return "Cancelled"
    state = resolve_state(config, current_state, resolver=resolver)
    state = replace(state, tool_version=state.tool_version or _package_version())
    candidate_root, candidate_compose = _traefik_candidate(config, state)
    try:
        unit_root = Path(unit_dir)
        sources = _units()
        timer_names, instances = _desired_timers(config, state, sources)
        dropins = _generated_dropins(config, unit_root, instances)
        introduced = _introduced_timers(unit_root, sources, instances)
        acme = managed.traefik / "acme/acme.json"
        setup_files = {
            managed.source,
            managed.machine_state,
            managed.secrets / "restic-password",
            dns_target,
            rclone_target,
            acme,
            managed.traefik / "compose.yaml",
            *(unit_root / source.name for source in sources),
            *dropins,
        }
        snapshots = _snapshot_files(setup_files)
        timers = _timer_states(timer_names)
        current_link = managed.tool / "current"
        stable_link = _stable_path(managed.tool)
        old_current = _optional_raw_link(current_link) if running_version is not None else None
        old_stable = _optional_raw_link(stable_link) if stable_link is not None else None
    except Exception:
        shutil.rmtree(candidate_root, ignore_errors=True)
        raise
    bootstrap_created = False

    try:
        _account(managed)
        _directories(config)
        if bootstrap_version is not None:
            _bootstrap_version(running_version, bootstrap_version, config)
            bootstrap_created = True
        if not existing:
            write_text(managed.source, dump(config), mode=0o640)
        if not (managed.secrets / "restic-password").exists():
            write_text(
                managed.secrets / "restic-password",
                random.token_urlsafe(48) + "\n",
                mode=0o600,
            )
        if not dns_target.exists():
            write_text(dns_target, dns_data, mode=0o600)
        if not rclone_target.exists() and rclone_data is not None:
            write_text(rclone_target, rclone_data, mode=0o600)
        if not acme.exists():
            write_text(acme, "{}\n", mode=0o600)
        write_state(config, state)
        write_bytes(managed.traefik / "compose.yaml", candidate_compose.read_bytes(), mode=0o640)
        units_changed, _fresh = _install_units(unit_root, sources)
        dropins_changed = _install_dropins(dropins)
        if running_version is not None:
            _link(current_link, running_version)
            _stable_command(managed.tool)
        _ownership(config)
        if units_changed or dropins_changed:
            run(["systemctl", "daemon-reload"], timeout=60)
        _reconcile_timers(timers, introduced)
        compose.ensure_network(timeout=config.host.timeouts["command"])
        run(
            compose.command(managed.traefik / "compose.yaml", compose.TRAEFIK_PROJECT, "up", "-d"),
            timeout=config.host.timeouts["command"],
        )
        result = check(config)
        if not result["host"]["infrastructure"]["healthy"]:
            raise HostError("native infrastructure check failed")
    except Exception as exc:
        failures = _rollback_setup(
            snapshots,
            timers,
            current_link if running_version is not None else None,
            stable_link,
            old_current,
            old_stable,
        )
        if bootstrap_created:
            try:
                _remove_candidate(running_version, True)
            except Exception as cleanup_error:
                failures.append(str(cleanup_error))
        if failures:
            raise HostError(
                f"host setup failed and rollback was incomplete: {exc}; {'; '.join(failures)}"
            ) from exc
        raise HostError(f"host setup failed; prior files restored: {exc}") from exc
    finally:
        shutil.rmtree(candidate_root, ignore_errors=True)
    return "Host setup complete"


def update(
    config: Config,
    selected: str,
    *,
    yes: bool = False,
    confirm=None,
    unit_dir: str | Path = "/etc/systemd/system",
) -> str:
    if not VERSION.fullmatch(selected):
        raise HostError("host update requires an exact semantic version, for example 1.2.3")
    _guard(config, config.paths.source)
    unit_root = Path(unit_dir)
    _validate_writable(config, unit_root)

    with operation(config, write=True, timeout=config.host.timeouts["maintenance"]):
        current_units = _units()
        _validate_artifacts(config, unit_root, current_units)
        versions = _public_dir(config.paths.tool / "versions")
        _validate_versions_root(versions)
        current = config.paths.tool / "current"
        previous = config.paths.tool / "previous"
        old_current = _required_tool_link(current, versions)
        old_previous = _optional_tool_link(previous, versions)
        current_state = load_state(config)
        _current_timers, current_instances = _desired_timers(config, current_state, current_units)
        current_dropins = _generated_dropins(config, unit_root, current_instances)

        candidate = versions / selected
        executable = candidate / "bin/evdb"
        created = False
        if not executable.is_file():
            if candidate.exists():
                raise HostError(f"candidate version directory is incomplete: {candidate}")
            candidate.mkdir(mode=0o755, exist_ok=True)
            created = True
            try:
                run(
                    ["uv", "tool", "install", f"evanovation-db=={selected}"],
                    timeout=config.host.timeouts["maintenance"],
                    env={
                        "UV_TOOL_DIR": str(candidate / "tools"),
                        "UV_TOOL_BIN_DIR": str(candidate / "bin"),
                    },
                )
            except Exception:
                _remove_candidate(candidate, created)
                raise

        try:
            _validate_version_dir(candidate, versions, selected)
            candidate_units = _candidate_units(candidate)
            _validate_unit_sources(candidate_units, current_units)
            _candidate_read_only(
                executable,
                config,
                unit_root,
                current_units,
                extra_paths=set(current_dropins),
                require_healthy=False,
            )
        except Exception:
            _remove_candidate(candidate, created)
            raise

        if candidate.resolve() == old_current:
            _stable_command(config.paths.tool)
            cleanup = _stage_version_cleanup(versions, {old_current, old_previous})
            pending = _finish_version_cleanup(cleanup) or _pending_version_cleanup(versions)
            return _update_result(f"evdb is already at {selected}", pending)

        preview = (
            f"Host: {config.host.id}\n"
            f"Tool version: {selected}\n"
            f"Machine state tool version: {load_state(config).tool_version} -> {selected}\n"
            "Database Compose and services will not change"
        )
        if not yes and (confirm is None or not confirm(preview)):
            _remove_candidate(candidate, created)
            return "Cancelled"

        candidate_timers, candidate_instances = _desired_timers(
            config, current_state, candidate_units
        )
        candidate_dropins = _generated_dropins(config, unit_root, candidate_instances)
        introduced = _introduced_timers(unit_root, candidate_units, candidate_instances)
        unit_names = {source.name for source in (*current_units, *candidate_units)}
        try:
            extra_paths = set(current_dropins) | set(candidate_dropins)
            snapshots = _snapshot_files(
                _artifact_paths(config, unit_root, unit_names, extra_paths=extra_paths)
            )
            timers = _timer_states(candidate_timers)
            stable = _stable_path(config.paths.tool)
            old_stable = _optional_raw_link(stable) if stable is not None else None
        except Exception:
            _remove_candidate(candidate, created)
            raise

        cleanup = None
        try:
            _link(previous, old_current)
            _link(current, candidate)
            _stable_command(config.paths.tool)
            units_changed, _fresh = _install_units(unit_root, candidate_units)
            dropins_changed = _install_dropins(candidate_dropins)
            write_state(config, replace(load_state(config), tool_version=selected))
            prior_state = snapshots[config.paths.machine_state]
            if prior_state is not None:
                _apply_metadata(config.paths.machine_state, prior_state)
            if units_changed or dropins_changed:
                run(["systemctl", "daemon-reload"], timeout=60)
            _reconcile_timers(timers, introduced)
            _candidate_read_only(
                executable,
                config,
                unit_root,
                candidate_units,
                extra_paths=set(candidate_dropins),
                require_healthy=True,
            )
            cleanup = _stage_version_cleanup(versions, {candidate.resolve(), old_current})
        except Exception as exc:
            failures = _rollback_update(
                current,
                previous,
                stable,
                old_current,
                old_previous,
                old_stable,
                snapshots,
                timers,
                cleanup,
            )
            _remove_candidate(candidate, created)
            if failures:
                detail = "; ".join(failures)
                raise HostError(
                    f"tool update failed and rollback was incomplete: {exc}; {detail}"
                ) from exc
            raise HostError(f"tool update failed; prior version restored: {exc}") from exc

        pending = _finish_version_cleanup(cleanup) or _pending_version_cleanup(versions)
        return _update_result(f"Updated evdb to {selected}", pending)


def _initial(values: dict[str, Any], paths: Paths) -> Config:
    required = (
        "host_id",
        "domain",
        "data_root",
        "acme_email",
        "dns_provider",
        "postgres_repo",
        "kv_repo",
    )
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise HostError("initial setup requires: " + ", ".join(missing))
    host = Host(
        values["host_id"],
        values["domain"],
        Path(values["data_root"]),
        BackupSettings({"postgres": values["postgres_repo"], "kv": values["kv_repo"]}),
        Routing(values["acme_email"], values["dns_provider"]),
    )
    return Config(host, (), paths)


def _guard(config: Config, source: Path) -> None:
    if config.host.id == "montreal-01" or "montreal-01" in source.parts:
        raise HostError("production migration for montreal-01 is a separate change")
    if config.paths.config == Path("/etc/evdb") and os.geteuid() != 0:
        raise HostError("host setup and update require root")


def _directories(config: Config) -> None:
    for path, mode in (
        (config.paths.config, 0o750),
        (config.paths.projects, 0o750),
        (config.paths.traefik, 0o750),
        (config.paths.secrets, 0o700),
        (config.paths.state, 0o700),
        (config.paths.state / "state", 0o700),
        (config.paths.backups, 0o700),
        (config.paths.restores, 0o700),
        (config.paths.locks, 0o700),
        (config.paths.rclone, 0o700),
        (config.paths.tool, 0o755),
        (config.paths.tool / "versions", 0o755),
        (config.paths.traefik / "acme", 0o700),
        (config.host.data_root, 0o700),
    ):
        path.mkdir(parents=True, exist_ok=True, mode=mode)
        path.chmod(mode)


def _account(paths: Paths) -> None:
    if paths.config != Path("/etc/evdb"):
        return
    group = run(["getent", "group", "evdb"], timeout=30, check=False)
    if group.code != 0:
        run(["groupadd", "--system", "evdb"], timeout=60)
    user = run(["getent", "passwd", "evdb"], timeout=30, check=False)
    if user.code != 0:
        run(
            [
                "useradd",
                "--system",
                "--gid",
                "evdb",
                "--home-dir",
                str(paths.state),
                "--shell",
                "/usr/sbin/nologin",
                "evdb",
            ],
            timeout=60,
        )
    run(["usermod", "--append", "--groups", "docker", "evdb"], timeout=60)


def _ownership(config: Config) -> None:
    if config.paths.config != Path("/etc/evdb"):
        return
    paths = config.paths
    run(["chown", "root:evdb", str(paths.config), str(paths.source)], timeout=60)
    run(["chmod", "0750", str(paths.config)], timeout=60)
    run(["chmod", "0640", str(paths.source)], timeout=60)
    service_paths = (
        paths.projects,
        paths.traefik,
        paths.secrets,
        paths.state,
    )
    run(["chown", "-R", "evdb:evdb", *(str(path) for path in service_paths)], timeout=120)
    run(["chown", "evdb:evdb", str(config.host.data_root)], timeout=60)
    run(["chown", "-R", "root:root", str(paths.tool)], timeout=120)
    run(["chmod", "0755", str(paths.tool), str(paths.tool / "versions")], timeout=60)


def _ports_available() -> bool:
    result = run(["docker", "inspect", compose.TRAEFIK_CONTAINER], timeout=10, check=False)
    if result.code == 0:
        return True
    sockets = []
    try:
        for port in (5432, 6379):
            current = socket.socket()
            sockets.append(current)
            current.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        for current in sockets:
            current.close()


def _install_units(target: Path, sources: tuple[Path, ...] | None = None) -> tuple[bool, bool]:
    target.mkdir(parents=True, exist_ok=True, mode=0o755)
    target.chmod(0o755)
    changed = False
    fresh = True
    for source in sources or _units():
        destination = target / source.name
        if destination.exists():
            fresh = False
        text = source.read_text()
        if not destination.is_file() or destination.read_text() != text:
            write_text(destination, text, mode=0o644)
            changed = True
    return changed, fresh


def _units() -> tuple[Path, ...]:
    root = Path(str(files("evanovation_db").joinpath("units")))
    return _unit_files(root)


def _candidate_status(executable: Path, config: Config, *, require_healthy: bool) -> None:
    result = run(
        [str(executable), "--config", str(config.paths.source), "status", "--json"],
        timeout=config.host.timeouts["command"],
        check=False,
    )
    if result.code not in {0, 1}:
        raise HostError(f"candidate status failed with exit code {result.code}")
    try:
        data = json.loads(result.out)
    except json.JSONDecodeError as exc:
        raise HostError("candidate returned invalid status JSON") from exc
    compatible = (
        isinstance(data, dict)
        and data.get("version") == status.VERSION
        and isinstance(data.get("healthy"), bool)
        and isinstance(data.get("host"), dict)
        and data["host"].get("id") == config.host.id
        and isinstance(data.get("databases"), dict)
        and isinstance(data.get("errors"), list)
    )
    if not compatible:
        raise HostError("candidate returned structurally incompatible status")
    if require_healthy and (result.code != 0 or data["healthy"] is not True):
        raise HostError("candidate post-activation status is unhealthy")


def _link(path: Path, target: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(path) and not path.is_symlink():
        raise HostError(f"managed tool link is not a symlink: {path}")
    temporary = path.with_name(f".{path.name}.new")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    temporary.replace(path)


def _link_target(path: Path) -> Path | None:
    if not path.is_symlink():
        return None
    target = Path(os.readlink(path))
    return (path.parent / target).resolve() if not target.is_absolute() else target.resolve()


def _optional_raw_link(path: Path) -> Path | None:
    if not os.path.lexists(path):
        return None
    if not path.is_symlink():
        raise HostError(f"managed tool link is not a symlink: {path}")
    target = Path(os.readlink(path))
    return path.parent / target if not target.is_absolute() else target


def _restore_link(path: Path, target: Path | None) -> None:
    if target is None:
        path.unlink(missing_ok=True)
    else:
        _link(path, target)


def _stable_command(tool: Path) -> None:
    path = _stable_path(tool)
    if path is not None:
        _link(path, tool / "current/bin/evdb")


def _validate_versions_root(root: Path) -> None:
    for path in root.iterdir():
        if path.name.startswith(".cleanup-"):
            if path.is_symlink() or not path.is_dir():
                raise HostError(f"invalid pending version cleanup: {path}")
            continue
        if not VERSION.fullmatch(path.name):
            raise HostError(f"unexpected entry in managed tool versions: {path}")
        _validate_version_dir(path, root, path.name)


def _stage_version_cleanup(root: Path, keep: set[Path | None]) -> Path | None:
    wanted = {path.resolve() for path in keep if path is not None}
    obsolete = [
        path
        for path in root.iterdir()
        if not path.name.startswith(".cleanup-") and path.resolve() not in wanted
    ]
    if not obsolete:
        return None
    stage = root / f".cleanup-{random.token_hex(8)}"
    stage.mkdir(mode=0o700)
    moved = []
    try:
        for path in obsolete:
            target = stage / path.name
            path.replace(target)
            moved.append(target)
    except Exception:
        for path in reversed(moved):
            path.replace(root / path.name)
        stage.rmdir()
        raise
    return stage


def _restore_version_cleanup(stage: Path | None) -> None:
    if stage is None or not stage.exists():
        return
    root = stage.parent
    for path in stage.iterdir():
        path.replace(root / path.name)
    stage.rmdir()


def _finish_version_cleanup(stage: Path | None) -> Path | None:
    if stage is None:
        return None
    try:
        shutil.rmtree(stage)
    except OSError:
        return stage
    return None


def _pending_version_cleanup(root: Path) -> Path | None:
    return next(
        (path for path in sorted(root.iterdir()) if path.name.startswith(".cleanup-")),
        None,
    )


def _update_result(message: str, pending: Path | None) -> str:
    return (
        message if pending is None else f"{message}; inactive version cleanup pending at {pending}"
    )


def _required_input(path: Path, label: str) -> str:
    if not path.is_file():
        raise HostError(f"{label} file is missing: {path}")
    data = path.read_text()
    if not data.strip():
        raise HostError(f"{label} file is empty")
    return data


def _traefik_candidate(config: Config, state) -> tuple[Path, Path]:
    parent = config.paths.config.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    root = Path(tempfile.mkdtemp(prefix=".evdb-setup-", dir=parent))
    root.chmod(0o700)
    candidate = root / "compose.yaml"
    try:
        compose.write(candidate, compose.traefik(config, state))
        compose.validate(
            candidate,
            compose.TRAEFIK_PROJECT,
            timeout=config.host.timeouts["command"],
        )
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return root, candidate


def _desired_timers(
    config: Config, state, sources: tuple[Path, ...]
) -> tuple[tuple[str, ...], dict[str, str]]:
    global_timers = {
        source.name
        for source in sources
        if source.name.endswith(".timer") and "@." not in source.name
    }
    instances = {}
    for database in config.databases:
        role = state.roles.get(database.identity)
        if database.durable and role is not None and role.installed:
            timer = _backup_timer(database.identity)
            instances[timer] = database.identity
    return tuple(sorted(global_timers | set(instances))), instances


def _backup_timer(identity: str) -> str:
    result = run(
        ["systemd-escape", "--template=evdb-backup@.timer", identity],
        timeout=30,
    )
    timer = result.out.strip()
    if (
        not timer.startswith("evdb-backup@")
        or not timer.endswith(".timer")
        or "/" in timer
        or "\n" in timer
    ):
        raise HostError(f"systemd returned an invalid backup timer for {identity}")
    return timer


def _generated_dropins(
    config: Config, unit_root: Path, instances: dict[str, str]
) -> dict[Path, str]:
    data_root = _systemd_path(config.host.data_root)
    paths = "[Service]\nReadWritePaths=\nReadWritePaths=/var/lib/evdb " + data_root + "\n"
    result = {
        unit_root / "evdb-backup@.service.d" / DATA_DROPIN: paths,
        unit_root / "evdb-backup-test.service.d" / DATA_DROPIN: paths,
    }
    for timer, identity in instances.items():
        result[unit_root / f"{timer}.d" / TIMER_MARKER] = (
            f"[Unit]\nDescription=Daily backup for {identity}\n"
        )
    return result


def _systemd_path(path: Path) -> str:
    value = str(path)
    if "\n" in value or "\r" in value:
        raise HostError("data_root cannot contain a line break in a systemd unit")
    return json.dumps(value.replace("%", "%%"))


def _install_dropins(values: dict[Path, str]) -> bool:
    changed = False
    for path, text in values.items():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        path.parent.chmod(0o755)
        if not path.is_file() or path.read_text() != text:
            write_text(path, text, mode=0o644)
            changed = True
    return changed


def _introduced_timers(
    unit_root: Path, sources: tuple[Path, ...], instances: dict[str, str]
) -> set[str]:
    introduced = {
        source.name
        for source in sources
        if source.name.endswith(".timer")
        and "@." not in source.name
        and not (unit_root / source.name).is_file()
    }
    introduced.update(
        timer for timer in instances if not (unit_root / f"{timer}.d" / TIMER_MARKER).is_file()
    )
    return introduced


def _reconcile_timers(previous: dict[str, tuple[bool, bool]], introduced: set[str]) -> None:
    _restore_timer_states(previous)
    if introduced:
        run(["systemctl", "enable", "--now", *sorted(introduced)], timeout=120)


def _rollback_setup(
    snapshots: dict[Path, _FileState | None],
    timers: dict[str, tuple[bool, bool]],
    current: Path | None,
    stable: Path | None,
    old_current: Path | None,
    old_stable: Path | None,
) -> list[str]:
    failures = []
    actions = (
        lambda: _restore_link(current, old_current) if current is not None else None,
        lambda: _restore_link(stable, old_stable) if stable is not None else None,
        lambda: _restore_files(snapshots),
        lambda: run(["systemctl", "daemon-reload"], timeout=60, check=False),
        lambda: _restore_timer_states(timers),
    )
    for action in actions:
        try:
            action()
        except Exception as exc:
            failures.append(str(exc))
    return failures


def _validate_writable(config: Config, unit_dir: Path) -> None:
    roots = {
        "configuration": config.paths.config,
        "state": config.paths.state,
        "tool": config.paths.tool,
        "data": config.host.data_root,
        "systemd units": unit_dir,
    }
    if config.paths.tool == Path("/opt/evdb"):
        roots["stable command"] = Path("/usr/local/bin")
    invalid = [f"{name} ({path})" for name, path in roots.items() if not _writable(path)]
    if invalid:
        raise HostError("canonical roots are not writable: " + ", ".join(invalid))


def _writable(path: Path) -> bool:
    if path.exists() and not path.is_dir():
        return False
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    try:
        read_only = bool(os.statvfs(current).f_flag & getattr(os, "ST_RDONLY", 1))
    except OSError:
        return False
    return not read_only and os.access(current, os.W_OK | os.X_OK)


def _public_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o755)
    path.chmod(0o755)
    return path


def _package_version() -> str | None:
    try:
        return package_version("evanovation-db")
    except PackageNotFoundError:
        return None


def _setup_version(tool: Path) -> tuple[Path | None, str | None]:
    if tool != Path("/opt/evdb"):
        return None, None
    versions = tool / "versions"
    current_path = tool / "current"
    current = _link_target(current_path)
    if current is None and os.path.lexists(current_path):
        raise HostError(f"managed tool link is invalid: {current_path}")
    command_name = shutil.which("evdb")
    command = Path(command_name) if command_name else Path(sys.argv[0])
    running = _version_root(command, versions)
    if current is not None:
        _validate_tool_target(current, versions)
        if running is not None and running != current:
            raise HostError("host setup is not running from the active evdb tool version")
        return current, None
    if running is not None:
        _validate_tool_target(running, versions)
        return running, None
    selected = _package_version()
    if selected is None or not VERSION.fullmatch(selected):
        raise HostError("installed evdb package does not have an exact semantic version")
    target = versions / selected
    if target.exists():
        _validate_version_dir(target, versions, selected)
        return target, None
    return target, selected


def _bootstrap_version(target: Path, selected: str, config: Config) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    target.mkdir(mode=0o755)
    try:
        run(
            ["uv", "tool", "install", f"evanovation-db=={selected}"],
            timeout=config.host.timeouts["maintenance"],
            env={
                "UV_TOOL_DIR": str(target / "tools"),
                "UV_TOOL_BIN_DIR": str(target / "bin"),
            },
        )
        _validate_version_dir(target, target.parent, selected)
    except Exception:
        _remove_candidate(target, True)
        raise


def _version_root(command: Path, versions: Path) -> Path | None:
    for value in (command.absolute(), command.resolve()):
        try:
            relative = value.relative_to(versions)
        except ValueError:
            continue
        if relative.parts:
            return (versions / relative.parts[0]).resolve()
    return None


def _validate_tool_target(target: Path, versions: Path) -> None:
    try:
        relative = target.resolve().relative_to(versions.resolve())
    except ValueError as exc:
        raise HostError(f"tool link points outside managed versions: {target}") from exc
    if len(relative.parts) != 1 or not VERSION.fullmatch(relative.name):
        raise HostError(f"tool link does not select an exact version: {target}")
    _validate_version_dir(target, versions, relative.name)


def _validate_version_dir(target: Path, versions: Path, selected: str) -> None:
    if target.is_symlink() or not target.is_dir():
        raise HostError(f"invalid version directory: {target}")
    if target.name != selected or target.parent.resolve() != versions.resolve():
        raise HostError(f"version directory does not match {selected}: {target}")
    executable = target / "bin/evdb"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise HostError(f"tool version has no executable evdb command: {target}")
    try:
        executable.resolve().relative_to(target.resolve())
    except ValueError as exc:
        raise HostError(f"tool executable points outside its version directory: {target}") from exc


def _required_tool_link(path: Path, versions: Path) -> Path:
    target = _link_target(path)
    if target is None:
        raise HostError(f"active tool link is missing: {path}")
    _validate_tool_target(target, versions)
    return target


def _optional_tool_link(path: Path, versions: Path) -> Path | None:
    target = _link_target(path)
    if target is not None:
        _validate_tool_target(target, versions)
    elif os.path.lexists(path):
        raise HostError(f"managed tool link is invalid: {path}")
    return target


def _stable_path(tool: Path) -> Path | None:
    return Path("/usr/local/bin/evdb") if tool == Path("/opt/evdb") else None


def _unit_files(root: Path) -> tuple[Path, ...]:
    return tuple(sorted((*root.glob("*.service"), *root.glob("*.timer"))))


def _candidate_units(candidate: Path) -> tuple[Path, ...]:
    roots = {
        path.resolve()
        for pattern in (
            "tools/*/lib/python*/site-packages/evanovation_db/units",
            "lib/python*/site-packages/evanovation_db/units",
        )
        for path in candidate.glob(pattern)
        if path.is_dir()
    }
    if len(roots) != 1:
        raise HostError("candidate package does not contain one canonical unit directory")
    return _unit_files(roots.pop())


def _validate_unit_sources(candidate: tuple[Path, ...], current: tuple[Path, ...]) -> None:
    required = {path.name for path in current}
    available = {path.name for path in candidate}
    missing = sorted(required - available)
    if missing:
        raise HostError("candidate package is missing canonical units: " + ", ".join(missing))
    for path in candidate:
        if path.is_symlink() or not path.is_file() or not path.read_text().strip():
            raise HostError(f"candidate package contains an invalid unit: {path.name}")


def _validate_artifacts(config: Config, unit_root: Path, sources: tuple[Path, ...]) -> None:
    loaded = load(config.paths.source, paths=config.paths)
    if loaded != config:
        raise HostError("current source configuration changed before update")
    if not config.paths.machine_state.is_file():
        raise HostError("current machine state is missing")
    state = load_state(config)
    _require_regular(config.paths.traefik / "compose.yaml", "current Traefik Compose")
    for database in config.databases:
        role = state.roles.get(database.identity)
        if role is not None and role.installed:
            _require_regular(database.compose, f"current Compose for {database.identity}")
    manifests = config.paths.backups.rglob("backup.json") if config.paths.backups.exists() else ()
    for manifest in manifests:
        _require_regular(manifest, "backup record")
        try:
            data = json.loads(manifest.read_text())
        except json.JSONDecodeError as exc:
            raise HostError(f"invalid backup record: {manifest}") from exc
        if not isinstance(data, dict):
            raise HostError(f"invalid backup record: {manifest}")
    for source in sources:
        destination = unit_root / source.name
        _require_regular(destination, f"installed unit {source.name}")
        if destination.read_bytes() != source.read_bytes():
            raise HostError(f"installed unit differs from the active package: {source.name}")


def _require_regular(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise HostError(f"{label} is missing or invalid: {path}")


def _artifact_paths(
    config: Config,
    unit_root: Path,
    unit_names: set[str],
    *,
    extra_paths: set[Path] | None = None,
) -> set[Path]:
    result = {
        config.paths.source,
        config.paths.previous,
        config.paths.machine_state,
        config.paths.traefik / "compose.yaml",
    }
    result.update(unit_root / name for name in unit_names)
    result.update(extra_paths or ())
    if config.paths.projects.exists():
        result.update(config.paths.projects.rglob("compose.yaml"))
    if config.paths.backups.exists():
        result.update(config.paths.backups.rglob("backup.json"))
    return result


def _snapshot_files(paths: set[Path]) -> dict[Path, _FileState | None]:
    result = {}
    for path in paths:
        if path.is_symlink():
            raise HostError(f"managed update file must not be a symlink: {path}")
        if not path.exists():
            result[path] = None
            continue
        if not path.is_file():
            raise HostError(f"managed update path must be a file: {path}")
        details = path.stat()
        result[path] = _FileState(
            path.read_bytes(), details.st_mode & 0o777, details.st_uid, details.st_gid
        )
    return result


def _matches_file(path: Path, state: _FileState | None) -> bool:
    if state is None:
        return not os.path.lexists(path)
    if path.is_symlink() or not path.is_file():
        return False
    details = path.stat()
    return (
        path.read_bytes() == state.data
        and details.st_mode & 0o777 == state.mode
        and details.st_uid == state.uid
        and details.st_gid == state.gid
    )


def _restore_files(values: dict[Path, _FileState | None]) -> None:
    for path, state in values.items():
        if _matches_file(path, state):
            continue
        if state is None:
            if path.is_dir() and not path.is_symlink():
                raise HostError(f"cannot remove unexpected directory during rollback: {path}")
            path.unlink(missing_ok=True)
            continue
        if path.is_symlink():
            path.unlink()
        write_bytes(path, state.data, mode=state.mode)
        _apply_metadata(path, state)


def _apply_metadata(path: Path, state: _FileState) -> None:
    path.chmod(state.mode)
    details = path.stat()
    if (details.st_uid, details.st_gid) != (state.uid, state.gid):
        os.chown(path, state.uid, state.gid)


def _candidate_read_only(
    executable: Path,
    config: Config,
    unit_root: Path,
    sources: tuple[Path, ...],
    *,
    extra_paths: set[Path],
    require_healthy: bool,
) -> None:
    names = {source.name for source in sources}
    before_paths = _artifact_paths(config, unit_root, names, extra_paths=extra_paths)
    before = _snapshot_files(before_paths)
    failure = None
    try:
        _candidate_status(executable, config, require_healthy=require_healthy)
    except Exception as exc:
        failure = exc
    after_paths = _artifact_paths(config, unit_root, names, extra_paths=extra_paths)
    after = _snapshot_files(after_paths)
    if before != after:
        added = after_paths - before_paths
        _restore_files({**{path: None for path in added}, **before})
        raise HostError("candidate compatibility check modified managed host files")
    if failure is not None:
        raise failure


def _timer_states(names: tuple[str, ...]) -> dict[str, tuple[bool, bool]]:
    result = {}
    for name in names:
        enabled = run(["systemctl", "is-enabled", name], timeout=20, check=False)
        active = run(["systemctl", "is-active", name], timeout=20, check=False)
        result[name] = (
            enabled.code == 0
            and enabled.out.strip() in {"enabled", "enabled-runtime", "linked", "linked-runtime"},
            active.code == 0 and active.out.strip() in {"active", "activating"},
        )
    return result


def _restore_timer_states(expected: dict[str, tuple[bool, bool]]) -> None:
    current = _timer_states(tuple(expected))
    for name, (enabled, active) in expected.items():
        now_enabled, now_active = current[name]
        if now_enabled != enabled:
            run(["systemctl", "enable" if enabled else "disable", name], timeout=60)
        if now_active != active:
            run(["systemctl", "start" if active else "stop", name], timeout=60)


def _rollback_update(
    current: Path,
    previous: Path,
    stable: Path | None,
    old_current: Path,
    old_previous: Path | None,
    old_stable: Path | None,
    snapshots: dict[Path, _FileState | None],
    timers: dict[str, tuple[bool, bool]],
    cleanup: Path | None,
) -> list[str]:
    failures = []
    actions = (
        lambda: _restore_version_cleanup(cleanup),
        lambda: _restore_link(current, old_current),
        lambda: _restore_link(previous, old_previous),
        lambda: _restore_link(stable, old_stable) if stable is not None else None,
        lambda: _restore_files(snapshots),
        lambda: run(["systemctl", "daemon-reload"], timeout=60, check=False),
        lambda: _restore_timer_states(timers),
    )
    for action in actions:
        try:
            action()
        except Exception as exc:
            failures.append(str(exc))
    return failures


def _remove_candidate(candidate: Path, created: bool) -> None:
    if created and candidate.is_dir() and not candidate.is_symlink():
        shutil.rmtree(candidate)

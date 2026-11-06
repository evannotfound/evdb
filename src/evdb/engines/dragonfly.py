from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .. import docker
from ..errors import BackupError, ConfigError
from ..files import require_file
from ..models import KV, Database
from . import kv

_MEMORY = re.compile(r"[1-9][0-9]*(?:kb|mb|gb)")


def validate(settings: KV) -> None:
    from ..config import validate_image

    if not isinstance(settings, KV) or settings.engine != "dragonfly":
        raise ConfigError("Dragonfly settings must use engine dragonfly")
    validate_image(settings.image, "Dragonfly image")
    validate_image(settings.http.image, "HTTP image")
    if settings.mode not in {"durable", "cache"}:
        raise ConfigError("Dragonfly mode must be durable or cache")
    if not isinstance(settings.memory, str) or not _MEMORY.fullmatch(settings.memory):
        raise ConfigError("Dragonfly memory must be a positive kb, mb, or gb value")
    if type(settings.threads) is not int or settings.threads < 1:
        raise ConfigError("Dragonfly threads must be positive")
    if settings.cluster_mode not in ("off", "emulated"):
        raise ConfigError("Dragonfly cluster_mode must be off or emulated")
    if type(settings.lock_on_hashtags) is not bool:
        raise ConfigError("Dragonfly lock_on_hashtags must be a boolean")
    if settings.lock_on_hashtags and settings.cluster_mode != "emulated":
        raise ConfigError("Dragonfly lock_on_hashtags requires cluster_mode emulated")
    if not isinstance(settings.snapshot_cron, str) or (
        settings.snapshot_cron != "off" and not _cron(settings.snapshot_cron)
    ):
        raise ConfigError(
            "Dragonfly snapshot_cron must be off or a five-field numeric cron schedule"
        )
    kv.validate_http(settings)


def _cron(value: str) -> bool:
    fields = value.split()
    if len(fields) != 5 or any(char in value for char in "\0\r\n"):
        return False
    for field, (low, high) in zip(
        fields, ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6)), strict=True
    ):
        for part in field.split(","):
            match = re.fullmatch(r"(\*|[0-9]+(?:-[0-9]+)?)(?:/([0-9]+))?", part)
            if not match:
                return False
            base, step = match.groups()
            if step is not None and int(step) < 1:
                return False
            if base != "*":
                bounds = [int(item) for item in base.split("-")]
                if not low <= bounds[0] <= bounds[-1] <= high:
                    return False
    return True


def files(database: Database) -> dict[str, str]:
    lines = [
        "--dir=/data",
        "--dbfilename=dump",
        f"--requirepass={database.credentials.password}",
        f"--proactor_threads={database.settings.threads}",
        f"--maxmemory={database.settings.memory}",
    ]
    if database.settings.mode == "cache":
        lines.append("--cache_mode=true")
    values = {"dragonfly.flags": "\n".join(lines) + "\n"}
    if database.settings.http.enabled:
        values["http.env"] = kv.http_env(database)
    return values


def services(database: Database) -> dict[str, Any]:
    primary = database.service("primary")
    command = [
        "/usr/local/bin/dragonfly",
        "--logtostderr",
        "--flagfile=/run/secrets/dragonfly.flags",
    ]
    if database.settings.cluster_mode == "emulated":
        command.extend(
            [
                "--cluster_mode=emulated",
                f"--cluster_announce_ip={database.domain}",
                f"--announce_port={database.port}",
            ]
        )
    if database.settings.lock_on_hashtags:
        command.append("--lock_on_hashtags=true")
    if database.settings.snapshot_cron != "off":
        command.append(f"--snapshot_cron={' '.join(database.settings.snapshot_cron.split())}")
    service: dict[str, Any] = {
        "image": database.settings.image,
        "container_name": primary,
        "restart": "unless-stopped",
        "command": command,
        "volumes": [
            f"{database.data}:/data",
            f"{database.generated / 'dragonfly.flags'}:/run/secrets/dragonfly.flags:ro",
        ],
        "cap_drop": ["ALL"],
        "cap_add": ["DAC_OVERRIDE"],
        "security_opt": ["no-new-privileges:true"],
        "networks": {docker.NETWORK: {"aliases": [primary]}},
        "labels": docker.route(database, 6379),
    }
    values = {primary: service}
    if database.settings.http.enabled:
        values[database.service("http")] = kv.http_service(database)
    return values


def health(database: Database, *, timeout: int = 10) -> bool:
    return kv.health(
        database.service("primary"),
        database.credentials.password,
        engine="dragonfly",
        timeout=timeout,
    )


def info(database: Database) -> dict[str, Any]:
    facts = kv.info(
        database.service("primary"), database.credentials.password, "server", engine="dragonfly"
    )
    return {
        "version": facts.get("dragonfly_version") or "unknown",
        "mode": database.settings.mode,
        "memory": database.settings.memory,
        "threads": database.settings.threads,
        "cluster_mode": database.settings.cluster_mode,
        "lock_on_hashtags": database.settings.lock_on_hashtags,
        "snapshot_cron": database.settings.snapshot_cron,
        "http": database.settings.http.enabled,
        "http_connections": database.settings.http.connections,
        "data": kv.usage(
            database.service("primary"), database.credentials.password, engine="dragonfly"
        ),
    }


def backup(database: Database, folder: Path, run_id: str) -> dict[str, Any]:
    password = database.credentials.password
    container = database.service("primary")
    base = f"evdb-{run_id}"
    if _sources(container, base):
        raise BackupError(f"{database.identity}: Dragonfly backup source already exists")
    files = ()
    error = None
    try:
        deadline = time.monotonic() + 1800
        while True:
            reply = kv.text(
                container, password, ["SAVE", "DF", base], engine="dragonfly", timeout=1800
            )
            if reply == "OK":
                break
            if reply != "ERR SAVING - can not save database":
                raise BackupError(f"{database.identity}: Dragonfly SAVE failed: {reply}")
            if time.monotonic() >= deadline:
                raise BackupError(f"{database.identity}: Dragonfly snapshot is still in progress")
            time.sleep(0.5)
        files = _snapshot(base, _sources(container, base))
        for item in files:
            docker.copy(f"{container}:/data/{item}", folder / item, timeout=1800)
            require_file(folder / item)
        facts = _check(database, folder, base, run_id)
    except BaseException as exc:
        error = exc
        raise
    finally:
        try:
            _cleanup(container, _sources(container, base))
        except BaseException as exc:
            if error is None:
                raise BackupError(f"{database.identity}: Dragonfly source cleanup failed") from exc
    return {
        "format": "dragonfly-dfs-v1",
        **facts,
        "snapshot_base": base,
        "files": list(files),
    }


def _sources(container: str, base: str) -> tuple[str, ...]:
    result = docker.exec(
        container,
        [
            "find",
            "/data",
            "-maxdepth",
            "1",
            "-type",
            "f",
            "-name",
            f"{base}-*.dfs",
            "-printf",
            "%f\\n",
        ],
        timeout=60,
    )
    files = tuple(sorted(item for item in result.out.splitlines() if item))
    if any(Path(item).name != item or not item.startswith(f"{base}-") for item in files):
        raise BackupError("Dragonfly returned an unsafe snapshot filename")
    return files


def _snapshot(base: str, files: tuple[str, ...]) -> tuple[str, ...]:
    summary = f"{base}-summary.dfs"
    shards = []
    for item in files:
        if item == summary:
            continue
        match = re.fullmatch(rf"{re.escape(base)}-([0-9]{{4}})\.dfs", item)
        if not match:
            raise BackupError(f"Dragonfly snapshot contains an unexpected file: {item}")
        shards.append(int(match.group(1)))
    if files.count(summary) != 1 or not shards or sorted(shards) != list(range(max(shards) + 1)):
        raise BackupError("Dragonfly snapshot file set is incomplete")
    return tuple(sorted(files))


def _cleanup(container: str, files: tuple[str, ...]) -> None:
    if files:
        result = docker.exec(
            container,
            ["rm", "-f", "--", *(f"/data/{item}" for item in files)],
            timeout=60,
            check=False,
        )
        if result.code:
            raise BackupError("Dragonfly snapshot cleanup command failed")


def _check(database: Database, folder: Path, base: str, run_id: str) -> dict[str, Any]:
    name = f"evdb-backup-check-{database.project}-{database.role}-{run_id}"
    args = [
        "/usr/local/bin/dragonfly",
        "--logtostderr",
        "--dir=/data",
        f"--dbfilename={base}",
        "--primary_port_http_enabled=false",
        f"--proactor_threads={database.settings.threads}",
        f"--maxmemory={database.settings.memory}",
    ]
    try:
        docker.start(database.image, name, args, mounts=[(folder, "/data", True)], memory="3g")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if kv.health(name, "", engine="dragonfly"):
                return kv.facts(name, "", engine="dragonfly")
            time.sleep(1)
        raise BackupError("Dragonfly backup check did not become healthy")
    finally:
        docker.remove(name)

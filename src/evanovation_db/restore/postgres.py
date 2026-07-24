from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import unquote

from .. import docker, manifest
from ..config import Host, Instance
from ..errors import RestoreError

OBJECT_SQL = (
    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
    "WHERE c.relkind IN ('r','p','v','m','S') "
    "AND n.nspname NOT IN ('pg_catalog','information_schema')"
)


def restore(host: Host, instance: Instance, folder: Path, name: str) -> dict:
    del host
    data = manifest.check(folder)
    docker.start(
        str(instance.target["image"]),
        name,
        env={"POSTGRES_USER": "restore_admin", "POSTGRES_HOST_AUTH_METHOD": "trust"},
        memory="2g",
        network="none",
        timeout=300,
    )
    _wait(name)
    docker.copy(folder / "globals.sql", f"{name}:/tmp/globals.sql")
    docker.exec(
        name,
        [
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "restore_admin",
            "-d",
            "postgres",
            "-f",
            "/tmp/globals.sql",
        ],
        timeout=600,
    )
    restored = {}
    owners = data.get("facts", {}).get("owners", {})
    for archive in sorted((folder / "databases").glob("*.dump")):
        database = unquote(archive.stem)
        owner = owners.get(database, "restore_admin")
        remote = f"/tmp/{archive.name}"
        docker.copy(archive, f"{name}:{remote}")
        if database != "postgres":
            docker.exec(
                name,
                [
                    "createdb",
                    "-U",
                    "restore_admin",
                    "-T",
                    "template0",
                    "-O",
                    owner,
                    database,
                ],
                timeout=120,
            )
        elif owner != "restore_admin":
            escaped = owner.replace('"', '""')
            docker.exec(
                name,
                [
                    "psql",
                    "-X",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-U",
                    "restore_admin",
                    "-d",
                    "postgres",
                    "-c",
                    f'ALTER DATABASE postgres OWNER TO "{escaped}"',
                ],
            )
        docker.exec(
            name,
            ["pg_restore", "--exit-on-error", "-U", "restore_admin", "-d", database, remote],
            timeout=7200,
        )
        result = docker.exec(
            name,
            [
                "psql",
                "-X",
                "-A",
                "-t",
                "-U",
                "restore_admin",
                "-d",
                database,
                "-c",
                OBJECT_SQL,
            ],
        )
        restored[database] = int(result.out.strip())
    expected = data.get("facts", {}).get("objects", {})
    if expected and restored != expected:
        raise RestoreError(f"Postgres object counts differ: {restored} != {expected}")
    return {"databases": sorted(restored), "objects": restored}


def _wait(name: str, timeout: int = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = docker.exec(
            name,
            ["pg_isready", "-U", "restore_admin", "-d", "postgres"],
            timeout=10,
            check=False,
        )
        if result.code == 0:
            return
        time.sleep(1)
    raise RestoreError("Postgres restore container did not become ready")

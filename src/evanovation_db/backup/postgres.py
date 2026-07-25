from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .. import docker
from ..config import Host, Instance
from ..errors import BackupError
from ..run import run

DATABASE_SQL = (
    "SELECT json_build_object('name',datname,'owner',pg_get_userbyid(datdba))::text "
    "FROM pg_database WHERE datallowconn AND NOT datistemplate ORDER BY datname"
)
OBJECT_SQL = (
    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
    "WHERE c.relkind IN ('r','p','v','m','S') "
    "AND n.nspname NOT IN ('pg_catalog','information_schema')"
)


def backup(host: Host, instance: Instance, folder: Path, run_id: str) -> dict[str, Any]:
    del host, run_id
    user = str(instance.settings.get("user", "default"))
    database_rows = _psql(instance, user, "postgres", DATABASE_SQL).splitlines()
    databases = [json.loads(row) for row in database_rows]
    if not databases:
        raise BackupError(f"{instance.id}: no databases found")

    database_dir = folder / "databases"
    database_dir.mkdir(mode=0o700)
    files = []
    objects = {}
    for item in databases:
        database = item["name"]
        name = quote(database, safe="") + ".dump"
        relative = f"databases/{name}"
        target = folder / relative
        with target.open("wb") as output:
            docker.exec(
                instance.container,
                ["pg_dump", "-Fc", "--no-password", "-U", user, "-d", database],
                stdout=output,
                timeout=7200,
            )
        target.chmod(0o600)
        _check_archive(instance, target)
        objects[database] = int(_psql(instance, user, database, OBJECT_SQL))
        files.append(relative)

    globals_file = folder / "globals.sql"
    with globals_file.open("wb") as output:
        docker.exec(
            instance.container,
            ["pg_dumpall", "--globals-only", "--no-password", "-U", user],
            stdout=output,
            timeout=1800,
        )
    globals_file.chmod(0o600)
    if globals_file.stat().st_size == 0:
        raise BackupError(f"{instance.id}: globals.sql is empty")
    files.append("globals.sql")

    version = _psql(instance, user, "postgres", "SHOW server_version")
    return {
        "version": version,
        "databases": [item["name"] for item in databases],
        "owners": {item["name"]: item["owner"] for item in databases},
        "objects": objects,
        "files": files,
    }


def _psql(instance: Instance, user: str, database: str, sql: str) -> str:
    result = docker.exec(
        instance.container,
        ["psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-U", user, "-d", database, "-c", sql],
        timeout=120,
    )
    return result.out.strip()


def _check_archive(instance: Instance, archive: Path) -> None:
    if archive.stat().st_size == 0:
        raise BackupError(f"{archive.name} is empty")
    image = instance.image
    run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--volume",
            f"{archive.resolve()}:/backup.dump:ro",
            image,
            "pg_restore",
            "--list",
            "/backup.dump",
        ],
        timeout=600,
    )

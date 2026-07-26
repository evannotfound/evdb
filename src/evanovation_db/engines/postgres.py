from __future__ import annotations

import json
import secrets as random
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from .. import docker
from ..config import Config, Database, MachineState
from ..errors import BackupError, RestoreError
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


def health(
    container: str,
    *,
    user: str = "default",
    database: str = "postgres",
    timeout: int = 10,
) -> bool:
    result = docker.exec(
        container,
        ["pg_isready", "-U", user, "-d", database],
        timeout=timeout,
        check=False,
    )
    return result.code == 0


def backup(
    config: Config,
    database: Database,
    folder: Path,
    run_id: str,
    state: MachineState,
) -> dict[str, Any]:
    del config, run_id
    container = _container(database)
    user = database.settings.user
    rows = _psql(container, user, "postgres", DATABASE_SQL).splitlines()
    databases = [json.loads(row) for row in rows]
    if not databases:
        raise BackupError(f"{database.identity}: no databases found")
    database_dir = folder / "databases"
    database_dir.mkdir(mode=0o700)
    files = []
    objects = {}
    for item in databases:
        name = item["name"]
        relative = f"databases/{quote(name, safe='')}.dump"
        target = folder / relative
        with target.open("wb") as output:
            docker.exec(
                container,
                ["pg_dump", "-Fc", "--no-password", "-U", user, "-d", name],
                stdout=output,
                timeout=7200,
            )
        target.chmod(0o600)
        _check_archive(state.roles[database.identity].images["primary"].image, target)
        objects[name] = int(_psql(container, user, name, OBJECT_SQL))
        files.append(relative)
    globals_file = folder / "globals.sql"
    with globals_file.open("wb") as output:
        docker.exec(
            container,
            ["pg_dumpall", "--globals-only", "--no-password", "-U", user],
            stdout=output,
            timeout=1800,
        )
    globals_file.chmod(0o600)
    if globals_file.stat().st_size == 0:
        raise BackupError(f"{database.identity}: globals.sql is empty")
    files.append("globals.sql")
    version = _psql(container, user, "postgres", "SHOW server_version")
    return {
        "format": "postgres-custom-v1",
        "version": version,
        "databases": [item["name"] for item in databases],
        "owners": {item["name"]: item["owner"] for item in databases},
        "objects": objects,
        "files": files,
    }


def restore(
    config: Config,
    database: Database,
    folder: Path,
    name: str,
    work: Path,
    state: MachineState,
    record: dict[str, Any],
) -> dict[str, Any]:
    del config
    if work.exists() and (not work.is_dir() or any(work.iterdir())):
        raise RestoreError("Postgres restore candidate directory is not empty")
    work.mkdir(mode=0o700, exist_ok=True)
    password = random.token_urlsafe(32)
    image = state.roles[database.identity].images["primary"].image
    docker.start(
        image,
        name,
        mounts=[(work, "/var/lib/postgresql/data", False)],
        env={
            "POSTGRES_USER": "restore_admin",
            "POSTGRES_PASSWORD": password,
            "POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256",
        },
        memory="2g",
        network="none",
        timeout=300,
        secrets=[password],
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
    owners = record.get("facts", {}).get("owners", {})
    for archive in sorted((folder / "databases").glob("*.dump")):
        db_name = unquote(archive.stem)
        owner = owners.get(db_name, "restore_admin")
        remote = f"/tmp/{archive.name}"
        docker.copy(archive, f"{name}:{remote}")
        if db_name != "postgres":
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
                    db_name,
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
            ["pg_restore", "--exit-on-error", "-U", "restore_admin", "-d", db_name, remote],
            timeout=7200,
        )
        restored[db_name] = int(_psql(name, "restore_admin", db_name, OBJECT_SQL))
    expected = record.get("facts", {}).get("objects", {})
    if expected and restored != expected:
        raise RestoreError(f"Postgres object counts differ: {restored} != {expected}")
    return {"databases": sorted(restored), "objects": restored}


def _psql(container: str, user: str, database: str, sql: str) -> str:
    result = docker.exec(
        container,
        [
            "psql",
            "-X",
            "-A",
            "-t",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            user,
            "-d",
            database,
            "-c",
            sql,
        ],
        timeout=120,
    )
    return result.out.strip()


def _check_archive(image: str, archive: Path) -> None:
    if not archive.is_file() or archive.stat().st_size == 0:
        raise BackupError(f"{archive.name} is empty")
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


def _wait(name: str, timeout: int = 120) -> None:
    deadline = time.monotonic() + timeout
    ready = 0
    while time.monotonic() < deadline:
        if health(name, user="restore_admin"):
            ready += 1
            if ready == 2:
                return
        else:
            ready = 0
        time.sleep(1)
    raise RestoreError("Postgres restore container did not become ready")


def _container(database: Database) -> str:
    return f"evdb-{database.project}-{database.role}-primary"

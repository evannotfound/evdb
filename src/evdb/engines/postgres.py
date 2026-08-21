from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .. import docker
from ..errors import BackupError, CommandError, ConfigError
from ..models import Database, Postgres

DATABASE_SQL = (
    "SELECT json_build_object('name',datname,'owner',pg_get_userbyid(datdba))::text "
    "FROM pg_database WHERE datallowconn AND NOT datistemplate ORDER BY datname"
)
OBJECT_SQL = (
    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
    "WHERE c.relkind IN ('r','p','v','m','S') "
    "AND n.nspname NOT IN ('pg_catalog','information_schema')"
)
DATA_SQL = (
    "SELECT json_build_object('logical_bytes',coalesce(sum(pg_database_size(datname)),0),"
    "'databases',count(*))::text FROM pg_database "
    "WHERE datallowconn AND NOT datistemplate"
)


def validate(settings: Postgres) -> None:
    from ..config import validate_image, validate_postgres_name

    if not isinstance(settings, Postgres):
        raise ConfigError("postgres settings are invalid")
    validate_image(settings.image, "postgres image")
    validate_postgres_name(settings.username, "username")
    validate_postgres_name(settings.database, "database name")
    pool = settings.pgbouncer
    validate_image(pool.image, "pgbouncer image")
    if type(pool.enabled) is not bool:
        raise ConfigError("pgbouncer.enabled must be a boolean")
    for name in ("max_clients", "pool_size", "reserve_size"):
        if type(getattr(pool, name)) is not int or getattr(pool, name) < 1:
            raise ConfigError(f"pgbouncer.{name} must be positive")


def files(database: Database) -> dict[str, str]:
    password = database.credentials.password
    values = {"postgres-password": password + "\n"}
    if database.settings.pgbouncer.enabled:

        def quoted(value: str) -> str:
            return '"' + value.replace('"', '""') + '"'

        values["pgbouncer-users"] = f"{quoted(database.settings.username)} {quoted(password)}\n"
        pool = database.settings.pgbouncer
        values["pgbouncer.ini"] = (
            "[databases]\n"
            f"* = host={database.service('primary')} port=5432\n\n"
            "[pgbouncer]\n"
            "listen_addr = 0.0.0.0\n"
            "listen_port = 5432\n"
            "auth_type = plain\n"
            "auth_file = /run/secrets/pgbouncer-users\n"
            f"max_client_conn = {pool.max_clients}\n"
            f"default_pool_size = {pool.pool_size}\n"
            f"reserve_pool_size = {pool.reserve_size}\n"
            "ignore_startup_parameters = extra_float_digits\n"
        )
    return values


def services(database: Database) -> dict[str, Any]:
    primary = database.service("primary")
    generated = database.generated
    service: dict[str, Any] = {
        "image": database.settings.image,
        "container_name": primary,
        "restart": "unless-stopped",
        "environment": {
            "POSTGRES_USER": database.settings.username,
            "POSTGRES_DB": database.settings.database,
            "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres-password",
        },
        "volumes": [
            f"{database.data}:/var/lib/postgresql/data",
            f"{generated / 'postgres-password'}:/run/secrets/postgres-password:ro",
        ],
        "networks": {docker.NETWORK: {"aliases": [primary]}},
    }
    values = {primary: service}
    route = service
    if database.settings.pgbouncer.enabled:
        pool = database.service("pgbouncer")
        group = _group(generated)
        values[pool] = {
            "image": database.settings.pgbouncer.image,
            "container_name": pool,
            "restart": "unless-stopped",
            "command": ["pgbouncer", "/etc/pgbouncer/pgbouncer.ini"],
            "depends_on": [primary],
            "healthcheck": docker.healthcheck(
                [
                    "CMD",
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-p",
                    "5432",
                    "-U",
                    database.settings.username,
                    "-d",
                    database.settings.database,
                ]
            ),
            "volumes": [
                f"{generated / 'pgbouncer.ini'}:/etc/pgbouncer/pgbouncer.ini:ro",
                f"{generated / 'pgbouncer-users'}:/run/secrets/pgbouncer-users:ro",
            ],
            "group_add": [str(group)],
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "networks": {docker.NETWORK: {"aliases": [pool]}},
        }
        route = values[pool]
    route["labels"] = docker.route(database, 5432)
    return values


def health(database: Database, *, timeout: int = 10) -> bool:
    result = docker.exec(
        database.service("primary"),
        [
            "pg_isready",
            "-U",
            database.settings.username,
            "-d",
            database.settings.database,
        ],
        timeout=timeout,
        check=False,
    )
    if result.code != 0 or not database.settings.pgbouncer.enabled:
        return result.code == 0
    environment = {"PGPASSWORD": database.credentials.password, "PGCONNECT_TIMEOUT": "5"}
    try:
        result = docker.exec(
            database.service("pgbouncer"),
            [
                "psql",
                "-X",
                "-A",
                "-t",
                "-v",
                "ON_ERROR_STOP=1",
                "-h",
                "127.0.0.1",
                "-p",
                "5432",
                "-U",
                database.settings.username,
                "-d",
                database.settings.database,
                "-c",
                "SELECT 1",
            ],
            env=environment,
            secrets=(database.credentials.password,),
            timeout=timeout,
            check=False,
        )
    except CommandError:
        return False
    return result.code == 0 and result.out.strip() == "1"


def info(database: Database) -> dict[str, Any]:
    version = _psql(
        database.service("primary"),
        database.settings.database,
        "SHOW server_version",
        username=database.settings.username,
        timeout=10,
    )
    pool = database.settings.pgbouncer
    data = json.loads(
        _psql(
            database.service("primary"),
            database.settings.database,
            DATA_SQL,
            username=database.settings.username,
            timeout=10,
        )
    )
    return {
        "version": version,
        "username": database.settings.username,
        "database": database.settings.database,
        "pgbouncer": pool.enabled,
        "max_clients": pool.max_clients,
        "pool_size": pool.pool_size,
        "reserve_size": pool.reserve_size,
        "data": {
            "available": True,
            "logical_bytes": int(data["logical_bytes"]),
            "databases": int(data["databases"]),
        },
    }


def backup(database: Database, folder: Path, _run_id: str) -> dict[str, Any]:
    container = database.service("primary")
    rows = _psql(
        container,
        database.settings.database,
        DATABASE_SQL,
        username=database.settings.username,
        timeout=120,
    ).splitlines()
    databases = [json.loads(row) for row in rows]
    if not databases:
        raise BackupError(f"{database.identity}: no databases found")
    (folder / "databases").mkdir(mode=0o700)
    names = []
    objects = {}
    for item in databases:
        name = item["name"]
        relative = f"databases/{quote(name, safe='')}.dump"
        target = folder / relative
        with target.open("wb") as output:
            docker.exec(
                container,
                [
                    "pg_dump",
                    "-Fc",
                    "--no-password",
                    "-U",
                    database.settings.username,
                    "-d",
                    name,
                ],
                stdout=output,
                timeout=7200,
            )
        target.chmod(0o600)
        _check_archive(database.image, target)
        objects[name] = int(
            _psql(
                container,
                name,
                OBJECT_SQL,
                username=database.settings.username,
                timeout=120,
            )
        )
        names.append(relative)
    globals_file = folder / "globals.sql"
    with globals_file.open("wb") as output:
        docker.exec(
            container,
            [
                "pg_dumpall",
                "--globals-only",
                "--no-password",
                "-U",
                database.settings.username,
            ],
            stdout=output,
            timeout=1800,
        )
    if not globals_file.stat().st_size:
        raise BackupError(f"{database.identity}: globals.sql is empty")
    globals_file.chmod(0o600)
    names.append("globals.sql")
    return {
        "format": "postgres-custom-v1",
        "version": _psql(
            container,
            database.settings.database,
            "SHOW server_version",
            username=database.settings.username,
            timeout=120,
        ),
        "databases": [item["name"] for item in databases],
        "owners": {item["name"]: item["owner"] for item in databases},
        "objects": objects,
        "files": names,
    }


def _psql(
    container: str,
    database: str,
    sql: str,
    *,
    username: str,
    timeout: int,
) -> str:
    return docker.exec(
        container,
        [
            "psql",
            "-X",
            "-A",
            "-t",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            username,
            "-d",
            database,
            "-c",
            sql,
        ],
        timeout=timeout,
    ).out.strip()


def _check_archive(image: str, archive: Path) -> None:
    if not archive.is_file() or not archive.stat().st_size:
        raise BackupError(f"{archive.name} is empty")
    from ..run import run

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


def _group(path: Path) -> int:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current.stat().st_gid

from __future__ import annotations

import json
import secrets as random
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .config import Config, Database
from .errors import ConfigError
from .files import write_text


@dataclass(frozen=True, repr=False)
class Credentials:
    password: str
    http_token: str | None = None

    def __repr__(self) -> str:
        return "Credentials(<redacted>)"


@dataclass(frozen=True, repr=False)
class SecretFile:
    path: Path
    content: str

    def __repr__(self) -> str:
        return f"SecretFile(path={self.path!r}, content=<redacted>)"


def path(config: Config, database: Database, name: str) -> Path:
    if name not in {
        "password",
        "http-token",
        "pgbouncer-users",
        "redis.conf",
        "dragonfly.flags",
        "http.env",
    }:
        raise ConfigError(f"unknown database secret file: {name}")
    return config.paths.role_secrets(database.project, database.role) / name


def read(config: Config, database: Database, name: str) -> str:
    target = path(config, database, name)
    if name == "password":
        return validate_password(_read_line(target))
    return _read(target)


def read_host(config: Config, name: str) -> str:
    allowed = {
        "restic-password": config.paths.secrets / "restic-password",
        "dns.env": config.paths.traefik / "dns.env",
    }
    try:
        target = allowed[name]
    except KeyError as exc:
        raise ConfigError(f"unknown host secret file: {name}") from exc
    return _read(target)


def ensure(
    config: Config,
    database: Database,
    *,
    generate: Callable[[], str] | None = None,
) -> Credentials:
    create = generate or (lambda: random.token_urlsafe(32))
    password_path = path(config, database, "password")
    password = _existing_or_create(password_path, create)
    token = None
    if database.role == "kv" and database.settings.http.enabled:
        token = _existing_or_create(path(config, database, "http-token"), create)
    credentials = Credentials(password, token)
    write(render(config, database, credentials))
    return credentials


def credentials(config: Config, database: Database) -> Credentials:
    password = read(config, database, "password")
    token = None
    if database.role == "kv" and database.settings.http.enabled:
        token = read(config, database, "http-token")
    return Credentials(password, token)


def render(config: Config, database: Database, values: Credentials) -> tuple[SecretFile, ...]:
    password = validate_password(values.password)
    files = [SecretFile(path(config, database, "password"), password + "\n")]
    if database.role == "postgres" and database.settings.pgbouncer.enabled:
        users = f"{_pgbouncer_quote(database.settings.user)} {_pgbouncer_quote(password)}\n"
        files.append(SecretFile(path(config, database, "pgbouncer-users"), users))
    if database.role == "kv":
        if database.engine == "redis":
            persistence = (
                "save 900 1\nsave 300 10\nsave 60 10000\n" if database.durable else 'save ""\n'
            )
            text = (
                "bind 0.0.0.0\n"
                "port 6379\n"
                "dir /data\n"
                "dbfilename dump.rdb\n"
                "appendonly no\n"
                f"{persistence}"
                f"requirepass {json.dumps(password)}\n"
            )
            files.append(SecretFile(path(config, database, "redis.conf"), text))
        else:
            settings = database.settings
            lines = [
                "--dir=/data",
                "--dbfilename=dump",
                f"--requirepass={password}",
                f"--proactor_threads={settings.threads}",
                f"--maxmemory={settings.memory}",
            ]
            if settings.mode == "cache":
                lines.append("--cache_mode=true")
            files.append(
                SecretFile(path(config, database, "dragonfly.flags"), "\n".join(lines) + "\n")
            )
        if database.settings.http.enabled:
            if values.http_token is None:
                raise ConfigError(f"{database.identity}: HTTP token is missing")
            service = f"evdb-{database.project}-{database.role}-primary"
            url = f"redis://default:{quote(password, safe='')}@{service}:6379"
            env = (
                f"SRH_TOKEN={json.dumps(values.http_token)}\n"
                f"SRH_CONNECTION_STRING={json.dumps(url)}\n"
            )
            files.extend(
                [
                    SecretFile(path(config, database, "http-token"), values.http_token + "\n"),
                    SecretFile(path(config, database, "http.env"), env),
                ]
            )
    return tuple(files)


def validate_password(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError("database password must not be empty")
    if "\0" in value or "\r" in value or "\n" in value:
        raise ConfigError("database password must be one line")
    return value


def password_from_text(text: str) -> str:
    """Remove one file terminator while rejecting embedded line breaks."""
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    return validate_password(text)


def write(files: Iterable[SecretFile]) -> None:
    for item in files:
        if item.path.is_symlink():
            raise ConfigError(f"secret path must not be a symlink: {item.path}")
        write_text(item.path, item.content, mode=0o600)


def protected(values: Iterable[str | SecretFile]) -> tuple[str, ...]:
    result = set()
    for item in values:
        content = item.content if isinstance(item, SecretFile) else item
        result.update({content, content.strip(), json.dumps(content)[1:-1]})
        for line in content.splitlines():
            if line:
                result.update({line, json.dumps(line)[1:-1]})
    result.discard("")
    return tuple(sorted(result, key=len, reverse=True))


def _read(target: Path) -> str:
    if target.is_symlink() or not target.is_file():
        raise ConfigError(f"secret file is missing or unsafe: {target}")
    if target.stat().st_mode & 0o077:
        raise ConfigError(f"secret file is not private: {target}")
    value = target.read_text().strip()
    if not value:
        raise ConfigError(f"secret file is empty: {target}")
    return value


def _read_line(target: Path) -> str:
    if target.is_symlink() or not target.is_file():
        raise ConfigError(f"secret file is missing or unsafe: {target}")
    if target.stat().st_mode & 0o077:
        raise ConfigError(f"secret file is not private: {target}")
    try:
        return password_from_text(target.read_text())
    except ConfigError as exc:
        raise ConfigError(f"secret file is invalid: {target}") from exc


def _pgbouncer_quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _existing_or_create(target: Path, create: Callable[[], str]) -> str:
    if target.exists() or target.is_symlink():
        return _read(target)
    value = create()
    if not isinstance(value, str) or not value:
        raise ConfigError("credential generator returned an empty value")
    write_text(target, value + "\n", mode=0o600)
    return value

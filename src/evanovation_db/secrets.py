from __future__ import annotations

import json
import os
import re
import secrets as random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .config import Config, Host, Instance
from .errors import ConfigError
from .run import Result, run

PASSWORD_FIELD = "password"
HTTP_TOKEN_FIELD = "http-token"
SYSTEM_FIELDS = ("restic-password", "rclone-config")


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
    preserve: bool = False

    def __repr__(self) -> str:
        return f"SecretFile(path={self.path!r}, content=<redacted>)"


class Op:
    def __init__(
        self,
        vault: str,
        *,
        timeout: int = 60,
        env: Mapping[str, str] | None = None,
        generate: Callable[[], str] | None = None,
    ):
        self.vault = vault
        self.timeout = timeout
        self.env = dict(env) if env is not None else None
        self.generate = generate or (lambda: random.token_urlsafe(32))

    @classmethod
    def for_host(cls, host: Host, **kwargs: Any) -> Op:
        vault, _, _ = _reference(host.secrets["restic_password"])
        return cls(vault, **kwargs)

    def preflight(self, *, write: bool = False) -> dict[str, Any]:
        environment = self.env if self.env is not None else os.environ
        connect = environment.get("OP_CONNECT_HOST") or environment.get("OP_CONNECT_TOKEN")
        service = environment.get("OP_SERVICE_ACCOUNT_TOKEN")
        if write and connect and not service:
            raise ConfigError(
                "1Password Connect authentication cannot write items; use desktop "
                "authentication or a service account with write_items"
            )
        identity = self._json(["whoami", "--format", "json"], "authentication")
        if not isinstance(identity, dict):
            raise ConfigError("1Password authentication returned invalid JSON")
        mode = " ".join(
            str(identity.get(key, "")) for key in ("type", "account_type", "auth_method")
        ).lower()
        if write and "connect" in mode and not service:
            raise ConfigError(
                "1Password Connect authentication cannot write items; use desktop "
                "authentication or a service account with write_items"
            )
        return identity

    def ensure(self, instance: Instance) -> None:
        self.preflight(write=True)
        title = item_name(instance)
        required = [PASSWORD_FIELD]
        if _http_enabled(instance):
            required.append(HTTP_TOKEN_FIELD)
        summary = self._find(title)
        if summary is None:
            values = {name: self.generate() for name in required}
            template = {
                "title": title,
                "category": "PASSWORD",
                "fields": [_concealed(name, value) for name, value in values.items()],
            }
            self._write(["item", "create", "-", "--vault", self.vault], template, values.values())
            return

        item = self._get(_item_id(summary), title)
        fields = _fields(item)
        add = []
        fill = []
        for name in required:
            matches = [field for field in fields if _field_name(field) == name]
            if len(matches) > 1:
                raise ConfigError(f"1Password item {title} has duplicate field {name}")
            if not matches:
                add.append(name)
            elif matches[0].get("type") != "CONCEALED":
                raise ConfigError(f"1Password item {title} field {name} must be concealed")
            elif not matches[0].get("value"):
                fill.append(name)
        if not add and not fill:
            return
        values = {name: self.generate() for name in [*fill, *add]}
        template = _template(item)
        template["fields"] = [
            *(
                {**field, "value": values[_field_name(field)]}
                if _field_name(field) in fill
                else field
                for field in fields
            ),
            *(_concealed(name, values[name]) for name in add),
        ]
        protected = [*values.values(), *_concealed_values(item)]
        self._write(
            ["item", "edit", _item_id(summary), "-", "--vault", self.vault],
            template,
            protected,
        )

    def credentials(self, instance: Instance) -> Credentials:
        title = item_name(instance)
        password = self._read(title, PASSWORD_FIELD)
        token = self._read(title, HTTP_TOKEN_FIELD) if _http_enabled(instance) else None
        return Credentials(password, token)

    def fields(self, title: str, names: Sequence[str]) -> dict[str, str]:
        return {name: self._read(title, name) for name in names}

    def _read(self, title: str, field: str) -> str:
        reference = f"op://{self.vault}/{title}/{field}"
        result = self._run(["read", reference])
        value = result.out.strip()
        if result.code != 0 or not value:
            raise ConfigError(f"1Password item {title} is missing required field {field}")
        return value

    def _find(self, title: str) -> dict[str, Any] | None:
        data = self._json(
            ["item", "list", "--vault", self.vault, "--format", "json"],
            "item list",
        )
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise ConfigError("1Password item list returned invalid JSON")
        matches = [item for item in data if item.get("title") == title]
        if len(matches) > 1:
            raise ConfigError(f"multiple 1Password items are named {title}")
        return matches[0] if matches else None

    def _get(self, item_id: str, title: str) -> dict[str, Any]:
        data = self._json(
            ["item", "get", item_id, "--vault", self.vault, "--format", "json"],
            f"item {title}",
        )
        if not isinstance(data, dict):
            raise ConfigError(f"1Password item {title} returned invalid JSON")
        _fields(data)
        return data

    def _json(self, args: Sequence[str], action: str) -> Any:
        result = self._run(args)
        if result.code != 0:
            raise ConfigError(f"1Password {action} failed")
        try:
            return json.loads(result.out)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"1Password {action} returned invalid JSON") from exc

    def _write(
        self, args: Sequence[str], template: dict[str, Any], protected: Sequence[str]
    ) -> None:
        result = self._run(
            [*args, "--format", "json"],
            input=json.dumps(template, separators=(",", ":")),
            protected=protected,
        )
        if result.code != 0:
            raise ConfigError("1Password item update failed")

    def _run(
        self,
        args: Sequence[str],
        *,
        input: str | None = None,
        protected: Sequence[str] = (),
    ) -> Result:
        return run(
            ["op", *args],
            timeout=self.timeout,
            env=self.env,
            input=input,
            secrets=protected,
            check=False,
        )


def read(host: Host, instance: Instance | None, key: str) -> str:
    prefix = f"{instance.group}_{instance.id}_" if instance else "host_"
    env_key = "EVANOVATION_DB_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_", prefix + key).upper()
    if env_key in os.environ:
        return os.environ[env_key]
    value = (instance.secrets if instance else host.secrets).get(key, "")
    if value.startswith("op://"):
        raise ConfigError(f"secret has not been rendered: {prefix}{key}")
    path = Path(value)
    if not path.is_file():
        raise ConfigError(f"secret file is missing: {path}")
    return path.read_text().strip()


def path(host: Host, instance: Instance | None, key: str) -> Path:
    value = (instance.secrets if instance else host.secrets).get(key, "")
    if value.startswith("op://"):
        raise ConfigError(f"secret has not been rendered: {key}")
    result = Path(value)
    if not result.is_file():
        raise ConfigError(f"secret file is missing: {result}")
    return result


def item_name(instance: Instance) -> str:
    return f"{instance.id}-{'postgres' if instance.engine == 'postgres' else 'kv'}"


def system_item(host: Host) -> str:
    _, item, _ = _reference(host.secrets["restic_password"])
    return item


def deployment_files(config: Config, client: Op | None = None) -> tuple[SecretFile, ...]:
    op = client or Op.for_host(config.host)
    system = op.fields(system_item(config.host), SYSTEM_FIELDS)
    root = config.host.config_dir / "secrets"
    files = [
        SecretFile(root / "restic_password", system["restic-password"] + "\n"),
        SecretFile(
            config.host.state_dir / "rclone/rclone.conf",
            system["rclone-config"] + "\n",
            preserve=True,
        ),
    ]
    for instance in config.instances:
        credentials = op.credentials(instance)
        password = credentials.password
        files.append(SecretFile(root / f"{instance.group}-{instance.id}.password", password + "\n"))
        if instance.engine == "postgres" and instance.settings.get("pgbouncer") is True:
            user = str(instance.settings["user"])
            users = f"{json.dumps(user)} {json.dumps(password)}\n"
            files.append(SecretFile(root / f"postgres-{instance.id}.users", users))
        elif instance.engine == "redis":
            text = (
                "bind 0.0.0.0\nport 6379\ndir /data\ndbfilename dump.rdb\n"
                "appendonly no\nsave 900 1\nsave 300 10\nsave 60 10000\n"
                f"requirepass {json.dumps(password)}\n"
            )
            files.append(SecretFile(root / f"kv-{instance.id}.conf", text))
        elif instance.engine == "dragonfly":
            text = (
                "--dir=/data\n--dbfilename=dump.rdb\n"
                f"--requirepass={json.dumps(password)}\n"
                f"--proactor_threads={instance.settings['threads']}\n"
                f"--maxmemory={instance.settings['maxmemory']}\n"
            )
            files.append(SecretFile(root / f"kv-{instance.id}.flags", text))
        if credentials.http_token is not None:
            token = credentials.http_token
            url = f"redis://default:{quote(password, safe='')}@{instance.container}:6379"
            env = f"SRH_TOKEN={json.dumps(token)}\nSRH_CONNECTION_STRING={json.dumps(url)}\n"
            files.extend(
                [
                    SecretFile(root / f"kv-{instance.id}-http.env", env),
                    SecretFile(root / f"kv-{instance.id}-http.token", token + "\n"),
                ]
            )
    return tuple(files)


def transfer(args: Sequence[str], files: Sequence[SecretFile], *, timeout: int = 300) -> Result:
    payload = {
        "files": [
            {"path": str(item.path), "content": item.content, "preserve": item.preserve}
            for item in files
        ]
    }
    return run(
        args,
        timeout=timeout,
        input=json.dumps(payload, separators=(",", ":")),
        secrets=protected(files),
    )


def protected(files: Sequence[SecretFile]) -> tuple[str, ...]:
    values = set()
    for item in files:
        content = item.content
        values.update({content, content.strip(), json.dumps(content)[1:-1]})
        for line in content.splitlines():
            if line:
                values.update({line, json.dumps(line)[1:-1]})
    values.discard("")
    return tuple(sorted(values, key=len, reverse=True))


def _reference(value: str) -> tuple[str, str, str]:
    if not isinstance(value, str) or not value.startswith("op://"):
        raise ConfigError("host 1Password reference is unavailable on this runtime")
    parts = value.removeprefix("op://").split("/")
    if len(parts) != 3 or not all(parts):
        raise ConfigError("host 1Password reference is invalid")
    return parts[0], parts[1], parts[2]


def _http_enabled(instance: Instance) -> bool:
    return instance.http is not None and instance.http.get("enabled") is True


def _concealed(name: str, value: str) -> dict[str, str]:
    field = {"id": name, "label": name, "type": "CONCEALED", "value": value}
    if name == PASSWORD_FIELD:
        field["purpose"] = "PASSWORD"
    return field


def _fields(item: dict[str, Any]) -> list[dict[str, Any]]:
    fields = item.get("fields")
    if not isinstance(fields, list) or not all(isinstance(field, dict) for field in fields):
        raise ConfigError("1Password item fields returned invalid JSON")
    return fields


def _field_name(field: dict[str, Any]) -> str | None:
    label = field.get("label")
    return label if isinstance(label, str) else None


def _field_value(item: dict[str, Any], name: str, title: str) -> str:
    matches = [field for field in _fields(item) if _field_name(field) == name]
    if len(matches) != 1:
        raise ConfigError(f"1Password item {title} is missing required field {name}")
    if matches[0].get("type") != "CONCEALED":
        raise ConfigError(f"1Password item {title} field {name} must be concealed")
    value = matches[0].get("value")
    if not isinstance(value, str) or not value:
        raise ConfigError(f"1Password item {title} is missing required field {name}")
    return value


def _item_id(summary: dict[str, Any]) -> str:
    value = summary.get("id")
    if not isinstance(value, str) or not value:
        raise ConfigError("1Password item list returned an invalid item id")
    return value


def _template(item: dict[str, Any]) -> dict[str, Any]:
    allowed = ("title", "category", "fields", "sections", "urls", "notes", "tags")
    return {name: item[name] for name in allowed if name in item}


def _concealed_values(item: dict[str, Any]) -> list[str]:
    return [
        value
        for field in _fields(item)
        if field.get("type") == "CONCEALED"
        and isinstance((value := field.get("value")), str)
        and value
    ]

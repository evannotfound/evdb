from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .config import Config, from_runtime, load, runtime_data
from .files import write_json
from .run import run

ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = ROOT / "ansible/databases.yml"


@dataclass(frozen=True)
class Inputs:
    inventory: Path
    variables: Path


@contextmanager
def inputs(
    source: str | Path | Config,
    *,
    target: str = "production",
    confirmed: bool = False,
) -> Iterator[Inputs]:
    config = source if isinstance(source, Config) else load(source)
    with TemporaryDirectory(prefix="evdb-ansible-") as name:
        root = Path(name)
        inventory = root / "inventory.json"
        variables = root / "variables.json"
        inventory_data, variables_data = data(config, group=target)
        variables_data.update(
            {
                "target": target,
                "apply": "yes" if confirmed else "no",
            }
        )
        write_json(inventory, inventory_data)
        write_json(variables, variables_data)
        yield Inputs(inventory, variables)


def data(config: Config, *, group: str = "production") -> tuple[dict[str, Any], dict[str, Any]]:
    if group not in {"production", "managed", "test"}:
        raise ValueError(f"unsupported Ansible inventory group: {group}")
    runtime = from_runtime(runtime_data(config))
    host = {"ansible_host": _ssh_host(runtime.host.ssh), "evdb_become": True}
    user = _ssh_user(runtime.host.ssh)
    if user:
        host["ansible_user"] = user
    inventory = {"all": {"children": {group: {"hosts": {runtime.host.id: host}}}}}
    variables = {
        "evdb_config": {
            "host": _plain(runtime.host),
            "databases": [_plain(item) for item in runtime.instances],
        }
    }
    return inventory, variables


def bootstrap(
    config: Config,
    *,
    timeout: int | None = None,
) -> None:
    with inputs(config, confirmed=True) as generated:
        run(
            [
                "ansible-playbook",
                "-i",
                str(generated.inventory),
                str(PLAYBOOK),
                "--extra-vars",
                f"@{generated.variables}",
            ],
            timeout=timeout or config.host.timeouts["command"],
        )


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _ssh_host(destination: str) -> str:
    return destination.rsplit("@", 1)[-1]


def _ssh_user(destination: str) -> str | None:
    return destination.rsplit("@", 1)[0] if "@" in destination else None

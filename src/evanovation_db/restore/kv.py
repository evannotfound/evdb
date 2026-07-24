from __future__ import annotations

import time
from pathlib import Path

from .. import docker, manifest
from ..backup import kv as kv_backup
from ..config import Host, Instance
from ..errors import RestoreError


def restore(host: Host, instance: Instance, folder: Path, name: str) -> dict:
    del host
    data = manifest.check(folder)
    work = folder.parent / f".{name}-data"
    work.mkdir(mode=0o700)
    target = work / "dump.rdb"
    target.write_bytes((folder / "dump.rdb").read_bytes())
    target.chmod(0o600)
    if instance.engine == "redis":
        args = [
            "redis-server",
            "--dir",
            "/data",
            "--dbfilename",
            "dump.rdb",
            "--protected-mode",
            "no",
        ]
    else:
        args = [
            "dragonfly",
            "--dir=/data",
            "--dbfilename=dump",
            "--primary_port_http_enabled=false",
        ]
    docker.start(
        str(instance.target["image"]),
        name,
        args,
        mounts=[(work, "/data", False)],
        memory="3g",
        network="none",
        timeout=300,
    )
    _wait(name)
    restored_instance = Instance(
        id=instance.id,
        env=instance.env,
        engine=instance.engine,
        container=name,
        project=instance.project,
        data=work,
        domain=instance.domain,
        durable=instance.durable,
        current=instance.current,
        target=instance.target,
        backup=instance.backup,
        resources=instance.resources,
        secrets={},
        settings=instance.settings,
    )
    expected = data.get("facts", {})
    facts = kv_backup.facts(restored_instance, "", sample_limit=0)
    facts["samples"] = [
        kv_backup._sample(restored_instance, "", str(item["database"]), item["key"])
        for item in expected.get("samples", [])
    ]
    if facts["databases"] != expected.get("databases") or facts["keys"] != expected.get("keys"):
        raise RestoreError("KV key counts differ after restore")
    _check_samples(facts.get("samples", []), expected.get("samples", []))
    return facts


def _wait(name: str, timeout: int = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if docker.exec(name, ["redis-cli", "PING"], timeout=10).out.strip() == "PONG":
                return
        except Exception:
            pass
        time.sleep(1)
    raise RestoreError("KV restore container did not become ready")


def _check_samples(current: list[dict], expected: list[dict]) -> None:
    wanted = {(item["database"], item["key"]): item for item in expected}
    found = {(item["database"], item["key"]): item for item in current}
    for key, item in wanted.items():
        other = found.get(key)
        if not other or other["type"] != item["type"] or other["sha256"] != item["sha256"]:
            raise RestoreError(f"KV sample differs after restore: db{key[0]}/{key[1]}")
        if item["ttl_ms"] > 0 and other["ttl_ms"] == -1:
            raise RestoreError(f"KV TTL was lost after restore: db{key[0]}/{key[1]}")

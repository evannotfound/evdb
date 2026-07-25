from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib.parse import quote

from . import ansible, deployment, planning, remote, status
from .config import (
    ENGINES,
    Config,
    Host,
    HostSource,
    Instance,
    add_database,
    load,
    load_lock,
    load_source,
    normalize,
    source_path,
    write_lock,
)
from .errors import Error, ProtocolError, ProtocolMismatchError, RuntimeUnavailableError
from .files import hash as file_hash
from .secrets import Credentials, Op, deployment_files, item_name, protected

DETAIL_KEYS = {
    "selector",
    "state",
    "running",
    "health",
    "engine_version",
    "image",
    "image_id",
    "service_hash",
    "active_release",
    "backup",
}
_RESTORE_ID = re.compile(r"restore-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}")
_HASH = re.compile(r"[0-9a-f]{64}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="evdb", description="Manage database hosts over SSH.")
    default_config = os.getenv("EVANOVATION_DB_CONFIG")
    result.add_argument(
        "--config",
        default=default_config,
        required=default_config is None,
        help="host config directory or host.yml",
    )
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="validate source configuration and generated lock")
    show = sub.add_parser("show", help="show database details and complete credentials")
    show.add_argument("database")
    sub.add_parser("plan", help="show desired deployment changes")
    apply = sub.add_parser("apply", help="apply desired deployment changes")
    apply.add_argument("--yes", action="store_true")
    create = sub.add_parser("create", help="add and deploy a database")
    create.add_argument("type", choices=sorted(ENGINES))
    create.add_argument("name")
    create.add_argument("--yes", action="store_true")
    for name in ("start", "stop", "restart"):
        command = sub.add_parser(name, help=f"{name} a database")
        command.add_argument("database")
        command.add_argument("--yes", action="store_true")
    logs = sub.add_parser("logs", help="show recent database logs")
    logs.add_argument("database")
    logs.add_argument("--lines", type=int, default=200, choices=range(1, 1001))
    status_command = sub.add_parser("status", help="show host and database health")
    status_command.add_argument("database", nargs="?")
    status_command.add_argument("--json", action="store_true")
    for name in ("backup", "backups"):
        help_text = "create a checked backup" if name == "backup" else "list backup history"
        command = sub.add_parser(name, help=help_text)
        command.add_argument("database")
    backup_check = sub.add_parser("backup-check", help="verify a backup end to end")
    backup_check.add_argument("database")
    backup_check.add_argument("backup", nargs="?")
    restore = sub.add_parser("restore", help="create and verify a restore candidate")
    restore.add_argument("database")
    restore.add_argument("--snapshot", required=True)
    promote = sub.add_parser("promote", help="promote a verified restore candidate")
    promote.add_argument("database")
    promote.add_argument("restore_id")
    promote.add_argument("--yes", action="store_true")
    sub.add_parser("releases", help="list deployment release history")
    rollback = sub.add_parser("rollback", help="activate a prior deployment release")
    rollback.add_argument("release", nargs="?")
    rollback.add_argument("--yes", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "validate":
            config = load(args.config)
            print(f"valid: {config.host.id} ({len(config.instances)} databases)")
            return 0
        if args.command == "show":
            config = load(args.config)
            instance = config.select(args.database)
            facts = remote.call(config, "show", instance.selector)
            _validate_facts(instance, facts)
            credentials = _op(config.host).credentials(instance)
            output = render_show(config, instance, facts, credentials)
            print(output)
            return 0
        if args.command == "plan":
            _, _, plan = _plan(args.config)
            print(plan.render())
            return 1 if plan.blocked else 0
        if args.command == "apply":
            return _apply(args.config, yes=args.yes)
        if args.command == "create":
            source = load_source(source_path(args.config))
            client = _source_op(source)
            client.preflight(write=True)
            add_database(args.config, args.type, args.name)
            wanted = planning.desired(args.config)
            client.ensure(wanted.config.select(f"{args.type}/{args.name}"))
            return _apply(args.config, yes=args.yes, client=client)
        if args.command == "releases":
            config = load(args.config)
            result = remote.call(config, "releases", config.host.id)
            rows = _validate_release_history(result)
            print(render_releases(config.host.id, result["active"], rows))
            return 0
        if args.command == "rollback":
            return _rollback(args.config, args.release, yes=args.yes)
        if args.command == "promote":
            return _promote(args.config, args.database, args.restore_id, yes=args.yes)
        if args.command == "status":
            return _status(args.config, args.database, json_output=args.json)
        config = load(args.config)
        instance = config.select(args.database)
        if args.command in {"start", "stop", "restart"}:
            print(f"Host: {config.host.id}\n{args.command.title()}: {instance.selector}")
            if not args.yes and not _confirm(f"{args.command.title()} this database?"):
                print("Cancelled.")
                return 0
            result = remote.call(config, args.command, instance.selector)
            print(f"{result['action']}: {result['selector']} ({result['release']})")
            return 0
        if args.command == "logs":
            result = remote.call(config, "logs", instance.selector, {"lines": args.lines})
            print(result["logs"], end="" if result["logs"].endswith("\n") else "\n")
            return 0
        if args.command == "backup":
            result = remote.call(
                config,
                "backup",
                instance.selector,
                timeout=config.host.timeouts["backup"],
            )
            _validate_backup_result(instance, result)
            print(
                f"Backup {result['backup']} completed at {result['time']} "
                f"(snapshot {result['snapshot']})"
            )
            return 0
        if args.command == "backups":
            result = remote.call(config, "backups", instance.selector)
            rows = _validate_history_result(instance, result)
            print(render_backups(instance, rows))
            return 0
        if args.command == "backup-check":
            result = remote.call(
                config,
                "backup_check",
                instance.selector,
                {"backup": args.backup},
                timeout=config.host.timeouts["restore"],
            )
            _validate_backup_check_result(instance, result)
            source = result["snapshot"] or result["backup"]
            print(f"Verified {instance.selector} backup {source} at {result['time']}")
            return 0
        if args.command == "restore":
            result = remote.call(
                config,
                "restore",
                instance.selector,
                {"snapshot": args.snapshot},
                timeout=config.host.timeouts["restore"],
            )
            _validate_restore_result(instance, result)
            print(
                f"Restore candidate {result['restore_id']} verified for {instance.selector} "
                f"from snapshot {result['snapshot']}"
            )
            return 0
    except (Error, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


def _plan(path: str, *, runtime: str = "current") -> tuple[planning.Desired, dict, planning.Plan]:
    wanted = planning.desired(path)
    if runtime == "current":
        state = remote.call(wanted.config, "release_state", wanted.config.host.id)
    else:
        state = remote.call(
            wanted.config,
            "release_state",
            wanted.config.host.id,
            runtime=runtime,
        )
    plan = planning.compare(wanted, state)
    return wanted, state, plan


def _status(path: str, database: str | None, *, json_output: bool) -> int:
    source_file = source_path(path)
    source = load_source(source_file)
    try:
        host_lock = load_lock(source_file.parent / "host.lock.json", source)
        config = normalize(source, host_lock)
    except Error as exc:
        result = status.config_error(source, exc, selector=database)
        print(json.dumps(result, sort_keys=True) if json_output else status.render(result))
        return 1
    selected = config.select(database).selector if database is not None else None
    wanted = deployment.build(config, host_lock, file_hash(source_file)).manifest
    try:
        observed = remote.call(
            config,
            "status",
            config.host.id,
            timeout=config.host.timeouts["command"]
            + len(config.instances) * min(10, config.host.timeouts["health"]) * 2,
        )
    except Error as exc:
        result = status.unreachable(config, exc, selector=selected)
    else:
        result = status.assess(config, wanted, observed, selector=selected)
    print(json.dumps(result, sort_keys=True) if json_output else status.render(result))
    return 0 if result["healthy"] else 1


def _apply(path: str, *, yes: bool, client: Op | None = None) -> int:
    runtime = "current"
    try:
        wanted, state, plan = _plan(path)
    except (RuntimeUnavailableError, ProtocolMismatchError):
        bootstrap = planning.desired(path)
        print(
            f"Host: {bootstrap.config.host.id}\n"
            "BOOTSTRAP internal host runtime (no database projects will be started)"
        )
        if not yes and not _confirm("Install or update the internal host runtime?"):
            print("Cancelled.")
            return 0
        ansible.bootstrap(
            bootstrap.config,
            timeout=bootstrap.config.host.timeouts["command"],
        )
        runtime = "bootstrap"
        wanted, state, plan = _plan(path, runtime=runtime)
    print(plan.render())
    planning.require_applicable(plan)
    if not plan.changed:
        return 0
    if not yes and not _confirm("Apply this production plan?"):
        print("Cancelled.")
        return 0

    refreshed = planning.desired(path)
    if refreshed.bundle.manifest != wanted.bundle.manifest:
        raise ProtocolError("desired configuration changed after confirmation; run plan again")
    write_lock(refreshed.lock_path, refreshed.lock)
    op = client or _op(refreshed.config.host)
    created = {
        item.selector
        for item in plan.actions
        if item.kind == "create" and item.selector in refreshed.bundle.manifest["databases"]
    }
    for selector in sorted(created):
        op.ensure(refreshed.config.select(selector))
    protected_files = deployment_files(refreshed.config, op)
    payload = deployment.request(
        refreshed.bundle,
        plan.affected,
        protected_files,
        state["release"],
        plan.summary(),
    )
    result = remote.call(
        refreshed.config,
        "apply",
        refreshed.config.host.id,
        payload,
        timeout=refreshed.config.host.timeouts["command"]
        + max(1, len(plan.affected)) * refreshed.config.host.timeouts["health"],
        protected=protected(protected_files),
        runtime=runtime,
    )
    print(f"Active release: {result['release']}")
    if result["affected"]:
        print("Applied: " + ", ".join(result["affected"]))
    return 0


def _rollback(path: str, release: str | None, *, yes: bool) -> int:
    config = load(path)
    plan = remote.call(
        config,
        "rollback_plan",
        config.host.id,
        {"release": release},
    )
    _validate_rollback_plan(config, plan)
    print(render_rollback(plan))
    if not plan["activate"]:
        return 0
    if not yes and not _confirm("Rollback to this production release?"):
        print("Cancelled.")
        return 0
    result = remote.call(
        config,
        "rollback",
        config.host.id,
        {"release": plan["release"], "expected": plan["active"]},
        timeout=config.host.timeouts["command"]
        + max(1, len(plan["changes"])) * config.host.timeouts["health"],
    )
    _validate_rollback_result(plan, result)
    print(f"Active release: {result['release']}")
    if result["affected"]:
        print("Restored: " + ", ".join(result["affected"]))
    return 0


def _promote(path: str, database: str, restore_id: str, *, yes: bool) -> int:
    config = load(path)
    instance = config.select(database)
    plan = remote.call(
        config,
        "promotion_plan",
        instance.selector,
        {"restore_id": restore_id},
    )
    _validate_promotion_plan(config, instance, plan)
    if plan["restore_id"] != restore_id:
        raise ProtocolError("remote promotion plan identifies a different restore candidate")
    print(render_promotion(plan))
    if not yes and not _confirm("Promote this restore candidate and stop the live database?"):
        print("Cancelled.")
        return 0
    result = remote.call(
        config,
        "promote",
        instance.selector,
        {
            "restore_id": restore_id,
            "expected_release": plan["active_release"],
            "manifest_hash": plan["manifest_hash"],
        },
        timeout=config.host.timeouts["command"] + 2 * config.host.timeouts["health"],
    )
    _validate_promotion_result(instance, plan, result)
    print(f"Promoted restore candidate: {result['restore_id']}")
    print(f"Retained prior data: {result['retained']}")
    return 0


def render_promotion(plan: dict) -> str:
    return "\n".join(
        [
            f"Host: {plan['host']}",
            f"Database: {plan['selector']}",
            f"Restore candidate: {plan['restore_id']}",
            f"Snapshot: {plan['snapshot']} ({plan['snapshot_time']})",
            f"Active release: {plan['active_release']}",
            f"Live data: {plan['live_path']}",
            f"Candidate data: {plan['candidate_path']}",
            "Expected outage: required while data directories are swapped and health checked",
        ]
    )


def render_rollback(plan: dict) -> str:
    lines = [
        f"Host: {plan['host']}",
        f"Active release: {plan['active']}",
        f"Rollback release: {plan['release']}",
    ]
    if not plan["activate"]:
        lines.append("No changes. Release is already active.")
        return "\n".join(lines)
    if not plan["changes"]:
        lines.append("No service changes; activate the selected runtime and release pointer.")
        return "\n".join(lines)
    for item in plan["changes"]:
        before = item["from_image"] or "absent"
        after = item["to_image"] or "stopped"
        lines.append(f"{item['action'].upper():6} {item['selector']}: {before} -> {after}")
    return "\n".join(lines)


def render_releases(host: str, active: str | None, rows: list[dict]) -> str:
    lines = [
        f"Host: {host}",
        f"Active release: {active or 'none'}",
        "CREATED                    STATE             RELEASE",
    ]
    if not rows:
        lines.append("none")
        return "\n".join(lines)
    for row in rows:
        marker = "active" if row["active"] else row["status"]
        if row["outcome"] == "recovery_failed":
            marker = "recovery_failed"
        lines.append(f"{(row['created_at'] or 'unknown'):<26} {marker:<17} {row['id']}")
        for step in row["recovery_steps"]:
            lines.append(f"  RECOVERY: {step}")
    return "\n".join(lines)


def _validate_rollback_plan(config: Config, value: object) -> None:
    if not _protocol_secret_free(value):
        raise ProtocolError("remote rollback plan contains protected data")
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "host", "active", "release", "activate", "changes"}
        or value.get("version") != deployment.ROLLBACK_VERSION
        or value.get("host") != config.host.id
        or not isinstance(value.get("active"), str)
        or not isinstance(value.get("release"), str)
        or not isinstance(value.get("activate"), bool)
        or not isinstance(value.get("changes"), list)
    ):
        raise ProtocolError("remote rollback plan does not match protocol version 2")
    if value["activate"] != (value["active"] != value["release"]):
        raise ProtocolError("remote rollback plan is inconsistent")
    selectors = set()
    for item in value["changes"]:
        if (
            not isinstance(item, dict)
            or set(item)
            != {
                "action",
                "selector",
                "from_image",
                "to_image",
                "from_major",
                "to_major",
            }
            or item.get("action") not in {"start", "stop", "update"}
            or not isinstance(item.get("selector"), str)
            or item["selector"] in selectors
            or any(
                item.get(name) is not None and not isinstance(item[name], str)
                for name in ("from_image", "to_image")
            )
            or any(
                item.get(name) is not None and type(item[name]) is not int
                for name in ("from_major", "to_major")
            )
            or (
                item.get("action") == "start"
                and (item.get("from_image") is not None or item.get("to_image") is None)
            )
            or (
                item.get("action") == "stop"
                and (item.get("from_image") is None or item.get("to_image") is not None)
            )
            or (
                item.get("action") == "update"
                and (item.get("from_image") is None or item.get("to_image") is None)
            )
        ):
            raise ProtocolError("remote rollback plan contains invalid changes")
        selectors.add(item["selector"])


def _validate_rollback_result(plan: dict, value: object) -> None:
    if not _protocol_secret_free(value):
        raise ProtocolError("remote rollback result contains protected data")
    if (
        not isinstance(value, dict)
        or set(value) != {"release", "previous", "affected"}
        or value.get("release") != plan["release"]
        or value.get("previous") != plan["active"]
        or not isinstance(value.get("affected"), list)
        or not all(isinstance(item, str) for item in value["affected"])
        or value["affected"] != [item["selector"] for item in plan["changes"]]
    ):
        raise ProtocolError("remote rollback result does not match the confirmed plan")


def _validate_release_history(value: object) -> list[dict]:
    if not _protocol_secret_free(value):
        raise ProtocolError("remote release history contains protected data")
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "active", "releases"}
        or value.get("version") != deployment.HISTORY_VERSION
        or (value.get("active") is not None and not isinstance(value["active"], str))
        or not isinstance(value.get("releases"), list)
    ):
        raise ProtocolError("remote release history does not match protocol version 2")
    fields = {
        "id",
        "active",
        "status",
        "outcome",
        "severity",
        "successful",
        "created_at",
        "updated_at",
        "finished_at",
        "activated_at",
        "predecessor",
        "source_hash",
        "lock_hash",
        "controller_version",
        "runtime_version",
        "engine_majors",
        "plan",
        "recovery_steps",
    }
    active_rows = 0
    for row in value["releases"]:
        if (
            not isinstance(row, dict)
            or set(row) != fields
            or not isinstance(row.get("id"), str)
            or not isinstance(row.get("active"), bool)
            or not isinstance(row.get("status"), str)
            or not isinstance(row.get("outcome"), str)
            or row.get("severity") not in {"info", "error", "high"}
            or not isinstance(row.get("successful"), bool)
            or any(
                row.get(name) is not None and not isinstance(row[name], str)
                for name in (
                    "created_at",
                    "updated_at",
                    "finished_at",
                    "activated_at",
                    "predecessor",
                )
            )
            or not all(isinstance(row.get(name), str) for name in ("source_hash", "lock_hash"))
            or not isinstance(row.get("controller_version"), str)
            or not isinstance(row.get("runtime_version"), str)
            or not isinstance(row.get("engine_majors"), dict)
            or not isinstance(row.get("plan"), dict)
            or not isinstance(row.get("recovery_steps"), list)
            or not all(isinstance(item, str) for item in row["recovery_steps"])
        ):
            raise ProtocolError("remote release history contains an invalid release")
        for selector, engine in row["engine_majors"].items():
            if (
                not isinstance(selector, str)
                or not isinstance(engine, dict)
                or set(engine) != {"engine", "major"}
                or engine.get("engine") not in ENGINES
                or type(engine.get("major")) is not int
                or engine["major"] < 1
            ):
                raise ProtocolError("remote release history contains invalid engine metadata")
        if set(row["plan"]) != {"actions", "blocked"} or not all(
            isinstance(row["plan"].get(name), list) for name in ("actions", "blocked")
        ):
            raise ProtocolError("remote release history contains an invalid release plan")
        for name in ("actions", "blocked"):
            for item in row["plan"][name]:
                if (
                    not isinstance(item, dict)
                    or set(item) != {"kind", "selector", "reason"}
                    or not all(isinstance(item.get(key), str) for key in item)
                ):
                    raise ProtocolError("remote release history contains an invalid release plan")
        active_rows += int(row["active"])
        if row["active"] != (row["id"] == value["active"]):
            raise ProtocolError("remote release history active state is inconsistent")
    if active_rows != int(value["active"] is not None):
        raise ProtocolError("remote release history active state is inconsistent")
    return value["releases"]


def _protocol_secret_free(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if any(word in str(key).lower() for word in ("password", "token", "secret")):
                return False
            if not _protocol_secret_free(item):
                return False
    elif isinstance(value, list):
        return all(_protocol_secret_free(item) for item in value)
    elif isinstance(value, str):
        return "op://" not in value
    return True


def _confirm(message: str) -> bool:
    try:
        answer = input(f"{message} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def postgres_url(instance: Instance, password: str) -> str:
    user = _setting(instance, "user")
    database = _setting(instance, "database")
    return (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@"
        f"{instance.domain}:{instance.port}/{quote(database, safe='')}?sslmode=require"
    )


def kv_url(instance: Instance, password: str) -> str:
    return (
        f"rediss://{quote('default', safe='')}:{quote(password, safe='')}@"
        f"{instance.domain}:{instance.port}/0"
    )


def render_show(
    config: Config,
    instance: Instance,
    facts: dict,
    credentials: Credentials,
) -> str:
    _validate_facts(instance, facts)
    backup = facts["backup"]
    lines = [
        f"Database: {instance.selector}",
        f"Engine: {instance.engine}",
        f"State: {facts['state']}",
        f"Running: {'yes' if facts['running'] else 'no'}",
        f"Health: {facts['health']}",
        f"Engine version: {facts['engine_version'] or 'unknown'}",
        f"Image: {facts['image'] or instance.image}",
        f"Image ID: {facts['image_id'] or 'unknown'}",
        f"Active release: {facts['active_release'] or 'none'}",
        f"Data path: {instance.data}",
        f"Container: {instance.container}",
        f"Latest backup: {_backup(backup)}",
        f"1Password item: {item_name(instance)}",
        f"Host: {instance.domain}",
        f"Port: {instance.port}",
        "TLS: required",
    ]
    if instance.engine == "postgres":
        lines.extend(
            [
                f"Username: {_setting(instance, 'user')}",
                f"Database name: {_setting(instance, 'database')}",
                f"Connection URL: {postgres_url(instance, credentials.password)}",
            ]
        )
    else:
        lines.append(f"Connection URL: {kv_url(instance, credentials.password)}")
        if instance.http is not None and instance.http.get("enabled") is True:
            if not credentials.http_token:
                raise ProtocolError("local 1Password credentials are incomplete")
            lines.extend(
                [
                    f"HTTP endpoint: https://{instance.http['domain']}",
                    f"HTTP token: {credentials.http_token}",
                ]
            )
        else:
            lines.append("HTTP: disabled")
    return "\n".join(lines)


def _validate_facts(instance: Instance, facts: dict) -> None:
    if not isinstance(facts, dict) or set(facts) != DETAIL_KEYS:
        raise ProtocolError("remote show result does not match protocol version 1")
    if facts["selector"] != instance.selector:
        raise ProtocolError("remote show result identifies a different database")
    for key in ("state", "health"):
        if not isinstance(facts[key], str) or not facts[key]:
            raise ProtocolError("remote show result contains invalid state")
    if not isinstance(facts["running"], bool):
        raise ProtocolError("remote show result contains invalid running state")
    for key in ("engine_version", "image", "image_id", "service_hash", "active_release"):
        if facts[key] is not None and not isinstance(facts[key], str):
            raise ProtocolError("remote show result contains invalid detail values")
    backup = facts["backup"]
    if backup is not None and (
        not isinstance(backup, dict)
        or set(backup) != {"finished", "uploaded", "snapshot", "upload_ok"}
        or any(
            backup[key] is not None and not isinstance(backup[key], str)
            for key in ("finished", "uploaded", "snapshot")
        )
        or not isinstance(backup["upload_ok"], bool)
    ):
        raise ProtocolError("remote show result contains invalid backup details")


def _backup(value: dict | None) -> str:
    if value is None:
        return "none"
    result = value["finished"] or "unknown time"
    if value["snapshot"]:
        result += f" (snapshot {value['snapshot']})"
    elif value["uploaded"]:
        result += " (uploaded)"
    return result


def render_backups(instance: Instance, rows: list[dict]) -> str:
    lines = [
        f"Backups: {instance.selector}",
        "TIME                       SOURCE        BACKUP                       "
        "SNAPSHOT       VERIFY",
    ]
    if not rows:
        lines.append("none")
        return "\n".join(lines)
    for row in rows:
        snapshot = row["snapshot"] or "-"
        lines.append(
            f"{(row['time'] or 'unknown'):<26} {row['source']:<13} {row['backup']:<28} "
            f"{snapshot:<14} {row['verification']['state']}"
        )
        if row["verification"]["error"]:
            lines.append(f"  DETAIL: {row['verification']['error']}")
    return "\n".join(lines)


def _validate_backup_result(instance: Instance, value: object) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != {"selector", "backup", "time", "snapshot"}
        or value.get("selector") != instance.selector
        or not all(
            isinstance(value.get(key), str) and value[key] for key in ("backup", "time", "snapshot")
        )
    ):
        raise ProtocolError("remote backup result does not match protocol version 1")


def _validate_history_result(instance: Instance, value: object) -> list[dict]:
    if (
        not isinstance(value, dict)
        or set(value) != {"selector", "backups"}
        or value.get("selector") != instance.selector
        or not isinstance(value.get("backups"), list)
    ):
        raise ProtocolError("remote backups result does not match protocol version 1")
    for row in value["backups"]:
        if not _valid_history_row(row):
            raise ProtocolError("remote backups result does not match protocol version 1")
    return value["backups"]


def _valid_history_row(row: object) -> bool:
    if not isinstance(row, dict) or set(row) != {
        "backup",
        "time",
        "snapshot",
        "local",
        "remote",
        "verification",
        "source",
    }:
        return False
    if (
        not isinstance(row["backup"], str)
        or not row["backup"]
        or row["source"] not in {"local", "remote", "local+remote"}
        or not isinstance(row["local"], bool)
        or not isinstance(row["remote"], bool)
        or not isinstance(row["time"], str)
        or not row["time"]
        or (row["snapshot"] is not None and not isinstance(row["snapshot"], str))
    ):
        return False
    expected_source = (
        "local+remote"
        if row["local"] and row["remote"]
        else "local"
        if row["local"]
        else "remote"
        if row["remote"]
        else None
    )
    if row["source"] != expected_source:
        return False
    verification = row["verification"]
    return (
        isinstance(verification, dict)
        and set(verification) == {"state", "time", "error"}
        and verification["state"] in {"verified", "failed", "unverified"}
        and (verification["time"] is None or isinstance(verification["time"], str))
        and (verification["error"] is None or isinstance(verification["error"], str))
    )


def _validate_backup_check_result(instance: Instance, value: object) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != {"selector", "backup", "snapshot", "time", "result"}
        or value.get("selector") != instance.selector
        or not isinstance(value.get("backup"), str)
        or not value["backup"]
        or (value.get("snapshot") is not None and not isinstance(value["snapshot"], str))
        or not isinstance(value.get("time"), str)
        or not isinstance(value.get("result"), dict)
    ):
        raise ProtocolError("remote backup-check result does not match protocol version 1")


def _validate_restore_result(instance: Instance, value: object) -> None:
    if not _protocol_secret_free(value):
        raise ProtocolError("remote restore result contains protected data")
    if (
        not isinstance(value, dict)
        or set(value) != {"restore_id", "selector", "snapshot", "created_at", "state", "promotable"}
        or value.get("selector") != instance.selector
        or not isinstance(value.get("restore_id"), str)
        or _RESTORE_ID.fullmatch(value["restore_id"]) is None
        or not isinstance(value.get("snapshot"), str)
        or not value["snapshot"]
        or not isinstance(value.get("created_at"), str)
        or value.get("state") != "verified"
        or value.get("promotable") is not True
    ):
        raise ProtocolError("remote restore result does not match protocol version 2")


def _validate_promotion_plan(config: Config, instance: Instance, value: object) -> None:
    if not _protocol_secret_free(value):
        raise ProtocolError("remote promotion plan contains protected data")
    fields = {
        "version",
        "host",
        "selector",
        "restore_id",
        "snapshot",
        "snapshot_time",
        "manifest_hash",
        "created_at",
        "engine_image",
        "active_release",
        "candidate_path",
        "live_path",
        "outage",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("version") != 1
        or value.get("host") != config.host.id
        or value.get("selector") != instance.selector
        or not isinstance(value.get("restore_id"), str)
        or _RESTORE_ID.fullmatch(value["restore_id"]) is None
        or not all(
            isinstance(value.get(name), str) and value[name]
            for name in (
                "snapshot",
                "snapshot_time",
                "created_at",
                "engine_image",
                "active_release",
                "candidate_path",
                "live_path",
            )
        )
        or not isinstance(value.get("manifest_hash"), str)
        or _HASH.fullmatch(value["manifest_hash"]) is None
        or value.get("live_path") != str(instance.data)
        or value.get("engine_image") != instance.image
        or value.get("outage") is not True
    ):
        raise ProtocolError("remote promotion plan does not match protocol version 2")


def _validate_promotion_result(instance: Instance, plan: dict, value: object) -> None:
    if not _protocol_secret_free(value):
        raise ProtocolError("remote promotion result contains protected data")
    if (
        not isinstance(value, dict)
        or set(value) != {"selector", "restore_id", "snapshot", "release", "retained"}
        or value.get("selector") != instance.selector
        or value.get("restore_id") != plan["restore_id"]
        or value.get("snapshot") != plan["snapshot"]
        or value.get("release") != plan["active_release"]
        or not isinstance(value.get("retained"), str)
        or not value["retained"]
    ):
        raise ProtocolError("remote promotion result does not match the confirmed plan")


def _setting(instance: Instance, name: str) -> str:
    value = instance.settings.get(name)
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"configured Postgres {name} is invalid")
    return value


def _op(host: Host) -> Op:
    return Op.for_host(host)


def _source_op(source: HostSource) -> Op:
    return Op(source.vault)


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import backup, database, interactive, restic, restore, status
from .config import CONFIG_DIR, Config, load, load_state
from .errors import Error


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="evdb")
    result.add_argument("--config", default=os.getenv("EVDB_CONFIG", str(CONFIG_DIR / "host.yml")))
    commands = result.add_subparsers(dest="command")

    show = commands.add_parser("status", help="show host and database health")
    show.add_argument("database", nargs="?")
    show.add_argument("--json", action="store_true")

    databases = commands.add_parser("database", help="manage one database role")
    database_commands = databases.add_subparsers(dest="database_command", required=True)
    database_commands.add_parser("list")
    add = database_commands.add_parser("add")
    add.add_argument("project", nargs="?")
    add.add_argument("role", nargs="?", choices=("postgres", "kv"))
    add.add_argument("--engine", choices=("dragonfly", "redis"))
    add.add_argument("--yes", action="store_true")
    info = database_commands.add_parser("info")
    info.add_argument("database", nargs="?")
    configure = database_commands.add_parser("configure")
    configure.add_argument("database", nargs="?")
    configure.add_argument("--image")
    configure.add_argument("--pgbouncer", action=argparse.BooleanOptionalAction)
    configure.add_argument("--pgbouncer-image")
    configure.add_argument("--max-clients", type=int)
    configure.add_argument("--pool-size", type=int)
    configure.add_argument("--reserve-size", type=int)
    configure.add_argument("--mode", choices=("durable", "cache"))
    configure.add_argument("--http", action=argparse.BooleanOptionalAction)
    configure.add_argument("--http-image")
    configure.add_argument("--http-connections", type=int)
    configure.add_argument("--memory")
    configure.add_argument("--threads", type=int)
    configure.add_argument("--reset", action="append", default=[])
    configure.add_argument("--yes", action="store_true")
    for name in ("start", "stop", "restart"):
        action = database_commands.add_parser(name)
        action.add_argument("database", nargs="?")
        action.add_argument("--yes", action="store_true")
    logs = database_commands.add_parser("logs")
    logs.add_argument("database", nargs="?")
    logs.add_argument("--lines", type=int, default=200)

    backups = commands.add_parser("backup", help="create and verify backups")
    backup_commands = backups.add_subparsers(dest="backup_command", required=True)
    create = backup_commands.add_parser("create")
    create.add_argument("database", nargs="?")
    create.add_argument("--yes", action="store_true")
    listing = backup_commands.add_parser("list")
    listing.add_argument("database", nargs="?")
    testing = backup_commands.add_parser("test")
    testing.add_argument("database", nargs="?")
    testing.add_argument("backup", nargs="?")
    testing.add_argument("--due", action="store_true")
    testing.add_argument("--yes", action="store_true")
    retention = backup_commands.add_parser("retention")
    retention.add_argument("database", nargs="?")
    retention.add_argument("--all", action="store_true")
    retention.add_argument("--yes", action="store_true")
    prune = backup_commands.add_parser("prune")
    prune.add_argument("role", nargs="?", choices=("postgres", "kv"))
    prune.add_argument("--all", action="store_true")
    prune.add_argument("--yes", action="store_true")
    check = backup_commands.add_parser("repository-check")
    check.add_argument("role", nargs="?", choices=("postgres", "kv"))
    check.add_argument("--all", action="store_true")
    check.add_argument("--part", type=int)
    check.add_argument("--rotate", action="store_true")

    live_restore = commands.add_parser("restore", help="restore one database")
    live_restore.add_argument("database", nargs="?")
    live_restore.add_argument("backup", nargs="?")
    live_restore.add_argument("--yes", action="store_true")

    host = commands.add_parser("host", help="check, set up, or update this host")
    host_commands = host.add_subparsers(dest="host_command", required=True)
    host_commands.add_parser("check")
    setup = host_commands.add_parser("setup")
    setup.add_argument("--host-id")
    setup.add_argument("--domain")
    setup.add_argument("--data-root")
    setup.add_argument("--acme-email")
    setup.add_argument("--dns-provider")
    setup.add_argument("--postgres-repo")
    setup.add_argument("--kv-repo")
    setup.add_argument("--dns-env-file")
    setup.add_argument("--rclone-config")
    setup.add_argument("--yes", action="store_true")
    update = host_commands.add_parser("update")
    update.add_argument("version", nargs="?")
    update.add_argument("--yes", action="store_true")
    return result


def main(
    argv: list[str] | None = None,
    *,
    input_fn=None,
    output=None,
    error=None,
) -> int:
    input_fn = input_fn or input
    output = output or print
    error = error or (lambda value: print(value, file=sys.stderr))
    args = parser().parse_args(argv)
    try:
        source = Path(args.config)
        if _mutating(args) and not _canonical(source):
            raise Error(
                f"alternate --config is read-only; mutating commands require "
                f"{CONFIG_DIR / 'host.yml'}"
            )
        if args.command == "host" and args.host_command == "setup":
            values = {
                "host_id": args.host_id,
                "domain": args.domain,
                "data_root": args.data_root,
                "acme_email": args.acme_email,
                "dns_provider": args.dns_provider,
                "postgres_repo": args.postgres_repo,
                "kv_repo": args.kv_repo,
                "dns_env_file": args.dns_env_file,
                "rclone_config": args.rclone_config,
            }
            values = {key: value for key, value in values.items() if value is not None}
            if not source.is_file() and _tty():
                values = interactive.setup(values, input_fn=input_fn, output=output)
                if values is None:
                    output("Cancelled")
                    return 0
            output(
                _host_setup(
                    source,
                    values,
                    args.yes,
                    input_fn,
                    output,
                )
            )
            return 0
        if args.command is None:
            if not _tty():
                raise Error("a command is required; example: evdb status")
            if not source.is_file():
                values = interactive.setup(input_fn=input_fn, output=output)
                if values is None:
                    output("Cancelled")
                    return 0
                output(_host_setup(source, values, False, input_fn, output))
                return 0
        config = load(args.config)
        if args.command is None:
            return interactive.run(
                config,
                _actions(config, input_fn, output),
                input_fn=input_fn,
                output=output,
            )
        return _dispatch(config, args, input_fn, output)
    except (Error, OSError, ValueError) as exc:
        error(f"evdb: {exc}")
        return 1


def _dispatch(config: Config, args, input_fn, output) -> int:
    if args.command == "status":
        target = config.select(args.database) if args.database else None
        value = status.collect(config, target)
        output(status.dumps(value) if args.json else status.render(value))
        return 0 if value["healthy"] else 1
    if args.command == "database":
        return _database(config, args, input_fn, output)
    if args.command == "backup":
        return _backup(config, args, input_fn, output)
    if args.command == "restore":
        selector = _required(args.database, "database", "app-prod-01/postgres", input_fn)
        target = config.select(selector)
        value = _required(args.backup, "backup", "latest", input_fn)
        output(_restore(config, target, value, args.yes, input_fn, output))
        return 0
    if args.command == "host":
        from . import host

        if args.host_command == "check":
            value = host.check(config)
            output(status.render(value))
            return 0 if value["healthy"] else 1
        version = _required(args.version, "version", "1.2.3", input_fn)
        output(_host_update(config, version, args.yes, input_fn, output))
        return 0
    raise Error("unknown command")


def _database(config, args, input_fn, output):
    command = args.database_command
    if command == "list":
        for item in config.databases:
            output(f"{item.identity}\t{item.engine}")
        return 0
    if command == "add":
        project = _required(args.project, "project", "app-prod-01", input_fn)
        role = _required(args.role, "role", "postgres", input_fn)
        if role == "postgres" and args.engine is not None:
            raise Error("--engine is only valid when adding a kv database")
        output(
            _add(
                config,
                project,
                role,
                args.engine or "dragonfly",
                args.yes,
                input_fn,
                output,
            )
        )
        return 0
    selector = _required(args.database, "database", "app-prod-01/postgres", input_fn)
    target = config.select(selector)
    if command == "info":
        if not sys.stdout.isatty():
            raise Error("database info prints credentials and requires a terminal")
        value = database.info(config, target)
        output(_info(value))
        return 0
    if command == "configure":
        values = {
            name: getattr(args, name)
            for name in (
                "image",
                "pgbouncer",
                "pgbouncer_image",
                "max_clients",
                "pool_size",
                "reserve_size",
                "mode",
                "http",
                "http_image",
                "http_connections",
                "memory",
                "threads",
            )
            if getattr(args, name) is not None
        }
        output(
            _configure(
                config,
                target.identity,
                values,
                tuple(args.reset),
                args.yes,
                input_fn,
                output,
            )
        )
        return 0
    if command == "logs":
        output(database.logs(config, target, lines=args.lines))
        return 0
    output(_lifecycle(config, target, command, args.yes, input_fn, output))
    return 0


def _backup(config, args, input_fn, output):
    command = args.backup_command
    if command in {"prune", "repository-check"}:
        roles = (
            ("postgres", "kv")
            if args.all
            else (_required(args.role, "role", "postgres", input_fn),)
        )
        if command == "prune":
            if not _confirm(
                f"Host: {config.host.id}\nPrune {', '.join(roles)} repositories",
                args.yes,
                input_fn,
                output,
            ):
                return 0
            for role in roles:
                restic.prune(config, role)
        else:
            for role in roles:
                restic.check(config, role, part=args.part, rotate=args.rotate)
        output(f"{', '.join(roles)}: {command} complete")
        return 0
    if command == "test" and args.due:
        target = backup.due(config)
    elif command == "retention" and args.all:
        target = None
    else:
        selector = _required(args.database, "database", "app-prod-01/postgres", input_fn)
        target = config.select(selector)
    if command == "list":
        for row in backup.history(config, target):
            output(
                f"{row['time']}  {row['backup']}  {row['source']}  {row['verification']['state']}"
            )
        return 0
    if command == "retention":
        targets = [item for item in config.databases if item.durable] if args.all else [target]
        if not _confirm(
            f"Host: {config.host.id}\nApply retention: "
            + ", ".join(item.identity for item in targets),
            args.yes,
            input_fn,
            output,
        ):
            return 0
        for item in targets:
            restic.forget(config, item, dry_run=False)
        output("Retention complete")
        return 0
    output(_backup_action(config, target, command, args.backup, args.yes, input_fn, output))
    return 0


def _actions(config: Config, input_fn, output) -> interactive.Actions:
    current = config

    def refresh():
        nonlocal current
        current = load(current.paths.source, paths=current.paths)
        load_state(current)
        return current

    def mutation(call):
        active = refresh()
        result = call(active)
        return result, refresh()

    def show_status():
        active = refresh()
        return status.render(status.collect(active))

    def show_health():
        value = status.collect(refresh())
        return {identity: item["health"] for identity, item in value["databases"].items()}

    def change(target, values, reset):
        return mutation(
            lambda active: _configure(active, target, values, reset, False, input_fn, output)
        )

    def history(target):
        active = refresh()
        return backup.history(active, active.select(target))

    def add(project, role, engine):
        return mutation(lambda active: _add(active, project, role, engine, False, input_fn, output))

    def lifecycle(target, command):
        return mutation(
            lambda active: _lifecycle(
                active,
                active.select(target),
                command,
                False,
                input_fn,
                output,
            )
        )

    def info(target):
        active = refresh()
        return _info(database.info(active, active.select(target)))

    def logs(target):
        active = refresh()
        return database.logs(active, active.select(target))

    return interactive.Actions(
        status=show_status,
        health=show_health,
        add=add,
        info=info,
        configure=change,
        start=lambda target: lifecycle(target, "start"),
        stop=lambda target: lifecycle(target, "stop"),
        restart=lambda target: lifecycle(target, "restart"),
        logs=logs,
        backup=lambda target: mutation(
            lambda active: _backup_action(
                active,
                active.select(target),
                "create",
                None,
                False,
                input_fn,
                output,
            )
        ),
        backup_list=lambda target: _history(history(target)),
        backup_history=history,
        backup_test=lambda target, selected: mutation(
            lambda active: _backup_action(
                active,
                active.select(target),
                "test",
                selected,
                False,
                input_fn,
                output,
            )
        ),
        restore=lambda target, selected: mutation(
            lambda active: _restore(
                active,
                active.select(target),
                selected,
                False,
                input_fn,
                output,
            )
        ),
        host_check=show_status,
        host_setup=lambda: mutation(
            lambda active: _host_setup(active.paths.source, {}, False, input_fn, output)
        ),
        host_update=lambda version: mutation(
            lambda active: _host_update(active, version, False, input_fn, output)
        ),
    )


def _add(config, project, role, engine, yes, input_fn, output):
    change = database.prepare_add(config, project, role, engine=engine)
    if not change.noop and not _confirm(change.preview(), yes, input_fn, output):
        change.cancel()
        return "Cancelled"
    return _result(database.commit(change))


def _configure(config, target, values, reset, yes, input_fn, output):
    change = database.prepare_configure(config, target, values, reset=reset)
    if change.noop:
        change.cancel()
        return "No changes"
    if not _confirm(_configure_preview(change), yes, input_fn, output):
        change.cancel()
        return "Cancelled"
    return _result(database.commit(change))


def _configure_preview(change):
    if not all(hasattr(change, name) for name in ("prior", "database", "changed")):
        return change.preview()
    lines = [change.preview(), "Values:"]
    for name in change.changed:
        before = interactive._display(interactive._current(change.prior, name))
        after = interactive._display(interactive._current(change.database, name))
        lines.append(f"  {name}: {before} -> {after}")
    return "\n".join(lines)


def _lifecycle(config, target, command, yes, input_fn, output):
    effect = f"Host: {config.host.id}\nDatabase: {target.identity}\nAction: {command}"
    if not _confirm(effect, yes, input_fn, output):
        return "Cancelled"
    getattr(database, command)(config, target)
    return f"{target.identity}: {command} complete"


def _backup_action(config, target, command, selected, yes, input_fn, output):
    effect = f"Host: {config.host.id}\nDatabase: {target.identity}\nBackup action: {command}"
    if selected:
        effect += f"\nBackup: {selected}"
    if not _confirm(effect, yes, input_fn, output):
        return "Cancelled"
    result = (
        backup.create(config, target, purpose="manual")
        if command == "create"
        else backup.test(config, target, selected)
    )
    return _result(result)


def _restore(config, target, selected, yes, input_fn, output):
    result = restore.restore(
        config,
        target,
        selected,
        yes=yes,
        confirm=lambda preview: _confirm(_preview(preview), False, input_fn, output),
    )
    return _result(result)


def _host_setup(source, values, yes, input_fn, output):
    from . import host

    return host.setup(
        source,
        values,
        yes=yes,
        confirm=lambda text: _confirm(text, False, input_fn, output),
    )


def _host_update(config, version, yes, input_fn, output):
    from . import host

    return host.update(
        config,
        version,
        yes=yes,
        confirm=lambda text: _confirm(text, False, input_fn, output),
    )


def _required(value, name, example, input_fn):
    if value:
        return value
    if _tty():
        entered = input_fn(f"{name}: ").strip()
        if entered:
            return entered
    raise Error(f"{name} is required; example: {example}")


def _confirm(text, yes, input_fn, output):
    output(text)
    if yes:
        return True
    if not _tty():
        raise Error("confirmation requires a terminal or --yes")
    return input_fn("Continue? [y/N] ").strip().lower() in {"y", "yes"}


def _tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _canonical(source: Path) -> bool:
    return source.expanduser().resolve(strict=False) == (CONFIG_DIR / "host.yml").resolve(
        strict=False
    )


def _mutating(args) -> bool:
    if args.command is None or args.command in {"restore"}:
        return True
    if args.command == "database":
        return args.database_command in {"add", "configure", "start", "stop", "restart"}
    if args.command == "backup":
        return args.backup_command != "list"
    if args.command == "host":
        return args.host_command in {"setup", "update"}
    return False


def _preview(value: dict[str, Any]) -> str:
    return "\n".join(f"{key.replace('_', ' ').title()}: {item}" for key, item in value.items())


def _result(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _history(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No backups available"
    return "\n".join(
        f"{row['time']}  {row['backup']}  {row.get('source', 'unknown')}  "
        f"{row.get('verification', {}).get('state', 'unknown')}"
        for row in rows
    )


def _info(value: dict[str, Any]) -> str:
    lines = []
    for key, item in value.items():
        if isinstance(item, dict):
            lines.append(f"{key.replace('_', ' ').title()}:")
            lines.extend(
                f"  {name.replace('_', ' ').title()}: {current}" for name, current in item.items()
            )
        else:
            lines.append(f"{key.replace('_', ' ').title()}: {item}")
    return "\n".join(lines)

from __future__ import annotations

import argparse
import json
import os
import pwd
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from . import __version__, backup, database, interactive, restic, restore, status
from .config import CONFIG_DIR, Config, load, load_state, require_no_orphans
from .errors import Error
from .log import sanitize
from .log import write as log_write


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="evdb")
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
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
    retention.add_argument("--dry-run", action="store_true")
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

    host = commands.add_parser("host", help="check, set up, update, or uninstall this host")
    host_commands = host.add_subparsers(dest="host_command", required=True)
    host_check = host_commands.add_parser("check")
    host_check.add_argument("--json", action="store_true")
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
    uninstall = host_commands.add_parser("uninstall")
    uninstall.add_argument("--purge", action="store_true")
    uninstall.add_argument("--yes", action="store_true")
    return result


def main(
    argv: list[str] | None = None,
    *,
    input_fn=None,
    output=None,
    error=None,
) -> int:
    terminal_output = output is None and sys.stdout.isatty()
    input_fn = input_fn or input
    output = output or print
    error = error or (lambda value: print(value, file=sys.stderr))
    args = parser().parse_args(argv)
    try:
        source = Path(args.config)
        _require_host_access(source, args)
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
            elif not source.is_file():
                _require_setup_values(values)
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
        if _mutating(args) and not _uninstalling(args):
            require_no_orphans(config)
        if args.command is None:
            return interactive.run(
                config,
                _actions(config, input_fn, output, terminal_output=terminal_output),
                input_fn=input_fn,
                output=output,
            )
        return _dispatch(config, args, input_fn, output, terminal_output=terminal_output)
    except KeyboardInterrupt:
        output("Cancelled")
        return 130
    except (Error, OSError, ValueError) as exc:
        error(f"evdb: {exc}")
        return 1


def _dispatch(config: Config, args, input_fn, output, *, terminal_output: bool = False) -> int:
    started = time.monotonic()
    target = None
    selector = getattr(args, "database", None)
    if isinstance(selector, str):
        with suppress(Error):
            target = config.select(selector)
    command = args.command
    subcommand = getattr(args, f"{command}_command", None)
    if subcommand:
        command = f"{command} {subcommand}"
    fields = {
        "host": config.host.id,
        "project": target.project if target else None,
        "role": target.role if target else None,
        "engine": target.engine if target else None,
        "command": command,
    }
    log_write("cli_operation", **fields, step="start", result="started")
    try:
        result = _dispatch_command(
            config,
            args,
            input_fn,
            output,
            terminal_output=terminal_output,
        )
    except BaseException as exc:
        log_write(
            "cli_operation",
            **fields,
            step="complete",
            result="failed",
            duration=round(time.monotonic() - started, 3),
            error=str(exc),
        )
        raise
    log_write(
        "cli_operation",
        **fields,
        step="complete",
        result="success" if result == 0 else "failed",
        duration=round(time.monotonic() - started, 3),
    )
    return result


def _dispatch_command(
    config: Config, args, input_fn, output, *, terminal_output: bool = False
) -> int:
    if args.command == "status":
        if os.getenv("EVDB_COMPATIBILITY_CHECK") == "1":
            from . import host

            host.compatibility(config, Path(os.getenv("EVDB_UNIT_DIR", "/etc/systemd/system")))
        target = config.select(args.database) if args.database else None
        value = status.collect(config, target)
        output(status.dumps(value) if args.json else status.render(value))
        return 0 if value["healthy"] else 1
    if args.command == "database":
        return _database(config, args, input_fn, output, terminal_output=terminal_output)
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
            output(status.dumps(value) if args.json else status.render(value))
            return 0 if value["healthy"] else 1
        if args.host_command == "uninstall":
            output(_host_uninstall(config, args.purge, args.yes, input_fn, output))
            return 0
        version = _required(args.version, "version", "1.2.3", input_fn)
        output(_host_update(config, version, args.yes, input_fn, output))
        return 0
    raise Error("unknown command")


def _database(config, args, input_fn, output, *, terminal_output=False):
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
        if not terminal_output:
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
        output(_history(backup.history(config, target)))
        return 0
    if command == "retention":
        targets = [item for item in config.databases if item.durable] if args.all else [target]
        if not args.dry_run and not _confirm(
            f"Host: {config.host.id}\nApply retention: "
            + ", ".join(item.identity for item in targets),
            args.yes,
            input_fn,
            output,
        ):
            return 0
        for item in targets:
            result = restic.forget(config, item, dry_run=args.dry_run)
            if args.dry_run:
                preview = sanitize(result.out).strip() or "No snapshots would be removed"
                output(f"{item.identity}:\n{preview}")
                restic.approve_retention(config, item)
        output("Retention dry run complete" if args.dry_run else "Retention complete")
        return 0
    output(_backup_action(config, target, command, args.backup, args.yes, input_fn, output))
    return 0


def _actions(
    config: Config, input_fn, output, *, terminal_output: bool = False
) -> interactive.Actions:
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

    def observe(active, target, command, call):
        started = time.monotonic()
        fields = {
            "host": active.host.id,
            "project": target.project,
            "role": target.role,
            "engine": target.engine,
            "command": command,
        }
        log_write("cli_operation", **fields, step="start", result="started")
        try:
            result = call()
        except BaseException as exc:
            log_write(
                "cli_operation",
                **fields,
                step="complete",
                result="failed",
                duration=round(time.monotonic() - started, 3),
                error=str(exc),
            )
            raise
        log_write(
            "cli_operation",
            **fields,
            step="complete",
            result="success",
            duration=round(time.monotonic() - started, 3),
        )
        return result

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
        selected = active.select(target)
        return observe(
            active,
            selected,
            "backup list",
            lambda: backup.history(active, selected),
        )

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
        if not terminal_output:
            raise Error("database info prints credentials and requires a terminal")
        active = refresh()
        selected = active.select(target)
        return observe(
            active,
            selected,
            "database info",
            lambda: _info(database.info(active, selected)),
        )

    def logs(target):
        active = refresh()
        selected = active.select(target)
        return observe(
            active,
            selected,
            "database logs",
            lambda: database.logs(active, selected),
        )

    def test_backup(target, selected):
        active = refresh()
        database_target = active.select(target)
        result = observe(
            active,
            database_target,
            "backup test",
            lambda: _backup_action(
                active,
                database_target,
                "test",
                selected,
                False,
                input_fn,
                output,
            ),
        )
        return result, refresh()

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
        backup_test=test_backup,
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
    if command == "create":
        return (
            f"{target.identity}: backup {result['backup']} completed at {result['finished']}; "
            f"snapshot {result.get('snapshot') or 'not uploaded'}"
        )
    return (
        f"{target.identity}: backup {result['backup']} tested at {result['time']}; "
        f"snapshot {result.get('snapshot') or 'local'}"
    )


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


def _host_uninstall(config, purge, yes, input_fn, output):
    from . import host

    return host.uninstall(
        config,
        purge=purge,
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


def _require_setup_values(values: dict[str, Any]) -> None:
    examples = {
        "host_id": "--host-id host-01",
        "domain": "--domain storage.example.com",
        "data_root": "--data-root /srv/databases",
        "acme_email": "--acme-email ops@example.com",
        "dns_provider": "--dns-provider cloudflare",
        "postgres_repo": "--postgres-repo rclone:remote:host-01/postgres",
        "kv_repo": "--kv-repo rclone:remote:host-01/kv",
        "dns_env_file": "--dns-env-file /root/evdb-dns.env",
    }
    for name, example in examples.items():
        if not values.get(name):
            raise Error(f"{name.replace('_', '-')} is required; example: evdb host setup {example}")


def _confirm(text, yes, input_fn, output):
    output(text)
    if yes:
        return True
    if not _tty():
        raise Error("confirmation requires a terminal or --yes")
    return input_fn("Continue? [y/N] ").strip().lower() in {"y", "yes"}


def _tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _require_host_access(source: Path, args) -> None:
    if not _canonical(source) or _host_access_allowed():
        return
    if args.command is None or (
        args.command == "host" and args.host_command in {"setup", "update", "uninstall"}
    ):
        raise Error("host access requires root; run sudo evdb")
    try:
        blocked = source.parent.exists() and not os.access(source.parent, os.X_OK)
    except OSError:
        blocked = True
    if blocked or source.exists() or os.path.lexists(source):
        raise Error("host access requires root; run sudo evdb")


def _host_access_allowed() -> bool:
    if os.geteuid() == 0:
        return True
    try:
        return pwd.getpwuid(os.geteuid()).pw_name == "evdb"
    except KeyError:
        return False


def _canonical(source: Path) -> bool:
    return source.expanduser().resolve(strict=False) == (CONFIG_DIR / "host.yml").resolve(
        strict=False
    )


def _uninstalling(args) -> bool:
    return args.command == "host" and args.host_command == "uninstall"


def _mutating(args) -> bool:
    if args.command is None or args.command in {"restore"}:
        return True
    if args.command == "database":
        return args.database_command in {"add", "configure", "start", "stop", "restart"}
    if args.command == "backup":
        return args.backup_command != "list"
    if args.command == "host":
        return args.host_command in {"setup", "update", "uninstall"}
    return False


def _preview(value: dict[str, Any]) -> str:
    return "\n".join(f"{key.replace('_', ' ').title()}: {item}" for key, item in value.items())


def _result(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _history(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No backups available"
    return "\n".join(
        f"{row['time']}  {row['backup']}  {row.get('purpose', 'unknown')}  "
        f"{row.get('source', 'unknown')}  {row.get('snapshot') or '-'}  "
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

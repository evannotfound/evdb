from __future__ import annotations

import argparse
import os
import pwd
import sys
from getpass import getpass
from pathlib import Path

from . import __version__, backup, database, status, ui
from .config import CONFIG_DIR, load
from .errors import Error
from .run import clean


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="evdb")
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    result.add_argument(
        "--config", default=os.getenv("EVDB_CONFIG", str(CONFIG_DIR / "config.yml"))
    )
    commands = result.add_subparsers(dest="command")

    init = commands.add_parser("init", help="initialize or refresh this host")
    init.add_argument("--host-id")
    init.add_argument("--domain")
    init.add_argument("--data-root")
    init.add_argument("--acme-email")
    init.add_argument("--dns-provider")
    init.add_argument("--repository")
    init.add_argument("--restic-password-file")
    init.add_argument("--dns-file")
    init.add_argument("--rclone-config")
    init.add_argument("--yes", action="store_true")

    show = commands.add_parser("status", help="show host and database health")
    show.add_argument("database", nargs="?")
    show.add_argument("--json", action="store_true")

    databases = commands.add_parser("database", help="manage one database role")
    database_commands = databases.add_subparsers(dest="database_command", required=True)
    database_commands.add_parser("list")
    add = database_commands.add_parser("add")
    add.add_argument("project")
    add.add_argument("role", choices=("postgres", "kv"))
    add.add_argument("--engine", choices=("dragonfly", "redis"))
    add.add_argument("--password-file")
    info = database_commands.add_parser("info")
    info.add_argument("database")
    configure = database_commands.add_parser("configure")
    configure.add_argument("database")
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
    for name in ("start", "stop", "restart"):
        command = database_commands.add_parser(name)
        command.add_argument("database")
    logs = database_commands.add_parser("logs")
    logs.add_argument("database")
    logs.add_argument("--lines", type=int, default=200)

    backups = commands.add_parser("backup", help="create and list backups")
    backup_commands = backups.add_subparsers(dest="backup_command", required=True)
    create = backup_commands.add_parser("create")
    create.add_argument("database", nargs="?")
    create.add_argument("--all", action="store_true")
    listing = backup_commands.add_parser("list")
    listing.add_argument("database")
    return result


def main(
    argv: list[str] | None = None,
    *,
    input_fn=None,
    output=None,
    error=None,
    password_fn=None,
) -> int:
    injected = output is not None
    terminal = not injected and sys.stdout.isatty()
    input_fn = input_fn or input
    output = output or print
    error = error or (lambda value: print(value, file=sys.stderr))
    password_fn = password_fn or getpass
    args = parser().parse_args(argv)
    try:
        source = Path(args.config)
        _require_access(source, args)
        if _mutating(args) and not _canonical(source):
            raise Error(
                "alternate --config is read-only; mutating commands require "
                f"{CONFIG_DIR / 'config.yml'}"
            )
        if args.command == "init":
            values = {
                name: getattr(args, name)
                for name in (
                    "host_id",
                    "domain",
                    "data_root",
                    "acme_email",
                    "dns_provider",
                    "repository",
                    "restic_password_file",
                    "dns_file",
                    "rclone_config",
                )
                if getattr(args, name) is not None
            }
            if not source.is_file() and not args.yes and _tty():
                values = _init_values(values, input_fn, password_fn)
            from . import host

            value = host.initialize(source, values)
            output(f"{value['host']['id']}: initialization complete")
            return 0 if value["host"]["healthy"] else 1
        if args.command is None:
            if not _tty():
                raise Error("a command is required; example: evdb status")
            config = load(source)
            return ui.run(config, input_fn=input_fn, output=output, password_fn=password_fn)
        config = load(source)
        if args.command == "status":
            target = config.select(args.database) if args.database else None
            value = status.collect(config, target)
            output(status.dumps(value) if args.json else status.render(value))
            return 0 if value["healthy"] else 1
        if args.command == "database":
            return _database(config, args, output, terminal=terminal)
        if args.command == "backup":
            return _backup(config, args, output)
        raise Error("unknown command")
    except KeyboardInterrupt:
        output("Cancelled")
        return 130
    except Error as exc:
        error(f"evdb: {clean(str(exc))}")
        return 1


def _database(config, args, output, *, terminal: bool) -> int:
    command = args.database_command
    if command == "list":
        for target in config.databases:
            output(f"{target.identity}\t{target.engine}")
        return 0
    if command == "add":
        if args.role == "postgres" and args.engine is not None:
            raise Error("--engine is only valid for a KV database")
        if args.role == "kv" and args.password_file:
            raise Error("--password-file is only valid for Postgres")
        password = database.password_file(args.password_file) if args.password_file else None
        database.add(
            config,
            args.project,
            args.role,
            engine=args.engine,
            password=password,
        )
        output(f"{args.project}/{args.role} is healthy")
        return 0
    target = config.select(args.database)
    if command == "info":
        if not terminal:
            raise Error("database info prints credentials and requires a terminal")
        output(ui.pairs(database.info(config, target)))
    elif command == "configure":
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
        database.configure(config, target, values, reset=tuple(args.reset))
        output(f"{target.identity} settings saved and healthy")
    elif command == "logs":
        output(database.logs(config, target, lines=args.lines))
    else:
        getattr(database, command)(config, target)
        output(f"{target.identity}: {command} complete")
    return 0


def _backup(config, args, output) -> int:
    if args.backup_command == "list":
        target = config.select(args.database)
        rows = backup.history(config, target)
        output(ui.backup_history(rows))
        return 0
    if args.all:
        if args.database:
            raise Error("backup create accepts either DATABASE or --all")
        results = backup.create_all(config)
        if not results:
            output("No durable databases configured; no backup was due")
            return 0
        failed = False
        for identity, value in results.items():
            failed = failed or not value["ok"]
            output(
                f"{identity}: backup {value['backup']} snapshot {value['snapshot']}"
                if value["ok"]
                else f"{identity}: {value['error']}"
            )
        return 1 if failed else 0
    if not args.database:
        raise Error("database is required unless --all is used")
    target = config.select(args.database)
    value = backup.create(config, target)
    output(
        f"{target.identity}: backup {value['backup']} completed at {value['finished']}; "
        f"snapshot {value['snapshot']}; repository {value['repository']}"
    )
    return 0


def _canonical(source: Path) -> bool:
    return source.expanduser().resolve(strict=False) == (CONFIG_DIR / "config.yml").resolve(
        strict=False
    )


def _mutating(args) -> bool:
    if args.command is None or args.command == "init":
        return True
    if args.command == "database":
        return args.database_command not in {"list", "info", "logs"}
    if args.command == "backup":
        return args.backup_command == "create"
    return False


def _require_access(source: Path, args) -> None:
    if not _canonical(source) or _host_access_allowed():
        return
    if args.command in {None, "init"}:
        raise Error("host access requires root; run sudo evdb")
    if source.exists() or source.parent.exists() and not os.access(source.parent, os.X_OK):
        raise Error("host access requires root; run sudo evdb")


def _host_access_allowed() -> bool:
    if os.geteuid() == 0:
        return True
    try:
        return pwd.getpwuid(os.geteuid()).pw_name == "evdb"
    except KeyError:
        return False


def _tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _init_values(values: dict[str, str], input_fn, password_fn=getpass) -> dict[str, str]:
    prompts = (
        ("host_id", "Host ID"),
        ("domain", "Base domain"),
        ("data_root", "Database data root"),
        ("acme_email", "ACME email"),
        ("dns_provider", "DNS provider"),
        ("repository", "Restic repository"),
        ("dns_file", "DNS credential file"),
        ("rclone_config", "Rclone configuration"),
    )
    result = dict(values)
    for name, prompt in prompts:
        if result.get(name):
            continue
        try:
            value = input_fn(f"{prompt}: ").strip()
        except EOFError as exc:
            raise Error(f"{name.replace('_', '-')} is required") from exc
        if not value:
            raise Error(f"{name.replace('_', '-')} is required")
        result[name] = value
    if not result.get("restic_password_file"):
        result["restic_password"] = password_fn("Initial Restic password (blank to generate): ")
    return result

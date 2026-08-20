from __future__ import annotations

import argparse
import os
import sys
from getpass import getpass
from pathlib import Path

from . import __version__, backup, database, dns, status, ui
from .config import (
    CONFIG_DIR,
    load,
    rclone_remotes,
    repository_parts,
    validate_data_root,
    validate_data_roots,
    validate_domain,
    validate_email,
    validate_host_id,
)
from .errors import Error
from .models import Paths
from .run import clean


def parser() -> argparse.ArgumentParser:
    dns.catalog()
    result = argparse.ArgumentParser(
        prog="evdb",
        description="Manage host-local Postgres and Redis-compatible databases.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n  sudo evdb init\n  sudo evdb status\n"
            "  sudo evdb database add notes-prod-01 postgres"
        ),
    )
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    result.add_argument(
        "--config", default=os.getenv("EVDB_CONFIG", str(CONFIG_DIR / "config.yml"))
    )
    commands = result.add_subparsers(dest="command")

    init = commands.add_parser("init", help="initialize or refresh this host")
    init.add_argument("--host-id", metavar="NAME", help="lowercase host name, e.g. example-01")
    init.add_argument("--domain", metavar="DOMAIN", help="base domain, e.g. storage.example.com")
    init.add_argument(
        "--data-root",
        dest="data_roots",
        metavar="PATH",
        action="append",
        help="allowed database data root; repeat to configure more than one",
    )
    init.add_argument("--acme-email", metavar="EMAIL", help="ACME account email")
    init.add_argument("--dns-provider", metavar="PROVIDER", help="cataloged lego provider code")
    init.add_argument(
        "--repository", metavar="REPOSITORY", help="rclone:REMOTE:PATH or absolute local path"
    )
    init.add_argument(
        "--restic-password-file", metavar="PATH", help="private initial Restic password file"
    )
    init.add_argument(
        "--dns-file", metavar="PATH", help="provider KEY=VALUE file for non-interactive setup"
    )
    init.add_argument("--rclone-config", metavar="PATH", help="private native rclone configuration")
    init.add_argument(
        "--yes", action="store_true", help="refresh an already configured host without prompts"
    )

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
    add.add_argument("--username", help="initial Postgres username (creation only)")
    add.add_argument("--database-name", help="initial Postgres database name (creation only)")
    add.add_argument("--data-root", metavar="PATH", help="configured database data root")
    add.add_argument(
        "--password-file", metavar="PATH", help="private initial Postgres password file"
    )
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
                    "data_roots",
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
                values = _init_values(values, input_fn, password_fn, output=output, source=source)
            from . import host

            value = host.initialize(source, values, output=output)
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
        if args.role == "kv" and (args.username or args.database_name):
            raise Error("--username and --database-name are only valid for Postgres")
        password = database.password_file(args.password_file) if args.password_file else None
        database.add(
            config,
            args.project,
            args.role,
            engine=args.engine,
            password=password,
            username=args.username,
            database_name=args.database_name,
            data_root=args.data_root,
        )
        output(f"{args.project}/{args.role} is healthy")
        return 0
    target = config.select(args.database)
    if command == "info":
        if not terminal:
            raise Error("database info prints credentials and requires a terminal")
        output(ui.database_details(database.info(config, target), include_connection=True))
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
    return os.geteuid() == 0


def _tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _init_values(
    values: dict[str, object],
    input_fn,
    password_fn=getpass,
    *,
    output=print,
    source: Path | None = None,
) -> dict[str, object]:
    result = dict(values)
    while True:
        _init_host(result, input_fn, output)
        _init_dns(result, input_fn, output, password_fn)
        _init_backup(result, input_fn, output)
        if not result.get("restic_password_file") and "restic_password" not in result:
            result["restic_password"] = (
                ui.ask_secret(password_fn, output, "Initial Restic password") or ""
            )

        error = None
        try:
            if source is not None:
                from .host import _initial

                _initial(result, Paths(config=source.parent))
        except Error as exc:
            error = clean(str(exc))

        output("")
        output("Review initialization")
        output(_init_review(result))
        if error:
            output("")
            output(f"Cannot apply: {error}")
            action = ui.choose(
                input_fn,
                output,
                "Next",
                [("1", "Edit"), ("0", "Cancel")],
            )
        else:
            action = ui.choose(
                input_fn,
                output,
                "Next",
                [("1", "Apply"), ("2", "Edit"), ("0", "Cancel")],
                default="1",
            )
            if action == "1":
                return result
        if action in {None, "0"}:
            raise KeyboardInterrupt
        _edit_init(result, input_fn, output)


def _init_host(result: dict, input_fn, output) -> None:
    fields = (
        (
            "host_id",
            "Host ID",
            "Used in wildcard and database hostnames; example: example-01",
            validate_host_id,
        ),
        (
            "domain",
            "Base domain",
            "DNS zone used for database hostnames; example: storage.example.com",
            validate_domain,
        ),
        (
            "acme_email",
            "ACME email",
            "Address used for certificate registration and expiry notices.",
            validate_email,
        ),
    )
    for name, label, help_text, validate in fields:
        if result.get(name):
            continue
        value = ui.ask_text(
            input_fn,
            output,
            label,
            help_text=help_text,
            validate=validate,
        )
        if value is None:
            raise KeyboardInterrupt
        result[name] = value
    _init_data_roots(result, input_fn, output)


def _init_data_roots(result: dict, input_fn, output) -> None:
    if result.get("data_roots"):
        return
    roots = []
    while True:
        value = ui.ask_text(
            input_fn,
            output,
            "Database data root",
            default=str(Paths().databases) if not roots else None,
            help_text="Dedicated directory allowed for database placement.",
            validate=lambda value: _data_root_candidate(roots, value),
        )
        if value is None:
            raise KeyboardInterrupt
        if value in roots:
            output("Database data roots must not contain duplicates")
            continue
        roots.append(value)
        if not ui.confirm(input_fn, "Add another database data root?"):
            result["data_roots"] = roots
            return


def _data_root_candidate(roots: list[str], value: str) -> str:
    path = validate_data_root(value)
    validate_data_roots([*(Path(root) for root in roots), path])
    return str(path)


def _init_dns(result: dict, input_fn, output, password_fn) -> None:
    if not result.get("dns_provider"):
        while True:
            term = ui.ask_text(
                input_fn,
                output,
                "Search DNS providers",
                help_text="Enter part of a provider name or code; example: cloudflare",
            )
            if term is None:
                raise KeyboardInterrupt
            matches = dns.search(term)
            if not matches:
                output("No supported DNS providers match that search")
                continue
            if len(matches) > 20:
                output(f"{len(matches)} providers match; enter a more specific search")
                continue
            key = ui.choose(
                input_fn,
                output,
                "Provider",
                [
                    (str(index), f"{item['name']} ({item['code']})")
                    for index, item in enumerate(matches, 1)
                ],
                default="1" if len(matches) == 1 else None,
            )
            if key is None:
                raise KeyboardInterrupt
            result["dns_provider"] = matches[int(key) - 1]["code"]
            break
    else:
        result["dns_provider"] = dns.normalize(result["dns_provider"])
    if result.get("dns_file") or "dns" in result:
        return
    selected = dns.provider(result["dns_provider"])
    output("")
    output(f"{selected['name']} ({selected['code']})")
    output(f"Provider help: {selected['help']}")
    result["dns"] = _dns_variables(
        selected["credentials"], input_fn, output, password_fn, heading="Credential variables"
    )
    if selected["additional"] and ui.confirm(input_fn, "Configure advanced DNS variables?"):
        result["dns"].update(
            _dns_variables(
                selected["additional"],
                input_fn,
                output,
                password_fn,
                heading="Advanced variables",
            )
        )


def _dns_variables(items, input_fn, output, password_fn, *, heading: str) -> dict[str, str]:
    values = {}
    names = tuple(items)
    while True:
        output("")
        output(heading)
        options = [(str(index), f"{name} - {items[name]}") for index, name in enumerate(names, 1)]
        options.append(("0", "Done"))
        selected = ui.choose(input_fn, output, "Variable", options)
        if selected in {None, "0"}:
            return values
        name = names[int(selected) - 1]
        if dns.secret_variable(name):
            value = ui.ask_secret(password_fn, output, name, required=True)
        else:
            value = ui.ask_text(input_fn, output, name)
        if value is None:
            raise KeyboardInterrupt
        values[name] = value


def _init_backup(result: dict, input_fn, output) -> None:
    parts = repository_parts(result["repository"]) if result.get("repository") else None
    if result.get("repository") and parts is None:
        result.pop("rclone_config", None)
        return
    if not result.get("repository"):
        mode = ui.choose(
            input_fn,
            output,
            "Backup storage",
            [("1", "Rclone remote"), ("2", "Local filesystem")],
            default="1",
        )
        if mode is None:
            raise KeyboardInterrupt
        if mode == "2":
            value = ui.ask_text(
                input_fn,
                output,
                "Local repository",
                help_text=(
                    "Use an absolute path under a safe non-root-owned parent; "
                    "example: /srv/restic/evdb"
                ),
                validate=_local_repository,
            )
            if value is None:
                raise KeyboardInterrupt
            result["repository"] = value
            result.pop("rclone_config", None)
            return
    if not result.get("rclone_config"):
        value = ui.ask_text(
            input_fn,
            output,
            "Rclone configuration",
            help_text=(
                "Absolute path reported by `rclone config file`; mode 0600 and non-root-owned."
            ),
            validate=lambda value: value if rclone_remotes(Path(value)) else _no_remotes(),
        )
        if value is None:
            raise KeyboardInterrupt
        result["rclone_config"] = value
    remotes = rclone_remotes(Path(result["rclone_config"]))
    if not result.get("repository"):
        key = ui.choose(
            input_fn,
            output,
            "Rclone remote",
            [(str(index), f"{remote}:") for index, remote in enumerate(remotes, 1)],
        )
        if key is None:
            raise KeyboardInterrupt
        remote = remotes[int(key) - 1]
        relative = ui.ask_text(
            input_fn,
            output,
            "Repository path",
            default=f"evdb/{result['host_id']}",
            validate=lambda value: value if repository_parts(f"rclone:{remote}:{value}") else value,
        )
        if relative is None:
            raise KeyboardInterrupt
        result["repository"] = f"rclone:{remote}:{relative}"


def _no_remotes():
    raise Error("rclone configuration contains no remotes")


def _local_repository(value: str) -> str:
    if repository_parts(value) is not None:
        raise Error("local repository must be an absolute filesystem path")
    return value


def _init_review(result: dict) -> str:
    credentials = result.get("dns", {})
    return ui.pairs(
        {
            "Host": result["host_id"],
            "Base domain": result["domain"],
            "Database data roots": "\n".join(result["data_roots"]),
            "Wildcard": f"*.{result['host_id']}.{result['domain']}",
            "ACME email": result["acme_email"],
            "DNS provider": result["dns_provider"],
            "DNS variables": ", ".join(sorted(credentials))
            if credentials
            else (f"file {result['dns_file']}" if result.get("dns_file") else "provider identity"),
            "Repository": result["repository"],
            "Rclone config": result.get("rclone_config") or "not used",
            "Restic password": "file"
            if result.get("restic_password_file")
            else ("provided" if result.get("restic_password") else "generated"),
        }
    )


def _edit_init(result: dict, input_fn, output) -> None:
    fields = (
        ("1", "Host ID", ("host_id",)),
        ("2", "Base domain", ("domain",)),
        ("3", "Database data roots", ("data_roots",)),
        ("4", "ACME email", ("acme_email",)),
        ("5", "DNS provider and credentials", ("dns_provider", "dns", "dns_file")),
        ("6", "Backup repository", ("repository", "rclone_config")),
        ("7", "Restic password", ("restic_password", "restic_password_file")),
    )
    selected = ui.choose(
        input_fn,
        output,
        "Edit",
        [(key, label) for key, label, _names in fields] + [("0", "Cancel")],
    )
    if selected in {None, "0"}:
        return
    names = next(names for key, _label, names in fields if key == selected)
    for name in names:
        result.pop(name, None)

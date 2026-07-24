from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import restic, status
from .backup import backup
from .config import ConfigError, load, render
from .errors import Error
from .restore import due, restore

DEFAULT_CONFIG = "/etc/evanovation-db"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="evanovation-db")
    result.add_argument("--config", default=os.getenv("EVANOVATION_DB_CONFIG", DEFAULT_CONFIG))
    sub = result.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("--source")
    validate.add_argument("--output")

    for name in ("backup", "restore-check"):
        command = sub.add_parser(name)
        command.add_argument("group", choices=("postgres", "kv"))
        command.add_argument("instance")
        if name == "restore-check":
            command.add_argument("--folder")

    show = sub.add_parser("status")
    show.add_argument("--json", action="store_true")

    due_command = sub.add_parser("restore-due")
    due_command.add_argument("--run", action="store_true")

    maintain = sub.add_parser("maintain")
    maintain.add_argument("action", choices=("init", "forget", "prune", "check"))
    maintain.add_argument("group", choices=("postgres", "kv"))
    maintain.add_argument("--apply", action="store_true")
    maintain.add_argument("--part", type=int)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "validate":
            source = Path(args.source or args.config)
            config = load(source)
            if args.output:
                render(source, args.output)
            print(f"valid: {config.host.id} ({len(config.instances)} instances)")
            return 0

        config = load(args.config)
        if args.command == "backup":
            folder = backup(config, config.get(args.group, args.instance))
            print(folder)
            return 0
        if args.command == "restore-check":
            result = restore(config, config.get(args.group, args.instance), args.folder)
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == "status":
            rows, failed = status.get(config)
            if args.json:
                print(json.dumps(rows, sort_keys=True))
            else:
                for row in rows:
                    state = "stale" if row["backup_stale"] or row["restore_stale"] else "ok"
                    print(f"{row['group']}/{row['instance']}: {state}")
            return 1 if failed else 0
        if args.command == "restore-due":
            instance = due(config)
            print(f"{instance.group}/{instance.id}")
            if args.run:
                restore(config, instance)
            return 0
        if args.command == "maintain":
            if args.action == "init":
                if not args.apply:
                    raise ConfigError("init requires --apply")
                result = restic.init(config.host, args.group)
            elif args.action == "forget":
                result = restic.forget(config.host, args.group, dry_run=not args.apply)
            elif args.action == "prune":
                if not args.apply:
                    raise ConfigError("prune requires --apply")
                result = restic.prune(config.host, args.group)
            else:
                result = restic.check(config.host, args.group, part=args.part)
            print(result.out, end="")
            return 0
    except (Error, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

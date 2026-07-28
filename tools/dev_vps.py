from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SYNC_PATHS = ("src", "tests", "pyproject.toml", "uv.lock", "Makefile", "README.md")
PRODUCTION_HOST = "montreal-01"
CANONICAL_EVDB = "/opt/evdb/current/bin/evdb"
STABLE_EVDB = "/usr/local/bin/evdb"
DEV_VERSION = "0.0.dev0"


class DevError(Exception):
    pass


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run evdb checks on a disposable VPS")
    result.add_argument("target", help="explicit SSH target")
    result.add_argument("checkout", help="explicit remote checkout under /srv")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("sync", help="sync source and install the locked environment")
    test = commands.add_parser("test", help="run pytest in a fresh remote process")
    test.add_argument("args", nargs=argparse.REMAINDER)
    check = commands.add_parser("check", help="run make check in a fresh remote process")
    check.add_argument("args", nargs=argparse.REMAINDER)
    evdb = commands.add_parser("evdb", help="run the checkout's evdb explicitly with sudo")
    evdb.add_argument("args", nargs=argparse.REMAINDER)
    commands.add_parser("activate", help="point the stable command at the checkout")
    commands.add_parser("deactivate", help="restore the stable command to the installed release")
    return result


def target_host(target: str) -> str:
    """Reject the production host before any subprocess can run."""
    match = re.fullmatch(
        r"(?:(?:[a-z_][a-z0-9_-]*)@)?(?P<host>[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)",
        target,
    )
    host = match.group("host") if match else None
    if host is None or host == PRODUCTION_HOST:
        raise DevError("development commands cannot target montreal-01")
    return host


def checkout_path(value: str) -> str:
    raw = value.split("/")
    path = PurePosixPath(value)
    safe = all(re.fullmatch(r"[A-Za-z0-9._-]+", part) for part in path.parts[2:])
    if (
        path.parts[:2] != ("/", "srv")
        or len(path.parts) < 3
        or not safe
        or any(part in {"", ".", ".."} for part in raw[2:])
    ):
        raise DevError("development checkout must be an absolute path below /srv")
    return str(path)


def ssh_command(target: str, script: str, *, tty: bool = False) -> list[str]:
    command = ["ssh"]
    if tty:
        command.append("-tt")
    return [*command, "--", target, "sh", "-lc", shlex.quote(script)]


def _checkout_guard(checkout: str, *, writable: bool = False) -> str:
    access = f"test -w {shlex.quote(checkout)}; " if writable else ""
    return (
        f"test -d {shlex.quote(checkout)}; test ! -L {shlex.quote(checkout)}; "
        f"resolved=$(readlink -f {shlex.quote(checkout)}); "
        f'test "$resolved" = {shlex.quote(checkout)}; '
        'case "$resolved" in /srv/*) ;; *) exit 1;; esac; '
        f"{access}"
    )


def remote_script(checkout: str, command: Sequence[str]) -> str:
    """Scope source activation to one guarded remote process."""
    return (
        "set -eu; "
        f'test "$(hostname -s)" != {PRODUCTION_HOST}; '
        f"{_checkout_guard(checkout)}"
        f"cd {shlex.quote(checkout)}; "
        f'exec env EVDB_DEV=1 PATH="{shlex.quote(checkout)}/.venv/bin:$HOME/.local/bin:$PATH" '
        f"{shlex.join(command)}"
    )


def sync_manifest(*, run=subprocess.run) -> bytes:
    """Select tracked and unignored source files from the approved paths."""
    listed = run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            *SYNC_PATHS,
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout
    ignored = run(
        ["git", "check-ignore", "--no-index", "-z", "--stdin"],
        cwd=ROOT,
        input=listed,
        stdout=subprocess.PIPE,
        check=False,
    )
    if ignored.returncode not in {0, 1}:
        raise DevError(f"git check-ignore failed with exit code {ignored.returncode}")
    excluded = {item for item in ignored.stdout.split(b"\0") if item}
    files = []
    for raw in listed.split(b"\0"):
        if not raw or raw in excluded:
            continue
        name = os.fsdecode(raw)
        if (ROOT / name).is_file():
            files.append(raw)
    return b"\0".join(files) + b"\0"


def sync(target: str, checkout: str, *, run=subprocess.run) -> None:
    target_host(target)
    checkout = checkout_path(checkout)
    manifest = sync_manifest(run=run)
    prepare = (
        "set -eu; "
        f'test "$(hostname -s)" != {PRODUCTION_HOST}; '
        f"{_checkout_guard(checkout, writable=True)}"
    )
    run(ssh_command(target, prepare), check=True)
    run(
        [
            "rsync",
            "--archive",
            "--delete-delay",
            "--filter=P /.venv/",
            "--filter=P **/__pycache__/",
            "--files-from=-",
            "--from0",
            "--",
            "./",
            f"{target}:{checkout}/",
        ],
        cwd=ROOT,
        input=manifest,
        check=True,
    )
    run(
        ssh_command(
            target,
            remote_script(
                checkout,
                [
                    "env",
                    f"SETUPTOOLS_SCM_PRETEND_VERSION_FOR_EVDB={DEV_VERSION}",
                    "uv",
                    "sync",
                    "--project",
                    checkout,
                    "--locked",
                ],
            ),
        ),
        check=True,
    )


def execute(
    target: str,
    checkout: str,
    command: str,
    args: Sequence[str],
    *,
    run=subprocess.run,
) -> int:
    sync(target, checkout, run=run)
    checkout = checkout_path(checkout)
    if command == "test":
        selected = ["uv", "run", "--project", checkout, "--locked", "pytest", *args]
        tty = False
    elif command == "check":
        selected = ["make", "check", *args]
        tty = False
    else:
        selected = ["sudo", f"{checkout}/.venv/bin/evdb", *args]
        tty = True
    result = run(ssh_command(target, remote_script(checkout, selected), tty=tty), check=False)
    return result.returncode


def activate(target: str, checkout: str, *, enabled: bool, run=subprocess.run) -> None:
    target_host(target)
    checkout = checkout_path(checkout)
    selected = f"{checkout}/.venv/bin/evdb" if enabled else CANONICAL_EVDB
    script = (
        "set -eu; "
        f'test "$(hostname -s)" != {PRODUCTION_HOST}; '
        f"{_checkout_guard(checkout) if enabled else ''}"
        f"test -x {shlex.quote(selected)}; "
        f"expected=$(readlink -f {shlex.quote(selected)}); "
        f"sudo ln -sfn {shlex.quote(selected)} {STABLE_EVDB}; "
        f'test "$(readlink -f {STABLE_EVDB})" = "$expected"'
    )
    run(ssh_command(target, script, tty=True), check=True)


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "sync":
            sync(args.target, args.checkout)
        elif args.command in {"activate", "deactivate"}:
            activate(args.target, args.checkout, enabled=args.command == "activate")
        else:
            return execute(args.target, args.checkout, args.command, args.args)
    except subprocess.CalledProcessError as exc:
        print(f"dev VPS: command failed with exit code {exc.returncode}", file=sys.stderr)
        return exc.returncode or 1
    except (DevError, OSError) as exc:
        print(f"dev VPS: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

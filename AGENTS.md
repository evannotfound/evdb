# Repository Guide

- Keep names short, direct, and natural.
- Use the existing modules before adding a new layer or abstraction.
- Production Python uses the standard library and subprocess argument arrays.
- Use `uv` for the development environment, lock file, and local commands.
- Never use `shell=True` or put passwords and tokens in command arguments.
- Never commit `.env`, backup files, dumps, rclone config, or resolved secrets.
- Source config contains `op://` references only.
- Tests use disposable containers and local Restic repositories only.

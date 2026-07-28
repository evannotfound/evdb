## Why

Interactive `evdb` sessions currently mix human menus on stdout with JSON structured log records on stderr, so routine status checks and database mutations look like broken CLI output. The guided command should remain SSH-friendly and scriptable, but terminal operators need readable tables, prompts, progress, and completion summaries instead of leaked event records and developer-shaped JSON results.

## What Changes

- Add Rich-backed terminal presentation for interactive stdout: adaptive status tables, numbered menu layout, operation previews, confirmation prompts, progress indicators, and concise success/failure summaries.
- Keep the existing `argparse` command model and numbered menus; do not introduce a full-screen TUI, arrow-key-only navigation, or parser rewrite.
- Make structured log emission terminal-aware so routine JSON event records remain available to systemd, journald, redirected stderr, and tests without appearing in ordinary interactive terminal sessions.
- Preserve `evdb status --json` and non-terminal machine-readable output contracts, including secret redaction and stable exit behavior.
- Add Rich as a production dependency and verify it remains compatible with the one-file PyInstaller release path.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `operator-cli`: interactive terminal output becomes a polished numbered CLI using Rich while preserving SSH-safe navigation and machine-readable output contracts.
- `jobs`: structured logs become terminal-aware, remaining useful for journald and local tests without leaking routine JSON into human terminal sessions.

## Impact

- Affected code: `src/evdb/cli.py`, `src/evdb/interactive.py`, `src/evdb/status.py`, `src/evdb/log.py`, and likely a small new presentation module under `src/evdb/`.
- Affected tests: CLI, interactive, status, and structured log tests for TTY versus non-TTY behavior, redirected stderr, JSON contracts, and terminal rendering.
- Dependencies and packaging: add `rich` to runtime dependencies, update `uv.lock`, and smoke-test the PyInstaller one-file binary.
- External behavior: interactive terminal output changes visually; non-interactive command behavior and `status --json` remain compatible.

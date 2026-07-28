## 1. Dependency and Baseline Checks

- [x] 1.1 Add `rich` as a runtime dependency in `pyproject.toml` and refresh `uv.lock` with `uv`.
- [x] 1.2 Record baseline CLI behavior with focused tests or fixtures for `status --json`, injected output, non-terminal output, and structured log capture before changing rendering.

## 2. Structured Log Policy

- [x] 2.1 Add tests proving `log.write()` emits redacted JSONL when stderr is non-terminal or redirected.
- [x] 2.2 Add tests proving routine structured log records are suppressed when stderr is an interactive terminal.
- [x] 2.3 Implement terminal-aware structured log emission without changing emitted JSON fields, sorting, or redaction for non-terminal streams.
- [x] 2.4 Verify existing structured log tests still parse captured JSON records.

## 3. Terminal Presenter

- [x] 3.1 Add a small `evdb` presentation module that constructs Rich consoles only for terminal rendering and keeps workflow modules terminal-agnostic.
- [x] 3.2 Implement adaptive status table rendering from existing status dictionaries without changing `status.dumps()` or the status JSON schema.
- [x] 3.3 Implement numbered menu and database-list rendering that preserves existing numeric choices and text prompts.
- [x] 3.4 Implement readable preview rendering for confirmations, ensuring prompts occur outside any progress/status indicator.
- [x] 3.5 Implement concise human summaries for database, backup, restore, host setup/update/uninstall, unchanged, and failure-result paths that currently print raw result dictionaries.
- [x] 3.6 Add presenter tests with captured console output, narrow width, no-color/plain terminal behavior, and literal markup characters in displayed values.

## 4. CLI and Guided Flow Wiring

- [x] 4.1 Wire terminal presenter selection through `cli.main()` only when using real terminal stdout and no injected output function.
- [x] 4.2 Preserve plain string output paths for injected output, non-terminal stdout, and automation.
- [x] 4.3 Route `evdb status --json` around Rich rendering so stdout remains exactly one parseable JSON document.
- [x] 4.4 Update guided `interactive.py` output calls to use presenter-backed rendering where available while preserving existing input flow and `_choice()` validation.
- [x] 4.5 Wrap long synchronous status, mutation, backup, restore, and host operations with terminal progress/status indicators without changing domain operation order.
- [x] 4.6 Preserve terminal-only credential disclosure for `database info` and avoid persisting credentials in logs, state, or non-terminal output.

## 5. Documentation and Validation

- [x] 5.1 Update user-facing command documentation or README examples if terminal output examples change materially.
- [x] 5.2 Run `uv run ruff check src tests tools` and `uv run ruff format --check src tests tools`.
- [x] 5.3 Run `uv run pytest tests/unit tests/config`.
- [x] 5.4 Run integration collection and any safe local integration checks that do not mutate production hosts.
- [x] 5.5 Build the PyInstaller one-file executable and smoke-test `evdb --version`, `status --json`, and at least one terminal-rendered path where practical.
- [x] 5.6 Record any binary size/startup impact or skipped validation in the implementation notes.

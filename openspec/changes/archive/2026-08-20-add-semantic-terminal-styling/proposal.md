## Why

The append-only CLI is readable but gives headings, questions, defaults, healthy states, warnings, and
failures the same visual weight. Selective terminal styling would make routine operation faster to scan
without returning to the broad tables, panels, spinners, or dense presentation removed for v1.

## What Changes

- Add restrained semantic styling to guided and direct human output when the relevant stream is an
  interactive terminal: bold headings and questions, emphasized default answers, and distinct healthy,
  warning, and failure states.
- Use Rich only as the terminal rendering primitive, with automatic terminal capability handling and
  literal treatment of displayed values; do not add Rich tables, panels, spinners, live displays, or
  cursor-addressed navigation.
- Preserve state wording so color is never the only distinction, and disable styling when color is
  unavailable or disabled.
- Preserve byte-stable plain text for injected output, redirected streams, and JSON output.
- Add Rich as a runtime dependency and verify the one-file release still renders terminal output and
  preserves plain and JSON output contracts.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `operator-cli`: interactive terminal presentation gains a restrained semantic style grammar while
  preserving append-only interaction and plain automation output.

## Impact

- Affected code: the CLI/UI presentation boundary and human status rendering in `src/evdb/cli.py`,
  `src/evdb/ui.py`, and `src/evdb/status.py`.
- Affected tests: focused terminal versus injected/redirected output, prompt emphasis, semantic state
  styling, no-color behavior, and JSON output tests.
- Dependencies and packaging: add Rich to runtime dependencies, update `uv.lock`, and exercise the
  PyInstaller one-file smoke path.
- External contracts: no command, prompt answer, exit status, plain-text wording, or JSON schema changes.

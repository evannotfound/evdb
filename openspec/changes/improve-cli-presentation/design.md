## Context

`evdb` is a host-local operator command. The current guided flow is deliberately simple: `argparse` owns command parsing, `interactive.py` owns numbered prompts, workflow modules return strings or dictionaries, and `log.write()` emits structured JSON records to stderr for observability. This keeps SSH and systemd usage reliable, but it makes interactive sessions noisy because terminals display stdout and stderr together.

The current status path has a second presentation issue. `status.render()` builds a fixed-width table for every human status view, while `status.dumps()` provides the versioned JSON contract for automation. Mutating commands often finish by calling `_result()`, which pretty-prints returned dictionaries as JSON even for humans. Operators therefore see both routine JSON log events and final JSON result objects during a guided add, restore, backup, or update.

The existing product constraints still apply:

- Guided mode must work over ordinary interactive SSH sessions.
- Menus must remain numbered and must not require arrow-key terminal support or a full-screen TUI.
- `status --json` and other machine-readable output must remain secret-free and parseable.
- Structured logs must remain useful through systemd/journald and local tests.
- Runtime dependencies must stay compatible with the PyInstaller one-file release path.

## Goals / Non-Goals

**Goals:**

- Improve interactive terminal readability with Rich tables, prompt styling, progress/status indicators, and concise operation summaries.
- Keep the existing command names, parser behavior, numbered menu choices, direct workflow functions, and non-terminal automation behavior.
- Stop routine JSON structured log records from appearing in ordinary interactive terminal sessions while preserving them for systemd, journald, redirected stderr, and tests.
- Keep Rich usage localized to presentation code so database, backup, restore, config, secrets, and engine modules remain terminal-agnostic.
- Preserve secret redaction and terminal-only credential disclosure behavior.
- Validate Rich and its dependencies in the one-file binary path before completing the change.

**Non-Goals:**

- No full-screen TUI, arrow-key navigation, curses application, or daemon UI.
- No rewrite from `argparse` to Click, Typer, or another parser framework.
- No change to `status --json` schema, status exit codes, backup formats, database configuration, operation state, or service behavior.
- No production migration or live host mutation outside disposable validation.
- No dependency-driven redesign of domain modules.

## Decisions

### Add a small Rich presenter module

Add a narrow presentation module, likely `src/evdb/ui.py`, that owns Rich `Console` construction and terminal renderers. The presenter should provide functions or a small object for:

- host/database status tables from collected status dictionaries;
- numbered menus and list rows;
- operation previews currently returned as plain text;
- operation results currently rendered by `_result()`;
- backup history and database info views where appropriate;
- status/progress context managers for long synchronous operations.

`cli.py` should select this presenter only when using the real stdout terminal. Tests and non-terminal callers that inject `output` should continue to use plain strings unless a test explicitly constructs the presenter.

Alternative considered: call Rich directly from `interactive.py`, `status.py`, and `cli.py`. That would be faster initially but spreads terminal concerns through multiple modules and makes later plain-output compatibility harder to reason about.

### Keep argparse and numbered input

Do not replace the parser or input model. `argparse` already defines the public grouped command surface, and `_choice()` already supports stable numbered choices. Rich should improve the display around those choices, not introduce a second interaction model.

Alternative considered: adopt Textual, Prompt Toolkit, Questionary, Click, or Typer. Textual and Prompt Toolkit are heavier interactive frameworks and conflict with the SSH-safe numbered-menu requirement. Click and Typer mostly replace parsing; they do not solve structured log leakage or operation result presentation.

### Treat Rich as terminal-only presentation, not machine output

Use Rich `Console` terminal detection and no-color behavior for real terminal stdout. Piped or injected output should remain plain and parseable. `evdb status --json` must continue to emit exactly one JSON document on stdout regardless of Rich availability.

Rich renderables should not become public API values. Workflow functions should continue returning dictionaries or strings; the CLI/presenter boundary decides how to display them.

Alternative considered: make all human output Rich-rendered and rely on Rich to strip styling when piped. That is acceptable for direct terminal commands, but keeping existing plain renderers for non-terminal paths reduces compatibility risk for scripts that parse current non-JSON text.

### Centralize structured log terminal policy

Update `log.write()` or its call path so routine JSON event records are not printed to an interactive terminal stderr. The simplest policy is:

- if stderr is not a TTY, emit the existing JSONL record unchanged;
- if stderr is a TTY, suppress routine structured event output;
- preserve redaction before any emitted record;
- keep tests able to capture records by redirecting or monkeypatching stderr to a non-TTY stream.

If a future implementation needs terminal-visible diagnostics, it should render concise human messages through the CLI error path rather than exposing raw structured log JSON.

Alternative considered: move log records to stdout in JSON mode or add a global `--log-json` flag first. stdout would break machine output. A new flag can be added later if operators need forced terminal JSON logs, but the immediate issue is the unconditional default.

### Summarize operation results for humans

Keep dictionary results as data at the workflow boundary, but render concise terminal summaries for humans. Examples:

- database add/configure: `<project>/<role> is healthy`, with changed settings and safety snapshot when present;
- no-op settings/add: `<project>/<role> unchanged`;
- backup create/test/restore/update: show the selected identity, result state, and important snapshot or backup identifiers;
- failures: continue using existing exception paths and redacted error messages.

Non-terminal mutation output can continue using JSON-formatted dictionaries unless a spec later creates a formal machine result schema for these commands.

Alternative considered: change all mutation command output to plain text. That improves human readability but risks breaking scripts that may rely on current non-terminal JSON-shaped results.

### Use progress indicators only around blocking work

Use Rich status indicators for operations where the terminal otherwise appears idle: status refreshes, database add/configure/start/stop/restart, backup create/test, restore, host setup/update/uninstall, and bounded log/history retrieval when slow. Do not keep a spinner active while asking for confirmation or reading input.

Alternative considered: add detailed progress events from each workflow module. That would require invasive domain changes and risks exposing internal steps before the presentation problem is solved.

### Validate binary packaging explicitly

Adding Rich increases runtime dependency surface. The implementation should update the lock file with `uv`, run unit/config checks, and build the PyInstaller one-file executable. A smoke test should verify `evdb --version`, `evdb status --json` against a fixture or safe config path if practical, and a terminal-rendered command path where possible.

Alternative considered: avoid the dependency and hand-format better tables. That can suppress JSON leakage and improve summaries, but Rich provides adaptive tables, color/no-color handling, prompt styling, and status indicators with less custom terminal code.

## Risks / Trade-offs

- [Rich dependency increases binary size and startup work] -> Measure the one-file binary after the change and keep Rich imports localized so non-terminal paths remain simple.
- [Terminal suppression could hide useful diagnostics during debugging] -> Preserve JSONL when stderr is redirected and keep normal command errors concise on stderr.
- [TTY detection differs under sudo, tests, and CI] -> Add focused tests for real/injected output, TTY stderr, non-TTY stderr, and `status --json`.
- [Styled output could corrupt machine output] -> Route JSON mode around the presenter and assert `status --json` emits one parseable stdout document.
- [Progress indicators could interfere with prompts] -> Limit status contexts to blocking operations and exit the context before confirmation or input.
- [Rich tables could wrap poorly on narrow terminals] -> Use bounded columns, ellipsis overflow, and tests with constrained console width.

## Migration Plan

1. Add Rich to runtime dependencies and refresh `uv.lock`.
2. Add the presenter module and focused rendering tests using captured Rich console output.
3. Add terminal-aware log emission tests before changing `log.write()` behavior.
4. Wire terminal mode through `cli.py` and `interactive.py` while preserving injected-output and non-terminal paths.
5. Replace terminal mutation result rendering with concise summaries while retaining existing non-terminal JSON-formatted results.
6. Update status and menu rendering for terminal mode.
7. Run lint, unit/config tests, and PyInstaller binary smoke validation.

Rollback is a normal code revert before release. After release, operators can update back to the previous exact evdb version through the existing host update path if the CLI presentation change causes operational problems.

## Open Questions

- Should there be an explicit environment variable such as `EVDB_LOG_JSON=1` to force terminal JSON logs, or is redirected stderr sufficient for the first implementation?
- Should non-terminal mutation result dictionaries remain indefinitely, or should a future change formalize `--json` for mutation commands and make plain text the default everywhere else?

## Context

The current CLI deliberately uses append-only text, numbered choices, compact aligned rows, and injected
input/output callables. `ui._screen()` emits plain headings and content, prompt helpers pass plain strings
to `input()` or `getpass()`, and `status.render()` returns one aligned plain string. This keeps SSH,
transcripts, tests, pipes, and JSON output predictable, but leaves important states and interaction cues
visually indistinguishable.

An older Rich presenter proved the dependency could support terminal detection, no-color output, captured
tests, and the one-file build, but it also owned wide Rich tables and spinners. Those presentation choices
were removed during the v1 simplification. This change needs Rich's low-level rendering behavior without
restoring that broader interface.

## Goals / Non-Goals

**Goals:**

- Make headings, questions, defaults, healthy states, warnings, and failures faster to distinguish in a
  real terminal.
- Apply the same semantic grammar to guided screens and direct human commands.
- Keep every state understandable from its wording when style is unavailable.
- Preserve exact plain text for injected output, redirected streams, and JSON mode.
- Keep arbitrary database names, paths, URLs, subprocess details, and secrets literal and unparsed.

**Non-Goals:**

- No Rich tables, panels, trees, progress bars, spinners, live displays, Markdown, or automatic syntax
  highlighting.
- No cursor-addressed navigation, screen clearing, arrow-key input, or change to numbered choices.
- No new status vocabulary, command behavior, prompt decisions, or JSON fields.
- No theme configuration or user-selectable palette.

## Decisions

### Use Rich as a narrow terminal renderer

Add Rich as a runtime dependency and keep its use at the CLI/UI presentation boundary. Construct consoles
with markup and automatic highlighting disabled, and build styled output with `Text` spans so displayed
values are always literal. Select Rich rendering only for real terminal streams; continue calling the
existing plain renderers and injected output functions everywhere else. Error styling follows stderr's
terminal capability independently from stdout.

This keeps terminal mechanics such as style resets, terminal capability detection, visible width, and
captured rendering in a maintained library. A hand-written ANSI helper was rejected because it would
reimplement those behaviors and become harder to extend safely. Routing all output through Rich was also
rejected because redirected and injected output must retain the current byte-stable plain contract.

### Keep the style grammar semantic and restrained

Presentation code assigns styles from known state values rather than searching rendered strings:

- headings use bold;
- question labels use a restrained bold accent while typed answers remain terminal-default text;
- the effective default token in `[Y/n]`, `[y/N]`, and other bracketed defaults uses bold without a
  success color;
- healthy, ready, current, and successful completion states use green;
- needs-attention, stale, unknown, and stopped states use yellow;
- unhealthy, failed, error, and required-missing states use red;
- expected disabled, absent, and ordinary informational values remain uncolored.

Only the state value or error heading receives color; identifiers and explanatory details remain in the
terminal's normal foreground. Wording remains authoritative, so the output has the same meaning under
`NO_COLOR`, `TERM=dumb`, monochrome terminals, and copied transcripts.

Coloring every affirmative `yes` or every line in a successful section was rejected because it would
make color decorative and reduce the contrast of operationally important states. Coloring defaults green
was rejected because it could bias confirmation choices.

### Preserve append-only rendering and existing callback boundaries

The terminal presenter writes ordinary lines and prompts without clearing or rewriting previous output.
Guided production prompts use the presenter while injected `input_fn`, `output`, and `password_fn`
callbacks keep receiving the existing plain prompt and output strings. This preserves focused tests and
keeps styling out of domain modules.

Aligned overview and status rows calculate truncation and padding from plain values before styles are
applied. Styled spans are then attached to the known heading and state columns; control sequences never
participate in width calculations. Human errors are cleaned and redacted before presentation as they are
today.

### Treat no-color and packaging as release contracts

Rich's terminal detection remains the default, with explicit no-color selection when `NO_COLOR` is set or
`TERM=dumb`. JSON mode bypasses the presenter before any Rich rendering. Runtime dependency changes use
`uv`, and the existing PyInstaller one-file checks cover import collection and startup. A binary smoke
check exercises version output plus styled terminal and plain redirected paths.

## Risks / Trade-offs

- [Rich increases runtime and frozen-binary size] -> Keep imports and renderables limited to the
  presentation module and verify the existing one-file release path.
- [Style leaks into automation] -> Select presenters per actual stream and retain focused assertions that
  injected, redirected, and JSON output contain no control sequences.
- [ANSI sequences disturb aligned rows] -> Perform fitting and padding on plain text before attaching Rich
  styles, and test constrained terminal widths.
- [Color meanings become noisy or ambiguous] -> Color only known operational states and headings, keep
  wording intact, and leave neutral states uncolored.
- [Arbitrary values are interpreted as Rich markup] -> Disable markup and highlighting and construct
  renderables from literal `Text` values.

## Migration Plan

Add the dependency and terminal presenter, wire terminal selection through the CLI, then apply semantic
styles to prompts and status renderers with focused tests. Existing scripts require no migration because
non-terminal output is unchanged. Reverting the presenter wiring and dependency restores the prior plain
terminal display without changing persisted data or command contracts.

## Open Questions

None.

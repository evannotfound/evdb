## 1. Terminal Renderer

- [x] 1.1 Add Rich with `uv` and implement the narrow literal-text terminal presenter, including focused tests for TTY selection, independent stdout/stderr behavior, `NO_COLOR`, `TERM=dumb`, and arbitrary values that resemble markup.

## 2. Semantic Presentation

- [x] 2.1 Wire the presenter through direct and guided CLI output and production prompts so headings, questions, effective defaults, results, and error headings are emphasized while injected callbacks, redirected streams, and JSON mode retain their exact plain contracts.
- [x] 2.2 Apply structured success, warning, and failure styles to the guided overview, direct status, selected database, and host views, with focused tests proving mixed states remain explicitly labeled and aligned at supported terminal widths.

## 3. Release Validation

- [x] 3.1 Update the one-file release smoke coverage for bundled Rich and verify styled terminal output alongside plain redirected and JSON output using the affected unit, lint, and packaging checks.

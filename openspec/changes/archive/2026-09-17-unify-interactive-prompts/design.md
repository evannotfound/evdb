## Context

`ui.Terminal` uses Rich for presentation but ordinary console input and getpass for questions. Shared helpers print validation errors then repeat prompts. The home menu instead runs a custom cbreak digit reader and Rich Live. This creates inconsistent behavior and lacks normal text editing. CLI input/output injection is used extensively by tests.

## Goals / Non-Goals

**Goals:** editable input and in-place validation for terminal questions throughout guided operations; masked secrets; numbered choices; live home updates; completed steps in scrollback.

**Non-Goals:** full-screen navigation, arrow-key menus, completion catalogs, persistent input history, changes to domain validators or mutation/confirmation semantics.

## Decisions

- Add prompt_toolkit as a packaged runtime dependency. `Terminal` owns a single question method used by its visible and secret input methods. Existing UI helpers provide their normalization and validation to this method when using the real terminal input boundary. Injected and dumb-terminal paths keep existing line-oriented behavior.
- Validate on Enter. Preserve the input buffer after validation failure and show a concise error at the prompt. Existing normalization/default/optional rules remain authoritative. Confirmation typos receive a local error instead of silently becoming No; blank and explicit No keep existing defaults and cancellation semantics. Deletion identity/phrase mismatches still cancel.
- Use Rich to render question styling and the home view to ANSI formatted text consumed by prompt_toolkit. The toolkit owns redraws during input, including periodic refresh of the home message. Do not run Rich Live concurrently with toolkit input. Replace the custom termios loop rather than adapting it into a text editor.
- Use a fresh prompt session per question, with no persistent history. Password fields and confirmation are masked. A mismatched password confirmation stays editable; no plaintext secret is rendered.
- Test toolkit interaction using its pipe-input and dummy/recording output facilities; preserve the existing injected guided-flow suite. Smoke-test the standalone executable since a new runtime dependency must be packaged.

## Risks / Trade-offs

- Competing terminal renderers → toolkit exclusively controls the active prompt region; Rich capture formats content without writing directly.
- Secret exposure through input history or error text → fresh sessions, masked input, fixed secret validation messages, and focused transcript checks.
- Terminal differences → preserve plain behavior for dumb terminals and injected input, and respect NO_COLOR.

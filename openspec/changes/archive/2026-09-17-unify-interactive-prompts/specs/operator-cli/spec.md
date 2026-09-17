## MODIFIED Requirements

### Requirement: Terminal presentation
Interactive output SHALL use an SSH-safe terminal presenter with readable semantic emphasis and compact
layout. In a color-capable terminal, headings and questions SHALL be emphasized, effective prompt defaults
SHALL be visually distinct from alternatives, and healthy, warning, pending, and failure states SHALL use
distinct styles. Color SHALL supplement explicit wording rather than replace any state label. The
presenter SHALL separate screens, results, errors, and prompts with blank lines and preserve numbered input.
It SHALL redraw the active question and guided overview as needed while retaining completed steps in
terminal scrollback. Plain or injected output SHALL contain no terminal control sequences or Rich markup.

#### Scenario: Guided flow advances between screens
- **WHEN** the operator selects a database and then a backup action
- **THEN** headings, content, result, and next prompt are visually separated without clearing prior terminal history

#### Scenario: Operator scans mixed database health
- **WHEN** a terminal overview contains checking, healthy, stopped, stale, unknown, or failed operational states
- **THEN** the known state values use the corresponding pending, success, warning, or failure style while retaining their explicit text labels

#### Scenario: Prompt has a default answer
- **WHEN** a guided terminal question shows `[Y/n]`, `[y/N]`, or another bracketed default
- **THEN** the question and effective default are visually distinguishable while the entered answer remains ordinary terminal text

#### Scenario: Output is captured by automation
- **WHEN** stdout is not a terminal or output is injected by a test
- **THEN** evdb emits the existing stable plain text without style control sequences

#### Scenario: Terminal cannot display color
- **WHEN** terminal settings disable color
- **THEN** labels and wording preserve every state distinction and interaction default without requiring color

#### Scenario: Guided status loads in the background
- **WHEN** the operator starts guided `evdb` while runtime or repository assessment is incomplete
- **THEN** evdb immediately shows stable numbered choices and accepts input while pending cells update in the active overview

#### Scenario: Operator types while status updates
- **WHEN** background status redraws the root while a numeric choice is partially entered
- **THEN** the prompt and complete entered buffer remain visible until Enter submits the choice

#### Scenario: Other interactive work takes noticeable time
- **WHEN** Details, backup, log, or another synchronous view waits for external work
- **THEN** one transient terminal status line identifies the current phase and is removed before the completed screen or error is appended

## ADDED Requirements

### Requirement: Editable guided input
Interactive terminal questions SHALL use prompt_toolkit for visible text, numbered choices, confirmations,
and masked secrets. Validation SHALL occur on Enter, show errors at the active question, and retain invalid
input for editing. Existing defaults, optional values, domain validators, cancellation, and deletion
identity/phrase checks SHALL remain effective. Completed answers SHALL remain in scrollback; secret values
SHALL never appear as plaintext in that transcript or persist in input history. Dumb terminals and injected
input SHALL retain line-oriented behavior.

#### Scenario: Operator corrects a text field
- **WHEN** a guided field rejects an answer
- **THEN** the answer remains editable with its error at the same prompt until corrected or cancelled

#### Scenario: Operator corrects a menu selection
- **WHEN** a root or submenu selection is invalid
- **THEN** the prompt shows the allowed choices and lets the operator edit the submitted value without appending another question

#### Scenario: Operator corrects a confirmation
- **WHEN** a terminal yes/no answer is neither a recognized yes nor no nor blank
- **THEN** the question remains active with an error; Enter on blank still uses the stated default

#### Scenario: Operator confirms a secret
- **WHEN** a secret confirmation differs from the first secret
- **THEN** the masked confirmation stays editable with a mismatch error and neither secret is printed

#### Scenario: Operator cancels input
- **WHEN** the operator presses Ctrl-C or submits EOF at an empty prompt
- **THEN** the existing caller cancellation path runs without applying an unconfirmed operation

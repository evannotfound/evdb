## MODIFIED Requirements

### Requirement: Terminal presentation
Interactive output SHALL use an SSH-safe terminal presenter with readable semantic emphasis and compact
layout. In a color-capable terminal, headings and questions SHALL be emphasized, effective prompt defaults
SHALL be visually distinct from alternatives, and healthy, warning, and failure states SHALL use distinct
styles. Color SHALL supplement explicit wording rather than replace any state label. The presenter SHALL
separate screens, results, errors, and prompts with blank lines, preserve numbered input, and avoid
cursor-addressed navigation. Plain or injected output SHALL contain no terminal control sequences or Rich
markup.

#### Scenario: Guided flow advances between screens
- **WHEN** the operator selects a database and then a backup action
- **THEN** headings, content, result, and next prompt are visually separated without clearing prior terminal history

#### Scenario: Operator scans mixed database health
- **WHEN** a terminal overview contains healthy, stopped, stale, unknown, or failed operational states
- **THEN** the known state values use the corresponding success, warning, or failure style while retaining their explicit text labels

#### Scenario: Prompt has a default answer
- **WHEN** a guided terminal question shows `[Y/n]`, `[y/N]`, or another bracketed default
- **THEN** the question and effective default are visually distinguishable while the entered answer remains ordinary terminal text

#### Scenario: Output is captured by automation
- **WHEN** stdout is not a terminal or output is injected by a test
- **THEN** evdb emits the existing stable plain text without style control sequences

#### Scenario: Terminal cannot display color
- **WHEN** terminal settings disable color
- **THEN** labels and wording preserve every state distinction and interaction default without requiring color

## Why

The home menu redraws input errors in place, while other guided prompts print errors and ask again. Operators need consistent editable input throughout setup and everyday database operations.

## What Changes

- Use prompt_toolkit for terminal text, numbered choices, confirmations, and masked secrets while retaining Rich presentation.
- Keep invalid answers editable with a validation message at the active prompt; preserve completed steps in scrollback.
- Replace the home menu's custom digit keyboard loop while preserving background status updates.
- Preserve plain and injected input, direct commands, cancellation, and deliberate deletion confirmations.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `operator-cli`: Consistent editable terminal prompts with local validation and live home status.

## Impact

The existing UI input helpers and terminal presenter, runtime dependency and lock file, CLI guidance, focused input tests, and executable packaging checks. Domain operations and validation rules stay with their existing owners.

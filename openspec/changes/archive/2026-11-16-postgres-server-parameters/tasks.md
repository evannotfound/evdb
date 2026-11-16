## 1. Persist and validate Postgres parameters

- [x] 1.1 Add the Postgres parameter mapping to the model, strict YAML loading/dumping, validation, defaults, and role replacement paths while preserving existing source round trips.

## 2. Render and configure parameters

- [x] 2.1 Render validated parameters as Postgres `-c name=value` arguments without replacing evdb's managed entrypoint or service contract.
- [x] 2.2 Add repeatable `database configure --parameter NAME=VALUE` parsing, replacement, and `--reset parameters` handling through the existing convergence and health flow.

## 3. Verify the contract

- [x] 3.1 Add focused unit coverage for config round trips, invalid and reserved parameters, generated Compose, direct CLI parsing, reset behavior, and the GlitchTip `max_locks_per_transaction=512` case.
- [x] 3.2 Run the affected unit tests and repository checks, then update the relevant user documentation with the Postgres parameter example.

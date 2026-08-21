## MODIFIED Requirements

### Requirement: Detailed database view
`evdb database info PROJECT/ROLE` SHALL display configured role and concrete engine, live image version
and health, data and Compose paths, latest backup summary, native connection fields, and HTTP fields when
enabled. Details SHALL show the data directory's allocated bytes and backing filesystem capacity, mount,
source, and type. A running PostgreSQL role SHALL show total logical database bytes and connectable
database count; a running Redis or Dragonfly role SHALL show dataset memory bytes and total key count.
The command SHALL deliberately retrieve credentials from private host files and print them only to the
terminal.

#### Scenario: Running database is shown
- **WHEN** the selected role is running
- **THEN** info distinguishes allocated directory storage from engine-native data size and count

#### Scenario: Stopped database is shown
- **WHEN** the selected role is configured but stopped
- **THEN** info still shows settings, storage and connection details, clearly identifies stopped state, and marks live data unavailable

## ADDED Requirements

### Requirement: Native port initialization arguments

`evdb init` SHALL accept repeatable integer `--postgres-port PORT` and `--kv-port PORT` arguments. Repetitions SHALL define the complete replacement list for that protocol in command-line order. Omission SHALL use standard defaults for a new host and preserve configured values for an existing host. Direct invocation SHALL not require another confirmation. A change to an existing published binding set SHALL report that native connections will briefly drop before applying it.

Guided first initialization SHALL accept comma-separated ordered port lists, offer standard defaults, and include the selected lists in its existing final Apply review. Neither interface SHALL prompt for native ports during a non-interactive refresh without port arguments.

#### Scenario: Direct initialization selects alternate ports
- **WHEN** the operator initializes a host with `--postgres-port 15432 --kv-port 16379` and all other required setup values
- **THEN** setup selects only those native host ports

#### Scenario: Direct refresh changes only Postgres ports
- **WHEN** an initialized host runs `evdb init --postgres-port 15432 --postgres-port 5432`
- **THEN** Postgres uses exactly `[15432, 5432]`, KV retains its existing list, and the command reports the native connection interruption before applying changed bindings

#### Scenario: Guided initialization reviews alternate ports
- **WHEN** the operator enters `15432` for Postgres and `16379` for KV during guided setup
- **THEN** the final review displays those lists and applying uses them

### Requirement: Configured native endpoint reporting

Connection details SHALL use the preferred external port in each native URL. Host text and JSON status SHALL assess all and only configured native host ports, retaining string port keys in the existing listener map. Failed listener assessment SHALL produce null values for the configured keys. Host routing health SHALL require both live listeners and matching published bindings on the running owned evdb router; unrelated listeners SHALL NOT substitute for missing evdb bindings. Guided views SHALL refresh their loaded configuration after a successful port edit.

#### Scenario: Connection details follow the preferred port
- **WHEN** Postgres ports are `[15432, 5432]` and KV ports are `[16379, 6379]`
- **THEN** native URLs contain ports 15432 and 16379 respectively, while existing HTTP URL, loopback, and token details remain unchanged

#### Scenario: Status checks an alternate-port host
- **WHEN** evdb is configured only for 15432 and 16379
- **THEN** listener status uses keys `15432` and `16379` and does not require evdb to publish 5432 or 6379

#### Scenario: Another router masks an unapplied binding
- **WHEN** evdb is configured to publish 5432 but only an unrelated router publishes it
- **THEN** host routing status is unhealthy rather than treating that listener as a successful evdb binding

## MODIFIED Requirements

### Requirement: Guided host actions

Selecting Host from the guided root SHALL show mount-aware state and database-root storage, routing infrastructure, ordered native port lists with their preferred ports, repository, timer, and current host errors followed by Native ports, Restart traffic, and Back actions. Restart traffic SHALL warn that active database connections may briefly drop and require confirmation before changing Traefik runtime state.

Native ports SHALL let the operator edit both ordered lists using comma-separated values, with blank input retaining the displayed list. It SHALL show the proposed lists and require one final Save confirmation, warning that changing bindings interrupts native connections. Cancel and unchanged saves SHALL leave source and runtime untouched. Confirmed changes SHALL use the same host initialization/convergence behavior as direct port flags and refresh subsequent Host and Connection views.

#### Scenario: Operator reviews custom storage
- **WHEN** the host configures several database roots
- **THEN** the Host view shows every exact path, backing mount and source, capacity, and assigned roles

#### Scenario: Operator declines traffic restart
- **WHEN** the operator selects Restart traffic and declines confirmation
- **THEN** Traefik and every database remain unchanged

#### Scenario: Operator confirms traffic restart
- **WHEN** the operator confirms Restart traffic
- **THEN** the Host view reports completion only after the dedicated Traefik container becomes healthy

#### Scenario: Operator saves dual-port routing
- **WHEN** the operator changes both native lists to include temporary and standard ports and confirms Save
- **THEN** evdb applies both lists together and subsequent Host and Connection views reflect the new configuration

#### Scenario: Operator cancels a port edit
- **WHEN** the operator edits native ports but declines Save
- **THEN** source, generated files, and running services remain unchanged

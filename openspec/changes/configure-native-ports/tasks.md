## 1. Native port configuration and routing

- [x] 1.1 Add ordered Postgres/KV host port lists to the routing model, configuration loading/serialization, validation, and preferred external port derivation. Cover omitted-field defaults, order preservation, and invalid lists in existing configuration tests.
- [x] 1.2 Update host initialization and Traefik rendering to apply replacement lists under the host lock, publish alternate or multiple ports per protocol, and check newly requested ports against actual owned bindings before mutation. Cover coexistence, adding an occupied port while evdb is running, stable rendering on reorder, and preservation of database/HTTP services in focused host tests.

## 2. Operator controls and migration guidance

- [x] 2.1 Add repeatable initialization flags, guided setup inputs, and the Host Native ports editor using the shared initialization flow. Update connection URLs and host status to reflect preferred ports and all requested bindings, including configuration reload after guided edits. Cover flag replacement/omission, save/cancel, endpoint reporting, and unrelated listeners in existing CLI/UI/status and database tests; verify internal engine and HTTP endpoints remain unchanged.
- [x] 2.2 Document the alternate-port → dual-port → standard-port sequence in README, including complete-list flag semantics, router reconnections, separate data cutovers, evdb native hostnames, and the existing Nginx Proxy Manager → localhost Redis HTTP gateway arrangement with its separate token migration step.

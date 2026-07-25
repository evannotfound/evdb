## ADDED Requirements

### Requirement: Managed instance list
The source config SHALL list the 25 instances managed under `/home/ubuntu/databases`: 14 Postgres, 7 Dragonfly, and 4 Redis instances. Databases outside those managed directories SHALL be treated as out of scope.

#### Scenario: Initial config is complete
- **WHEN** the checked-in config is validated
- **THEN** it contains all 25 managed instances with the expected engine counts

#### Scenario: Unmanaged host database is found
- **WHEN** a database container outside the managed directories is inspected
- **THEN** it is reported as outside the managed list and is not added automatically

### Requirement: Instance settings
Each instance SHALL record its id, engine, container name, Compose project, current image, pinned target image, data path, ports, domains, resource settings, environment, durability, backup rule, required secret references, and any serverless HTTP settings.

#### Scenario: Required setting is missing
- **WHEN** an instance omits a required setting
- **THEN** validation fails and names the instance and missing setting

### Requirement: Current and target settings
The config SHALL keep what is running now separate from what generated Compose will use. During this change, each target engine and data path SHALL match the current engine and data path.

#### Scenario: Redis instance has a Dragonfly Compose template
- **WHEN** a current Redis instance points at the shared Dragonfly template
- **THEN** config records Redis as the current and target engine and records the Compose difference without changing production

#### Scenario: Data path changes
- **WHEN** a target data path differs from the current data path
- **THEN** validation fails because data moves are outside this change

### Requirement: Pinned target images
Every target image SHALL use a fixed version and digest. Current image records MAY preserve a mutable tag when that is what the running container reports, but they SHALL also record the running image id.

#### Scenario: Target image uses latest
- **WHEN** a target image uses `latest` or has no digest
- **THEN** validation fails

### Requirement: Secret references
Source config SHALL contain only `op://` references for database passwords, Redis passwords, Restic passwords, rclone bootstrap data, and other secrets. Secret values SHALL NOT be accepted in source config.

#### Scenario: Secret value is committed
- **WHEN** a secret field does not start with `op://`
- **THEN** validation fails without printing the value

### Requirement: Unique names and routes
Validation SHALL reject duplicate instance ids and Compose projects within the same `postgres` or `kv` group. Container names, published host ports, and database domains SHALL remain unique across the host.

#### Scenario: REST port is reused
- **WHEN** two managed instances publish the same host address, port, and protocol
- **THEN** validation fails and names both instances

#### Scenario: Product has Postgres and KV
- **WHEN** one product id exists once in the `postgres` group and once in the `kv` group
- **THEN** validation accepts both and commands distinguish them by group

#### Scenario: Shared database listener is valid
- **WHEN** Traefik owns host ports 5432 and 6379 and individual database services publish neither port
- **THEN** validation accepts the shared listeners and checks instance routes by unique domain

### Requirement: HTTP settings
Each KV instance SHALL record whether its serverless HTTP sidecar is enabled, its unique loopback port, intended external domain, pinned image, token reference, and connection limit. The initial `montreal-01` config SHALL enable the sidecar for all 11 KV instances.

#### Scenario: External route data is recorded
- **WHEN** a KV instance defines its intended HTTP domain and loopback port
- **THEN** validation checks the contract without inspecting or changing the external proxy

#### Scenario: Initial HTTP coverage is checked
- **WHEN** the initial `montreal-01` config is validated
- **THEN** all 11 KV instances have enabled HTTP settings with unique loopback ports

### Requirement: Standard HTTP domains
Target HTTP domains SHALL use `<instance>.kv-montreal-01.storage.evanovation.com`. Current legacy domains MAY be recorded as current facts, but the target SHALL NOT use `kv-na01.storage.evanovation.com`.

#### Scenario: Legacy HTTP domain is recorded
- **WHEN** a current route uses the old `kv-na01.storage.evanovation.com` suffix
- **THEN** config records the current domain and the new target domain without changing production in this change

### Requirement: Durable instances have backups
Every production instance SHALL be treated as durable unless it is explicitly marked as a cache. Every durable instance SHALL have an enabled backup rule.

#### Scenario: Durable production instance has no backup
- **WHEN** a production instance is durable and its backup rule is missing or disabled
- **THEN** validation fails

### Requirement: Runtime JSON
Ansible SHALL render one JSON file per instance for the production Python tool. The production tool SHALL read JSON without requiring a YAML package.

#### Scenario: Source config is rendered
- **WHEN** Ansible renders config for a host
- **THEN** the result is valid JSON that passes the same instance checks as the source config

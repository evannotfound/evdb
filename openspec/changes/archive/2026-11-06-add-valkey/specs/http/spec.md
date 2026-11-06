## MODIFIED Requirements

### Requirement: Per-database HTTP choice
KV project roles SHALL have authenticated HTTP access enabled by default and SHALL allow disabling it during creation or through guided or explicit settings. The role's concrete Valkey, Redis, or Dragonfly engine SHALL not change the public KV identity.

#### Scenario: Default KV is created
- **WHEN** the operator adds a KV role with default settings
- **THEN** generated KV Compose includes its HTTP API sidecar

#### Scenario: HTTP is disabled
- **WHEN** the operator confirms `http: false`
- **THEN** generated Compose removes the sidecar while native KV access and data remain unchanged

#### Scenario: HTTP is enabled after disabled creation
- **WHEN** a new KV role is created with HTTP disabled and HTTP is enabled later through configure
- **THEN** its sidecar uses a privately persisted token and becomes available without recreating the database identity

### Requirement: Local HTTP routing test
Disposable integration tests SHALL run Valkey, Redis, and Dragonfly HTTP sidecars and prove authentication, backend isolation, stable loopback-only bindings, and generated YAML without calling an external proxy or production host.

#### Scenario: Sidecars use separate ports
- **WHEN** tests write distinct values through separate loopback sidecars
- **THEN** each value appears only in its matching KV engine

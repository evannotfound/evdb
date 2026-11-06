## ADDED Requirements

### Requirement: KV creation choices
Direct creation SHALL offer Valkey, Redis, and Dragonfly engines with Valkey as default, plus mode, supported memory, and HTTP options. Guided creation SHALL list Valkey first, offer optional KV configuration, and summarize engine, mode, memory when supported, persistence behavior, and HTTP before confirmation. Existing Valkey settings SHALL expose memory, image, and HTTP controls but not mode or Dragonfly threads.

#### Scenario: Guided Valkey creation is customized
- **WHEN** the operator chooses cache mode, a valid memory limit, and disabled HTTP
- **THEN** those choices appear in the creation summary and are passed into the first service startup after confirmation

#### Scenario: Creation settings are invalid
- **WHEN** an operator supplies an unsupported or invalid KV setting
- **THEN** guided creation requests a corrected value and direct creation fails before writing source or starting services

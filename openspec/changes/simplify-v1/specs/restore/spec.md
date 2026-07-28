## REMOVED Requirements

### Requirement: One-command live restore
**Reason**: Live data replacement is outside the v1 container-and-backup boundary.

**Migration**: Use engine-native tools manually until a focused restore capability is proposed.

### Requirement: Safety backup gates replacement
**Reason**: Safety-backup gating exists only for the removed live restore transaction.

**Migration**: Create an explicit manual backup before manual recovery when needed.

### Requirement: Checked restore input
**Reason**: evdb no longer selects inputs for an automated restore workflow.

**Migration**: Use `backup list` and inspect the retained manifest during manual recovery.

### Requirement: Postgres restore verification
**Reason**: Isolated Postgres restoration is removed from v1.

**Migration**: Use matching Postgres tools manually.

### Requirement: Redis restore verification
**Reason**: Isolated Redis restoration is removed from v1.

**Migration**: Use matching Redis tools manually.

### Requirement: Dragonfly restore verification
**Reason**: Isolated Dragonfly restoration is removed from v1.

**Migration**: Use matching Dragonfly tools manually.

### Requirement: Isolated candidate verification
**Reason**: Persistent restore candidates and verification containers retain the removed recovery
framework.

**Migration**: Engine-native backup checks remain part of backup creation.

### Requirement: Restore verification scheduling
**Reason**: Scheduled restore-based backup testing is removed.

**Migration**: The one v1 timer creates backups only.

### Requirement: Atomic data-directory promotion
**Reason**: evdb does not promote restored data in v1.

**Migration**: Data replacement is a manual operator procedure.

### Requirement: Post-promotion health gate
**Reason**: There is no automated promotion to health-gate.

**Migration**: `database start` still waits for native health after manual correction.

### Requirement: Automatic failed-promotion recovery
**Reason**: Automatic restore rollback is outside v1 and is a major source of orchestration complexity.

**Migration**: Operators retain responsibility for manual recovery paths.

### Requirement: Retained recovery data
**Reason**: evdb no longer creates restore transaction directories or prior-data copies.

**Migration**: Manual recovery owns any temporary or prior data directories it creates.

### Requirement: Engine compatibility
**Reason**: Backup-test and restore compatibility selection are removed.

**Migration**: Backup manifests retain concrete engine and image metadata for manual use.

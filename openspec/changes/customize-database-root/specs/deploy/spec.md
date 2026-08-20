## MODIFIED Requirements

### Requirement: Production paths
Canonical evdb source SHALL live under `/etc/evdb`; generated services, Traefik assets, local backups,
and locks SHALL live under `/var/lib/evdb`; database data SHALL live under the explicitly configured
`host.data_root`; and the verified tool SHALL be the regular executable `/usr/local/bin/evdb`. No copied
rclone file, deployment machine state, activity record, restore staging, or transaction tree SHALL be
created.

#### Scenario: Tool version changes
- **WHEN** the verified installer atomically replaces `/usr/local/bin/evdb`
- **THEN** every database continues using stable source, generated, credential, backup, and configured data paths

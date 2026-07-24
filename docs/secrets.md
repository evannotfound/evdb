# Secrets and rclone

Git stores `op://` references only. Database passwords, HTTP tokens, Restic passwords, and
rclone bootstrap config must not be replaced with resolved values in source YAML, Compose,
tests, logs, or release directories.

Ansible runs `op read` on the trusted controller with `command.argv`, `delegate_to:
localhost`, and `no_log`. The value is immediately written to the managed host as the
service account with mode `0600`. Check mode and the disposable inventory skip resolution,
so non-secret templates remain checkable without 1Password access or placeholder secrets.

Postgres receives a mounted password file through `POSTGRES_PASSWORD_FILE`. PgBouncer uses
a private users file. Redis starts from a private config containing `requirepass`.
Dragonfly starts with only `--flagfile` in Compose and reads its password from that private
file. HTTP sidecars read `SRH_TOKEN` and `SRH_CONNECTION_STRING` from a private environment
file.

The live rclone config is mutable OAuth state, not a normal rendered secret. Ansible checks
`/var/lib/evanovation-db/rclone/rclone.conf`, seeds it from 1Password only when absent, sets
mode `0600`, and uses `force: false`. Later deploys leave refreshed tokens untouched.

If rclone loses authorization, stop jobs using that repository, run the documented rclone
reconnect flow interactively as the service account, test read access, then resume jobs.
After a successful reconnect, refresh the separately controlled bootstrap escrow before a
host rebuild. Never copy the live file into Git, Ansible output, a ticket, or a release.

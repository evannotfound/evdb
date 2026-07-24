# evanovation-db

`evanovation-db` is the source for Evanovation's managed Postgres, Redis, and Dragonfly
setup. It builds backup, restore, config, Compose, Ansible, and systemd files.

This change builds and tests the new system locally. It does not deploy to `montreal-01`,
replace cron, restart databases, change images, or write to the production Restic repositories.

## Development

```sh
uv sync
make check
```

Main commands:

```text
evanovation-db validate
evanovation-db backup <postgres|kv> <instance>
evanovation-db restore-check <postgres|kv> <instance>
evanovation-db status
```

See `docs/` for setup and operating notes. Production migration belongs to the later
`move-prod` change.

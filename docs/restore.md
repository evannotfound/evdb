# Data restore and promotion

## Create a candidate

```sh
evdb backups app-prod-01
evdb restore app-prod-01 --snapshot latest
evdb restore app-prod-01 --snapshot SNAPSHOT_ID
```

`restore` selects the latest or exact Restic snapshot for the typed database, verifies its
manifest and identity, and creates a unique persistent candidate beside the live data directory.
It uses the active release's locked compatible image and an isolated container with no published
ports or live data mounts. Only a candidate that passes engine and content checks is promotable.

The command returns an immutable `restore-...` ID. Candidate metadata records host and database
identity, snapshot ID and time, manifest hash, source engine and major, locked image, active
release, candidate path, and verification result. Creating a candidate does not stop or modify
the live database.

## Promote with an outage

```sh
evdb promote app-prod-01 RESTORE_ID
```

Promotion validates identity, active release, locked image compatibility, candidate completeness,
freshness, and same-filesystem placement before stopping the live project. It displays the exact
snapshot, live and candidate paths, and expected outage, then requires confirmation unless
`--yes` is supplied.

During the outage, promotion stops the active project, atomically renames the live directory to a
timestamped retained path, atomically renames the candidate into the configured live path, starts
the same active release, and requires container plus engine-native health. Same-filesystem sibling
directories make both swaps atomic. This is not a zero-downtime operation.

Successful promotion keeps the prior data directory and reports its path. Nothing purges retained
prior data, failed candidates, restore records, backups, or 1Password items. Cleanup is a separate
manual decision outside this change.

## Failure recovery

If promoted data fails to start or become healthy, the controller stops it, retains it at a
timestamped failed path, moves the prior directory back atomically, restarts the prior service,
and verifies health before returning failure. If that automatic recovery also fails, no candidate,
failed, or prior directory is deleted. The immutable promotion journal and status output report
every protected path and exact recovery guidance.

There is no destructive in-place restore and no purge command.

## Rollback is different

`evdb rollback [release]` restores deployment code, generated service definitions, and locked
images. It never changes a data directory or selects a snapshot. Use release rollback for a bad
deployment whose current data is still correct. Use restore plus promotion when database contents
must return to a backup point.

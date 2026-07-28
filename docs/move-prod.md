# Production migration boundary

Production conversion is a separate OpenSpec change. The `simplify-v1` implementation and validation
must not mutate `montreal-01` or attempt to convert its pre-v1 files.

Repository development and CI use disposable containers, temporary host paths, synthetic credentials,
and local Restic repositories only. The guarded VPS workflow may target an explicitly approved
non-production host and rejects `montreal-01` before any copy, remote command, or link change.

The future production change owns inventory, naming, data compatibility, credential import, routing,
scheduling, and cutover procedures. None of those procedures are part of the v1 development workflow.

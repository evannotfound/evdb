## MODIFIED Requirements

### Requirement: Idempotent host setup

Top-level `sudo evdb init` SHALL validate prerequisites and canonical source, create root-owned directories, initialize routing and Traefik, initialize or verify the one Restic repository, install the two systemd units, enable the backup timer, and finish with status. On an existing host it SHALL rerun those direct convergence steps without replacing `secrets.yml` values, the configured external rclone file, database data, or healthy database Compose projects.

Explicit native port flags SHALL replace only the corresponding host routing lists; omitted flags SHALL preserve existing lists. The operation SHALL reload existing source, validate the candidate, check requested port availability, and persist changed lists under the exclusive host operation lock before converging host assets. A port conflict SHALL leave source and runtime unchanged. Later convergence failures SHALL retain the readable desired configuration and report the failure without claiming successful cutover or automatically rolling back.

#### Scenario: Initialization runs twice
- **WHEN** an already initialized host runs `evdb init` with unchanged source
- **THEN** the second run verifies the repository and host assets without replacing credentials or restarting databases

#### Scenario: Repository path is absent
- **WHEN** initialization reaches a configured missing rclone repository path
- **THEN** it initializes that path before enabling the backup timer

#### Scenario: Initialization fails midway
- **WHEN** one direct initialization command fails
- **THEN** evdb reports that command and preserves completed canonical files so the operator can correct the cause and rerun init

#### Scenario: Port bindings are expanded during migration
- **WHEN** an initialized host supplies both its existing temporary ports and free standard ports
- **THEN** evdb saves the replacement lists and converges the router without changing database project settings, services, data, or credentials

#### Scenario: Installer refresh preserves alternate ports
- **WHEN** `evdb init --yes` runs without native port flags on a host configured with alternate ports
- **THEN** the configured lists and their order are retained

### Requirement: Prerequisite boundary

Initialization SHALL validate Docker with Compose, Restic 0.17 or newer, rclone, systemd, writable canonical roots, DNS routing input, one backup repository, and availability of every configured native host port. It SHALL NOT require host Python, a Python package manager, or `uv`, and SHALL NOT install or upgrade unrelated host packages.

Availability checks SHALL exempt only requested ports actually published by the running, ownership-verified evdb Traefik container. They SHALL check newly requested ports even when that router is already running. A stopped container SHALL NOT exempt any ports. Standard ports not requested by evdb SHALL NOT block initialization beside the existing router.

#### Scenario: Port 5432 belongs to another proxy
- **WHEN** initialization requests 5432 and detects an unrelated listener on that port
- **THEN** it fails before changing configuration or Traefik and names the occupied port

#### Scenario: Restic is too old
- **WHEN** the installed Restic version predates deterministic missing-repository exit codes
- **THEN** initialization names version 0.17 as the minimum and does not inspect or initialize the repository

#### Scenario: Python is absent
- **WHEN** the standalone release initializes a host without Python or `uv`
- **THEN** neither development tool is reported as a missing prerequisite

#### Scenario: Existing router keeps standard ports
- **WHEN** an unrelated router owns 5432 and 6379 and evdb requests available ports 15432 and 16379
- **THEN** the native port check succeeds without stopping or changing the unrelated router

#### Scenario: Running evdb router adds an occupied standard port
- **WHEN** evdb owns 15432, another router owns 5432, and the requested Postgres list becomes `[15432, 5432]`
- **THEN** evdb accepts its existing binding but rejects occupied port 5432 before source or runtime changes

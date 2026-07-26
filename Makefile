UV ?= uv
RUFF ?= $(UV) run ruff
PYTEST ?= $(UV) run pytest
PYINSTALLER ?= $(UV) run pyinstaller
EVDB ?= /usr/local/bin/evdb

YES_ARG = $(if $(filter yes,$(YES)),--yes,)
DB_ARG = $(if $(DB),"$(DB)",)
BACKUP_ARG = $(if $(BACKUP),"$(BACKUP)",)
ENGINE_ARG = $(if $(ENGINE),--engine "$(ENGINE)",)
LINES_ARG = $(if $(LINES),--lines "$(LINES)",)
JSON_ARG = $(if $(filter yes,$(JSON)),--json,)

.PHONY: help check lint test unit config-check restic-integration integration-collect binary status \
	database-list database-add database-info database-configure database-start \
	database-stop database-restart database-logs backup-create backup-list backup-test \
	restore host-check host-setup host-update require-db require-project-role require-version

help:
	@printf '%s\n' \
		'Usage: make TARGET [DB=project/role] [YES=yes]' \
		'Status: status database-list database-info' \
		'Database: database-add database-configure database-start database-stop database-restart database-logs' \
		'Recovery: backup-create backup-list backup-test restore' \
		'Host: host-check host-setup host-update' \
		'Development: check lint test unit config-check restic-integration integration-collect binary'

check: lint test restic-integration integration-collect

lint:
	$(RUFF) check src tests tools
	$(RUFF) format --check src tests tools

test: unit config-check

unit:
	$(PYTEST) tests/unit

config-check:
	$(PYTEST) tests/config

restic-integration:
	@if command -v restic >/dev/null 2>&1; then \
		$(PYTEST) tests/integration/test_restic.py; \
	else \
		printf '%s\n' 'restic unavailable; skipping local Restic integration tests'; \
	fi

integration-collect:
	$(PYTEST) --collect-only -q tests/integration

binary:
	$(PYINSTALLER) --clean --noconfirm --onefile --name evdb --paths src src/evanovation_db/__main__.py

require-db:
	@test -n "$(DB)" || (printf '%s\n' 'DB is required: DB=example-prod-01/postgres' >&2; exit 2)

require-project-role:
	@test -n "$(PROJECT)" || (printf '%s\n' 'PROJECT is required: PROJECT=example-prod-01' >&2; exit 2)
	@test "$(ROLE)" = "postgres" -o "$(ROLE)" = "kv" || (printf '%s\n' 'ROLE must be postgres or kv' >&2; exit 2)

require-version:
	@test -n "$(VERSION)" || (printf '%s\n' 'VERSION is required: VERSION=1.2.3' >&2; exit 2)

status:
	$(EVDB) status $(DB_ARG) $(JSON_ARG)

database-list:
	$(EVDB) database list

database-add: require-project-role
	$(EVDB) database add "$(PROJECT)" "$(ROLE)" $(ENGINE_ARG) $(YES_ARG)

database-info: require-db
	$(EVDB) database info "$(DB)"

database-configure: require-db
	$(EVDB) database configure "$(DB)" $(ARGS) $(YES_ARG)

database-start database-stop database-restart: require-db
	$(EVDB) database $(patsubst database-%,%,$@) "$(DB)" $(YES_ARG)

database-logs: require-db
	$(EVDB) database logs "$(DB)" $(LINES_ARG)

backup-create: require-db
	$(EVDB) backup create "$(DB)" $(YES_ARG)

backup-list: require-db
	$(EVDB) backup list "$(DB)"

backup-test: require-db
	$(EVDB) backup test "$(DB)" $(BACKUP_ARG) $(YES_ARG)

restore: require-db
	$(EVDB) restore "$(DB)" $(BACKUP_ARG) $(YES_ARG)

host-check:
	$(EVDB) host check

host-setup:
	$(EVDB) host setup $(ARGS) $(YES_ARG)

host-update: require-version
	$(EVDB) host update "$(VERSION)" $(YES_ARG)

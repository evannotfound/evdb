UV ?= uv
RUFF ?= $(UV) run ruff
PYTEST ?= $(UV) run pytest
PYINSTALLER ?= $(UV) run pyinstaller
EVDB ?= /usr/local/bin/evdb

ENGINE_ARG = $(if $(ENGINE),--engine "$(ENGINE)",)
LINES_ARG = $(if $(LINES),--lines "$(LINES)",)
JSON_ARG = $(if $(filter yes,$(JSON)),--json,)

.PHONY: help check lint test unit config-check restic-integration integration-collect binary init status \
	database-list database-add database-info database-configure database-start \
	database-stop database-restart database-logs backup-create backup-create-all backup-list \
	require-db require-project-role

help:
	@printf '%s\n' \
		'Usage: make TARGET [DB=project/role] [PROJECT=project] [ROLE=postgres|kv]' \
		'Host: init status' \
		'Status: status database-list database-info' \
		'Database: database-add database-configure database-start database-stop database-restart database-logs' \
		'Backup: backup-create backup-create-all backup-list' \
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
	$(PYINSTALLER) --clean --noconfirm --onefile --name evdb --paths src src/evdb/__main__.py

require-db:
	@test -n "$(DB)" || (printf '%s\n' 'DB is required: DB=example-prod-01/postgres' >&2; exit 2)

require-project-role:
	@test -n "$(PROJECT)" || (printf '%s\n' 'PROJECT is required: PROJECT=example-prod-01' >&2; exit 2)
	@test "$(ROLE)" = "postgres" -o "$(ROLE)" = "kv" || (printf '%s\n' 'ROLE must be postgres or kv' >&2; exit 2)

init:
	$(EVDB) init $(ARGS)

status:
	$(EVDB) status $(JSON_ARG)

database-list:
	$(EVDB) database list

database-add: require-project-role
	$(EVDB) database add "$(PROJECT)" "$(ROLE)" $(ENGINE_ARG)

database-info: require-db
	$(EVDB) database info "$(DB)"

database-configure: require-db
	$(EVDB) database configure "$(DB)" $(ARGS)

database-start database-stop database-restart: require-db
	$(EVDB) database $(patsubst database-%,%,$@) "$(DB)"

database-logs: require-db
	$(EVDB) database logs "$(DB)" $(LINES_ARG)

backup-create: require-db
	$(EVDB) backup create "$(DB)"

backup-create-all:
	$(EVDB) backup create --all

backup-list: require-db
	$(EVDB) backup list "$(DB)"

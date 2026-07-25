UV ?= uv
PYTHON ?= $(UV) run python
RUFF ?= $(UV) run ruff
PYTEST ?= $(UV) run pytest
EVDB ?= $(UV) run evdb
YES_ARG = $(if $(filter yes,$(YES)),--yes,)
DB_ARG = $(if $(DB),"$(DB)",)
BACKUP_ARG = $(if $(BACKUP),"$(BACKUP)",)
LINES_ARG = $(if $(LINES),--lines "$(LINES)",)

.PHONY: help check lint test config-check compose-check ansible-check systemd-check \
	validate plan apply create show status start stop restart logs backup backups \
	backup-check releases rollback restore promote require-config require-db require-create \
	require-snapshot require-restore-id require-release

help:
	@printf '%s\n' \
		'Usage: make TARGET CONFIG=path [DB=selector] [YES=yes]' \
		'Operator: validate plan apply status releases' \
		'Database: create show start stop restart logs backup backups backup-check' \
		'Recovery: restore promote rollback' \
		'Developer: check lint test config-check compose-check ansible-check systemd-check' \
		'Run the selected target without arguments to see its required variables.'

check: lint test config-check compose-check ansible-check systemd-check

lint:
	$(RUFF) check src tests
	$(RUFF) format --check src tests

test:
	$(PYTEST)

config-check:
	$(EVDB) --config config/montreal-01 validate

compose-check:
	$(PYTEST) tests/config/test_compose.py

ansible-check:
	$(UV) run ansible-playbook --syntax-check -i ansible/test-hosts.yml ansible/backup.yml
	$(UV) run ansible-playbook --syntax-check -i ansible/test-hosts.yml ansible/databases.yml
	$(UV) run ansible-playbook --syntax-check -i ansible/test-hosts.yml ansible/restore.yml

systemd-check:
	systemd-analyze verify systemd/*.service systemd/*.timer

require-config:
	@test -n "$(CONFIG)" || (printf '%s\n' 'CONFIG is required: CONFIG=config/<host>' >&2; exit 2)

require-db:
	@test -n "$(DB)" || (printf '%s\n' 'DB is required: DB=<name|type/name>' >&2; exit 2)

require-create:
	@test "$(TYPE)" = "postgres" -o "$(TYPE)" = "redis" -o "$(TYPE)" = "dragonfly" || (printf '%s\n' 'TYPE must be postgres, redis, or dragonfly' >&2; exit 2)
	@test -n "$(NAME)" || (printf '%s\n' 'NAME is required: NAME=example-prod-01' >&2; exit 2)

require-snapshot:
	@test -n "$(SNAPSHOT)" || (printf '%s\n' 'SNAPSHOT is required: SNAPSHOT=latest' >&2; exit 2)

require-restore-id:
	@test -n "$(RESTORE_ID)" || (printf '%s\n' 'RESTORE_ID is required' >&2; exit 2)

require-release:
	@test -n "$(RELEASE)" || (printf '%s\n' 'RELEASE is required' >&2; exit 2)

validate: require-config
	$(EVDB) --config "$(CONFIG)" validate

plan: require-config
	$(EVDB) --config "$(CONFIG)" plan

apply: require-config
	$(EVDB) --config "$(CONFIG)" apply $(YES_ARG)

create: require-config require-create
	$(EVDB) --config "$(CONFIG)" create "$(TYPE)" "$(NAME)" $(YES_ARG)

show: require-config require-db
	$(EVDB) --config "$(CONFIG)" show "$(DB)"

status: require-config
	$(EVDB) --config "$(CONFIG)" status $(DB_ARG)

start stop restart: require-config require-db
	$(EVDB) --config "$(CONFIG)" $@ "$(DB)" $(YES_ARG)

logs: require-config require-db
	$(EVDB) --config "$(CONFIG)" logs "$(DB)" $(LINES_ARG)

backup backups: require-config require-db
	$(EVDB) --config "$(CONFIG)" $@ "$(DB)"

backup-check: require-config require-db
	$(EVDB) --config "$(CONFIG)" backup-check "$(DB)" $(BACKUP_ARG)

releases: require-config
	$(EVDB) --config "$(CONFIG)" releases

rollback: require-config require-release
	$(EVDB) --config "$(CONFIG)" rollback "$(RELEASE)" $(YES_ARG)

restore: require-config require-db require-snapshot
	$(EVDB) --config "$(CONFIG)" restore "$(DB)" --snapshot "$(SNAPSHOT)"

promote: require-config require-db require-restore-id
	$(EVDB) --config "$(CONFIG)" promote "$(DB)" "$(RESTORE_ID)" $(YES_ARG)

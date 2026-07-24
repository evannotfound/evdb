UV ?= uv
PYTHON ?= $(UV) run python
RUFF ?= $(UV) run ruff
PYTEST ?= $(UV) run pytest
APP = $(PYTHON) -m evanovation_db.cli

.PHONY: check lint test config-check compose-check ansible-check systemd-check plan deploy-backup deploy-db backup restore-check status

check: lint test config-check compose-check ansible-check systemd-check

lint:
	$(RUFF) check src tests
	$(RUFF) format --check src tests

test:
	$(PYTEST)

config-check:
	$(APP) validate --source config/montreal-01

compose-check:
	$(PYTEST) tests/config/test_compose.py

ansible-check:
	$(UV) run ansible-playbook --syntax-check -i ansible/test-hosts.yml ansible/backup.yml
	$(UV) run ansible-playbook --syntax-check -i ansible/test-hosts.yml ansible/databases.yml
	$(UV) run ansible-playbook --syntax-check -i ansible/test-hosts.yml ansible/restore.yml

systemd-check:
	systemd-analyze verify systemd/*.service systemd/*.timer

plan:
	@test -n "$(HOST)" || (printf '%s\n' 'HOST is required: make plan HOST=test' >&2; exit 2)
	$(UV) run ansible-playbook --check --diff -i $(if $(filter test,$(HOST)),ansible/test-hosts.yml,ansible/hosts.yml) ansible/backup.yml -e target=$(HOST)

deploy-backup:
	@test -n "$(HOST)" || (printf '%s\n' 'HOST is required' >&2; exit 2)
	@test "$(APPLY)" = "yes" || (printf '%s\n' 'APPLY=yes is required' >&2; exit 2)
	$(UV) run ansible-playbook -i ansible/hosts.yml ansible/backup.yml -e target=$(HOST) -e apply=yes

deploy-db:
	@test -n "$(HOST)" || (printf '%s\n' 'HOST is required' >&2; exit 2)
	@test "$(APPLY)" = "yes" || (printf '%s\n' 'APPLY=yes is required' >&2; exit 2)
	$(UV) run ansible-playbook -i ansible/hosts.yml ansible/databases.yml -e target=$(HOST) -e apply=yes

backup:
	@test "$(ENGINE)" = "postgres" -o "$(ENGINE)" = "kv" || (printf '%s\n' 'ENGINE must be postgres or kv' >&2; exit 2)
	@test -n "$(NAME)" || (printf '%s\n' 'NAME is required: make backup NAME=test-dev-01' >&2; exit 2)
	$(APP) backup $(ENGINE) $(NAME)

restore-check:
	@test "$(ENGINE)" = "postgres" -o "$(ENGINE)" = "kv" || (printf '%s\n' 'ENGINE must be postgres or kv' >&2; exit 2)
	@test -n "$(NAME)" || (printf '%s\n' 'NAME is required: make restore-check NAME=test-dev-01' >&2; exit 2)
	$(APP) restore-check $(ENGINE) $(NAME)

status:
	$(APP) status

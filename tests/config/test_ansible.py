from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
ANSIBLE = ROOT / "ansible"


def test_required_roles_and_playbooks_exist():
    roles = {path.parent.parent.name for path in (ANSIBLE / "roles").glob("*/tasks/main.yml")}
    assert roles == {"app", "backup", "base", "kv", "postgres", "traefik"}
    assert all(
        (ANSIBLE / name).is_file() for name in ("backup.yml", "databases.yml", "restore.yml")
    )
    assert (ANSIBLE / "test-hosts.yml").is_file()


def test_production_is_not_the_default_and_requires_apply():
    for name in ("backup.yml", "databases.yml", "restore.yml"):
        text = (ANSIBLE / name).read_text()
        assert "target | default('test')" in text
        assert "target | default('') == 'production'" in text
        assert "'production' not in group_names" in text
        assert "ansible_check_mode or apply | default('no') == 'yes'" in text
        assert "evdb_prod" not in text

    defaults = (ANSIBLE / "group_vars/all.yml").read_text()
    assert "apply | default('no') == 'yes'" in defaults
    assert "evdb_write:" in defaults
    assert "evdb_prod" not in defaults

    production = yaml.safe_load((ANSIBLE / "hosts.yml").read_text())
    assert "test" not in production["all"]["children"]
    assert "montreal-01" in production["all"]["children"]["production"]["hosts"]


def test_roles_consume_only_normalized_controller_input_and_filter_databases():
    role_text = "\n".join(
        path.read_text() for path in (ANSIBLE / "roles").rglob("*.yml") if path.is_file()
    )
    postgres = (ANSIBLE / "roles/postgres/tasks/main.yml").read_text()
    kv = (ANSIBLE / "roles/kv/tasks/main.yml").read_text()

    assert "evdb_config.host" in role_text
    assert "evdb_config.databases" in role_text
    assert "include_vars" not in role_text
    assert "evdb_source_dir" not in role_text
    assert "postgres.yml" not in role_text
    assert "kv.yml" not in role_text
    assert "selectattr('engine', 'equalto', 'postgres')" in postgres
    assert "rejectattr('engine', 'equalto', 'postgres')" in kv
    assert "instance.image" in postgres + kv
    assert "instance.data" in postgres + kv
    assert "instance.settings" in postgres + kv
    assert "instance.http" in postgres + kv


def test_ansible_bootstrap_never_accepts_or_writes_protected_files():
    text = (ANSIBLE / "roles/backup/tasks/main.yml").read_text()

    assert "evdb_secret_files" not in text
    assert "Stage protected runtime files" not in text
    assert "ansible.builtin.command:" not in text
    assert "shell:" not in text


def test_rclone_seed_is_never_overwritten():
    text = (ANSIBLE / "roles/backup/tasks/main.yml").read_text()
    assert "rclone.conf" not in text


def test_ansible_installs_assets_without_compose_or_release_lifecycle():
    text = "\n".join(path.read_text() for path in ANSIBLE.rglob("*") if path.is_file())

    assert "host-runtime/src" not in text
    assert "evdb_runtime_root" in text
    assert "docker compose" not in text
    assert "tasks_from: activate" not in text
    assert "state: restarted" not in text
    assert "/etc/systemd/system" not in text
    assert "systemd_service" not in text


def test_bootstrap_refreshes_only_dedicated_normalized_runtime():
    backup = (ANSIBLE / "roles/backup/tasks/main.yml").read_text()

    assert "evdb_bootstrap_runtime" in backup
    assert "force: false" not in backup
    assert "Remove stale bootstrap runtime database JSON" in backup
    assert "{{ evdb_config_dir }}/host.json" not in backup
    assert "jobs/" not in backup


def test_external_http_proxy_is_not_managed():
    text = "\n".join(path.read_text() for path in ANSIBLE.rglob("*") if path.is_file())

    assert "evdb_" + "n" + "pm" not in text
    assert "nginx-" + "proxy-manager" not in text
    assert "/nginx/proxy-hosts" not in text
    assert "ansible.builtin.uri:" not in text

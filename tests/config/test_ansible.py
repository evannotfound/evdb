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
    assert "'production' not in group_names" in defaults
    assert "evdb_prod" not in defaults

    production = yaml.safe_load((ANSIBLE / "hosts.yml").read_text())
    assert "test" not in production["all"]["children"]
    assert "montreal-01" in production["all"]["children"]["production"]["hosts"]


def test_secret_resolution_is_controller_side_hidden_and_check_safe():
    secret_files = [
        ANSIBLE / "roles/backup/tasks/main.yml",
        ANSIBLE / "roles/postgres/tasks/secret.yml",
        ANSIBLE / "roles/kv/tasks/secret.yml",
    ]

    for path in secret_files:
        text = path.read_text()
        assert "delegate_to: localhost" in text
        assert "no_log: true" in text
        assert "ansible.builtin.command:" in text
        assert "argv:" in text
        assert "shell:" not in text

    for path in secret_files:
        if path.name != "secret.yml":
            assert "not ansible_check_mode" in path.read_text()


def test_rclone_seed_is_never_overwritten():
    text = (ANSIBLE / "roles/backup/tasks/main.yml").read_text()
    assert "Check mutable rclone config" in text
    assert "not evdb_rclone_file.stat.exists" in text
    assert "force: false" in text


def test_external_http_proxy_is_not_managed():
    text = "\n".join(path.read_text() for path in ANSIBLE.rglob("*") if path.is_file())

    assert "evdb_" + "n" + "pm" not in text
    assert "nginx-" + "proxy-manager" not in text
    assert "/nginx/proxy-hosts" not in text
    assert "ansible.builtin.uri:" not in text

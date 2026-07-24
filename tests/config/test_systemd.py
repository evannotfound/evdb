import configparser
from pathlib import Path

ROOT = Path(__file__).parents[2]
SYSTEMD = ROOT / "systemd"


def unit(path: Path) -> configparser.ConfigParser:
    result = configparser.ConfigParser(interpolation=None, strict=False)
    result.optionxform = str
    result.read(path)
    return result


def test_job_families_exist():
    names = {path.name for path in SYSTEMD.iterdir()}
    assert names == {
        "evanovation-db-backup@.service",
        "evanovation-db-backup@.timer",
        "evanovation-db-status.service",
        "evanovation-db-status.timer",
        "evanovation-db-restore.service",
        "evanovation-db-restore.timer",
        "evanovation-db-weekly@.service",
        "evanovation-db-weekly@.timer",
        "evanovation-db-monthly@.service",
        "evanovation-db-monthly@.timer",
    }


def test_timers_are_persistent_and_randomized():
    for path in SYSTEMD.glob("*.timer"):
        timer = unit(path)["Timer"]
        assert timer["Persistent"] == "true"
        assert timer["RandomizedDelaySec"]
        assert timer["FixedRandomDelay"] == "true"
        assert timer["Unit"].endswith(".service")


def test_services_have_limits_and_low_priority():
    for path in SYSTEMD.glob("*.service"):
        service = unit(path)["Service"]
        assert service["User"] == "evanovation-db"
        assert service["Group"] == "evanovation-db"
        assert service["TimeoutStartSec"]
        assert int(service["Nice"]) >= 10
        assert service["IOSchedulingClass"] == "idle"
        assert int(service["CPUWeight"]) <= 20
        assert int(service["IOWeight"]) <= 20


def test_deployment_keeps_timers_disabled_by_default():
    ansible = ROOT / "ansible"
    defaults = (ansible / "group_vars/all.yml").read_text()
    role = (ansible / "roles/backup/tasks/main.yml").read_text()

    assert "evdb_enable_timers: false" in defaults
    assert 'enabled: "{{ evdb_enable_timers | bool }}"' in role

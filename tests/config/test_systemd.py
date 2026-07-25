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
        assert service["Environment"] == "PYTHONPATH=/opt/evanovation-db/current/src"
        assert (
            "-m evanovation_db.cli --config /opt/evanovation-db/current/runtime"
            in service["ExecStart"]
        )


def test_units_execute_the_active_release_runtime_code():
    text = "\n".join(path.read_text() for path in SYSTEMD.glob("*.service"))

    assert "/current/src" in text
    assert "/host-runtime/" not in text
    assert "/releases/" not in text


def test_deployment_preserves_timer_state_without_explicit_migration():
    deployment = (ROOT / "src/evanovation_db/deployment.py").read_text()
    ansible = "\n".join(path.read_text() for path in (ROOT / "ansible").rglob("*.yml"))

    assert '["systemctl", "daemon-reload"]' in deployment
    assert "systemd_service" not in ansible
    assert "enabled:" not in ansible
    assert "state: started" not in ansible
    assert "state: stopped" not in ansible


def test_weekly_maintenance_rotates_data_checks():
    text = (SYSTEMD / "evanovation-db-weekly@.service").read_text()

    assert "maintain check %i\n" in text
    assert "maintain check %i --rotate" in text

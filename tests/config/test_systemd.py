from pathlib import Path

ROOT = Path(__file__).parents[2]
UNITS = ROOT / "src/evdb/units"


def test_package_has_exactly_one_low_priority_backup_service_and_timer():
    assert {path.name for path in UNITS.iterdir()} == {
        "evdb-backup.service",
        "evdb-backup.timer",
    }
    service = (UNITS / "evdb-backup.service").read_text()
    timer = (UNITS / "evdb-backup.timer").read_text()
    assert "evdb backup create --all" in service
    assert "User=" not in service and "Group=" not in service
    assert "Nice=" in service and "IOSchedulingClass=idle" in service
    assert "TimeoutStartSec=" in service
    assert "OnCalendar=daily" in timer
    assert "Persistent=true" in timer
    assert "RandomizedDelaySec=" in timer

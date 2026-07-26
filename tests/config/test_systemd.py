from pathlib import Path

ROOT = Path(__file__).parents[2]
UNITS = ROOT / "src/evanovation_db/units"


def test_package_contains_canonical_public_command_units():
    names = {path.name for path in UNITS.iterdir()}

    assert names == {
        "evdb-backup@.service",
        "evdb-backup@.timer",
        "evdb-status.service",
        "evdb-status.timer",
        "evdb-backup-test.service",
        "evdb-backup-test.timer",
        "evdb-retention.service",
        "evdb-retention.timer",
        "evdb-prune.service",
        "evdb-prune.timer",
        "evdb-repository-check.service",
        "evdb-repository-check.timer",
    }
    text = "\n".join(path.read_text() for path in UNITS.iterdir())
    assert "/usr/local/bin/evdb" in text
    assert "evanovation-db" not in text
    assert "PYTHONPATH" not in text
    assert "active release" not in text


def test_timers_are_persistent_randomized_and_low_priority():
    for timer in UNITS.glob("*.timer"):
        text = timer.read_text()
        assert "Persistent=true" in text
        assert "RandomizedDelaySec=" in text
    for service in UNITS.glob("*.service"):
        text = service.read_text()
        assert "TimeoutStartSec=" in text
        assert "Nice=" in text
        assert "IOSchedulingClass=idle" in text
        assert "User=evdb" in text

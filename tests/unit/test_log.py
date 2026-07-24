import json

from evanovation_db.log import write


def test_structured_log_has_fields_and_redacts(capsys):
    write(
        "backup_failed",
        secrets=("secret-value",),
        host="test-host",
        instance="test-db",
        command="backup",
        step="engine",
        result="failed",
        duration=1.25,
        error="secret-value failed",
    )

    data = json.loads(capsys.readouterr().err)
    assert data["host"] == "test-host"
    assert data["instance"] == "test-db"
    assert data["duration"] == 1.25
    assert data["error"] == "<redacted> failed"

import json

from evanovation_db.log import sanitize, write


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


def test_generic_redaction_covers_urls_tokens_and_op_references(capsys):
    write(
        "database_log",
        host="test-host",
        project="app-test-01",
        role="kv",
        api_token="token-value",
        error="rediss://default:hunter2@db.example/0 op://vault/item/password",
    )

    data = json.loads(capsys.readouterr().err)
    assert data["api_token"] == "<redacted>"
    assert data["error"] == "rediss://<redacted>@db.example/0 <redacted>"

    text = sanitize(
        "password=private token:abc Authorization: Bearer bearer-value op://vault/item/token"
    )
    assert "private" not in text
    assert "abc" not in text
    assert "bearer-value" not in text
    assert "op://" not in text

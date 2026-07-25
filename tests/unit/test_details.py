import json

from evanovation_db import details
from evanovation_db.run import Result


def test_remote_details_gather_running_container_release_version_and_backup(
    config, tmp_path, monkeypatch
):
    instance = config.select("postgres/test-dev-01")
    release = tmp_path / "releases/release-123"
    release.mkdir(parents=True)
    link = tmp_path / "current"
    link.symlink_to(release)
    monkeypatch.setattr(details, "RELEASE_LINK", link)
    state = config.host.state_dir / "state/postgres/test-dev-01.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps(
            {
                "backup": {
                    "finished": "2026-07-25T10:00:00+00:00",
                    "upload": {"ok": False},
                },
                "upload": {
                    "ok": True,
                    "time": "2026-07-25T10:01:00+00:00",
                    "snapshot": "snapshot-123",
                },
            }
        )
    )
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ["docker", "inspect"]:
            data = [
                {
                    "State": {
                        "Status": "running",
                        "Running": True,
                        "Health": {"Status": "healthy"},
                    },
                    "Config": {
                        "Image": "postgres:16@sha256:live",
                        "Labels": {details.CONTRACT_LABEL: "contract-live"},
                    },
                    "Image": "sha256:image-id",
                }
            ]
            return Result(tuple(args), 0, json.dumps(data), "")
        return Result(tuple(args), 0, "postgres (PostgreSQL) 16.9\n", "")

    monkeypatch.setattr(details, "run", fake_run)

    result = details.remote(config, instance)

    assert result == {
        "selector": "postgres/test-dev-01",
        "state": "running",
        "running": True,
        "health": "healthy",
        "engine_version": "postgres (PostgreSQL) 16.9",
        "image": "postgres:16@sha256:live",
        "image_id": "sha256:image-id",
        "service_hash": "contract-live",
        "active_release": "release-123",
        "backup": {
            "finished": "2026-07-25T10:00:00+00:00",
            "uploaded": "2026-07-25T10:01:00+00:00",
            "snapshot": "snapshot-123",
            "upload_ok": True,
        },
    }
    assert [call[0] for call in calls] == [
        ["docker", "inspect", instance.container],
        ["docker", "exec", instance.container, "postgres", "--version"],
    ]
    assert all(call[1] == {"timeout": 120, "check": False} for call in calls)


def test_remote_details_report_stopped_without_engine_command(config, tmp_path, monkeypatch):
    instance = config.select("dragonfly/vercount-prod-01")
    monkeypatch.setattr(details, "RELEASE_LINK", tmp_path / "missing")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        data = [
            {
                "State": {"Status": "exited", "Running": False},
                "Config": {"Image": instance.image},
                "Image": "sha256:stopped",
            }
        ]
        return Result(tuple(args), 0, json.dumps(data), "")

    monkeypatch.setattr(details, "run", fake_run)

    result = details.remote(config, instance)

    assert result["state"] == "exited"
    assert result["running"] is False
    assert result["health"] == "exited"
    assert result["engine_version"] is None
    assert result["active_release"] is None
    assert result["backup"] is None
    assert calls == [["docker", "inspect", instance.container]]


def test_remote_details_report_absent_container_without_leaking_error(
    config, tmp_path, monkeypatch
):
    instance = config.select("redis/xai-server-prod-01")
    monkeypatch.setattr(details, "RELEASE_LINK", tmp_path / "missing")
    monkeypatch.setattr(
        details,
        "run",
        lambda args, **kwargs: Result(tuple(args), 1, "", "error containing protected data"),
    )

    result = details.remote(config, instance)

    assert result["state"] == "absent"
    assert result["health"] == "unknown"
    assert "protected data" not in json.dumps(result)
